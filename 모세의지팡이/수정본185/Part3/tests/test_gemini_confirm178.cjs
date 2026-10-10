/* Real settings and AI page handlers; all HTTP is synthetic and external traffic is blocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const scenario = process.argv[3], root = path.resolve(__dirname, '../..');
const isToken = scenario.startsWith('token-'), key = isToken ? 'TELEGRAM_TOKEN' : 'gemini_api_key';
const initial = scenario === 'recheck' || scenario === 'gemini-delete';
const config = {
    live: [{key: 'TELEGRAM_TOKEN', secret: true, configured: initial}],
    part2: {cores: null, overlap_trading_days: 3}, part2_cores: 8,
    connections: {warehouse: 'synthetic-warehouse', python_executable: ''},
    ai: {provider: 'gemini', gemini_model: 'chosen-model', gemini_api_key_configured: initial, timeout: 90, watch_enabled: true}
};
let held, sent = 0;
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage();
        const errors = [];
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
            else if (name === '/api/ai/settings') body = {settings: config.ai};
            else if (name === '/api/ai/gemini-models') body = {available: true, models: ['chosen-model']};
            else if (name === '/api/mo/settings') body = structuredClone(config);
            else if (name === '/api/mo/settings/telegram' || name === '/api/mo/settings/gemini_key') {
                sent++;
                if (scenario !== 'recheck') await new Promise(resolve => {held = resolve;});
                if (data.value !== '') {
                    if (isToken) config.live[0].configured = data.value !== null;
                    else config.ai.gemini_api_key_configured = data.value !== null;
                }
                body = {ok: true, key, configured: isToken ? config.live[0].configured : config.ai.gemini_api_key_configured,
                    message: data.value === '' ? '확인했습니다' : '확인하고 저장했습니다'};
            }
            return route.fulfill({json: body});
        });
        await page.goto('http://127.0.0.1:8763/#token=synthetic-token-178');
        await page.waitForFunction(() => !!state.meta);
        await page.evaluate(() => moView('settings'));
        const input = page.locator(`[data-setting="${key}"]`);
        const label = input.locator('xpath=ancestor::label');
        const button = label.locator('.moses-telegram-confirm');
        if (scenario === 'recheck') {
            // An already approved preview and pending AI work survive a read-only credential check.
            await page.evaluate(() => {
                state.recipe = {name: 'keep-this-preview'}; state.code = 'preview'; state.valid = true;
                document.querySelector('#ai-pending').classList.remove('hidden');
            });
            await button.click();
            await page.waitForFunction(() => document.querySelector('#ai-gemini-key-status').dataset.phase === 'success');
            const work = await page.evaluate(() => ({recipe: state.recipe, valid: state.valid,
                pending: !document.querySelector('#ai-pending').classList.contains('hidden'),
                text: document.querySelector('#ai-messages').textContent}));
            assert.deepEqual(work.recipe, {name: 'keep-this-preview'});
            assert.equal(work.valid, true); assert.equal(work.pending, true);
            assert.equal(work.text.includes('AI 설정이 변경되었습니다'), false);
        } else {
            if (scenario === 'gemini-delete') await label.locator('.mo-secret-clear').check();
            else await input.fill('synthetic-key-A');
            await button.click();
            for (let i = 0; !held && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
            assert.ok(held, 'confirmation request is waiting');
            await input.press('Enter'); assert.equal(sent, 1);
            if (scenario.endsWith('-redraw')) await page.evaluate(() => moSettingsLoad());
            else await input.fill(scenario.endsWith('-clear') ? '' : 'synthetic-key-B');
            held();
            await page.waitForFunction(key => !document.querySelector(`[data-setting="${key}"]`)
                .closest('label').querySelector('.moses-telegram-confirm').disabled, key);
            const configured = scenario !== 'gemini-delete';
            assert.equal(await input.getAttribute('data-configured'), String(configured));
            assert.equal(await input.inputValue(), scenario.endsWith('-edit') || scenario.endsWith('-delete') ? 'synthetic-key-B' : '');
            assert.equal(await label.locator('.mo-secret-clear').isVisible(), configured);
            assert.equal(await button.isVisible(), true);
            assert.equal((await input.getAttribute('placeholder')).startsWith(configured ? '설정됨' : '미설정'), true);
            if (!isToken) assert.equal(await page.evaluate(() => moState.settings.ai.gemini_api_key_configured), configured);
        }
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({passed: true, scenario}));
    } finally {await browser.close();}
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
