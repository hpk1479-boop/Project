/* Revision 161 virtual-entry panel in isolated Edge: other base frames tested together with the chosen one.
   One replay, one result each. Checked frames reach the request as virtual_bases (chosen frame first);
   the chosen frame never doubles as a compared one; another strategy clears them; a strategy whose
   frames cannot move shows no comparison; a result names the frames tested with it.
   argv: playwright module, evidence folder. stdin: profiles and the base-frame table. */
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
    const page = await browser.newPage({viewport: {width: 1280, height: 1400}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html); await page.addStyleTag({content: css});
    await page.evaluate(() => { window.setInterval = () => 0; window.api = async () => ({modules: {}, lines: []}); });
    await page.addScriptTag({content: dashboard}); await page.addScriptTag({content: js});
    await page.evaluate(async ({engine}) => {
        await Promise.resolve();
        const names = Object.keys(engine.profiles);
        const data = {symbol: 'XAUUSD+', symbols: ['XAUUSD+'], start: '2025-09-01', end: '2025-09-08',
            mode: 'BAR', warehouse_set: true, specials: names, strategy_names: {},
            special_settings: Object.fromEntries(names.map(name => [name, {enabled: false, trigger: null, time_filters: null}])),
            default_triggers: {}, trigger_choices: [], spread_points: {},
            virtual_entry: null, virtual_entry_target: null, virtual_entry_strategy: null,
            virtual_profiles: engine.profiles, virtual_ladder: engine.ladder};
        window.api = async route => route === 'mo/backtest/options' ? data : {modules: {}, lines: []};
        moState.view = 'backtest';
        mo('#view-strategy').classList.add('hidden'); mo('#view-live').classList.add('hidden');
        mo('#view-backtest').classList.remove('hidden');
        await moBacktestOptions();
    }, {engine});
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
const request = page => page.evaluate(() => moBacktestRequest());
const base = page => page.locator('#mo-bt-virtual-main [data-virtual-field="tf"]');
const compare = (page, tf) => page.locator(`[data-compare-base="${tf}"]`);
const offered = page => page.locator('[data-compare-base]').evaluateAll(boxes => boxes.map(box => box.dataset.compareBase));

add('checked base frames reach the request with the chosen one first', async page => {
    await choose(page, 'SPECIAL9');
    const own = engine.profiles.SPECIAL9.base;
    assert.deepEqual(await offered(page), engine.profiles.SPECIAL9.bases.filter(tf => tf !== own));
    assert.equal((await request(page)).virtual_bases, undefined);
    await compare(page, '2m').check(); await compare(page, '5m').check();
    const value = await request(page);
    assert.deepEqual(value.virtual_bases, [own, '2m', '5m']);
    assert.equal(value.virtual_entry.tf, 'SIGNAL');
    assert.match(await page.locator('.moses-virtual-compare').innerText(), /레시피 값으로 시험해 결과를 따로 저장/);
    await page.locator('.moses-virtual-compare').screenshot({path: path.join(out, 'compare_row.png')});
    await compare(page, '2m').uncheck(); await compare(page, '5m').uncheck();
    assert.equal((await request(page)).virtual_bases, undefined);
});
add('the chosen base frame is never also a compared one', async page => {
    await choose(page, 'SPECIAL9');
    await compare(page, '2m').check(); await compare(page, '5m').check();
    await base(page).selectOption('2m');
    assert.ok(!(await offered(page)).includes('2m') && (await offered(page)).includes(engine.profiles.SPECIAL9.base));
    assert.deepEqual((await request(page)).virtual_bases, ['2m', '5m']);
    await compare(page, engine.profiles.SPECIAL9.base).check();
    assert.deepEqual((await request(page)).virtual_bases, ['2m', '5m', engine.profiles.SPECIAL9.base]);
});
add('another strategy starts without compared frames', async page => {
    await choose(page, 'SPECIAL9');
    await compare(page, '3m').check();
    await choose(page, 'SPECIAL8');
    assert.equal((await request(page)).virtual_bases, undefined);
    assert.equal(await page.locator('[data-compare-base]:checked').count(), 0);
});
add('a strategy whose frames cannot move offers no comparison', async page => {
    await choose(page, 'SPECIAL2');
    assert.equal(await page.locator('.moses-virtual-compare').count(), 0);
    assert.equal((await request(page)).virtual_bases, undefined);
});
add('a result names its own and the other base frames', async page => {
    await page.evaluate(() => {
        window.api = async () => ({run_id: 'a'.repeat(32), status: '완료', result_mode: 'VIRTUAL_ENTRY',
            virtual_entry: {summary: []}, analytics: null, warnings: [], base_frame: '1m',
            compared_runs: [{run_id: 'b'.repeat(32), base_frame: '2m'}, {run_id: 'c'.repeat(32), base_frame: '5m'}]});
        moState.job = 'a'.repeat(32);
    });
    await page.evaluate(() => moReadResult('a'.repeat(32)));
    assert.match(await page.locator('#mo-bt-result').innerText(), /기준 프레임 1분 · 함께 시험: 2분 · 5분/);
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
            environment: 'isolated Edge, actual HTML/CSS/JS, mocked APIs, recipe profiles and table from Part2', results};
        fs.writeFileSync(path.join(out, 'virtual_bases_ui_checks.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
