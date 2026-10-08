/* Isolated current settings UI; APIs mocked, no engine or Ollama calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        const page = await browser.newPage({viewport: {width: 1280, height: 800}});
        await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
            route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
        await page.goto('http://127.0.0.1:8763/');
        await page.addStyleTag({content: fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8')});
        await page.evaluate(() => {
            window.aiConfig = {provider: 'ollama', model: 'qwen3:8b', base_url: 'http://127.0.0.1:11434', timeout: 90};
            window.saves = [];
            window.settingsReads = [];
            window.part3InvalidateAIRecipe = () => {};
            window.setInterval = () => 0;
            window.flush = async () => {for (let i=0;i<16;i++) await Promise.resolve();};
            window.fetch = async url => {
                if (url.includes('ai/settings')) settingsReads.push({...aiConfig});
                return {ok: true, status: 200, json: async () =>
                    url.includes('ai/settings') ? {settings: {...aiConfig}, base_url: aiConfig.base_url} : {ok: true}};
            };
            window.api = async (route, data) => {
                if (route === 'mo/settings' && data) {
                    saves.push(data); Object.assign(aiConfig, data.changes);
                    return {ok: true, message: '저장 완료 - 다음 AI 요청부터 적용'};
                }
                if (route === 'mo/settings') return {live: [], part2: {}, connections: {}, ai: {...aiConfig}};
                return {modules: {STAFF: {name: 'STAFF', state: '대기'}, ENGINE: {name: 'ENGINE', state: '대기'}}, lines: []};
            };
        });
        await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web/ai_chat.js'), 'utf8')});
        await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8')});
        await page.evaluate(async () => {
            document.dispatchEvent(new Event('DOMContentLoaded')); await flush(); await moView('settings');
        });
        const editable = await page.evaluate(() => {
            const field = mo('#settings-ai-fields [data-setting="model"]');
            return {value: field.value, editable: !field.readOnly,
                    endpointReadOnly: mo('#settings-ai-fields [data-setting="base_url"]').readOnly};
        });
        assert.deepEqual(editable, {value: 'qwen3:8b', editable: true, endpointReadOnly: true});
        results.push({name: 'default model is editable; endpoint remains read only', passed: true});

        const selected = await page.evaluate(async () => {
            const reads = settingsReads.length;
            mo('#settings-ai-fields [data-setting="model"]').value = 'qwen3.5:4b';
            await mo('[data-save-settings="ai"]').onclick(); await flush();
            return {body: saves.at(-1), saved: aiConfig.model,
                    field: mo('#settings-ai-fields [data-setting="model"]').value,
                    refreshed: settingsReads.length > reads, chatModel: settingsReads.at(-1).model};
        });
        assert.deepEqual(selected.body, {group: 'ai', changes: {model: 'qwen3.5:4b'}});
        assert.equal(selected.saved, 'qwen3.5:4b'); assert.equal(selected.field, 'qwen3.5:4b');
        assert.equal(selected.refreshed, true); assert.equal(selected.chatModel, 'qwen3.5:4b');
        results.push({name: 'save sends model as a string and refreshes both settings and chat', passed: true});

        const timeout = await page.evaluate(async () => {
            mo('#settings-ai-fields [data-setting="timeout"]').value = '120';
            await mo('[data-save-settings="ai"]').onclick(); await flush();
            return {body: saves.at(-1), model: aiConfig.model};
        });
        assert.deepEqual(timeout, {body: {group: 'ai', changes: {timeout: 120}}, model: 'qwen3.5:4b'});
        results.push({name: 'timeout-only save remains numeric and keeps selected model', passed: true});

        const restored = await page.evaluate(async () => {
            const reads = settingsReads.length;
            mo('#settings-ai-fields [data-setting="model"]').value = 'qwen3:8b';
            await mo('[data-save-settings="ai"]').onclick(); await flush();
            return {model: aiConfig.model, timeout: aiConfig.timeout,
                    refreshed: settingsReads.length > reads, chatModel: settingsReads.at(-1).model};
        });
        assert.equal(restored.model, 'qwen3:8b'); assert.equal(restored.timeout, 120);
        assert.equal(restored.refreshed, true); assert.equal(restored.chatModel, 'qwen3:8b');
        results.push({name: 'default model can be selected again without changing timeout', passed: true});
        await page.screenshot({path: path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'AI모델설정/settings_ui.png'), fullPage: true});
        await page.close();
    } finally {
        await browser.close();
        const output = {tests: results.length, results, environment: 'isolated Edge; mocked APIs'};
        const folder = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'AI모델설정'); fs.mkdirSync(folder, {recursive: true});
        fs.writeFileSync(path.join(folder, 'ui_tests.json'), JSON.stringify(output, null, 2)+'\n');
    }
    console.log('PASS ' + results.length + ' model settings UI tests');
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
