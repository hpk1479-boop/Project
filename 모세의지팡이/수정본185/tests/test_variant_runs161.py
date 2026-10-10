"""161: several base frames of one strategy compared in one replay.

Each base frame is a complete run of its own (its scenario equals a virtual entry on that frame alone,
with the same replay key); a chunk reads the recordings and publishes STAFF once for all of them while
each run keeps its own engine. Orchestration uses fake replay chunks; the lane replay itself runs the
real engine on a small recorded capture, together and alone.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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


def scenario(**changes):
    from event_backtest.settings import scenario as make
    values = dict(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL8'], overlap_trading_days=0, cores=2,
                  result_mode='VIRTUAL_ENTRY')
    values.update(changes)
    return make(**values)


def on_base(tf, name='SPECIAL8'):
    from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
    return recipe_on_base(strategy_profile(name), tf)


# ---- scenarios ------------------------------------------------------------------------------------

def test_each_base_frame_is_the_virtual_entry_on_that_frame_alone():
    from event_backtest.settings import variant_scenarios
    s = scenario(virtual_bases=['5m', '1m', '2m'], _run_id='a' * 32)
    assert s['virtual_bases'] == ['1m', '2m', '5m']
    runs = variant_scenarios(s)
    # The recipe's own frame (1m for SPECIAL8) is its policy as written: SIGNAL.
    assert [run['virtual_entry']['tf'] for run in runs] == ['SIGNAL', '2m', '5m']
    assert [run['_run_id'] for run in runs] == ['a' * 32, None, None]
    for run in runs:
        alone = scenario(virtual_entry=on_base(run['virtual_entry']['tf']))
        assert {k: v for k, v in run.items() if k != '_run_id'} == alone


def test_one_base_frame_or_none_is_an_ordinary_run():
    from event_backtest.settings import variant_scenarios
    s = scenario(virtual_entry=on_base('2m'))
    assert s['virtual_bases'] == [] and variant_scenarios(s) == [s]


@pytest.mark.parametrize('changes,message', [
    ({'virtual_bases': ['2m']}, '두 개 이상'),
    ({'virtual_bases': ['2m', '2m']}, '두 개 이상'),
    ({'virtual_bases': ['1m', 'SIGNAL']}, '중에서 고르세요'),
    ({'virtual_bases': ['1m', '7m']}, '중에서 고르세요'),
    ({'virtual_bases': '1m,2m'}, '형식'),
    ({'virtual_bases': ['1m', '2m'], 'strategies': ['SPECIAL2']}, '바꿀 수 없어'),
    ({'virtual_bases': ['1m', '2m'], 'result_mode': 'ALERT_ONLY'}, '가상 진입'),
])
def test_invalid_base_frame_lists_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        scenario(**changes)


# ---- orchestration (fake chunks) -----------------------------------------------------------------

@pytest.fixture
def backtest(monkeypatch, tmp_path):
    from event_backtest import runner, build_plan, joins, virtual_entry
    from event_backtest.warehouse import FIELDS
    from event_backtest.settings import milliseconds
    seen, state = [], {'fail': False}
    monkeypatch.setattr(runner, 'runtime_config', lambda s: {'POINT_XAUUSD+': '0.01'})
    monkeypatch.setattr(runner, 'code_hash', lambda: 'code-161')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    # Fixed blocks, each its own chain: nothing to join, so the fake chunks stand for whole replays.
    monkeypatch.setattr(joins, 'plan_periods', lambda *a: (list(zip(BOUNDS, BOUNDS[1:])), [[i] for i in range(len(BOUNDS) - 1)]))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)

    def lane_result(task, lane):
        out = Path(lane['out'])
        out.mkdir(parents=True, exist_ok=True)
        stamp = milliseconds(task['start'])
        frame = (lane['scenario']['virtual_entry'] or {}).get('tf', 'SIGNAL')
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow({'run_id': lane['run_id'], 'time_ms': stamp, 'strategy': 'SPECIAL8', 'tf': frame,
                             'signal_id': frame + str(stamp), 'recipient': 'A', 'direction': 'LONG',
                             'message': 'on ' + frame, 'signal_source': 'SIGNAL', 'signal_price': '2400'})
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1, 'approximate': False,
                'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {'2026-09': 1}}

    def chunk(task):
        if state['fail']:
            raise RuntimeError('replay failure')
        lanes = runner.lanes_of(task)
        seen.append([lane['scenario']['virtual_entry']['tf'] for lane in lanes])
        results = [lane_result(task, lane) for lane in lanes]
        return results if task.get('lanes') else results[0]
    monkeypatch.setattr(runner, 'run_chunk', chunk)
    monkeypatch.setattr(virtual_entry, 'calculate',
                        lambda export, captures, root, s, config, out, **k: {'cancelled': False, 'summary': [], 'policy': s['virtual_entry']})
    warehouse = tmp_path / 'warehouse'

    def run_many(scenarios):
        del seen[:]
        return runner.run_many(scenarios, warehouse, captures=[]), [list(x) for x in seen]
    return run_many, warehouse, state


def alerts(warehouse, result):
    with (warehouse / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return [{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)]


def test_base_frames_replay_together_and_each_keeps_its_own_run(backtest):
    from event_backtest.settings import variant_scenarios
    run_many, warehouse, _ = backtest
    results, seen = run_many(variant_scenarios(scenario(virtual_bases=['1m', '2m', '5m'], _run_id='b' * 32)))
    assert seen == [['SIGNAL', '2m', '5m']] * 2                   # one replay task per period, three lanes each
    assert [r['run_id'] for r in results][0] == 'b' * 32 and len({r['run_id'] for r in results}) == 3
    # The first run stands for the request; the others point to it until their results are written.
    assert not (warehouse / 'runs' / results[0]['run_id'] / 'lane.json').exists()
    for result in results[1:]:
        lane = json.loads((warehouse / 'runs' / result['run_id'] / 'lane.json').read_text('utf-8'))
        assert lane == {'lead_run_id': 'b' * 32}
    for result, tf in zip(results, ('SIGNAL', '2m', '5m')):
        assert result['status'] == 'COMPLETE' and result['virtual_entry']['policy']['tf'] == tf
        assert [row['tf'] for row in alerts(warehouse, result)] == [tf, tf]
        assert sorted(result['replayed_with']) == sorted(r['run_id'] for r in results if r is not result)
        stored = json.loads((warehouse / result['result_path']).read_text('utf-8'))
        assert stored['status'] == 'COMPLETE' and stored['run_id'] == result['run_id']


def test_a_base_frame_run_stands_in_for_the_run_on_that_frame_alone(backtest):
    from event_backtest.settings import variant_scenarios
    run_many, warehouse, _ = backtest
    together, _ = run_many(variant_scenarios(scenario(virtual_bases=['1m', '2m'])))
    (alone,), seen = run_many([scenario(virtual_entry=on_base('2m'))])
    assert seen == [] and alone['replay_reused_from'] == together[1]['run_id']
    assert alone['replay_key'] == together[1]['replay_key'] and alerts(warehouse, alone) == alerts(warehouse, together[1])


def test_only_the_runs_without_a_finished_replay_are_replayed(backtest):
    from event_backtest.settings import variant_scenarios
    run_many, _, _ = backtest
    (done,), _ = run_many([scenario(virtual_entry=on_base('2m'))])
    results, seen = run_many(variant_scenarios(scenario(virtual_bases=['1m', '2m', '5m'])))
    assert seen == [['SIGNAL', '5m']] * 2
    assert results[1]['replay_reused_from'] == done['run_id'] and all(r['status'] == 'COMPLETE' for r in results)


def test_a_failed_replay_fails_every_run(backtest):
    from event_backtest.settings import variant_scenarios
    from event_backtest.warehouse import Warehouse
    run_many, warehouse, state = backtest
    state['fail'] = True
    scenarios = variant_scenarios(scenario(virtual_bases=['1m', '2m'], _run_id='c' * 32))
    with pytest.raises(RuntimeError, match='replay failure'):
        run_many(scenarios)
    catalog = Warehouse(warehouse, results=True)
    try:
        statuses = [row[0] for row in catalog.db.execute('SELECT status FROM runs').fetchall()]
    finally:
        catalog.close()
    assert statuses == ['FAILED', 'FAILED']


def test_runs_that_disagree_on_the_replay_are_refused(backtest):
    run_many, _, _ = backtest
    with pytest.raises(ValueError, match='같아야'):
        run_many([scenario(virtual_entry=on_base('1m')), scenario(virtual_entry=on_base('2m'), end='2026-09-03')])


# ---- lanes on the real engine (a small recorded capture) -----------------------------------------

SCRIPT = r'''
import json, sys
from copy import deepcopy
from pathlib import Path
from event_backtest import runner
from event_backtest.settings import scenario
from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
root, capture, mode, how = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
common = dict(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', mode=mode, overlap_trading_days=0)
scenarios = {'s7': scenario(strategies=['SPECIAL7'], **common), 's8': scenario(strategies=['SPECIAL8'], **common),
             's9': scenario(strategies=['SPECIAL9'], result_mode='VIRTUAL_ENTRY',
                            virtual_entry=recipe_on_base(strategy_profile('SPECIAL9'), '2m'), **common)}
config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'OFFLINE'}
base = {'config': config, 'start': common['start'], 'end': common['end'], 'warm_start': common['start'],
        'captures': [{'path': capture.relative_to(root).as_posix()}], 'warehouse': str(root)}
def out(name): return str(root / how / name / 'chunk_000')
for name in scenarios: Path(out(name)).parent.mkdir(parents=True)   # the run folders, as run_many makes them
if how == 'alone':
    results = {name: runner.run_chunk({**base, 'scenario': s, 'run_id': name, 'out': out(name)}) for name, s in scenarios.items()}
else:
    lanes = [{'scenario': s, 'run_id': name, 'out': out(name)} for name, s in scenarios.items()]
    task = {**base, 'scenario': lanes[0]['scenario'], 'run_id': lanes[0]['run_id'], 'out': lanes[0]['out'], 'lanes': lanes}
    results = dict(zip(scenarios, runner.run_chunk(task)))
report = {}
for name, result in results.items():
    rows = (root / how / name / 'chunk_000' / 'alerts.csv').read_text('utf-8').splitlines()
    report[name] = {key: result[key] for key in ('bundles', 'alerts', 'notifications', 'warmup_bundles', 'processed_start_ms',
                                                 'processed_end_ms', 'strategies', 'alert_months', 'cancelled')}
    report[name]['rows'] = rows
print(json.dumps(report))
'''


@pytest.mark.parametrize('mode', ['TICK', 'BAR'])
def test_lanes_replay_exactly_like_each_run_alone(tmp_path, mode):
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
