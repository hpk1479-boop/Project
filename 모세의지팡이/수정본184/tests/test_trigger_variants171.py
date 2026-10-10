"""171: several OZ triggers of one strategy compared in one replay.

Each trigger is a complete run of its own: its scenario equals the run with that trigger alone (the
strategy's own trigger left unset, as the screen sends it), so it has the same replay key and can stand
in for that run. The lanes are 161's: a chunk reads the recordings and publishes STAFF once for all the
runs while each keeps its own engine. Orchestration uses fake replay chunks; the lane replay itself runs
the real engine on a small recorded capture, together and alone.
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
TRIGGERS = ['올존', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존']


def scenario(**changes):
    from event_backtest.settings import scenario as make
    values = dict(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL2'], overlap_trading_days=0, cores=2)
    values.update(changes)
    return make(**values)


def own(name='SPECIAL2'):
    from strategy_recipe.registry import default_settings
    return default_settings(name)[0]


def alone(trigger, **changes):
    """The run of that trigger alone, as the screen sends it: the strategy's own trigger unset."""
    return scenario(triggers={} if trigger == own() else {'SPECIAL2': trigger}, **changes)


# ---- scenarios ------------------------------------------------------------------------------------

@pytest.mark.parametrize('result_mode', ['ALERT_ONLY', 'VIRTUAL_ENTRY'])
def test_each_trigger_run_is_the_run_with_that_trigger_alone(result_mode):
    from event_backtest.settings import variant_scenarios
    order = ['무지성 올존', '올존', '브레이커 올존', '무지성 브레이커 올존']
    s = scenario(result_mode=result_mode, triggers={'SPECIAL2': '올존'}, trigger_variants=order, _run_id='a' * 32)
    # The first trigger is the request's own, whatever its triggers said.
    assert s['trigger_variants'] == order and s['triggers'] == {'SPECIAL2': '무지성 올존'}
    runs = variant_scenarios(s)
    assert [run['_run_id'] for run in runs] == ['a' * 32, None, None, None]
    for run, trigger in zip(runs, order):
        assert {k: v for k, v in run.items() if k != '_run_id'} == alone(trigger, result_mode=result_mode)
    # SPECIAL2's own trigger (브레이커 올존) is left unset, not written out.
    assert own() == '브레이커 올존' and runs[2]['triggers'] == {}


def test_the_request_names_triggers_as_the_screen_does():
    s = scenario(trigger_variants=['OZ', '무지성 브레이커 올존'])
    assert s['trigger_variants'] == ['올존', '무지성 브레이커 올존'] and s['triggers'] == {'SPECIAL2': '올존'}


def test_no_trigger_list_is_an_ordinary_run():
    from event_backtest.settings import variant_scenarios
    s = scenario(triggers={'SPECIAL2': '올존'})
    assert s['trigger_variants'] == [] and variant_scenarios(s) == [s]


@pytest.mark.parametrize('changes,message', [
    ({'trigger_variants': ['올존']}, '두 개 이상'),
    ({'trigger_variants': ['올존', '올존']}, '두 개 이상'),
    ({'trigger_variants': ['올존', 'OZ']}, '두 개 이상'),                      # the same trigger written another way
    ({'trigger_variants': ['올존', '없는 트리거']}, '비교할 트리거'),
    ({'trigger_variants': '올존,무지성 올존'}, '형식'),
    ({'trigger_variants': ['올존', '무지성 올존'], 'strategies': ['SPECIAL8']}, 'OZ 트리거가 없어'),
    ({'trigger_variants': ['올존', '무지성 올존'], 'strategies': ['SPECIAL1', 'SPECIAL2']}, '전략 1개'),
    ({'trigger_variants': ['올존', '무지성 올존'], 'strategies': ['WATCH'],
      'commands': [{'strategy': 'WATCH', 'text': '1분 올존 알려줘', 'chat_id': 'BACKTEST'}]}, '전략 1개'),
    ({'trigger_variants': ['올존', '무지성 올존'], 'strategies': [], 'build_only': True}, '데이터 구축'),
    ({'trigger_variants': ['올존', '무지성 올존'], 'strategies': ['SPECIAL7'], 'result_mode': 'VIRTUAL_ENTRY',
      'virtual_bases': ['1m', '2m']}, '함께 할 수 없습니다'),
])
def test_invalid_trigger_lists_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        scenario(**changes)


# ---- orchestration (fake chunks) -----------------------------------------------------------------

