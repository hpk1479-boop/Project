/* Real settings page in Edge with mocked settings APIs: 라이브 알림 기록 is 사용 / 사용 안 함, with no folder input. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
fs.mkdirSync(evidence, {recursive: true});
const config = {
    live: [{key: 'SYMBOLS', secret: false, configured: null, value: 'XAUUSD+'},
           {key: 'ECONOMY_ENABLED', secret: false, configured: null, value: 'true'},
           // The user's older config.txt had no line for it: the server reports the default.
           {key: 'LIVE_RECORD_ENABLED', secret: false, configured: null, value: 'true'}],
    part2: {cores: null, overlap_trading_days: 3, capture_start: 'keyframe', oz_evaluation: 'selected', broker_symbols: {}},
    connections: {warehouse: '창고', python_executable: ''}, ai: {provider: 'disabled'}
};
const requests = [];
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const checks = [], errors = [];
    try {
        const page = await browser.newPage({viewport: {width: 1280, height: 1100}});
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', route => route.abort());
        await page.route('http://127.0.0.1:8763/**', async route => {
            const name = new URL(route.request().url()).pathname;
            if (!name.startsWith('/api/')) {
                const file = name === '/' ? 'index.html' : name.slice(1);
                return route.fulfill({body: fs.readFileSync(path.join(root, 'Part3/web', file)),
                    contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8'});
            }
            const data = route.request().postDataJSON();
            let body = {ok: true};
            if (name === '/api/init') body = {connections: {warehouse: ''}, recent: []};
            else if (name === '/api/mo/live/status') body = {modules: {}, lines: []};
            else if (name === '/api/ai/settings') body = {settings: config.ai, base_url: 'http://127.0.0.1:11434'};
            else if (name === '/api/ai/models') body = {available: false, models: []};
            else if (name === '/api/mo/settings' && !data) body = structuredClone(config);
            else if (name === '/api/mo/settings') {
                requests.push(data);
                if (data.group === 'live') for (const [key, value] of Object.entries(data.changes))
                    config.live.find(row => row.key === key).value = value;
                body = {ok: true, message: '설정이 저장되었습니다 · 라이브 다시 시작 후 적용'};
            }
            return route.fulfill({json: body});
        });
        await page.goto('http://127.0.0.1:8763/#token=live-record');
        await page.waitForFunction(() => !!state.meta);
        await page.evaluate(() => moView('settings'));
        const field = page.locator('[data-setting="LIVE_RECORD_ENABLED"]');
        await field.waitFor();

        assert.deepEqual(await field.locator('option').allTextContents(), ['사용', '사용 안 함']);
        assert.equal(await field.evaluate(select => select.selectedOptions[0].textContent), '사용');
        const screen = await page.locator('#view-settings').innerText();
        assert.match(screen, /라이브 알림 기록/);
        assert.equal(screen.includes('LIVE 기록 폴더'), false);
        assert.equal(await page.locator('[data-setting="live_records"]').count(), 0);
        for (const word of ['LIVE_RECORD_ENABLED', 'live_records']) assert.equal(screen.includes(word), false, word);
        checks.push('설정에 라이브 알림 기록이 사용 / 사용 안 함으로 보이고 폴더 입력칸은 없음');
        await page.screenshot({path: path.join(evidence, 'live_record_setting.png'), fullPage: true});

        await field.selectOption('false');
        await page.locator('[data-save-settings="live"]').click();
        await page.waitForFunction(() => document.querySelector('[data-setting="LIVE_RECORD_ENABLED"]').dataset.original === 'false');
        assert.deepEqual(requests.at(-1), {group: 'live', changes: {LIVE_RECORD_ENABLED: 'false'}});
        assert.equal(await field.evaluate(select => select.selectedOptions[0].textContent), '사용 안 함');
        // The result shows next to the button that was pressed, not at the bottom of the page.
        assert.equal(await page.locator('[data-save-status="live"]').textContent(), '설정이 저장되었습니다 · 라이브 다시 시작 후 적용');
        checks.push('사용 안 함으로 바꿔 저장하면 그 값 하나만 전송하고 버튼 옆에 저장 결과 표시');

        await field.selectOption('true');
        await page.locator('[data-save-settings="live"]').click();
        await page.waitForFunction(() => document.querySelector('[data-setting="LIVE_RECORD_ENABLED"]').dataset.original === 'true');
        assert.deepEqual(requests.at(-1), {group: 'live', changes: {LIVE_RECORD_ENABLED: 'true'}});
        checks.push('다시 사용으로 저장');

        const connections = await page.locator('#settings-connection-fields').innerText();
        assert.equal(connections.includes('LIVE'), false);
        checks.push('연결 위치에는 데이터 폴더와 파이썬 프로그램만 남음');
        assert.deepEqual(errors, []);
        fs.writeFileSync(path.join(evidence, 'live_record_ui_checks.json'), JSON.stringify({passed: true, checks, errors}, null, 2) + '\n');
        console.log(JSON.stringify({passed: true, checks: checks.length}));
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
