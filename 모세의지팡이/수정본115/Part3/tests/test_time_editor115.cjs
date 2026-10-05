/* Real web files in Edge; mocked strategy APIs. Nothing is written outside the evidence folder. */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
fs.mkdirSync(evidence, {recursive: true});

const SESSIONS = ['MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK'];
const TIMES = {MAIN_ASIA: '0900-1100', MAIN_LONDON: '1600-1800', MAIN_NEWYORK: '2100-2400'};
const HELPER = 'KST · 시작/종료 포함 · 자정 통과 지원';
const DEVELOPER_WORDS = ['모든 세션을 해제', '기본값은', '최종 알림이 없습니다', '24시간 알림이 옵니다', '코드 기본값', '알림 꺼짐', '선택한 세션 없음'];
const row = (name, defaults) => ({name, enabled: true, trigger: null, default_trigger: '브레이커 올존',
    time_filters: null, default_time_filters: defaults, load_error: null});
const specials = {
    session_labels: {MAIN_ASIA: '아시아', MAIN_LONDON: '런던', MAIN_NEWYORK: '뉴욕'}, session_times: TIMES,
    default_time_filters: {SPECIAL1: SESSIONS, SPECIAL3: TIMES, SPECIAL8: 0},
    strategy_names: {SPECIAL1: '세션 전략', SPECIAL3: '구간 전략', SPECIAL8: '시간 제한 없는 전략'},
    items: {SPECIAL1: row('세션 전략', SESSIONS), SPECIAL3: row('구간 전략', TIMES), SPECIAL8: row('시간 제한 없는 전략', 0)},
    trigger_choices: ['브레이커 올존', '무지성 올존'], uses_code_defaults: false, settings_error: null,
    needs_recovery: false, catalog_errors: []
};

(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const checks = [], errors = [], saved = [];
    try {
        const page = await browser.newPage({viewport: {width: 1280, height: 900}});
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
            else if (name === '/api/mo/live/specials' && data?.items) {
                saved.push(data.items); body = {ok: true, message: '전략 설정을 저장했습니다.', warnings: []};
            } else if (name === '/api/mo/live/specials') body = structuredClone(specials);
            return route.fulfill({json: body});
        });
        await page.goto('http://127.0.0.1:8763/#token=time-editor');
        await page.waitForFunction(() => !!state.meta);
        await page.evaluate(() => moOpenStrategyPopup('live'));
        await page.waitForSelector('#strategy-settings-cards [data-strategy="SPECIAL8"]');

        const card = id => page.locator('#strategy-settings-cards [data-strategy="' + id + '"]');
        const summary = async id => (await card(id).locator('.moses-strategy-time-summary').innerText()).trim();
        const draft = id => page.evaluate(id => moStrategyPopup.draft[id].time_filters, id);
        const line = index => page.locator('#strategy-editor-body .moses-strategy-time-editor > label').nth(index);
        const checkbox = index => line(index).locator('input[type=checkbox]');
        const clock = (index, part) => line(index).locator('input[type=text]').nth(part);
        const helper = () => page.locator('#strategy-editor-body small');
        const open = async id => {
            await card(id).locator('.moses-strategy-time').click();
            await page.waitForSelector('#strategy-editor-dialog[open]');
        };
        const editorOpen = () => page.evaluate(() => document.querySelector('#strategy-editor-dialog').open);
        const shot = name => page.screenshot({path: path.join(evidence, name + '.png')});

        // Cards show the real sessions and times, never a "default" label.
        const sessionsText = '아시아 09:00–11:00 · 런던 16:00–18:00 · 뉴욕 21:00–24:00';
        assert.equal(await summary('SPECIAL1'), sessionsText);
        assert.equal(await summary('SPECIAL3'), sessionsText);
        assert.equal(await summary('SPECIAL8'), '24시간');
        const cards = await page.locator('#strategy-settings-cards').innerText();
        for (const word of DEVELOPER_WORDS) assert.equal(cards.includes(word), false, word);
        assert.equal(await card('SPECIAL1').locator('.moses-strategy-time-item').count(), 3);
        assert.equal(await card('SPECIAL1').locator('.moses-strategy-time-item').first()
            .evaluate(item => getComputedStyle(item).whiteSpace), 'nowrap', '세션 하나가 두 줄로 끊기지 않음');
        checks.push('카드는 기본값 표기 없이 실제 세션·시간을 보여 주고, 시간 제한이 없으면 24시간');
        await shot('cards');

        // A strategy without its own time limit starts with nothing checked and plain helper text.
        await open('SPECIAL8');
        assert.equal((await page.locator('#strategy-editor-title').innerText()).trim(), '시간 제한 없는 전략 · 최종 알림 시간');
        for (const index of [0, 1, 2]) assert.equal(await checkbox(index).isChecked(), false);
        assert.equal(await clock(0, 0).inputValue(), '09:00');
        assert.equal(await clock(2, 1).inputValue(), '24:00');
        assert.equal((await helper().innerText()).trim(), HELPER);
        const editorText = await page.locator('#strategy-editor-dialog').innerText();
        for (const word of DEVELOPER_WORDS) assert.equal(editorText.includes(word), false, word);
        checks.push('시간 제한 없는 전략은 모두 해제로 시작하고 안내 문구는 한 줄뿐');
        await shot('editor_nothing_checked');

        // Saving keeps the editor open, says so briefly, then the helper text returns.
        await checkbox(0).check();
        await page.locator('#strategy-editor-save').click();
        assert.equal(await editorOpen(), true);
        assert.equal((await helper().innerText()).trim(), '설정되었습니다');
        assert.equal(await summary('SPECIAL8'), '아시아 09:00–11:00');
        assert.deepEqual(await draft('SPECIAL8'), {MAIN_ASIA: {enabled: true}, MAIN_LONDON: {enabled: false}, MAIN_NEWYORK: {enabled: false}});
        await shot('editor_saved_message');
        await page.waitForFunction(helperText => document.querySelector('#strategy-editor-body small').textContent.trim() === helperText, HELPER, {timeout: 6000});
        assert.equal(await editorOpen(), true);
        checks.push('저장하면 창이 닫히지 않고 "설정되었습니다"가 잠시 뜬 뒤 안내 문구로 돌아옴');

        // Unchecking it again equals the strategy's own setting, so nothing is stored.
        await checkbox(0).uncheck();
        await page.locator('#strategy-editor-save').click();
        assert.equal(await draft('SPECIAL8'), null);
        assert.equal(await summary('SPECIAL8'), '24시간');
        checks.push('화면이 전략 자체 설정과 같아지면 별도 설정을 저장하지 않음');

        // 기본값 changes the screen only; 저장 is what applies it.
        await page.locator('#strategy-editor-cancel').click();
        assert.equal(await editorOpen(), false);
        await open('SPECIAL1');
        await clock(0, 1).fill('12:00');
        await page.locator('#strategy-editor-save').click();
        assert.equal((await draft('SPECIAL1')).MAIN_ASIA.end, '1200');
        assert.equal(await summary('SPECIAL1'), '아시아 09:00–12:00 · 런던 16:00–18:00 · 뉴욕 21:00–24:00');
        await checkbox(2).uncheck();
        await clock(1, 0).fill('15:30');
        await page.locator('#strategy-editor-default').click();
        assert.equal(await editorOpen(), true);
        for (const index of [0, 1, 2]) assert.equal(await checkbox(index).isChecked(), true);
        assert.equal(await clock(0, 1).inputValue(), '11:00');
        assert.equal(await clock(1, 0).inputValue(), '16:00');
        assert.equal((await draft('SPECIAL1')).MAIN_ASIA.end, '1200', '기본값 버튼만으로는 설정이 바뀌지 않음');
        assert.equal(await summary('SPECIAL1'), '아시아 09:00–12:00 · 런던 16:00–18:00 · 뉴욕 21:00–24:00');
        assert.notEqual((await helper().innerText()).trim(), '설정되었습니다');
        await shot('editor_default_pressed');
        await page.locator('#strategy-editor-save').click();
        assert.equal(await draft('SPECIAL1'), null);
        assert.equal(await summary('SPECIAL1'), sessionsText);
        assert.equal(await editorOpen(), true);
        assert.equal((await helper().innerText()).trim(), '설정되었습니다');
        checks.push('기본값은 화면만 바꾸고 저장해야 적용되며, 둘 다 창을 닫지 않음');

        // 기본값 then closing without saving leaves the earlier setting.
        await page.locator('#strategy-editor-cancel').click();
        await open('SPECIAL3');
        await clock(1, 1).fill('19:00');
        await page.locator('#strategy-editor-save').click();
        assert.equal((await draft('SPECIAL3')).MAIN_LONDON.end, '1900');
        await page.locator('#strategy-editor-default').click();
        await page.locator('#strategy-editor-cancel').click();
        assert.equal((await draft('SPECIAL3')).MAIN_LONDON.end, '1900');
        assert.equal(await summary('SPECIAL3'), '아시아 09:00–11:00 · 런던 16:00–19:00 · 뉴욕 21:00–24:00');
        checks.push('기본값을 누르고 저장 없이 닫으면 이전 설정이 그대로');

        // A bad time keeps the editor open with a short message and applies nothing.
        await open('SPECIAL1');
        await clock(0, 0).fill('2500');
        await page.locator('#strategy-editor-save').click();
        assert.equal(await editorOpen(), true);
        assert.equal((await page.locator('#strategy-editor-error').innerText()).trim(), '시각은 00:00~24:00 범위로 입력하세요.');
        assert.equal(await draft('SPECIAL1'), null);
        await clock(0, 0).fill('09:00');
        await page.locator('#strategy-editor-save').click();
        assert.equal(await page.locator('#strategy-editor-error').isVisible(), false);
        await page.locator('#strategy-editor-cancel').click();
        checks.push('잘못된 시각은 짧은 안내만 띄우고 창을 유지');

        // What the strategy popup sends: the saved choice, or null for "the strategy's own".
        await open('SPECIAL8');
        await checkbox(1).check();
        await page.locator('#strategy-editor-save').click();
        await page.locator('#strategy-editor-cancel').click();
        await page.locator('#strategy-settings-apply').click();
        await page.waitForFunction(() => !document.querySelector('#strategy-settings-dialog').open);
        assert.equal(saved.length, 1);
        assert.equal(saved[0].SPECIAL1.time_filters, null);
        assert.deepEqual(saved[0].SPECIAL3.time_filters.MAIN_LONDON, {enabled: true, start: '1600', end: '1900'});
        assert.deepEqual(saved[0].SPECIAL8.time_filters, {MAIN_ASIA: {enabled: false}, MAIN_LONDON: {enabled: true}, MAIN_NEWYORK: {enabled: false}});
        checks.push('저장 요청에는 사용자가 정한 세션만 담기고 전략 기본 상태는 null');

        assert.deepEqual(errors, []);
        fs.writeFileSync(path.join(evidence, 'time_editor_checks.json'), JSON.stringify({passed: true, checks, errors,
            environment: 'real web files in Edge; mocked strategy APIs; no config or settings files written'}, null, 2) + '\n');
        console.log(JSON.stringify({passed: true, checks: checks.length}));
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