@pytest.fixture
def backtest(monkeypatch, tmp_path):
    from event_backtest import runner, build_plan, joins, virtual_entry
    from event_backtest.warehouse import FIELDS
    from event_backtest.settings import milliseconds
    seen = []
    monkeypatch.setattr(runner, 'runtime_config', lambda s: {'POINT_XAUUSD+': '0.01'})
    monkeypatch.setattr(runner, 'code_hash', lambda: 'code-171')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    # Fixed blocks, each its own chain: nothing to join, so the fake chunks stand for whole replays.
    monkeypatch.setattr(joins, 'plan_periods', lambda *a: (list(zip(BOUNDS, BOUNDS[1:])), [[i] for i in range(len(BOUNDS) - 1)]))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)

    def lane_result(task, lane):
        out = Path(lane['out'])
        out.mkdir(parents=True, exist_ok=True)
        stamp = milliseconds(task['start'])
        trigger = lane['scenario']['triggers'].get('SPECIAL2') or own()
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow({'run_id': lane['run_id'], 'time_ms': stamp, 'strategy': 'SPECIAL2', 'tf': '1m',
                             'signal_id': trigger + str(stamp), 'recipient': 'A', 'direction': 'LONG',
                             'message': 'on ' + trigger, 'signal_source': 'OZ', 'signal_price': '2400'})
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1, 'approximate': False,
                'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {'2026-09': 1}}

    def chunk(task):
        lanes = runner.lanes_of(task)
        seen.append([lane['scenario']['triggers'].get('SPECIAL2') or own() for lane in lanes])
        results = [lane_result(task, lane) for lane in lanes]
        return results if task.get('lanes') else results[0]
    monkeypatch.setattr(runner, 'run_chunk', chunk)
    monkeypatch.setattr(virtual_entry, 'calculate',
                        lambda export, captures, root, s, config, out, **k: {'cancelled': False, 'summary': [], 'policy': s['virtual_entry']})
    warehouse = tmp_path / 'warehouse'

    def run_many(scenarios):
        del seen[:]
        return runner.run_many(scenarios, warehouse, captures=[]), [list(x) for x in seen]
    return run_many, warehouse


def alerts(warehouse, result):
    with (warehouse / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return [{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)]


@pytest.mark.parametrize('result_mode', ['ALERT_ONLY', 'VIRTUAL_ENTRY'])
def test_triggers_replay_together_and_each_keeps_its_own_run(backtest, result_mode):
    from event_backtest.settings import variant_scenarios
    run_many, warehouse = backtest
    results, seen = run_many(variant_scenarios(scenario(result_mode=result_mode, trigger_variants=TRIGGERS, _run_id='b' * 32)))
    assert seen == [TRIGGERS] * 2                                  # one replay task per period, four lanes each
    assert results[0]['run_id'] == 'b' * 32 and len({r['run_id'] for r in results}) == 4
    assert not (warehouse / 'runs' / results[0]['run_id'] / 'lane.json').exists()
    for result in results[1:]:
        assert json.loads((warehouse / 'runs' / result['run_id'] / 'lane.json').read_text('utf-8')) == {'lead_run_id': 'b' * 32}
    for result, trigger in zip(results, TRIGGERS):
        assert result['status'] == 'COMPLETE' and result['result_mode'] == result_mode
        assert [row['message'] for row in alerts(warehouse, result)] == ['on ' + trigger] * 2
        assert result['applied_special_settings']['SPECIAL2']['trigger'] == trigger
        assert sorted(result['tested_with']) == sorted(r['run_id'] for r in results if r is not result)
        stored = json.loads((warehouse / result['result_path']).read_text('utf-8'))
        assert stored['status'] == 'COMPLETE' and stored['run_id'] == result['run_id']
        assert stored['scenario']['trigger_variants'] == [] and stored['applied_special_settings']['SPECIAL2']['trigger'] == trigger


@pytest.mark.parametrize('trigger', ['무지성 올존', '브레이커 올존'])        # another trigger, and SPECIAL2's own
def test_a_trigger_run_stands_in_for_the_run_with_that_trigger_alone(backtest, trigger):
    from event_backtest.settings import variant_scenarios
    run_many, warehouse = backtest
    together, _ = run_many(variant_scenarios(scenario(trigger_variants=TRIGGERS)))
    (single,), seen = run_many([alone(trigger)])
    index = TRIGGERS.index(trigger)
    assert seen == [] and single['replay_reused_from'] == together[index]['run_id']
    assert single['replay_key'] == together[index]['replay_key'] and alerts(warehouse, single) == alerts(warehouse, together[index])


# ---- lanes on the real engine (a small recorded capture) -----------------------------------------

SCRIPT = r'''
import json, sys
from pathlib import Path
from event_backtest import runner
from event_backtest.settings import scenario, variant_scenarios
root, capture, mode, how = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
common = dict(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', mode=mode, overlap_trading_days=0)
triggers = ['올존', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존']
runs = dict(zip(['t%d' % i for i in range(4)], variant_scenarios(scenario(strategies=['SPECIAL7'], trigger_variants=triggers, **common))))
config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'OFFLINE'}
base = {'config': config, 'start': common['start'], 'end': common['end'], 'warm_start': common['start'],
        'captures': [{'path': capture.relative_to(root).as_posix()}], 'warehouse': str(root)}
def out(name): return str(root / how / name / 'chunk_000')
for name in runs: Path(out(name)).parent.mkdir(parents=True)   # the run folders, as run_many makes them
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
def test_trigger_lanes_replay_exactly_like_each_run_alone(tmp_path, mode):
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
