"""184: several strategies (each with its OZ trigger) replayed together as lanes.

A scenario's lanes are [{"strategy", "trigger"}]: each lane is a complete run of its own whose scenario equals
the run of that strategy (with that trigger, and its own virtual entry) alone, so it has the same replay key
and can stand in for that run. More runs than max_lanes are replayed in groups one after another; every run
of every group refers to the request's first run, and that run's lanes.json lists them all. Orchestration
uses fake replay chunks; the lane replay itself runs the real engine on a small recorded capture.
"""
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

BOUNDS = ['2026-09-01', '2026-09-01T12:00:00+00:00', '2026-09-02']
LANES = [{'strategy': 'SPECIAL2', 'trigger': '무지성 올존'}, {'strategy': 'SPECIAL5'},
         {'strategy': 'SPECIAL2', 'trigger': '올존'}, {'strategy': 'SPECIAL8'}]


def scenario(**changes):
    from event_backtest.settings import scenario as make
    values = dict(start='2026-09-01', end='2026-09-02', overlap_trading_days=0, cores=2)
    values.update(changes)
    return make(**values)


def own(name):
    from strategy_recipe.registry import default_settings
    return default_settings(name)[0]


def alone(lane, **changes):
    """The run of that lane's strategy alone, as the screen sends it: the strategy's own trigger unset."""
    trigger = lane.get('trigger')
    triggers = {} if trigger is None or trigger == own(lane['strategy']) else {lane['strategy']: trigger}
    return scenario(strategies=[lane['strategy']], triggers=triggers, **changes)


# ---- scenarios ------------------------------------------------------------------------------------

@pytest.mark.parametrize('result_mode', ['ALERT_ONLY', 'VIRTUAL_ENTRY'])
def test_each_lane_run_is_the_run_of_that_strategy_alone(result_mode):
    from event_backtest.settings import variant_scenarios
    s = scenario(result_mode=result_mode, lanes=LANES, _run_id='a' * 32)
    # The first lane is the request's own run.
    assert s['strategies'] == ['SPECIAL2'] and s['triggers'] == {'SPECIAL2': '무지성 올존'}
    runs = variant_scenarios(s)
    assert [run['_run_id'] for run in runs] == ['a' * 32, None, None, None]
    for run, lane in zip(runs, LANES):
        assert {k: v for k, v in run.items() if k != '_run_id'} == alone(lane, result_mode=result_mode)


def test_a_lane_virtual_entry_is_that_strategy_s_own_policy():
    from event_backtest.settings import variant_scenarios
    from event_backtest.virtual_defaults import strategy_profile
    policy = strategy_profile('SPECIAL8')['default']
    policy['stop'] = {**policy['stop'], 'multiplier': 2.0}
    s = scenario(result_mode='VIRTUAL_ENTRY', lanes=LANES, virtual_entries={'SPECIAL8': policy})
    runs = variant_scenarios(s)
    assert runs[3]['virtual_entry']['stop']['multiplier'] == 2.0
    assert runs[3] == {**alone(LANES[3], result_mode='VIRTUAL_ENTRY', virtual_entry=policy), '_run_id': None}
    # A strategy left out of virtual_entries runs its recipe's values.
    assert runs[1]['virtual_entry'] == alone(LANES[1], result_mode='VIRTUAL_ENTRY')['virtual_entry']
    assert all(run['lanes'] == [] and run['virtual_entries'] == {} for run in runs)


def test_a_built_lanes_scenario_builds_again_unchanged():
    from event_backtest.settings import scenario as make
    s = scenario(result_mode='VIRTUAL_ENTRY', lanes=LANES)
    assert make(**s) == s
    assert s['lanes'][1] == {'strategy': 'SPECIAL5', 'trigger': None}


def test_triggers_are_named_as_the_screen_names_them():
    s = scenario(lanes=[{'strategy': 'SPECIAL2', 'trigger': 'OZ'}, {'strategy': 'SPECIAL2', 'trigger': '무지성 올존'}])
    assert [lane['trigger'] for lane in s['lanes']] == ['올존', '무지성 올존']


