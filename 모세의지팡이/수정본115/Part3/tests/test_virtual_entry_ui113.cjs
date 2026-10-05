/* Actual HTML/CSS/JS in isolated Edge; no engines or Telegram requests. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const out = path.join(root, '검증결과/revision113/ui');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const js = fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8');
const dashboard = fs.readFileSync(path.join(root, 'Part3/web/backtest_dashboard.js'), 'utf8');
const tests = [];
const add = (name, fn) => tests.push({name, fn});
async function fixture(browser) {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html); await page.addStyleTag({content: css});
    await page.evaluate(() => {
        window.setInterval = () => 0;
        window.api = async () => ({modules: {}, lines: []});
    });
    await page.addScriptTag({content: dashboard}); await page.addScriptTag({content: js});
    await page.evaluate(async () => {
        await Promise.resolve();
        window.savedPolicy = moVirtualEntryDefault();
        window.optionData = () => ({symbol: 'XAUUSD+', symbols: ['XAUUSD+'],
            start: '2025-09-01', end: '2025-09-08', mode: 'BAR', warehouse_set: true,
            specials: [], special_settings: {}, spread_points: {}, trigger_choices: [],
            virtual_entry: structuredClone(savedPolicy), virtual_timeframes: ['1m', '5m', '1h', '1d']});
        window.api = async route => route === 'mo/backtest/options' ? optionData() : {modules: {}, lines: []};
        moState.view = 'backtest';
        mo('#view-strategy').classList.add('hidden'); mo('#view-live').classList.add('hidden');
        mo('#view-backtest').classList.remove('hidden');
        mo('#mo-bt-target').value = 'WATCH';
        await moBacktestOptions();
    });
    return page;
}
async function virtualMode(page, mode='CONFIRM') {
    await page.evaluate(mode => {
        mo('#mo-bt-build-only').checked = false;
        mo('#mo-bt-result-mode').value = 'VIRTUAL_ENTRY';
        moVirtualEntryDraft.mode = mode; moVirtualEntryRender(); moBacktestMode();
    }, mode);
}
add('virtual panel is present only for virtual replay', async page => {
    assert.equal(await page.locator('#mo-bt-virtual-panel').isVisible(), false);
    await page.locator('[data-bt-result="VIRTUAL_ENTRY"]').click();
    assert.equal(await page.locator('#mo-bt-virtual-panel').isVisible(), true);
    await page.locator('[data-bt-result="BUILD_ONLY"]').click();
    assert.equal(await page.locator('#mo-bt-virtual-panel').isVisible(), false);
    const request = await page.evaluate(() => moBacktestRequest());
    assert.equal('virtual_entry' in request, false);
    await page.locator('[data-bt-result="ALERT_ONLY"]').click();
    assert.equal(await page.locator('#mo-bt-virtual-panel').isVisible(), false);
});
add('all common confirmation conditions can be added and deleted', async page => {
    await virtualMode(page);
    for (const kind of ['MA_POSITION', 'MA_CROSS', 'MA_TOUCH', 'ENGULFING', 'NECKLINE_BREAK']) {
        await page.locator('#mo-bt-virtual-condition-kind').selectOption(kind);
        await page.locator('#mo-bt-virtual-condition-add').click();
    }
    const first = page.locator('[data-virtual-condition="MA_POSITION"]');
    await first.locator('[data-virtual-field="family"]').selectOption('EMA');
    await first.locator('[data-virtual-field="period"]').fill('200');
    await first.locator('[data-virtual-field="tf"]').selectOption('1h');
    const value = await page.evaluate(() => moBacktestRequest().virtual_entry);
    assert.equal(value.mode, 'CONFIRM');
    assert.deepEqual(value.conditions[0], {kind: 'MA_POSITION', family: 'EMA', period: 200, tf: '1h'});
    assert.deepEqual(value.conditions.map(row => row.kind), ['MA_POSITION', 'MA_CROSS', 'MA_TOUCH', 'ENGULFING', 'NECKLINE_BREAK']);
    assert.deepEqual(value.conditions[3], {kind: 'ENGULFING'});
    await first.getByRole('button', {name: '이평 위·아래 조건 삭제'}).click();
    assert.equal((await page.evaluate(() => moBacktestRequest().virtual_entry)).conditions.length, 4);
});
add('plain candle confirmation has no compulsory MA rule', async page => {
    await virtualMode(page);
    const value = await page.evaluate(() => moBacktestRequest().virtual_entry);
    assert.deepEqual(value.conditions, []);
    assert.match(await page.locator('#mo-bt-virtual-conditions').textContent(), /양봉·음봉 마감만/);
    assert.equal(await page.locator('#mo-bt-virtual-conditions select').count(), 0);
});
add('candle ATR min max and MA distance are transmitted separately', async page => {
    await virtualMode(page);
    await page.locator('#mo-bt-virtual-filter-details summary').click();
    for (const kind of ['CANDLE_ATR', 'MA_DISTANCE_ATR']) {
        await page.locator('#mo-bt-virtual-filter-kind').selectOption(kind);
        await page.locator('#mo-bt-virtual-filter-add').click();
    }
    const candle = page.locator('[data-virtual-filter="CANDLE_ATR"]');
    await candle.locator('[data-virtual-field="measure"]').selectOption('RANGE');
    await candle.locator('[data-virtual-field="min"]').fill('0.3');
    await candle.locator('[data-virtual-field="max"]').fill('2');
    const distance = page.locator('[data-virtual-filter="MA_DISTANCE_ATR"]');
    await distance.locator('[data-virtual-field="period"]').fill('6');
    await distance.locator('[data-virtual-field="max"]').fill('1');
    await page.locator('#mo-bt-virtual-atr [data-virtual-field="tf"]').selectOption('5m');
    await page.locator('#mo-bt-virtual-atr [data-virtual-field="period"]').fill('14');
    await page.locator('#mo-bt-virtual-mode-help').click();
    const value = await page.evaluate(() => moBacktestRequest().virtual_entry);
    assert.deepEqual(value.filters[0], {kind: 'CANDLE_ATR', min: .3, max: 2, measure: 'RANGE'});
    assert.deepEqual(value.filters[1], {kind: 'MA_DISTANCE_ATR', family: 'HMA', period: 6, tf: 'SIGNAL', min: null, max: 1});
    assert.deepEqual(value.atr, {tf: '5m', period: 14});
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'confirm-atr.png')});
});
add('stop modes expose only their necessary fields and preserve edits', async page => {
    await virtualMode(page, 'IMMEDIATE');
    const stop = page.locator('#mo-bt-virtual-stop');
    await stop.locator('[data-virtual-field="kind"]').selectOption('RECENT_EXTREME');
    await stop.locator('[data-virtual-field="bars"]').fill('8');
    await stop.locator('[data-virtual-field="tf"]').selectOption('5m');
    let value = await page.evaluate(() => moBacktestRequest().virtual_entry);
    assert.deepEqual(value.stop, {kind: 'RECENT_EXTREME', tf: '5m', bars: 8, multiplier: 1});
    await stop.locator('[data-virtual-field="kind"]').selectOption('ATR');
    assert.equal(await stop.locator('[data-virtual-field="bars"]').count(), 0);
    await stop.locator('[data-virtual-field="multiplier"]').fill('0.5');
    await stop.locator('[data-virtual-field="tf"]').selectOption('1h');
    value = await page.evaluate(() => moBacktestRequest().virtual_entry);
    assert.deepEqual(value.stop, {kind: 'ATR', tf: '1h', bars: 5, multiplier: .5});
    await stop.locator('[data-virtual-field="kind"]').selectOption('RECENT_EXTREME');
    assert.equal(await stop.locator('[data-virtual-field="bars"]').inputValue(), '8');
});
add('navigation and options refresh keep the current policy draft', async page => {
    await virtualMode(page);
    await page.locator('#mo-bt-virtual-condition-add').click();
    await page.locator('[data-virtual-condition] [data-virtual-field="period"]').fill('50');
    await page.locator('#mo-bt-virtual-mode-help').click();
    const before = await page.evaluate(() => moVirtualEntryValue());
    await page.evaluate(async () => {moBacktestPage('specials'); moBacktestPage('setup'); await moBacktestOptions();});
    assert.deepEqual(await page.evaluate(() => moVirtualEntryValue()), before);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('IMMEDIATE');
    assert.equal(await page.locator('#mo-bt-virtual-confirm').isVisible(), false);
    assert.deepEqual((await page.evaluate(() => moVirtualEntryValue())).conditions, []);
    await page.locator('#mo-bt-virtual-main [data-virtual-field="mode"]').selectOption('CONFIRM');
    assert.deepEqual(await page.evaluate(() => moVirtualEntryValue()), before);
});
add('saved policy is restored when a new settings screen initializes', async page => {
    await page.evaluate(async () => {
        savedPolicy = {...moVirtualEntryDefault(), mode: 'CONFIRM', tf: '5m',
            conditions: [{kind: 'ENGULFING'}], stop: {kind: 'ATR', tf: '1h', bars: 5, multiplier: 2}};
        moVirtualEntryDraft = null; await moBacktestOptions();
    });
    const value = await page.evaluate(() => moVirtualEntryValue());
    assert.equal(value.tf, '5m'); assert.deepEqual(value.conditions, [{kind: 'ENGULFING'}]);
    assert.equal(value.stop.multiplier, 2);
});
add('API policy validation errors remain visible and retryable', async page => {
    await virtualMode(page);
    await page.evaluate(async () => {
        mo('#mo-bt-watch').value = 'test';
        window.api = async () => {throw Error('ATR 최대 배수는 최소 배수보다 커야 합니다.');};
        await mo('#mo-bt-start-button').onclick();
    });
    assert.match(await page.locator('#mo-bt-setup-error').textContent(), /ATR 최대 배수/);
    assert.equal(await page.locator('#mo-bt-setup-error').isVisible(), true);
    assert.equal(await page.locator('#mo-bt-start-button').isEnabled(), true);
});
add('virtual controls fit a narrow viewport without overflowing their panel', async page => {
    await virtualMode(page);
    await page.setViewportSize({width: 600, height: 900});
    await page.locator('#mo-bt-virtual-condition-add').click();
    const fit = await page.locator('#mo-bt-virtual-panel').evaluate(panel => panel.scrollWidth <= panel.clientWidth);
    assert.equal(fit, true);
    await page.locator('#mo-bt-virtual-panel').screenshot({path: path.join(out, 'confirm-narrow.png')});
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
        window.MosesBacktestDashboard.render(mo('#mo-bt-result'), {analytics: {rr_results: {'2.0': {
            summary, monthly: [{month: '2026-10', ...summary}], drawdown: {}, streaks: {}}}}}, 'test');
    });
    assert.match(await page.locator('.bt-analysis-notice').textContent(), /순서 미확정 1건/);
    assert.equal(await page.locator('[data-metric="win_rate"] strong').textContent(), '50.0%');
    await page.locator('#bt-tab-monthly').click();
    assert.equal(await page.locator('#mo-bt-result th').filter({hasText: /^순서 미확정$/}).count(), 1);
    await page.locator('#mo-bt-result').screenshot({path: path.join(out, 'uncertain-narrow.png')});
    await page.locator('#bt-tab-trades').click();
    assert.match(await page.locator('#mo-bt-result tbody').textContent(), /순서 미확정/);
    assert.doesNotMatch(await page.locator('#mo-bt-result tbody').textContent(), /UNCERTAIN/);
    const exitTimes = await page.locator('#mo-bt-result table').evaluate(table => {
        const names = [...table.querySelectorAll('th')].map(node => node.textContent);
        return [...table.querySelectorAll('tbody tr')].map(row => row.children[names.indexOf('청산 (UTC)')].textContent);
    });
    assert.deepEqual(exitTimes, ['—', '1970-01-01 00:03:00 UTC']);
    const fits = await page.locator('#mo-bt-result').evaluate(panel => panel.scrollWidth <= panel.clientWidth);
    assert.equal(fits, true);
});
(async () => {
    fs.mkdirSync(out, {recursive: true});
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        for (const test of tests) {
            const page = await fixture(browser);
            try {await test.fn(page); results.push({name: test.name, passed: true}); console.log('PASS ' + test.name);}
            catch (error) {results.push({name: test.name, passed: false, error: error.message}); console.error('FAIL ' + test.name + ': ' + error.message);}
            finally {await page.close();}
        }
        const output = {tests: results.length, failed: results.filter(item => !item.passed).length,
            environment: 'isolated Edge, actual HTML/CSS/JS, mocked APIs', results};
        fs.writeFileSync(path.join(out, 'frontend.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally {await browser.close();}
})().catch(error => {console.error(error.message); process.exitCode = 1;});
