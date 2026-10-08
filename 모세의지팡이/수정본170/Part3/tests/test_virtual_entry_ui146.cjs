/* Revision 146 virtual-entry panel in isolated Edge: the base frame (기준 프레임) is the one frame a test
   chooses. It refills every value from the recipe; every other frame, of the entry and of the folded
   strategy conditions (전략 조건), only shows the frame the base frame gives. Other values stay editable.
   argv: playwright module, evidence folder. stdin: profiles, expected recipe values, a saved run, table. */
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
async function fixture(browser, saved = null) {
    const page = await browser.newPage({viewport: {width: 1280, height: 1400}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html); await page.addStyleTag({content: css});
    await page.evaluate(() => { window.setInterval = () => 0; window.api = async () => ({modules: {}, lines: []}); });
    await page.addScriptTag({content: dashboard}); await page.addScriptTag({content: js});
    await page.evaluate(async ({engine, saved}) => {
        await Promise.resolve();
        const names = Object.keys(engine.profiles);
        const data = {symbol: 'XAUUSD+', symbols: ['XAUUSD+'], start: '2025-09-01', end: '2025-09-08',
            mode: 'BAR', warehouse_set: true, specials: names, strategy_names: {},
            special_settings: Object.fromEntries(names.map(name => [name, {enabled: false, trigger: null, time_filters: null}])),
            default_triggers: {}, trigger_choices: ['올존'], spread_points: {},
            virtual_entry: saved?.policy || null, virtual_entry_target: saved?.target || null,
            virtual_entry_strategy: saved?.strategy || null,
            virtual_profiles: engine.profiles, virtual_ladder: engine.ladder};
        window.api = async route => route === 'mo/backtest/options' ? data : {modules: {}, lines: []};
        moState.view = 'backtest';
        mo('#view-strategy').classList.add('hidden'); mo('#view-live').classList.add('hidden');
        mo('#view-backtest').classList.remove('hidden');
        await moBacktestOptions();
    }, {engine, saved});
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
const step = (page, at) => page.locator(`#mo-bt-virtual-strategy [data-strategy-step="${at}"]`);
const shown = locator => locator.evaluate(field => field.tagName === 'SELECT' ? field.options[field.selectedIndex]?.textContent : field.value);
const unfold = page => page.locator('#mo-bt-virtual-strategy-details > summary').click();
// Every frame field but the base frame: [kind of element, disabled] for each.
const frameFields = page => page.locator('#mo-bt-virtual-panel').evaluate(panel =>
    [...panel.querySelectorAll('[data-virtual-field="tf"], [data-virtual-field="price_tf"]')]
        .filter(field => !field.closest('#mo-bt-virtual-main')).map(field => [field.tagName, field.disabled]));
add('only the base frame is a choice; every other frame field shows its frame', async page => {
    for (const name of ['SPECIAL9', 'SPECIAL1', 'SPECIAL7']) {
        await choose(page, name);
        await unfold(page);
        const fields = await frameFields(page);
        assert.ok(fields.length > 3, name + ' frame fields: ' + fields.length);
        assert.deepEqual(fields.filter(([tag, disabled]) => tag !== 'INPUT' || !disabled), [], name);
        assert.equal(await base(page).evaluate(field => field.tagName), 'SELECT');
        await page.locator('#mo-bt-virtual-strategy-details > summary').click();
    }
    await choose(page, 'SPECIAL1');
    const environment = page.locator('[data-virtual-filter="ENVIRONMENT"]').nth(1);
    assert.equal(await shown(environment.locator('[data-virtual-field="tf"]')), '환경 프레임 (1시간·2시간·3시간·4시간)');
});
add('new conditions and limits take the base frame', async page => {
    await choose(page, 'SPECIAL8');
    await base(page).selectOption('2m');
    await page.locator('#mo-bt-virtual-condition-kind').selectOption('MA_TOUCH_CANDLE');
    await page.locator('#mo-bt-virtual-condition-add').click();
    await page.locator('#mo-bt-virtual-filter-kind').selectOption('ENVIRONMENT');
    await page.locator('#mo-bt-virtual-filter-add').click();
    const value = (await request(page)).virtual_entry;
    assert.equal(value.conditions.at(-1).tf, 'SIGNAL');
    assert.equal(value.filters.at(-1).tf, 'SIGNAL');
    assert.equal(await shown(page.locator('[data-virtual-condition="MA_TOUCH_CANDLE"] [data-virtual-field="tf"]')), '2분');
    assert.equal(await shown(page.locator('[data-virtual-filter="ENVIRONMENT"]').last().locator('[data-virtual-field="tf"]')), '2분');
    assert.deepEqual((await frameFields(page)).filter(([tag, disabled]) => tag !== 'INPUT' || !disabled), []);
});
add('a base frame refills every value from the recipe, dropping the edits made before', async page => {
    await choose(page, 'SPECIAL9');
    await page.locator('#mo-bt-virtual-stop [data-virtual-field="multiplier"]').fill('2');
    await page.getByRole('button', {name: '이평 위·아래 삭제'}).click();
    await page.locator('#mo-bt-virtual-condition-kind').selectOption('CANDLE_SHAPE');
    await page.locator('#mo-bt-virtual-condition-add').click();
    await page.locator('[data-virtual-filter="ENVIRONMENT"]').nth(1).locator('[data-virtual-field="bar_state"]').selectOption('FORMING');
    await unfold(page);
    await step(page, 'steps.4').locator('[data-virtual-field="slow_period"]').fill('60');
    let value = await request(page);
    assert.equal(value.virtual_entry.stop.multiplier, 2);
    assert.equal(value.virtual_strategy.steps[4].slow_period, 60);
    await base(page).selectOption('2m');
    value = await request(page);
    assert.deepEqual(value.virtual_entry, engine.expected.SPECIAL9['2m'].policy);
    assert.deepEqual(value.virtual_strategy, engine.expected.SPECIAL9['2m'].strategy);
    assert.equal(await shown(step(page, 'steps.1').locator('[data-virtual-field="tf"]')), '20분');
    assert.equal(await shown(step(page, 'steps.0').locator('[data-virtual-field="tf"]')), '2분');
    assert.equal(await shown(page.locator('[data-virtual-filter="ENVIRONMENT"]').nth(1).locator('[data-virtual-field="tf"]')), '20분');
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special9-refilled-2m.png')});
    await base(page).selectOption('SIGNAL');
    value = await request(page);
    assert.deepEqual(value.virtual_entry, engine.profiles.SPECIAL9.default);
    assert.deepEqual(value.virtual_strategy, engine.profiles.SPECIAL9.strategy);
});
add('the strategy conditions are folded by default and show the recipe values', async page => {
    await choose(page, 'SPECIAL8');
    assert.equal(await page.locator('#mo-bt-virtual-strategy-details').evaluate(details => details.open), false);
    assert.equal(await page.locator('#mo-bt-virtual-strategy').isVisible(), false);
    await unfold(page);
    const titles = await page.locator('#mo-bt-virtual-strategy [data-strategy-step] strong').allTextContents();
    assert.deepEqual(titles, ['골든크로스', '이평 터치', '데드크로스']);
    const groups = await page.locator('#mo-bt-virtual-strategy > p').allTextContents();
    assert.deepEqual(groups, ['조건', '취소 조건']);
    const cross = step(page, 'steps.0');
    assert.deepEqual(await cross.locator('[data-virtual-field="family"]').evaluateAll(rows => rows.map(row => row.value)), ['HMA', 'HMA']);
    assert.deepEqual(await cross.locator('[data-virtual-field="period"]').evaluateAll(rows => rows.map(row => row.value)), ['17', '50']);
    assert.equal(await shown(cross.locator('[data-virtual-field="tf"]')), '1분');
    assert.equal(await cross.locator('[data-virtual-field="tf"]').isDisabled(), true);
    assert.equal(await shown(cross.locator('[data-virtual-field="bar_state"]')), '확정봉');
    assert.equal(await step(page, 'steps.1').locator('[data-virtual-field="slow_period"]').inputValue(), '50');
    assert.deepEqual((await request(page)).virtual_strategy, engine.profiles.SPECIAL8.strategy);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'special8-strategy-conditions.png')});
});
add('an edited strategy value is sent with the test, and nothing else changes', async page => {
    await choose(page, 'SPECIAL8');
    await unfold(page);
    await step(page, 'steps.1').locator('[data-virtual-field="slow_period"]').fill('60');
    const cross = step(page, 'steps.0');
    await cross.locator('[data-virtual-field="family"]').first().selectOption('EMA');
    await cross.locator('[data-virtual-field="period"]').first().fill('20');
    const value = await request(page);
    const recipe = structuredClone(engine.profiles.SPECIAL8.strategy);
    recipe.steps[0].ma_left = 'EMA20'; recipe.steps[1].slow_period = 60;
    assert.deepEqual(value.virtual_strategy, recipe);
    assert.deepEqual(value.virtual_entry, engine.profiles.SPECIAL8.default);
});
add('a strategy on several frames shows its frames and its branches', async page => {
    await choose(page, 'SPECIAL1');
    await unfold(page);
    const oz = step(page, 'final').locator('input');
    assert.equal(await oz.isDisabled(), true);
    assert.equal(await oz.inputValue(), '1분·2분·3분·4분·5분·6분·10분·12분·15분·20분·30분·1시간');
    const groups = await page.locator('#mo-bt-virtual-strategy > p').allTextContents();
    assert.equal(groups[0], '분기 1 · 매수 · 조건');
    const touch = step(page, 'branches.0.steps.1');
    assert.equal(await shown(touch.locator('[data-virtual-field="side"]')), '하단');
    await touch.locator('[data-virtual-field="side"]').selectOption('UPPER');
    assert.equal((await request(page)).virtual_strategy.branches[0].steps[1].side, 'UPPER');
    assert.equal(await base(page).isDisabled(), true);
});
add("the last run's strategy edits return for their own strategy", async page => {
    const restored = await fixture(page.context().browser(), engine.saved);
    try {
        await choose(restored, 'SPECIAL8');
        const value = await request(restored);
        assert.deepEqual(value.virtual_entry, engine.saved.policy);
        assert.deepEqual(value.virtual_strategy, engine.saved.strategy);
        assert.equal(await shown(base(restored)), '2분');
        await choose(restored, 'SPECIAL9');
        assert.deepEqual((await request(restored)).virtual_strategy, engine.profiles.SPECIAL9.strategy);
    } finally { await restored.close(); }
});
add('SPECIAL8 on 2m and 5m shows those frames everywhere; SPECIAL9 trends on 20m and 30m', async page => {
    await choose(page, 'SPECIAL8');
    await unfold(page);
    for (const [tf, text] of [['2m', '2분'], ['5m', '5분']]) {
        await base(page).selectOption(tf);
        const value = await request(page);
        assert.deepEqual(value.virtual_entry, engine.expected.SPECIAL8[tf].policy);
        assert.equal(await shown(page.locator('[data-virtual-condition="MA_POSITION"] [data-virtual-field="tf"]')), text);
        assert.equal(await shown(page.locator('#mo-bt-virtual-stop [data-virtual-field="tf"]')), text);
        assert.equal(await shown(step(page, 'steps.1').locator('[data-virtual-field="tf"]')), text);
    }
    await choose(page, 'SPECIAL9');
    for (const [tf, trend] of [['2m', '20m'], ['3m', '30m']]) {
        await base(page).selectOption(tf);
        const value = await request(page);
        assert.equal(value.virtual_entry.filters.find(row => row.condition === 'TREND').tf, trend);
        assert.deepEqual(value.virtual_strategy, engine.expected.SPECIAL9[tf].strategy);
    }
    const text = await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.textContent);
    assert.doesNotMatch(text, /자동|SIGNAL|신호/);
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
