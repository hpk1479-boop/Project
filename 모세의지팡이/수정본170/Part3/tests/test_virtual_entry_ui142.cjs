/* Revision 142 virtual-entry panel in isolated Edge: the base frame (기준 프레임) and the table.
   argv: playwright module, evidence folder. stdin: strategy profiles and the table. */
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
            virtual_profiles: engine.profiles, virtual_ladder: engine.ladder};
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
// The base frame is a choice; every other frame field shows its frame (수정본146).
const shown = locator => locator.evaluate(field => field.tagName === 'SELECT' ? field.options[field.selectedIndex]?.textContent : field.value);
const base = page => page.locator('#mo-bt-virtual-main [data-virtual-field="tf"]');
const UP_TO_3H = ['1분', '2분', '3분', '4분', '5분', '6분', '10분', '12분', '15분', '20분', '30분', '1시간', '2시간', '3시간'];
add('the field next to the entry mode is the base frame, in both entry modes', async page => {
    await choose(page, 'SPECIAL9');
    assert.equal(await base(page).evaluate(select => select.closest('label').firstChild.textContent), '기준 프레임');
    // SPECIAL9 uses the top frame (15m), which 4h does not have.
    assert.deepEqual(await options(base(page)), UP_TO_3H);
    assert.equal(await base(page).isDisabled(), false);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('IMMEDIATE');
    assert.equal(await base(page).count(), 1);
    assert.doesNotMatch(await page.locator('#mo-bt-virtual-main').textContent(), /확인봉/);
    assert.doesNotMatch(await page.locator('#mo-bt-virtual-panel').textContent(), /자동|SIGNAL|신호/);
});
add('choosing 2m moves every frame of the panel by the table', async page => {
    await choose(page, 'SPECIAL9');
    await base(page).selectOption('2m');
    let value = await request(page);
    assert.equal(value.tf, '2m');
    assert.equal(value.filters.find(row => row.condition === 'TREND').tf, '20m');
    // The strategy's own frame is now 2m, and its fields show it.
    assert.equal(await shown(page.locator('[data-virtual-condition="MA_TOUCH_CANDLE"] [data-virtual-field="tf"]')), '2분');
    const limits = page.locator('[data-virtual-filter="ENVIRONMENT"]');
    assert.equal(await shown(limits.nth(0).locator('[data-virtual-field="tf"]')), '2분');
    assert.equal(await shown(limits.nth(1).locator('[data-virtual-field="tf"]')), '20분');
    assert.equal(await shown(page.locator('#mo-bt-virtual-stop [data-virtual-field="tf"]')), '2분');
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special9-2m.png')});
    await base(page).selectOption('3m');
    value = await request(page);
    assert.equal(value.filters.find(row => row.condition === 'TREND').tf, '30m');
    await base(page).selectOption('SIGNAL');
    assert.deepEqual(await request(page), engine.profiles.SPECIAL9.default);
});
add('conditional entry on another base frame starts from the recipe conditions', async page => {
    await choose(page, 'SPECIAL7');
    await base(page).selectOption('5m');
    const mode = page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]');
    await mode.selectOption('IMMEDIATE'); await mode.selectOption('CONFIRM');
    const value = await request(page);
    assert.deepEqual(value.conditions, engine.profiles.SPECIAL7.default.conditions);
    assert.equal(value.tf, '5m');
    assert.equal(value.filters.find(row => row.condition === 'TREND').tf, '1h');
    assert.equal(await shown(page.locator('[data-virtual-condition="MA_POSITION"] [data-virtual-field="tf"]')), '5분');
});
add('SPECIAL8 has no top frame to lose and can also be tested on 4h', async page => {
    await choose(page, 'SPECIAL8');
    assert.deepEqual(await options(base(page)), [...UP_TO_3H, '4시간']);
    await base(page).selectOption('4h');
    assert.equal((await request(page)).tf, '4h');
    assert.equal(await shown(page.locator('#mo-bt-virtual-stop [data-virtual-field="tf"]')), '4시간');
});
add('a strategy that cannot move keeps its own frames', async page => {
    await choose(page, 'SPECIAL1');
    assert.equal(await base(page).isDisabled(), true);
    assert.deepEqual(await options(base(page)), ['1분·2분·3분·4분·5분·6분·10분·12분·15분·20분·30분·1시간']);
    await choose(page, 'SPECIAL4');
    assert.equal(await base(page).isDisabled(), true);
    assert.deepEqual(await options(base(page)), ['1분']);
    assert.equal((await request(page)).tf, 'SIGNAL');
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special4-locked.png')});
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
        fs.writeFileSync(path.join(out, 'virtual_ui_checks.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
