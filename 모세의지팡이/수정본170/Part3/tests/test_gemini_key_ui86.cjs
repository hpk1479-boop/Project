/* Real settings DOM handlers; synthetic credentials and mocked APIs only. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage();
        await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
            route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
        await page.goto('http://127.0.0.1:8763/');
        await page.evaluate(() => {
            window.aiConfig = {provider: 'gemini', gemini_model: 'selected-model',
                gemini_api_key_configured: true, timeout: 90};
            window.saves = []; window.rejectSave = false; window.setInterval = () => 0;
            window.flush = async () => {for (let i=0;i<16;i++) await Promise.resolve();};
            window.fetch = async () => ({ok: true, json: async () => ({settings: {...aiConfig}})});
            window.api = async (route, data) => {
                if (route === 'mo/settings' && data) {
                    saves.push(data);
                    if (rejectSave) throw Error('Gemini API 키가 유효하지 않습니다.');
                    if ('gemini_api_key' in data.changes)
                        aiConfig.gemini_api_key_configured = data.changes.gemini_api_key !== null;
                    return {ok: true, message: '저장 완료 - 다음 AI 요청부터 적용'};
                }
                if (route === 'mo/settings') return {live: [], part2: {}, connections: {}, ai: {...aiConfig}};
                return {modules: {}, lines: []};
            };
        });
        for (const file of ['ai_chat.js', 'unified.js'])
            await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web', file), 'utf8')});
        await page.evaluate(async () => {
            document.dispatchEvent(new Event('DOMContentLoaded')); await flush(); await moView('settings');
        });
        const save = async (key, clear = false, fail = false) => page.evaluate(async ({key, clear, fail}) => {
            const input = mo('#settings-ai-fields [data-setting="gemini_api_key"]');
            input.value = key;
            const checkbox = input.parentElement.querySelector('.mo-secret-clear');
            if (checkbox) checkbox.checked = clear;
            rejectSave = fail;
            await mo('[data-save-settings="ai"]').onclick(); await flush();
            return {body: saves.at(-1), message: mo('#settings-message').textContent,
                configured: aiConfig.gemini_api_key_configured,
                input: mo('#settings-ai-fields [data-setting="gemini_api_key"]').value};
        }, {key, clear, fail});
        const replacement = await save('AQ.synthetic.replacement', true);
        assert.equal(replacement.body.changes.gemini_api_key, 'AQ.synthetic.replacement');
        assert.equal(replacement.input, ''); assert.equal(replacement.configured, true);
        const deletion = await save('', true);
        assert.equal(deletion.body.changes.gemini_api_key, null);
        assert.equal(deletion.configured, false); assert.match(deletion.message, /저장 완료/);
        const reentry = await save('AQ.synthetic.reentered');
        assert.equal(reentry.body.changes.gemini_api_key, 'AQ.synthetic.reentered');
        assert.equal(reentry.configured, true);
        const invalid = await save('AQ.synthetic.invalid', false, true);
        assert.match(invalid.message, /API 키가 유효하지/);
        assert.equal(invalid.configured, true);
        assert.ok(!invalid.message.includes('AQ.synthetic.invalid'));
        console.log('PASS 4 Gemini key UI scenarios: replace, delete, re-enter, invalid-key feedback');
    } finally { await browser.close(); }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