@pytest.mark.parametrize('changes,message', [
    ({'lanes': 'SPECIAL2,SPECIAL5'}, '목록'),
    ({'lanes': [{'name': 'SPECIAL2'}]}, '형식'),
    ({'lanes': [{'strategy': 'SPECIAL2', 'trigger': '올존', 'extra': 1}]}, '형식'),
    ({'lanes': [{'strategy': 'NO_SUCH'}]}, '찾지 못했습니다'),
    ({'lanes': [{'strategy': 'ALL'}]}, 'ALL'),
    ({'lanes': [{'strategy': 'SPECIAL8', 'trigger': '올존'}]}, 'OZ 트리거가 없어'),
    ({'lanes': [{'strategy': 'SPECIAL2', 'trigger': '없는 트리거'}]}, '트리거'),
    ({'lanes': [{'strategy': 'SPECIAL2', 'trigger': '브레이커 올존'}, {'strategy': 'SPECIAL2'}]}, '같습니다'),
    ({'lanes': LANES, 'strategies': ['SPECIAL5']}, 'strategies는 비워'),
    ({'lanes': LANES, 'trigger_variants': ['올존', '무지성 올존']}, 'trigger_variants'),
    ({'lanes': LANES, 'build_only': True}, '데이터 구축'),
    ({'lanes': LANES, 'virtual_entries': {'SPECIAL9': {}}, 'result_mode': 'VIRTUAL_ENTRY'}, 'lanes에 없는'),
    ({'lanes': LANES, 'virtual_entries': {'SPECIAL8': {}}}, 'VIRTUAL_ENTRY'),
    ({'virtual_entries': {'SPECIAL8': {}}}, 'lanes와 함께만'),
])
def test_invalid_lanes_are_rejected_with_what_to_do(changes, message):
    with pytest.raises(ValueError, match=message):
        scenario(**changes)


def test_a_lane_keeps_only_its_own_trading_time():
    # The screen sends each strategy's saved trading time; a lane is its strategy's run alone with its own.
    from event_backtest.settings import variant_scenarios
    london = {'MAIN_LONDON': {'enabled': True}, 'MAIN_ASIA': {'enabled': False}}
    runs = variant_scenarios(scenario(lanes=LANES, special_time_filters={'SPECIAL2': london, 'SPECIAL5': {}}))
    assert [run['special_time_filters'] for run in runs] == [{'SPECIAL2': london}, {'SPECIAL5': {}}, {'SPECIAL2': london}, {}]
    for run, lane in zip(runs, LANES):
        own_time = {key: item for key, item in {'SPECIAL2': london, 'SPECIAL5': {}}.items() if key == lane['strategy']}
        assert {k: v for k, v in run.items() if k != '_run_id'} == alone(lane, special_time_filters=own_time)


def test_one_policy_for_every_lane_is_refused():
    from event_backtest.virtual_defaults import strategy_profile
    other = strategy_profile('SPECIAL2')['default']
    other['stop'] = {**other['stop'], 'kind': 'ATR'}
    with pytest.raises(ValueError, match='virtual_entries에 전략별로'):
        scenario(result_mode='VIRTUAL_ENTRY', lanes=LANES, virtual_entry=other)


def test_no_lanes_is_an_ordinary_run():
    from event_backtest.settings import variant_scenarios
    s = scenario(strategies=['SPECIAL2'])
    assert s['lanes'] == [] and s['virtual_entries'] == {} and variant_scenarios(s) == [s]


# ---- orchestration (fake chunks) -----------------------------------------------------------------

@pytest.fixture
def backtest(monkeypatch, tmp_path):
    from event_backtest import runner, build_plan, joins, virtual_entry
    from event_backtest.warehouse import FIELDS
    from event_backtest.settings import milliseconds
    seen = []
    monkeypatch.setattr(runner, 'runtime_config', lambda s: {'POINT_XAUUSD+': '0.01'})
    monkeypatch.setattr(runner, 'code_hash', lambda: 'code-184')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(joins, 'plan_periods', lambda *a: (list(zip(BOUNDS, BOUNDS[1:])), [[i] for i in range(len(BOUNDS) - 1)]))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)

    def label(s):
        name = s['strategies'][0]
        return name + ':' + (s['triggers'].get(name) or own(name) or '-')

    def lane_result(task, lane):
        out = Path(lane['out'])
        out.mkdir(parents=True, exist_ok=True)
        stamp = milliseconds(task['start'])
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow({'run_id': lane['run_id'], 'time_ms': stamp, 'strategy': lane['scenario']['strategies'][0],
                             'tf': '1m', 'signal_id': label(lane['scenario']) + str(stamp), 'recipient': 'A',
                             'direction': 'LONG', 'message': 'on ' + label(lane['scenario']), 'signal_source': 'OZ',
                             'signal_price': '2400'})
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1, 'approximate': False,
                'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {'2026-09': 1}}

    def chunk(task):
        lanes = runner.lanes_of(task)
        seen.append([label(lane['scenario']) for lane in lanes])
        results = [lane_result(task, lane) for lane in lanes]
        return results if task.get('lanes') else results[0]
    monkeypatch.setattr(runner, 'run_chunk', chunk)
    monkeypatch.setattr(virtual_entry, 'calculate',
                        lambda export, captures, root, s, config, out, **k: {'cancelled': False, 'summary': [], 'policy': s['virtual_entry']})
    warehouse = tmp_path / 'warehouse'
    events = []

    def run(s, max_lanes=None, cancel=lambda: None):
        from event_backtest.settings import variant_scenarios
        from event_backtest.workflow import run_groups
        del seen[:], events[:]
        results = run_groups(s, variant_scenarios(s), warehouse, captures=[], max_lanes=max_lanes, cancel=cancel,
                             emit=lambda kind, data: events.append((kind, data)))
        return results, [list(x) for x in seen], list(events)
    return run, warehouse, label


