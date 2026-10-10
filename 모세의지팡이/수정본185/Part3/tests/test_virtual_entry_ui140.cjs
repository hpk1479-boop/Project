/* Revision 140 virtual-entry panel in isolated Edge: SPECIAL9's entry cell and the new candle conditions.
   argv: playwright module, evidence folder. stdin: strategy profiles with their recipe values. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const out = process.argv[3];
const engine = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const js = fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8');
const dashboard = fs.readFileSync(path.join(root, 'Part3/web/backtest_dashboard.js'), 'utf8');
const tests = [];
const add = (name, fn) => tests.push({name, fn});
async function fixture(browser) {
    const page = await browser.newPage({viewport: {width: 1280, height: 1100}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html); await page.addStyleTag({content: css});
    await page.evaluate(() => { window.setInterval = () => 0; window.api = async () => ({modules: {}, lines: []}); });
    await page.addScriptTag({content: dashboard}); await page.addScriptTag({content: js});
    await page.evaluate(async engine => {
        await Promise.resolve();
        const names = Object.keys(engine.profiles);
        const data = {symbol: 'XAUUSD+', symbols: ['XAUUSD+'], start: '2025-09-01', end: '2025-09-08',
            mode: 'BAR', warehouse_set: true, specials: names, strategy_names: {},
            special_settings: Object.fromEntries(names.map(name => [name, {enabled: false, trigger: null, time_filters: null}])),
            default_triggers: {}, trigger_choices: ['올존'], spread_points: {}, virtual_entry: null, virtual_entry_target: null,
            virtual_profiles: engine.profiles};
        window.api = async route => route === 'mo/backtest/options' ? data : {modules: {}, lines: []};
        moState.view = 'backtest';
        mo('#view-strategy').classList.add('hidden'); mo('#view-live').classList.add('hidden');
        mo('#view-backtest').classList.remove('hidden');
        await moBacktestOptions();
    }, engine);
    return page;
}
async function choose(page, name) {
    await page.evaluate(name => {
        mo('#mo-bt-target').value = 'SPECIAL';
        mo('#mo-bt-build-only').checked = false; mo('#mo-bt-result-mode').value = 'VIRTUAL_ENTRY';
        document.querySelectorAll('#mo-bt-specials [data-special]').forEach(box => {
            box.querySelector('.mo-special-enabled').checked = box.dataset.special === name;
        });
        mo('#mo-bt-specials').dispatchEvent(new Event('change'));
    }, name);
}
const request = page => page.evaluate(() => moBacktestRequest().virtual_entry);
const options = locator => locator.evaluate(select => [...select.options].map(o => o.textContent));
add("SPECIAL9's entry cell is shown: the next candle touching HMA50", async page => {
    await choose(page, 'SPECIAL9');
    assert.deepEqual(await request(page), engine.profiles.SPECIAL9.default);
    const conditions = page.locator('#mo-bt-virtual-conditions [data-virtual-condition]');
    assert.deepEqual(await conditions.evaluateAll(rows => rows.map(row => row.dataset.virtualCondition)),
        ['CANDLE_CLOSE', 'MA_TOUCH_CANDLE', 'MA_POSITION']);
    const touch = page.locator('[data-virtual-condition="MA_TOUCH_CANDLE"]');
    assert.equal(await touch.locator('.moses-virtual-heading strong').textContent(), '이평 터치');
    assert.equal(await touch.locator('[data-virtual-field="family"]').inputValue(), 'HMA');
    assert.equal(await touch.locator('[data-virtual-field="period"]').inputValue(), '50');
    assert.equal(await page.locator('[data-virtual-filter="MAX_BARS"] [data-virtual-field="bars"]').inputValue(), '1');
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special9-entry.png')});
});
add('a candle shape is added, switched between hammer and inverted hammer, and removed', async page => {
    await choose(page, 'SPECIAL8');
    const kinds = await options(page.locator('#mo-bt-virtual-condition-kind'));
    assert.ok(kinds.includes('망치형·역망치형') && kinds.includes('이평 터치'), kinds.join(','));
    await page.locator('#mo-bt-virtual-condition-kind').selectOption('CANDLE_SHAPE');
    await page.locator('#mo-bt-virtual-condition-add').click();
    assert.deepEqual((await request(page)).conditions.at(-1), {kind: 'CANDLE_SHAPE', shape: 'HAMMER'});
    const row = page.locator('[data-virtual-condition="CANDLE_SHAPE"]');
    assert.deepEqual(await options(row.locator('[data-virtual-field="shape"]')), ['망치형', '역망치형']);
    assert.equal(await row.locator('[data-virtual-field="family"]').count(), 0);
    await row.locator('[data-virtual-field="shape"]').selectOption('INVERTED_HAMMER');
    assert.deepEqual((await request(page)).conditions.at(-1), {kind: 'CANDLE_SHAPE', shape: 'INVERTED_HAMMER'});
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'candle-shape.png')});
    await page.getByRole('button', {name: '망치형·역망치형 삭제'}).click();
    assert.deepEqual((await request(page)).conditions, engine.profiles.SPECIAL8.default.conditions);
});
add('a touch on the confirmation candle carries its average', async page => {
    await choose(page, 'SPECIAL8');
    await page.locator('#mo-bt-virtual-condition-kind').selectOption('MA_TOUCH_CANDLE');
    await page.locator('#mo-bt-virtual-condition-add').click();
    assert.deepEqual((await request(page)).conditions.at(-1), {kind: 'MA_TOUCH_CANDLE', family: 'HMA', period: 6, tf: 'SIGNAL'});
    const row = page.locator('[data-virtual-condition="MA_TOUCH_CANDLE"]');
    await row.locator('[data-virtual-field="period"]').fill('50');
    assert.deepEqual((await request(page)).conditions.at(-1), {kind: 'MA_TOUCH_CANDLE', family: 'HMA', period: 50, tf: 'SIGNAL'});
    const text = await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.textContent);
    assert.doesNotMatch(text, /자동|SIGNAL|HAMMER|CANDLE_SHAPE|MA_TOUCH_CANDLE/);
});
(async () => {
    fs.mkdirSync(out, {recursive: true});
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        for (const test of tests) {
            const page = await fixture(browser);
            const errors = []; page.on('pageerror', error => errors.push(error.message));
            try {
                await test.fn(page); assert.deepEqual(errors, []);
                results.push({name: test.name, passed: true}); console.log('PASS ' + test.name);
            } catch (error) {
                results.push({name: test.name, passed: false, error: error.message}); console.error('FAIL ' + test.name + ': ' + error.message);
            } finally { await page.close(); }
        }
        const output = {tests: results.length, failed: results.filter(item => !item.passed).length,
            environment: 'isolated Edge, actual HTML/CSS/JS, mocked APIs, recipe profiles from Part2', results};
        fs.writeFileSync(path.join(out, 'virtual_ui_checks.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
