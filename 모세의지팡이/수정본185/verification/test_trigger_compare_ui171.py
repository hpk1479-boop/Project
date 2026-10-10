"""171: OZ triggers compared together on the shipped backtest screen (unified.js, backtest_dashboard.js).

The row under the target shows the chosen strategy's other triggers; checked ones go into the request after
the chosen trigger, in the list's order; it is hidden for WATCH, a data build and a strategy without OZ
triggers; the result names the triggers tested together, and base frames as before.
"""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
document.createElementNS = (namespace, tag) => new Element(tag);
const choices = ['올존', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존'];
const options = {symbol: 'XAUUSD+', symbols: ['XAUUSD+'], start: '2026-09-01', end: '2026-10-01', mode: 'BAR',
    specials: ['SPECIAL2', 'SPECIAL8'], strategy_names: {SPECIAL2: '외부유동성 스윕', SPECIAL8: '전략 8'},
    special_settings: {SPECIAL2: {enabled: true, trigger: null, time_filters: null},
                       SPECIAL8: {enabled: false, trigger: null, time_filters: null}},
    trigger_choices: choices, default_triggers: {SPECIAL2: '브레이커 올존', SPECIAL8: null},
    watch_text: '', watch_chat_id: 'BACKTEST', spread_points: {}, warehouse_set: true, result_mode: 'ALERT_ONLY'};
const context = vm.createContext({console, document, Option, Event: class {constructor(type) {this.type = type;}},
    window: {dispatchEvent() {}, addEventListener() {}, confirm: () => true}, sessionStorage: {getItem: () => null, setItem() {}},
    setInterval: () => 1, setTimeout: () => 1, clearTimeout() {},
    api: async name => {
        if (name === 'mo/backtest/options') return JSON.parse(JSON.stringify(options));
        if (name.startsWith('mo/live/status')) return {modules: {}, lines: []};
        throw Error('Unexpected API ' + name);
    }});
const run = code => vm.runInContext(code, context), get = id => document.querySelector('#' + id);
const json = code => JSON.parse(JSON.stringify(run(code) ?? null));
const box = () => get('mo-bt-trigger-compare');
const checks = () => box().querySelectorAll('input').map(input => [input.dataset.compareTrigger, input.checked]);
const tick = value => {
    const input = box().querySelectorAll('input').find(element => element.dataset.compareTrigger === value);
    input.checked = !input.checked; input.onchange();
};
const card = name => document.querySelector('[data-special="' + name + '"]');
(async () => {
    run(fs.readFileSync(root + '/unified.js', 'utf8'));
    await run('moView("backtest")');
    // SPECIAL2's own trigger is the chosen one: the other three can be tested with it.
    assert.equal(box().classList.contains('hidden'), false);
    assert.deepEqual(checks(), [['올존', false], ['무지성 올존', false], ['무지성 브레이커 올존', false]]);
    assert.equal(json('moBacktestRequest().trigger_variants'), null);
    if (scenario === 'request') {
        tick('무지성 브레이커 올존'); tick('올존');
        assert.deepEqual(json('moBacktestRequest().trigger_variants'), ['브레이커 올존', '올존', '무지성 브레이커 올존']);
        assert.match(box().textContent, /트리거마다 결과를 따로 저장합니다/);
        tick('올존');
        assert.deepEqual(json('moBacktestRequest().trigger_variants'), ['브레이커 올존', '무지성 브레이커 올존']);
        tick('무지성 브레이커 올존');
        assert.equal(json('moBacktestRequest().trigger_variants'), null);
    } else if (scenario === 'chosen') {
        tick('올존'); tick('무지성 올존');
        card('SPECIAL2').querySelector('.mo-special-trigger').value = '올존'; run('moBacktestMode()');
        // The newly chosen trigger leaves the compared ones; the request's own trigger is the card's.
        assert.deepEqual(checks(), [['무지성 올존', true], ['브레이커 올존', false], ['무지성 브레이커 올존', false]]);
        assert.deepEqual(json('moBacktestRequest().trigger_variants'), ['올존', '무지성 올존']);
        assert.equal(json('moBacktestRequest().special_settings.SPECIAL2.trigger'), '올존');
    } else if (scenario === 'hidden') {
        tick('올존');
        get('mo-bt-target').value = 'WATCH'; run('moBacktestMode()');
        assert(box().classList.contains('hidden'));
        assert.equal(json('moBacktestRequest().trigger_variants'), null);
        get('mo-bt-target').value = 'SPECIAL'; run('moBacktestMode()');
        assert.deepEqual(checks().filter(([, on]) => on), []);          // another target cleared the list
        tick('올존');
        get('mo-bt-build-only').checked = true; run('moBacktestMode()');
        assert(box().classList.contains('hidden'));
        assert.equal(json('moBacktestRequest().trigger_variants'), null);
        get('mo-bt-build-only').checked = false; run('moBacktestMode()');
        card('SPECIAL2').querySelector('.mo-special-enabled').checked = false;
        card('SPECIAL8').querySelector('.mo-special-enabled').checked = true; run('moBacktestMode()');
        assert(box().classList.contains('hidden'));                       // SPECIAL8 has no OZ trigger
        assert.equal(json('moBacktestRequest().trigger_variants'), null);
    } else if (scenario === 'result') {
        run(fs.readFileSync(root + '/backtest_dashboard.js', 'utf8'));
        const lines = () => get('mo-bt-result').querySelectorAll('.bt-basis').map(p => p.textContent);
        context.data = {analysis: null, trigger: '올존', base_frame: null, compared_runs: [
            {run_id: 'b'.repeat(32), base_frame: null, trigger: '무지성 올존'},
            {run_id: 'c'.repeat(32), base_frame: null, trigger: '브레이커 올존'}]};
        run(`window.MosesBacktestDashboard.render(document.getElementById('mo-bt-result'), data, 'job')`);
        assert(lines().includes('트리거 올존 · 함께 시험: 무지성 올존 · 브레이커 올존 (실행 목록에서 따로 확인)'), lines().join('\n'));
        // Base frames of an OZ strategy share its trigger: they are named by frame, as before.
        context.data = {analysis: null, trigger: '브레이커 올존', base_frame: '1m', compared_runs: [
            {run_id: 'b'.repeat(32), base_frame: '2m', trigger: '브레이커 올존'}]};
        run(`window.MosesBacktestDashboard.render(document.getElementById('mo-bt-result'), data, 'job')`);
        assert(lines().includes('기준 프레임 1분 · 함께 시험: 2분 (실행 목록에서 따로 확인)'), lines().join('\n'));
        // Strategies × triggers replayed together (수정본184): each run is named by its strategy and trigger.
        context.data = {analysis: null, strategy: 'SPECIAL2', trigger: '올존', base_frame: null, compared_runs: [
            {run_id: 'b'.repeat(32), base_frame: null, trigger: '무지성 올존', strategy: 'SPECIAL2'},
            {run_id: 'c'.repeat(32), base_frame: null, trigger: '올존', strategy: 'SPECIAL5'},
            {run_id: 'd'.repeat(32), base_frame: null, trigger: null, strategy: 'SPECIAL8'}]};
        run(`window.MosesBacktestDashboard.render(document.getElementById('mo-bt-result'), data, 'job')`);
        assert(lines().includes('실행 SPECIAL2 올존 · 함께 시험: SPECIAL2 무지성 올존 · SPECIAL5 올존 · SPECIAL8 ' +
            '(실행 목록에서 따로 확인)'), lines().join('\n'));
    }
    console.log('PASS ' + scenario);
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
'''


@pytest.mark.parametrize('scenario', ['request', 'chosen', 'hidden', 'result'])
def test_triggers_compared_on_the_backtest_screen(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout
