/* Real settings page in Edge with mocked APIs: no help text or removed settings, chats loaded
   from the bot after asking to pause live, and the Gemini key checked by its own button. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
fs.mkdirSync(evidence, {recursive: true});
const row = (key, value) => ({key, secret: false, configured: null, value});
const secret = (key, configured) => ({key, secret: true, configured});
const config = {
    live: [row('SYMBOLS', 'XAUUSD+,NAS100'), secret('TELEGRAM_TOKEN', true), secret('TELEGRAM_CHAT_ID', false),
           secret('TELEGRAM_COMMAND_CHAT_IDS', false), row('PRIVATE_LIVE_ALERTS_ENABLED', 'false'),
           row('PRIVATE_ECONOMY_ALERTS_ENABLED', 'false'), row('ECONOMY_ENABLED', 'true'), row('LIVE_RECORD_ENABLED', 'true'),
           row('STAFF_PIPE_NAME', '\\\\.\\pipe\\StaffOfMoses_v1'), row('STAFF_STALE_SEC', '30'), row('WONBI_SIGMA', '2.0'),
           row('MAIN_START', '0900'), row('MAIN_END', '0100'), row('ECONOMY_FETCH_SEC', '3600'), row('ECONOMY_POLL_SEC', '30')],
    part2: {cores: null, overlap_trading_days: 3, capture_start: 'keyframe', oz_evaluation: 'selected', broker_symbols: {}},
    part2_cores: 8,
    connections: {warehouse: '창고', python_executable: ''},
    ai: {provider: 'gemini', gemini_model: 'gemini-2.5-flash', gemini_api_key_configured: false, timeout: 90, watch_enabled: true}
};
const chats = [{id: '555', type: 'private', name: '홍길동', username: 'hong'},
               {id: '-100777', type: 'channel', name: '골드 알림', username: ''}];
const requests = [];
let rejectKey = false, liveRunning = true, noChats = false;
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
            if (data) requests.push({name, data});
            let body = {ok: true};
            if (name === '/api/init') body = {connections: {warehouse: ''}, recent: []};
            else if (name === '/api/mo/live/status') body = {modules: {}, lines: []};
            else if (name === '/api/ai/settings') body = {settings: config.ai};
            else if (name === '/api/ai/gemini-models') body = {available: true, models: ['gemini-2.5-flash', 'gemini-2.5-pro']};
            else if (name === '/api/mo/settings' && !data) body = structuredClone(config);
            else if (name === '/api/mo/settings/telegram_chats')
                body = liveRunning && !data.pause_live ? {ok: true, needs_live_pause: true} :
                    {ok: true, chats: noChats ? [] : chats, live_restarted: !!data.pause_live};
            else if (name === '/api/mo/settings/telegram')
                body = {ok: true, key: data.key, configured: true, message: '확인하고 저장했습니다 · 라이브 다시 시작 후 적용'};
            else if (name === '/api/mo/settings' && data.group === 'ai') {
                if (rejectKey) return route.fulfill({status: 400, json: {error: 'Gemini API 키 ' + data.changes.gemini_api_key + '를 확인하지 못했습니다.'}});
                config.ai.gemini_api_key_configured = true;
                body = {ok: true, message: '설정이 저장되었습니다'};
            }
            return route.fulfill({json: body});
        });
        await page.goto('http://127.0.0.1:8763/#token=settings-cleanup');
        await page.waitForFunction(() => !!state.meta);
        await page.evaluate(() => moView('settings'));
        await page.locator('#telegram-chats-load').waitFor();

        const view = page.locator('#view-settings');
        assert.equal(await view.locator('p:not(#settings-message):not(#settings-version)').count(), 0);
        const screen = await view.innerText();
        for (const word of ['자동', 'LoRA', 'LORA', '작업 단위', 'ZMQ', 'endpoint', 'STAFF_', 'TELEGRAM_', '알림받을 채팅창', '명령할 채팅창'])
            assert.equal(screen.includes(word), false, word);
        for (const key of ['work_size', 'base_model', 'adapter_path', 'load_in_4bit', 'local_files_only', 'STAFF_ENDPOINT',
                           'STAFF_BARS', 'CONFIG_RELOAD_SEC', 'OZ_COMMAND_FILE', 'COMPOSER_POLL_SEC'])
            assert.equal(await page.locator(`[data-setting="${key}"]`).count(), 0, key);
        assert.deepEqual(await page.locator('#ai-provider-select option').evaluateAll(rows => rows.map(row => row.value)),
            ['gemini', 'ollama', 'local_gguf', 'disabled']);
        for (const label of ['감시 종목', '봇 토큰', '알림방 (그룹·채널 번호)', '명령방 (내 텔레그램 번호)'])
            assert.match(screen, new RegExp(label.replace(/[()]/g, '\\$&')));
        assert.equal(await page.locator('[data-setting="cores"]').getAttribute('placeholder'), '8');
        // A group title never repeats the only field under it.
        const legends = await page.locator('#settings-live-fields legend').allTextContents();
        for (const legend of legends) assert.equal(await page.locator(`#settings-live-fields label:text-is("${legend}")`).count(), 0, legend);
        assert.deepEqual(legends.slice(0, 3), ['기본', '텔레그램', '명령방 알림']);
        checks.push('설명 문단·삭제한 설정·LoRA·작업 단위가 화면에 없고 텔레그램 칸 이름이 알림방/명령방');
        await page.screenshot({path: path.join(evidence, 'settings_page.png'), fullPage: true});

        // 1. Live is running: the user is asked first; cancelling sends nothing more.
        const dialogs = [];
        page.once('dialog', dialog => { dialogs.push(dialog.message()); dialog.dismiss(); });
        await page.locator('#telegram-chats-load').click();
        await page.waitForFunction(() => !document.querySelector('#telegram-chats-load').disabled);
        assert.deepEqual(dialogs, ['불러오려면 라이브 감시를 잠시 멈춰야 합니다. 멈추고 불러올까요?']);
        assert.deepEqual(requests.filter(row => row.name === '/api/mo/settings/telegram_chats').map(row => row.data), [{}]);
        assert.equal(await page.locator('.moses-telegram-chat').count(), 0);
        checks.push('라이브가 돌면 먼저 묻고, 취소하면 멈추지 않음');

        // 2. Accepting pauses live, lists the chats and reports the restart.
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#telegram-chats-load').click();
        await page.locator('.moses-telegram-chat').nth(1).waitFor();
        assert.deepEqual(requests.filter(row => row.name === '/api/mo/settings/telegram_chats').at(-1).data, {pause_live: true});
        const privateRow = page.locator('[data-chat="555"]'), channelRow = page.locator('[data-chat="-100777"]');
        assert.equal(await privateRow.locator('span').textContent(), '개인 · 홍길동 @hong · 555');
        assert.equal(await channelRow.locator('span').textContent(), '채널 · 골드 알림 · -100777');
        assert.deepEqual(await privateRow.locator('button').allTextContents(), ['알림방으로', '명령방으로']);
        assert.deepEqual(await channelRow.locator('button').allTextContents(), ['알림방으로']);
        assert.match(await page.locator('.moses-telegram-load .moses-telegram-status').textContent(), /라이브를 다시 시작했습니다/);
        checks.push('확인하면 라이브를 멈추고 불러온 뒤 다시 시작, 채널은 알림방으로만 고를 수 있음');
        await page.locator('.moses-telegram-load').screenshot({path: path.join(evidence, 'telegram_chats.png')});

        // 3. Choosing a chat confirms that one field at once.
        await channelRow.locator('[data-use-as="TELEGRAM_CHAT_ID"]').click();
        await page.waitForFunction(() => document.querySelector('#telegram-status-TELEGRAM_CHAT_ID').dataset.phase === 'success');
        await privateRow.locator('[data-use-as="TELEGRAM_COMMAND_CHAT_IDS"]').click();
        await page.waitForFunction(() => document.querySelector('#telegram-status-TELEGRAM_COMMAND_CHAT_IDS').dataset.phase === 'success');
        assert.deepEqual(requests.filter(row => row.name === '/api/mo/settings/telegram').map(row => [row.data.key, row.data.value]),
            [['TELEGRAM_CHAT_ID', '-100777'], ['TELEGRAM_COMMAND_CHAT_IDS', '555']]);
        assert.equal(await page.locator('#telegram-status-TELEGRAM_CHAT_ID').textContent(), '확인하고 저장했습니다 · 라이브 다시 시작 후 적용');
        checks.push('고른 대화방 번호가 해당 칸에 들어가 바로 확인·저장됨');

        // 4. Gemini key: the button appears with a typed key, failures hide the key, success clears it.
        const key = page.locator('[data-setting="gemini_api_key"]'), confirm = page.locator('#ai-gemini-key-confirm');
        const keyStatus = page.locator('#ai-gemini-key-status');
        assert.equal(await confirm.isVisible(), false);
        await key.fill('AIza-synthetic-138');
        assert.equal(await confirm.isVisible(), true);
        rejectKey = true;
        await confirm.click();
        await page.waitForFunction(() => document.querySelector('#ai-gemini-key-status').textContent !== '');
        assert.equal(await keyStatus.textContent(), 'Gemini API 키 [숨김]를 확인하지 못했습니다.');
        assert.equal(await keyStatus.evaluate(node => node.classList.contains('moses-telegram-error')), true);
        rejectKey = false;
        await confirm.click();
        await page.waitForFunction(() => document.querySelector('#ai-gemini-key-status').textContent === '확인하고 저장했습니다');
        assert.deepEqual(requests.filter(row => row.data.group === 'ai').map(row => row.data),
            [{group: 'ai', changes: {gemini_api_key: 'AIza-synthetic-138'}}, {group: 'ai', changes: {gemini_api_key: 'AIza-synthetic-138'}}]);
        assert.equal(await key.inputValue(), '');
        assert.equal(await confirm.isVisible(), false);
        // The saved key now lists the models it can use.
        await page.waitForFunction(() => !document.querySelector('#ai-gemini-model-select').disabled);
        assert.deepEqual(await page.locator('#ai-gemini-model-select option').allTextContents(), ['gemini-2.5-flash', 'gemini-2.5-pro']);
        checks.push('제미나이 키 확인 버튼: 입력하면 보이고, 실패 시 키를 숨긴 오류, 성공 시 저장 후 칸 비우고 모델 목록 불러옴');
        await page.locator('#settings-ai-fields').screenshot({path: path.join(evidence, 'ai_settings.png')});

        // 5. Every save button reports next to itself.
        for (const group of ['part2', 'connections']) {
            await page.locator(`[data-save-settings="${group}"]`).click();
            await page.waitForFunction(group => document.querySelector(`[data-save-status="${group}"]`).textContent !== '', group);
        }
        assert.equal(await page.locator('#settings-message').isHidden(), true);
        checks.push('Part2·연결 위치 저장도 각 버튼 옆에 결과 표시, 위쪽 안내줄은 숨김');

        // 6. Nothing found: a running live engine had already read the messages, so say to stop it first.
        const loadStatus = page.locator('.moses-telegram-load .moses-telegram-status');
        const loadAgain = async () => {
            await page.locator('#telegram-chats-load').click();
            await page.waitForFunction(() => document.querySelector('.moses-telegram-load .moses-telegram-status').textContent.startsWith('찾은'));
            const text = await loadStatus.textContent();
            await loadStatus.evaluate(node => { node.textContent = ''; });
            return text;
        };
        noChats = true;
        page.once('dialog', dialog => dialog.accept());
        assert.equal(await loadAgain(), '찾은 대화방이 없습니다 · 라이브 전체 종료 후 /start를 보내거나 그룹에 초대하고 다시 불러오세요');
        liveRunning = false;
        const asked = [];
        page.once('dialog', dialog => { asked.push(dialog.message()); dialog.dismiss(); });
        assert.equal(await loadAgain(), '찾은 대화방이 없습니다 · 봇에게 /start를 보내거나 그룹·채널에 초대한 뒤 다시 불러오세요');
        assert.deepEqual(asked, []);
        page.removeAllListeners('dialog');
        assert.equal(await page.locator('.moses-telegram-chat').count(), 0);
        checks.push('찾은 방이 없으면 라이브가 돌던 경우 전체 종료 후 다시 하라고 안내, 꺼져 있으면 묻지 않고 바로 읽음');
        assert.deepEqual(errors, []);
        fs.writeFileSync(path.join(evidence, 'settings_cleanup_ui_checks.json'), JSON.stringify({passed: true, checks, errors}, null, 2) + '\n');
        console.log(JSON.stringify({passed: true, checks: checks.length}));
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
