/* Virtual-entry panel in isolated Edge with the real HTML/CSS/JS; APIs are mocked, no engines run.
   argv: playwright module, evidence folder. stdin: the strategy profiles with their recipe values. */
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
async function fixture(browser, saved = {policy: null, target: null}) {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html); await page.addStyleTag({content: css});
    await page.evaluate(() => { window.setInterval = () => 0; window.api = async () => ({modules: {}, lines: []}); });
    await page.addScriptTag({content: dashboard}); await page.addScriptTag({content: js});
    await page.evaluate(async ({engine, saved}) => {
        await Promise.resolve();
        const names = Object.keys(engine.profiles);
        window.optionData = () => ({symbol: 'XAUUSD+', symbols: ['XAUUSD+'], start: '2025-09-01', end: '2025-09-08',
            mode: 'BAR', warehouse_set: true, specials: names, strategy_names: {},
            special_settings: Object.fromEntries(names.map(name => [name, {enabled: false, trigger: null, time_filters: null}])),
            default_triggers: Object.fromEntries(names.map(name => [name, engine.profiles[name].oz ? '올존' : null])),
            trigger_choices: ['올존'], spread_points: {}, virtual_entry: saved.policy, virtual_entry_target: saved.target,
            virtual_profiles: engine.profiles});
        window.api = async (route, body) => route === 'mo/backtest/options' ? optionData() : {modules: {}, lines: []};
        moState.view = 'backtest';
        mo('#view-strategy').classList.add('hidden'); mo('#view-live').classList.add('hidden');
        mo('#view-backtest').classList.remove('hidden');
        await moBacktestOptions();
    }, {engine, saved});
    return page;
}
async function choose(page, names, result = 'VIRTUAL_ENTRY') {
    await page.evaluate(({names, result}) => {
        mo('#mo-bt-target').value = 'SPECIAL';
        mo('#mo-bt-build-only').checked = false; mo('#mo-bt-result-mode').value = result;
        document.querySelectorAll('#mo-bt-specials [data-special]').forEach(box => {
            box.querySelector('.mo-special-enabled').checked = names.includes(box.dataset.special);
        });
        mo('#mo-bt-specials').dispatchEvent(new Event('change'));
    }, {names, result});
}
const request = page => page.evaluate(() => moBacktestRequest().virtual_entry);
const options = (page, selector) => page.locator(selector).evaluate(select => [...select.options].map(o => o.textContent));
add('no auto choice or signal-frame wording for any target', async page => {
    for (const names of [['SPECIAL5'], ['SPECIAL8'], ['SPECIAL7']]) {
        await choose(page, names);
        for (const mode of ['IMMEDIATE', 'CONFIRM']) {
            await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption(mode);
            const text = await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.textContent);
            assert.doesNotMatch(text, /자동|신호|SIGNAL|AUTO/);
        }
    }
});
add('an OZ strategy is filled with its recipe: conditional entry, closed candle, HMA6 and B0', async page => {
    await choose(page, ['SPECIAL5']);
    assert.deepEqual(await request(page), engine.profiles.SPECIAL5.default);
    assert.deepEqual(await options(page, '#mo-bt-virtual-main [data-virtual-field="mode"]'), ['즉시 진입', '조건 진입']);
    assert.equal(await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').inputValue(), 'CONFIRM');
    assert.equal((await options(page, '#mo-bt-virtual-main [data-virtual-field="tf"]'))[0], '1분·2분·3분');
    const conditions = page.locator('#mo-bt-virtual-conditions [data-virtual-condition]');
    assert.deepEqual(await conditions.evaluateAll(rows => rows.map(row => row.dataset.virtualCondition)),
        ['CANDLE_CLOSE', 'MA_POSITION']);
    const stop = page.locator('#mo-bt-virtual-stop');
    assert.deepEqual(await options(page, '#mo-bt-virtual-stop [data-virtual-field="kind"]'),
        ['올존 B0', '직전 N봉 저점·고점', 'ATR']);
    assert.equal(await stop.locator('[data-virtual-field]').count(), 1);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'oz-default.png')});
});
add('a non-OZ strategy is filled with its recipe values, without B0', async page => {
    await choose(page, ['SPECIAL8']);
    assert.deepEqual(await request(page), engine.profiles.SPECIAL8.default);
    assert.equal(await page.locator('#mo-bt-virtual-confirm').isVisible(), true);
    assert.deepEqual(await options(page, '#mo-bt-virtual-stop [data-virtual-field="kind"]'), ['직전 N봉 저점·고점', 'ATR']);
    // The stop's frame is the strategy's own, shown as 1분 and not chosen here (수정본146).
    const stopFrame = page.locator('#mo-bt-virtual-stop [data-virtual-field="tf"]');
    assert.equal(await stopFrame.inputValue(), '1분');
    assert.equal(await stopFrame.isDisabled(), true);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'other-default.png')});
});
add('ATR settings appear only for the ATR stop', async page => {
    await choose(page, ['SPECIAL7']);
    const stop = page.locator('#mo-bt-virtual-stop');
    const fields = () => stop.locator('[data-virtual-field]').evaluateAll(rows => rows.map(row => row.dataset.virtualField));
    assert.deepEqual(await fields(), ['kind']);
    await stop.locator('[data-virtual-field="kind"]').selectOption('ATR');
    assert.deepEqual(await fields(), ['kind', 'tf', 'period', 'multiplier']);
    await stop.locator('[data-virtual-field="multiplier"]').fill('1.5');
    await stop.locator('[data-virtual-field="period"]').fill('21');
    assert.deepEqual((await request(page)).stop, {...engine.profiles.SPECIAL7.default.stop, kind: 'ATR', period: 21, multiplier: 1.5});
    await stop.locator('[data-virtual-field="kind"]').selectOption('RECENT_EXTREME');
    assert.deepEqual(await fields(), ['kind', 'tf', 'bars']);
    assert.equal(await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.textContent.includes('ATR 기준')), false);
});
add('conditional entry starts from the recipe conditions and its frames follow the base frame', async page => {
    await choose(page, ['SPECIAL8']);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('IMMEDIATE');
    assert.deepEqual((await request(page)).conditions, []);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('CONFIRM');
    assert.deepEqual((await request(page)).conditions, engine.profiles.SPECIAL8.default.conditions);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="tf"]').selectOption('5m');
    const value = await request(page);
    assert.equal(value.tf, '5m');
    // 수정본142: the whole test runs on the base frame, so the strategy's own frame is 5m and shown as such.
    assert.equal(value.conditions[1].tf, 'SIGNAL');
    assert.equal(value.stop.tf, 'SIGNAL');
    assert.equal(await page.locator('#mo-bt-virtual-stop [data-virtual-field="tf"]').inputValue(), '5분');
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('IMMEDIATE');
    assert.deepEqual((await request(page)).conditions, []);
});
add('conditions and limits can be added and removed', async page => {
    await choose(page, ['SPECIAL5']);
    assert.deepEqual(await options(page, '#mo-bt-virtual-condition-kind'),
        ['양봉·음봉 마감', '망치형·역망치형', '이평 위·아래', '이평 종가 돌파', '이평 터치 후 확인',
         '확인봉 이평 터치', '인걸핑', '넥라인 종가 돌파']);
    await page.locator('#mo-bt-virtual-condition-kind').selectOption('ENGULFING');
    await page.locator('#mo-bt-virtual-condition-add').click();
    await page.getByRole('button', {name: '양봉·음봉 마감 삭제'}).click();
    assert.deepEqual((await request(page)).conditions.map(row => row.kind), ['MA_POSITION', 'ENGULFING']);
    // A recipe with limits shows them unfolded already.
    assert.equal(await page.locator('#mo-bt-virtual-filter-details').evaluate(details => details.open), true);
    await page.locator('#mo-bt-virtual-filter-kind').selectOption('CANDLE_ATR');
    await page.locator('#mo-bt-virtual-filter-add').click();
    const filter = page.locator('[data-virtual-filter="CANDLE_ATR"]');
    await filter.locator('[data-virtual-field="measure"]').selectOption('RANGE');
    await filter.locator('[data-virtual-field="min"]').fill('0.3');
    assert.deepEqual((await request(page)).filters.slice(-1), [{kind: 'CANDLE_ATR', min: .3, max: 2, measure: 'RANGE'}]);
    await choose(page, ['SPECIAL8']);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('CONFIRM');
    assert.equal((await options(page, '#mo-bt-virtual-condition-kind')).includes('넥라인 종가 돌파'), false);
});
add('both backtest modes keep one selected strategy', async page => {
    await choose(page, ['SPECIAL5', 'SPECIAL8']);
    assert.equal(await page.locator('#mo-bt-special-count').textContent(), '1개 전략 선택됨');
    assert.equal(await page.locator('#mo-bt-virtual-body').isVisible(), true);
    assert.deepEqual(await page.evaluate(() => moBacktestRequest().specials), ['SPECIAL8']);
    assert.deepEqual(await request(page), engine.profiles.SPECIAL8.default);
    await choose(page, ['SPECIAL5', 'SPECIAL8'], 'ALERT_ONLY');
    assert.equal(await page.locator('#mo-bt-special-count').textContent(), '1개 전략 선택됨');
    assert.deepEqual(await page.evaluate(() => moBacktestRequest().specials), ['SPECIAL8']);
    assert.equal(await request(page), undefined);
});
add('the strategy popup keeps one checked strategy in virtual entry', async page => {
    await choose(page, ['SPECIAL5']);
    await page.evaluate(async () => {
        if (!mo('#strategy-settings-dialog').showModal) mo('#strategy-settings-dialog').showModal = () => {};
        await moOpenStrategyPopup('backtest');
    });
    const checks = page.locator('#strategy-settings-cards .moses-strategy-toggle input[type=checkbox]');
    await checks.nth(2).check();
    assert.deepEqual(await checks.evaluateAll(rows => rows.map(row => row.checked)), [false, false, true]);
    assert.equal(await page.locator('#strategy-list-select-all').isDisabled(), true);
});
add('the last run returns only for its own strategy', async page => {
    const custom = {...engine.profiles.SPECIAL7.default, conditions: [{kind: 'ENGULFING'}], stop: {...engine.profiles.SPECIAL7.default.stop, kind: 'ATR', multiplier: 2}};
    const restored = await fixture(page.context().browser(), {policy: custom, target: 'SPECIAL7'});
    try {
        await choose(restored, ['SPECIAL7']);
        assert.deepEqual(await request(restored), custom);
        await choose(restored, ['SPECIAL5']);
        assert.deepEqual(await request(restored), engine.profiles.SPECIAL5.default);
    } finally { await restored.close(); }
});
add('a WATCH command is alert-only', async page => {
    await page.evaluate(() => {
        mo('#mo-bt-build-only').checked = false; mo('#mo-bt-result-mode').value = 'VIRTUAL_ENTRY';
        mo('#mo-bt-target').value = 'WATCH'; mo('#mo-bt-watch').value = '골드 5분 무지성 브레이커 매수 올존 알려줘';
        moBacktestMode();
    });
    assert.equal(await page.evaluate(() => mo('#mo-bt-result-mode').value), 'ALERT_ONLY');
    assert.equal(await page.locator('[data-bt-result="VIRTUAL_ENTRY"]').isDisabled(), true);
    assert.equal(await page.locator('#mo-bt-virtual-panel').isVisible(), false);
    assert.equal(await request(page), undefined);
});
add('virtual controls fit a narrow viewport without overflowing their panel', async page => {
    await choose(page, ['SPECIAL5']);
    await page.setViewportSize({width: 600, height: 900});
    await page.locator('#mo-bt-virtual-condition-add').click();
    assert.equal(await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.scrollWidth <= panel.clientWidth), true);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'narrow.png')});
});
add('RR summary shows one uncertainty column and an exclusion explanation', async page => {
    await page.evaluate(() => {
        moState.resultShown = true; moBacktestPage('result');
        window.MosesBacktestDashboard.render(mo('#mo-bt-result'), {virtual_entry: {summary: [
            {strategy: 'TEST', rr: 2, alerts: 3, entries: 3, wins: 1, losses: 1,
             unclosed: 0, uncertain: 1, win_rate: .5, average_r: .5, total_r: 1}]}}, 'test');
    });
    const shown = await page.locator('#mo-bt-result table').evaluate(table => {
        const names = [...table.querySelectorAll('th')].map(node => node.textContent);
        return {columns: names.filter(text => text === '순서 미확정').length,
            value: table.querySelector('tbody tr').children[names.indexOf('순서 미확정')].textContent};
    });
    assert.deepEqual(shown, {columns: 1, value: '1'});
    assert.match(await page.locator('#mo-bt-result').textContent(), /승률·평균 R에서 제외/);
});
add('analytics uncertainty count and trade labels fit the existing result screen', async page => {
    await page.setViewportSize({width: 600, height: 900});
    await page.evaluate(() => {
        moState.resultShown = true; moBacktestPage('result');
        const summary = {total_trades: 3, wins: 1, losses: 1, unclosed: 0, uncertain: 1,
            win_rate: .5, total_r: 1, average_r: .5, profit_factor: 2, expectancy_r: .5};
        window.api = async () => ({rows: [{strategy: 'TEST', symbol: 'XAUUSD+', tf: '1m',
            direction: 'LONG', rr: 2, result: 'UNCERTAIN', r: null,
            alert_time: 60_500, entry_time: 60_500, exit_time: 120_000},
            {strategy: 'TEST', symbol: 'XAUUSD+', tf: '1m', direction: 'LONG', rr: 2,
             result: 'WIN', r: 2, alert_time: 60_500, entry_time: 60_500, exit_time: 180_000}], next_offset: null});
        // 수정본165: the screen comes from Part2's result view (analysis), not the stored analytics.json.
        window.MosesBacktestDashboard.render(mo('#mo-bt-result'), {analysis: {period: 'all', rr: '2.0', default_rr: '2.0',
            periods: {years: ['2026'], months: ['2026-10']}, start_balance: 10000,
            rr_table: [{rr: '2.0', trades: 2, summary, grades: {}, significant: false,
                values: {total_r: 1, average_r: .5, win_rate: .5, profit_factor: 2}}],
            detail: {monthly: [{month: '2026-10', ...summary}], hourly: [], equity: [], drawdown: []},
            slots: {min_trades: 30, ranges: [], all: {trades: 2}, chosen: {trades: 0}, check: null},
            accounts: {all: {one_percent: null, quarter_kelly: null}, chosen: {one_percent: null, quarter_kelly: null}}}}, 'test');
    });
    assert.match(await page.locator('.bt-analysis-notice').textContent(), /순서 미확정 1건/);
    assert.equal(await page.locator('[data-metric="win_rate"] strong').textContent(), '50.0%');
    await page.locator('#bt-tab-monthly').click();
    assert.equal(await page.locator('#bt-dashboard-panel th').filter({hasText: /^순서 미확정$/}).count(), 1);
    await page.locator('#bt-tab-trades').click();
    await page.locator('#bt-dashboard-panel tbody').waitFor();
    assert.match(await page.locator('#bt-dashboard-panel tbody').textContent(), /순서 미확정/);
    assert.doesNotMatch(await page.locator('#bt-dashboard-panel tbody').textContent(), /UNCERTAIN/);
    assert.equal(await page.locator('#mo-bt-result').evaluate(panel => panel.scrollWidth <= panel.clientWidth), true);
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
            environment: 'isolated Edge, actual HTML/CSS/JS, mocked APIs', results};
        fs.writeFileSync(path.join(out, 'frontend.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