def alerts(warehouse, result):
    with (warehouse / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return [{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)]


EXPECTED = ['SPECIAL2:무지성 올존', 'SPECIAL5:' + '{own}', 'SPECIAL2:올존', 'SPECIAL8:-']


def labels():
    return [text.replace('{own}', own('SPECIAL5')) for text in EXPECTED]


@pytest.mark.parametrize('result_mode', ['ALERT_ONLY', 'VIRTUAL_ENTRY'])
def test_lanes_replay_together_and_each_keeps_its_own_run(backtest, result_mode):
    run, warehouse, _ = backtest
    results, seen, events = run(scenario(result_mode=result_mode, lanes=LANES, _run_id='b' * 32))
    assert seen == [labels()] * 2                                   # one replay task per period, four lanes each
    assert results[0]['run_id'] == 'b' * 32 and len({r['run_id'] for r in results}) == 4
    assert not [kind for kind, _ in events if kind == 'LANE_GROUP']   # one group: no group progress
    for result, text in zip(results, labels()):
        assert result['status'] == 'COMPLETE' and result['result_mode'] == result_mode
        assert [row['message'] for row in alerts(warehouse, result)] == ['on ' + text] * 2
        assert sorted(result['tested_with']) == sorted(r['run_id'] for r in results if r is not result)
        stored = json.loads((warehouse / result['result_path']).read_text('utf-8'))
        assert stored['scenario']['lanes'] == [] and stored['strategies'] == [text.split(':')[0]]
    record = json.loads((warehouse / 'runs' / ('b' * 32) / 'lanes.json').read_text('utf-8'))
    assert record['lead_run_id'] == 'b' * 32 and record['groups'] == 1
    assert [(row['run_id'], row['strategy'], row['trigger'], row['group'], row['status']) for row in record['runs']] == [
        (r['run_id'], strategy, trigger, 1, 'COMPLETE') for r, (strategy, trigger) in
        zip(results, [('SPECIAL2', '무지성 올존'), ('SPECIAL5', None), ('SPECIAL2', '올존'), ('SPECIAL8', None)])]


def test_more_runs_than_max_lanes_replay_in_groups_one_after_another(backtest):
    run, warehouse, _ = backtest
    results, seen, events = run(scenario(lanes=LANES, _run_id='c' * 32), max_lanes=3)
    # Three lanes, then one: each group replays its periods with its own lanes.
    assert seen == [labels()[:3], labels()[:3], labels()[3:], labels()[3:]]
    assert [data for kind, data in events if kind == 'LANE_GROUP'] == [
        {'group': 1, 'groups': 2, 'runs': 3, 'total_runs': 4}, {'group': 2, 'groups': 2, 'runs': 1, 'total_runs': 4}]
    ids = [r['run_id'] for r in results]
    assert ids[0] == 'c' * 32 and len(set(ids)) == 4
    for result in results:
        # Every run of every group names the request and all its other runs.
        assert sorted(result['tested_with']) == sorted(i for i in ids if i != result['run_id'])
        lane = warehouse / 'runs' / result['run_id'] / 'lane.json'
        assert (json.loads(lane.read_text('utf-8')) == {'lead_run_id': 'c' * 32}) if result['run_id'] != 'c' * 32 else not lane.exists()
    record = json.loads((warehouse / 'runs' / ('c' * 32) / 'lanes.json').read_text('utf-8'))
    assert record['groups'] == 2 and record['max_lanes'] == 3
    assert [row['group'] for row in record['runs']] == [1, 1, 1, 2] and {row['status'] for row in record['runs']} == {'COMPLETE'}


def test_a_stop_after_a_group_starts_no_other(backtest, monkeypatch):
    from event_backtest import runner
    from event_backtest.cancellation import Cancelled
    run, warehouse, _ = backtest
    finished = []
    original = runner.run_many

    def run_many(*args, **kwargs):
        done = original(*args, **kwargs)
        finished.append(len(done))
        return done
    monkeypatch.setattr(runner, 'run_many', run_many)

    def cancel():
        # The stop is asked for once the first group has ended.
        if finished:
            raise Cancelled('중단됨(부분 결과)')
    results, seen, _ = run(scenario(lanes=LANES, _run_id='d' * 32), max_lanes=2, cancel=cancel)
    assert finished == [2] and len(results) == 2 and {r['status'] for r in results} == {'COMPLETE'}
    assert seen == [labels()[:2]] * 2
    record = json.loads((warehouse / 'runs' / ('d' * 32) / 'lanes.json').read_text('utf-8'))
    assert [row['status'] for row in record['runs']] == ['COMPLETE', 'COMPLETE', 'WAITING', 'WAITING']


def test_a_lane_stands_in_for_the_run_of_that_strategy_alone(backtest):
    from event_backtest import runner
    run, warehouse, _ = backtest
    together, _, _ = run(scenario(lanes=LANES))
    single = runner.run_many([alone(LANES[1])], warehouse, captures=[])[0]
    assert single['replay_reused_from'] == together[1]['run_id'] and single['replay_key'] == together[1]['replay_key']
    assert alerts(warehouse, single) == alerts(warehouse, together[1])


def test_the_progress_names_the_group_it_is_on():
    from event_backtest.progress_view import ProgressView
    view = ProgressView(clock=lambda: 0.0)
    view.accept({'event': 'RUN_START'})
    assert view.phase == '백테스트 중'                    # one replay: unchanged
    view.accept({'event': 'LANE_GROUP', 'group': 2, 'groups': 4, 'runs': 36, 'total_runs': 120})
    view.accept({'event': 'RUN_PROGRESS', 'percent': 50.0, 'elapsed_seconds': 10})
    assert view.phase == '백테스트 중 (묶음 2/4)' and '묶음 2/4 시작: 실행 36개 / 전체 120개' in view.lines
    view.accept({'event': 'VIRTUAL_ENTRY_START', 'alerts': 9})
    assert view.phase == '가상 진입 계산 중 (묶음 2/4)'


# ---- lanes on the real engine (a small recorded capture) -----------------------------------------

SCRIPT = r'''
import json, sys
from pathlib import Path
from event_backtest import runner
from event_backtest.settings import scenario, variant_scenarios
root, capture, mode, how = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
common = dict(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', mode=mode, overlap_trading_days=0)
lanes = [{'strategy': 'SPECIAL7', 'trigger': '올존'}, {'strategy': 'SPECIAL1'}, {'strategy': 'SPECIAL7', 'trigger': '무지성 브레이커 올존'}]
runs = dict(zip(['l%d' % i for i in range(len(lanes))], variant_scenarios(scenario(lanes=lanes, **common))))
config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'OFFLINE'}
base = {'config': config, 'start': common['start'], 'end': common['end'], 'warm_start': common['start'],
        'captures': [{'path': capture.relative_to(root).as_posix()}], 'warehouse': str(root)}
def out(name): return str(root / how / name / 'chunk_000')
for name in runs: Path(out(name)).parent.mkdir(parents=True)
if how == 'alone':
    results = {name: runner.run_chunk({**base, 'scenario': s, 'run_id': name, 'out': out(name)}) for name, s in runs.items()}
else:
    lanes = [{'scenario': s, 'run_id': name, 'out': out(name)} for name, s in runs.items()]
    task = {**base, 'scenario': lanes[0]['scenario'], 'run_id': lanes[0]['run_id'], 'out': lanes[0]['out'], 'lanes': lanes}
    results = dict(zip(runs, runner.run_chunk(task)))
report = {}
for name, result in results.items():
    rows = (root / how / name / 'chunk_000' / 'alerts.csv').read_text('utf-8').splitlines()
    report[name] = {key: result[key] for key in ('bundles', 'alerts', 'notifications', 'warmup_bundles', 'processed_start_ms',
                                                 'processed_end_ms', 'strategies', 'alert_months', 'cancelled')}
    report[name]['rows'] = rows
print(json.dumps(report))
'''


@pytest.mark.parametrize('mode', ['TICK', 'BAR'])
def test_strategy_lanes_replay_exactly_like_each_run_alone(tmp_path, mode):
    from test_parallel_oz import keyframe_fixture
    _, _, capture = keyframe_fixture(tmp_path)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(ROOT / 'Part1/program'), str(ROOT / 'Part2')))}
    out = {}
    for how in ('alone', 'together'):
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', SCRIPT, str(tmp_path), str(capture), mode, how],
                              env=env, capture_output=True, text=True, encoding='utf-8')
        assert done.returncode == 0, done.stderr
        out[how] = json.loads(done.stdout.strip().splitlines()[-1])
    assert out['together'] == out['alone']
    assert all(report['bundles'] > 0 for report in out['alone'].values())
    assert {tuple(report['strategies']) for report in out['alone'].values()} == {('SPECIAL7',), ('SPECIAL1',)}
