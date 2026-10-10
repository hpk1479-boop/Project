/* 수정본177: the Gemini key field is the bot token field. The real settings page in Edge with mocked APIs
   runs one sequence of user actions on each field and compares what the screen shows at every step. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
fs.mkdirSync(evidence, {recursive: true});
const secret = (key, configured) => ({key, secret: true, configured});
const config = {
    live: [{key: 'SYMBOLS', secret: false, configured: null, value: 'XAUUSD+'}, secret('TELEGRAM_TOKEN', false),
           secret('TELEGRAM_CHAT_ID', false), secret('TELEGRAM_COMMAND_CHAT_IDS', false)],
    part2: {cores: null, overlap_trading_days: 3, capture_start: 'keyframe', oz_evaluation: 'selected', broker_symbols: {}},
    part2_cores: 8, connections: {warehouse: '창고', python_executable: ''},
    ai: {provider: 'gemini', gemini_model: 'gemini-2.5-flash', gemini_api_key_configured: false, timeout: 90, watch_enabled: true}
};
const configured = key => key === 'gemini_api_key' ? config.ai.gemini_api_key_configured : config.live.find(row => row.key === key).configured;
const setConfigured = (key, value) => {
    if (key === 'gemini_api_key') config.ai.gemini_api_key_configured = value;
    else config.live.find(row => row.key === key).configured = value;
};
const requests = [], held = [];
let hold = false, reject = false;
const until = async (condition, ms = 5000) => {
    for (const end = Date.now() + ms; !condition();) {
        if (Date.now() > end) throw Error('timed out waiting for a held request');
        await new Promise(resolve => setTimeout(resolve, 20));
    }
};
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const errors = [];
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
            if (data) requests.push({name, data});
            let body = {ok: true};
            if (name === '/api/init') body = {connections: {warehouse: ''}, recent: []};
            else if (name === '/api/mo/live/status') body = {modules: {}, lines: []};
            else if (name === '/api/ai/settings') body = {settings: config.ai};
            else if (name === '/api/ai/gemini-models') body = {available: true, models: ['gemini-2.5-flash']};
            else if (name === '/api/mo/settings' && !data) body = structuredClone(config);
            else if (name === '/api/mo/settings/telegram' || name === '/api/mo/settings/gemini_key') {
                if (hold) await new Promise(resolve => held.push(resolve));
                if (reject) return route.fulfill({status: 400, json: {error: '입력 ' + data.value + '을 확인하지 못했습니다.'}});
                if (data.value !== '') setConfigured(data.key, data.value !== null);
                body = {ok: true, key: data.key, configured: configured(data.key), message: '확인 응답'};
            }
            return route.fulfill({json: body});
        });
        await page.goto('http://127.0.0.1:8763/#token=secret-fields-177');
        await page.waitForFunction(() => !!state.meta);
        await page.evaluate(() => moView('settings'));
        await page.locator('[data-setting="gemini_api_key"]').waitFor();

        // The field itself: the same elements in the same places.
        const shape = key => page.evaluate(key => {
            const walk = node => [node.tagName.toLowerCase() + [...node.classList].sort().map(name => '.' + name).join(''),
                                  [...node.children].map(walk)];
            return walk(document.querySelector(`[data-setting="${key}"]`).closest('label'));
        }, key);
        assert.deepEqual(await shape('gemini_api_key'), await shape('TELEGRAM_TOKEN'));
        await page.locator('[data-setting="TELEGRAM_TOKEN"]').locator('xpath=ancestor::label').screenshot({path: path.join(evidence, 'bot_token_field.png')});
        await page.locator('[data-setting="gemini_api_key"]').locator('xpath=ancestor::label').screenshot({path: path.join(evidence, 'gemini_key_field.png')});

        async function run(key, route, group, other) {
            const input = page.locator(`[data-setting="${key}"]`), label = input.locator('xpath=ancestor::label');
            const button = label.locator('button.moses-telegram-confirm'), status = label.locator('.moses-telegram-status');
            const clear = label.locator('.mo-secret-clear');
            const sent = () => requests.filter(row => row.name === route).map(row => row.data);
            const phase = () => page.evaluate(key => document.querySelector(`[data-setting="${key}"]`)
                .closest('label').querySelector('.moses-telegram-status').dataset.phase || '', key);
            const settled = async (expected) => page.waitForFunction(([key, expected]) => document.querySelector(`[data-setting="${key}"]`)
                .closest('label').querySelector('.moses-telegram-status').dataset.phase === expected, [key, expected]);
            const trace = [];
            const snap = async step => trace.push({step, value: await input.inputValue(), type: await input.getAttribute('type'),
                placeholder: (await input.getAttribute('placeholder')).replace(/예: .*$/, '예: …'),
                button: await button.isVisible(), text: await button.textContent(), disabled: await button.isDisabled(),
                clear: await clear.isVisible(), status: await status.textContent(), phase: await phase(),
                role: await status.getAttribute('role')});
            await snap('처음');
            await input.fill('typed-secret-1'); await snap('입력');
            hold = true; await input.press('Enter');
            await until(() => held.length === 1);
            await snap('확인 중');
            // A second Enter while the first check is pending sends nothing.
            await input.press('Enter'); await new Promise(resolve => setTimeout(resolve, 150));
            assert.equal(sent().length, 1);
            hold = false; held.shift()(); await settled('success'); await snap('저장됨');
            await button.click(); await settled('success'); await snap('빈 칸 확인');
            reject = true; await input.fill('typed-secret-2'); await button.click(); await settled('error'); await snap('실패');
            reject = false; await input.fill('draft-3');
            await page.evaluate(async () => {await moSettingsLoad();}); await snap('다시 그림');
            await page.locator(`[data-setting="${other[0]}"]`).fill(other[1]);
            await page.evaluate(async group => {await moSettingsSave(group);}, group); await snap('그룹 저장');
            await input.fill(''); await clear.check(); await button.click(); await settled('success'); await snap('삭제');
            const groupSave = requests.filter(row => row.name === '/api/mo/settings' && row.data.group === group).at(-1).data;
            return {trace, sent: sent().map(row => ({...row, key: 'FIELD'})), groupSave};
        }
        const token = await run('TELEGRAM_TOKEN', '/api/mo/settings/telegram', 'live', ['SYMBOLS', 'XAUUSD+,NAS100']);
        const gemini = await run('gemini_api_key', '/api/mo/settings/gemini_key', 'ai', ['timeout', '120']);
        assert.deepEqual(gemini.trace, token.trace);
        assert.deepEqual(gemini.sent, token.sent);
        assert.deepEqual(token.sent.map(row => row.value), ['typed-secret-1', '', 'typed-secret-2', null]);
        assert.deepEqual(token.groupSave, {group: 'live', changes: {SYMBOLS: 'XAUUSD+,NAS100'}});
        assert.deepEqual(gemini.groupSave, {group: 'ai', changes: {timeout: 120}});
        const at = step => gemini.trace.find(row => row.step === step);
        assert.deepEqual([at('처음').placeholder, at('처음').button, at('처음').clear], ['미설정 · 예: …', false, false]);
        assert.deepEqual([at('확인 중').text, at('확인 중').disabled, at('확인 중').status],
                         ['확인 중', true, '연결을 확인하고 저장하는 중입니다.']);
        assert.deepEqual([at('저장됨').value, at('저장됨').placeholder, at('저장됨').button, at('저장됨').clear],
                         ['', '설정됨 · 변경할 때만 입력', true, true]);
        assert.deepEqual([at('실패').value, at('실패').status, at('실패').role], ['typed-secret-2', '입력 [숨김]을 확인하지 못했습니다.', 'alert']);
        assert.equal(at('다시 그림').value, 'draft-3');
        assert.equal(at('그룹 저장').value, 'draft-3');
        assert.deepEqual([at('삭제').placeholder, at('삭제').button, at('삭제').clear], ['미설정 · 예: …', false, false]);
        assert.deepEqual(errors, []);
        const report = {passed: true, steps: gemini.trace.map(row => row.step), trace: gemini.trace};
        fs.writeFileSync(path.join(evidence, 'secret_fields_same.json'), JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify({passed: true, steps: report.steps.length}));
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
