"""165: the shipped result screen: RR table sorted by an index, Korean-time periods, hours and accounts."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
document.createElementNS = (namespace, tag) => new Element(tag);
const clone = value => JSON.parse(JSON.stringify(value));
const row = (rr, total, average, drawdown, significant, grades = {}) => ({rr, trades: 100, significant, grades,
    values: {total_r: total, average_r: average, win_rate: .4, profit_factor: 1.2, recovery_factor: total / drawdown,
             sqn: 1, sharpe: 1, sortino: 1, max_drawdown_r: drawdown, max_consecutive_losses: 5, kelly: .01, t: significant ? 3 : 1},
    summary: {total_trades: 100, wins: 40, losses: 60, unclosed: 0, uncertain: 0, win_rate: .4, total_r: total, average_r: average}});
function view(period, rr) {
    const rows = [row('1.0', 10, .1, 5, false, {profit_factor: 'weak'}), row('2.0', 30, .3, 12, true, {profit_factor: 'good'}),
                  row('3.0', 20, .2, 8, true)];
    return {period, rr: rr || '2.0', default_rr: '2.0', start_balance: 10000, months: 4,
        periods: {years: ['2025', '2026'], months: ['2025-01', '2025-02', '2026-01']}, rr_table: rows,
        detail: {equity: [[1767225600000, 1], [1767312000000, 3]], drawdown: [[1767225600000, 0], [1767312000000, 1]],
                 monthly: [{month: '2026-01', total_trades: 3, total_r: 2, uncertain: 0}], hourly: [{hour: 9, total_trades: 3}],
                 by_direction: {LONG: {total_trades: 3}}, by_strategy: {}, by_symbol: {}, by_timeframe: {}},
        slots: {min_trades: 30, ranges: [[9, 12], [22, 2]], all: {trades: 100, win_rate: .4, average_r: .1, total_r: 10},
                chosen: {trades: 60, win_rate: .5, average_r: .2, total_r: 12},
                check: {ranges: [[9, 12]], all: {trades: 30, average_r: .05}, chosen: {trades: 20, average_r: -.01}}},
        accounts: {all: {one_percent: {fraction: .01, final: 10500, profit: 500, return: .05, max_drawdown: .1, max_open: 2},
                         quarter_kelly: null, kelly: -.01},
                   chosen: {one_percent: null, quarter_kelly: null, kelly: null}}};
}
const calls = [];
const context = vm.createContext({console, document, Option, window: {}, setTimeout, clearTimeout,
    api: async name => {
        calls.push(name);
        const query = new URLSearchParams(name.split('?')[1] || '');
        if (name.startsWith('mo/backtest/analysis')) return clone(view(query.get('period'), query.get('rr')));
        if (name.startsWith('mo/backtest/trades')) return {rows: [{alert_time: 1767225600000, rr: 2, result: 'UNCERTAIN',
            r: null, entry_time: 1767225600000, exit_time: 1767225660000, direction: 'LONG'}], next_offset: null};
        throw Error('unexpected ' + name);
    }});
const run = code => vm.runInContext(code, context), get = id => document.getElementById(id);
const settle = async () => {for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));};
const order = () => get('mo-bt-result').querySelectorAll('.bt-rr-table tbody tr').map(tr => tr.dataset.rr);
const selected = () => get('mo-bt-result').querySelectorAll('.bt-rr-table tbody tr').filter(tr => tr.classList.contains('bt-rr-selected')).map(tr => tr.dataset.rr);
const analysisCalls = () => calls.filter(name => name.startsWith('mo/backtest/analysis'));
(async () => {
    run(fs.readFileSync(root + '/backtest_dashboard.js', 'utf8'));
    const box = get('mo-bt-result');
    if (scenario === 'fallback') {
        run(`window.MosesBacktestDashboard.render(document.getElementById('mo-bt-result'), {analysis: null,
            analytics_message: '이 실행에는 거래내역이 없어 성과 분석을 표시하지 않습니다.',
            virtual_entry: {summary: [{strategy: 'S', rr: 2, uncertain: 1}]}}, 'job')`);
        assert(box.querySelector('.bt-rr-compare').classList.contains('hidden'));
        assert.match(box.querySelector('.bt-analysis-notice').textContent, /거래내역이 없어/);
        assert.equal(box.querySelectorAll('th').filter(th => th.textContent === '순서 미확정').length, 1);
        console.log('PASS ' + scenario); return;
    }
    context.initial = view('all');
    run(`window.MosesBacktestDashboard.render(document.getElementById('mo-bt-result'), {analysis: initial, status: '완료'}, 'job')`);
    assert.deepEqual(order(), ['2.0', '3.0', '1.0']);                          // total R, the default index
    assert.deepEqual(selected(), ['2.0']);
    const rows = box.querySelectorAll('.bt-rr-table tbody tr');
    assert(rows[2].classList.contains('bt-rr-chance') && !rows[0].classList.contains('bt-rr-chance'));
    assert(rows[2].children[5].classList.contains('bt-grade-weak'));           // PF of RR 1.0
    assert.equal(box.querySelector('[data-metric="total_r"] strong').textContent, '30.00 R');
    assert.equal(box.querySelector('[data-metric="average_r"] strong').textContent, '0.300 R');
    assert.match(box.querySelector('.bt-slot-hours').textContent, /09–12시, 22–02시/);
    assert.match(box.querySelector('.bt-slot-check').textContent, /09–12시.*-0\.010 R \(20건\).*0\.050 R \(30건\)/);
    const accounts = box.querySelector('.bt-accounts').textContent;
    assert.match(accounts, /24시간 · 1%.*\$10,500/); assert.match(accounts, /24시간 · ¼ 켈리 · 우위 없음/);
    assert.match(accounts, /선정 시간대 · 1% · 거래 없음/);
    assert.equal(get('bt-period-month').disabled, true);
    if (scenario === 'sort') {
        get('bt-rr-sort').value = 'max_drawdown_r'; get('bt-rr-sort').onchange(); await settle();
        assert.deepEqual(order(), ['1.0', '3.0', '2.0']);                      // less is better; 1.0 is chance
        assert.equal(analysisCalls().at(-1), 'mo/backtest/analysis?id=job&period=all&rr=3.0');
        assert.deepEqual(selected(), ['3.0']);
    } else if (scenario === 'period') {
        // 수정본170: the period's RR table first, then once the top RR of the ranking on screen.
        get('bt-period-year').value = '2025'; get('bt-period-year').onchange(); await settle();
        assert.deepEqual(analysisCalls(), ['mo/backtest/analysis?id=job&period=2025&table=1',
            'mo/backtest/analysis?id=job&period=2025&rr=2.0']);
        assert.equal(get('bt-period-month').disabled, false);
        assert.deepEqual(get('bt-period-month').options.map(option => option.value), ['2025', '2025-01', '2025-02']);
        get('bt-period-month').value = '2025-02'; get('bt-period-month').onchange(); await settle();
        assert.equal(analysisCalls().at(-1), 'mo/backtest/analysis?id=job&period=2025-02&rr=2.0');
    } else if (scenario === 'period_sorted') {
        get('bt-rr-sort').value = 'max_drawdown_r'; get('bt-rr-sort').onchange(); await settle();
        get('bt-period-year').value = '2025'; get('bt-period-year').onchange(); await settle();
        // The index on screen picks its own top RR from the period's table; that RR is computed once.
        assert.deepEqual(analysisCalls().slice(-2), ['mo/backtest/analysis?id=job&period=2025&table=1',
            'mo/backtest/analysis?id=job&period=2025&rr=3.0']);
        assert.deepEqual(selected(), ['3.0']);
    } else if (scenario === 'trades') {
        get('bt-tab-trades').onclick(); await settle();
        assert.equal(calls.at(-1), 'mo/backtest/trades?id=job&rr=2.0&period=all&offset=0&limit=100');
        const text = get('bt-dashboard-panel').textContent;
        assert.match(text, /2026-01-01 09:00/); assert.match(text, /순서 미확정/); assert.doesNotMatch(text, /UNCERTAIN/);
    }
    console.log('PASS ' + scenario);
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
'''


@pytest.mark.parametrize('scenario', ['initial', 'sort', 'period', 'period_sorted', 'trades', 'fallback'])
def test_result_screen(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout
