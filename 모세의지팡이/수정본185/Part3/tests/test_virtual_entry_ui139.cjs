/* Revision 139 virtual-entry panel in isolated Edge: entry limits (environment, N bars) from the recipes.
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
const options = (locator) => locator.evaluate(select => [...select.options].map(o => o.textContent));
add('the recipe environment limits are shown with real frames', async page => {
    await choose(page, 'SPECIAL1');
    assert.deepEqual(await request(page), engine.profiles.SPECIAL1.default);
    assert.equal(await page.locator('#mo-bt-virtual-filter-details').evaluate(details => details.open), true);
    const rows = page.locator('[data-virtual-filter="ENVIRONMENT"]');
    assert.equal(await rows.count(), 2);
    assert.equal(await rows.nth(0).locator('[data-virtual-field="condition"]').inputValue(), 'MA_STATE');
    assert.equal(await rows.nth(1).locator('[data-virtual-field="condition"]').inputValue(), 'TREND');
    assert.equal(await rows.nth(1).locator('[data-virtual-field="bar_state"]').inputValue(), 'FORMING');
    // Frames are shown, never chosen (수정본146): the recipe's environment frame and its own frames.
    assert.equal(await rows.nth(1).locator('[data-virtual-field="tf"]').inputValue(), '환경 프레임 (1시간·2시간·3시간·4시간)');
    assert.equal(await rows.nth(1).locator('[data-virtual-field="tf"]').isDisabled(), true);
    assert.equal(await rows.nth(0).locator('[data-virtual-field="tf"]').inputValue(), '1분·2분·3분·4분·5분·6분·10분·12분·15분·20분·30분·1시간');
    assert.equal(await rows.nth(1).locator('[data-virtual-field="family"]').count(), 0);
    const text = await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.textContent);
    assert.doesNotMatch(text, /자동|신호|SIGNAL|AUTO|ENV\b/);
    // No ATR header without an ATR limit.
    assert.equal(await page.locator('#mo-bt-virtual-filters [data-virtual-field="period"]').count(), 0);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special1-limits.png')});
});
add('an environment limit is added, switched between conditions and removed', async page => {
    await choose(page, 'SPECIAL8');
    await page.locator('#mo-bt-virtual-filter-kind').selectOption('ENVIRONMENT');
    await page.locator('#mo-bt-virtual-filter-add').click();
    const row = page.locator('[data-virtual-filter="ENVIRONMENT"]').last();
    assert.deepEqual((await request(page)).filters.at(-1),
        {kind: 'ENVIRONMENT', condition: 'MA_STATE', family: 'HMA', fast: 6, slow: 17, tf: 'SIGNAL', bar_state: 'CLOSED'});
    await row.locator('[data-virtual-field="condition"]').selectOption('MA_SLOPE_STATE');
    assert.deepEqual((await request(page)).filters.at(-1),
        {kind: 'ENVIRONMENT', condition: 'MA_SLOPE_STATE', tf: 'SIGNAL', bar_state: 'CLOSED', family: 'HMA', period: 50, lookback: 2});
    await page.locator('[data-virtual-filter="ENVIRONMENT"]').last().locator('[data-virtual-field="condition"]').selectOption('TREND');
    const trend = page.locator('[data-virtual-filter="ENVIRONMENT"]').last();
    assert.equal(await trend.locator('[data-virtual-field="tf"]').inputValue(), '1분');
    await trend.locator('[data-virtual-field="bar_state"]').selectOption('FORMING');
    assert.deepEqual((await request(page)).filters.at(-1),
        {kind: 'ENVIRONMENT', condition: 'TREND', tf: 'SIGNAL', bar_state: 'FORMING'});
    await page.getByRole('button', {name: '환경조건 삭제'}).last().click();
    assert.equal((await request(page)).filters.length, engine.profiles.SPECIAL8.default.filters.length);
});
add('N bars is one limit, only for a conditional entry', async page => {
    await choose(page, 'SPECIAL8');
    const kinds = () => options(page.locator('#mo-bt-virtual-filter-kind'));
    assert.deepEqual(await kinds(), ['확인봉 크기', '시가와 이평 거리', '환경조건', '알림 후 N봉']);
    await page.locator('#mo-bt-virtual-filter-kind').selectOption('MAX_BARS');
    await page.locator('#mo-bt-virtual-filter-add').click();
    await page.locator('[data-virtual-filter="MAX_BARS"] [data-virtual-field="bars"]').fill('5');
    assert.deepEqual((await request(page)).filters.at(-1), {kind: 'MAX_BARS', bars: 5});
    assert.deepEqual(await kinds(), ['확인봉 크기', '시가와 이평 거리', '환경조건']);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('IMMEDIATE');
    const value = await request(page);
    assert.equal(value.filters.some(row => row.kind === 'MAX_BARS'), false);
    assert.deepEqual(await kinds(), ['확인봉 크기', '시가와 이평 거리', '환경조건']);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'immediate-limits.png')});
});
add('a WATCH command never shows a virtual entry', async page => {
    await page.evaluate(() => {
        mo('#mo-bt-build-only').checked = false; mo('#mo-bt-result-mode').value = 'VIRTUAL_ENTRY';
        mo('#mo-bt-target').value = 'WATCH'; moBacktestMode();
    });
    assert.equal(await page.locator('[data-bt-result="VIRTUAL_ENTRY"]').isDisabled(), true);
    assert.equal(await page.evaluate(() => mo('#mo-bt-result-mode').value), 'ALERT_ONLY');
    assert.equal(await request(page), undefined);
    await page.evaluate(() => { mo('#mo-bt-target').value = 'SPECIAL'; moBacktestMode(); });
    assert.equal(await page.locator('[data-bt-result="VIRTUAL_ENTRY"]').isDisabled(), false);
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
