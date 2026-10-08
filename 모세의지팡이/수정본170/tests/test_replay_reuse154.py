"""154: a run whose replay inputs equal a finished run's uses that run's alerts instead of replaying.

Only the fields the virtual entry reads after the replay (entry policy, spread, result mode) may
differ. The replay workers are replaced by a recording fake so each test sees whether it replayed;
the results database is the real DuckDB one.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import csv
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

BOUNDS = ['2026-09-01', '2026-09-01T08:00:00+00:00', '2026-09-01T16:00:00+00:00', '2026-09-02']


@pytest.fixture
def backtest(monkeypatch, tmp_path):
    """runner.run with fake replay chunks (each writes one alert) and a fake virtual entry."""
    from event_backtest import runner, build_plan, joins, virtual_entry
    from event_backtest.warehouse import FIELDS
    from event_backtest.settings import milliseconds
    replayed, entered, events = [], [], []
    state = {'config': {'POINT_XAUUSD+': '0.01'}, 'code': 'code-154'}
    monkeypatch.setattr(runner, 'runtime_config', lambda s: dict(state['config']))
    monkeypatch.setattr(runner, 'code_hash', lambda: state['code'])
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    # Fixed blocks, each its own chain: nothing to join, so the fake chunks stand for whole replays.
    monkeypatch.setattr(joins, 'plan_periods', lambda *a: (list(zip(BOUNDS, BOUNDS[1:])), [[i] for i in range(len(BOUNDS) - 1)]))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)

    def chunk(task):
        out = Path(task['out'])
        out.mkdir(parents=True, exist_ok=True)
        replayed.append(out.name)
        stamp = milliseconds(task['start'])
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for recipient in ('A', 'B'):
                writer.writerow({'run_id': task['run_id'], 'time_ms': stamp, 'strategy': 'SPECIAL8', 'tf': '1m',
                                 'signal_id': 'S' + str(stamp), 'recipient': recipient, 'direction': 'LONG',
                                 'message': 'chunk ' + task['start'], 'signal_source': 'SIGNAL', 'signal_price': '2400'})
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1, 'approximate': False,
                'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {'2026-09': 1}}
    monkeypatch.setattr(runner, 'run_chunk', chunk)

    def calculate(export, captures, root, s, config, out, **kwargs):
        with Path(export).open(encoding='utf-8', newline='') as handle:
            entered.append([{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)])
        return {'cancelled': False, 'summary': [], 'policy': s['virtual_entry']}
    monkeypatch.setattr(virtual_entry, 'calculate', calculate)
    warehouse = tmp_path / 'warehouse'

    def run(s, **options):
        del replayed[:]
        result = runner.run(s, warehouse, captures=[], emit=lambda kind, data: events.append(kind), **options)
        return result, list(replayed)
    return run, warehouse, state, entered, events


def profile():
    from event_backtest.virtual_defaults import strategy_profile
    return strategy_profile('SPECIAL8')


def special8(policy=None, **changes):
    from event_backtest.settings import scenario
    values = dict(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL8'], overlap_trading_days=0, cores=2,
                  result_mode='VIRTUAL_ENTRY', virtual_entry=policy or deepcopy(profile()['default']))
    values.update(changes)
    return scenario(**values)


def entry_only(**stop):
    policy = deepcopy(profile()['default'])
    policy['stop'].update(stop)
    return policy


def alerts(warehouse, result):
    with (warehouse / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return [{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)]


def test_entry_only_changes_use_the_finished_replay(backtest):
    run, warehouse, _, entered, events = backtest
    first, replayed = run(special8())
    assert first['status'] == 'COMPLETE' and len(replayed) == 3 and 'replay_reused_from' not in first
    second, replayed = run(special8(entry_only(multiplier=2.5, period=20), spread_points={'XAUUSD+': 5.0}))
    assert replayed == [] and second['replay_reused_from'] == first['run_id']
    assert second['replay_key'] == first['replay_key'] and second['run_id'] != first['run_id']
    assert alerts(warehouse, second) == alerts(warehouse, first) and second['alert_rows'] == first['alert_rows'] == 6
    assert entered[1] == entered[0]                        # the virtual entry reads the same alerts
    assert second['virtual_entry']['policy']['stop']['multiplier'] == 2.5
    assert '알림 재생: 같은 조건으로 끝난 실행의 결과를 썼습니다.' in second['warnings']
    assert 'REPLAY_REUSED' in events and second['processed_periods'] == first['processed_periods']
    assert second['alert_statistics'] == first['alert_statistics']
    # An alert-only run of the same strategy needs the same replay; the newest finished one is used.
    third, replayed = run(special8(result_mode='ALERT_ONLY', virtual_entry=None))
    assert replayed == [] and third['replay_reused_from'] == second['run_id']
    assert alerts(warehouse, third) == alerts(warehouse, first)


def edited_strategy():
    from event_backtest.virtual_defaults import strategy_on_base
    intent = strategy_on_base(profile(), 'SIGNAL')
    intent['steps'][1]['slow_period'] = 60
    return intent


def test_another_worker_count_uses_a_continuous_replay(backtest):
    # 168: blocks joined where their states meet replay each period as one continuous replay, so the
    # worker count (the blocks) changes nothing an exact replay produced (joins_exact; joins.py).
    run, warehouse, _, _, _ = backtest
    first, _ = run(special8())
    second, replayed = run(special8(cores=3))
    assert replayed == [] and second['replay_reused_from'] == first['run_id']
    assert second['replay_key'] == first['replay_key']


@pytest.mark.parametrize('change', ['base frame', 'strategy edit', 'time filter', 'period', 'oz evaluation',
                                    'config', 'code'])
def test_anything_the_replay_reads_replays_again(backtest, change):
    from event_backtest.virtual_defaults import recipe_on_base
    run, _, state, _, _ = backtest
    first, _ = run(special8())
    s = {'base frame': lambda: special8(recipe_on_base(profile(), '1h')),
         'strategy edit': lambda: special8(virtual_strategy=edited_strategy()),
         'time filter': lambda: special8(special_time_filters={'SPECIAL8': [{'start': '09:00', 'end': '10:00'}]}),
         'period': lambda: special8(end='2026-09-03'),
         'oz evaluation': lambda: special8(oz_evaluation='all'),
         'config': lambda: special8(),
         'code': lambda: special8()}[change]()
    if change == 'config':
        state['config']['WONBI_SIGMA'] = '2.5'
    if change == 'code':
        state['code'] = 'code-155'
    second, replayed = run(s)
    assert replayed and 'replay_reused_from' not in second and second['replay_key'] != first['replay_key']


def test_only_a_listed_complete_run_with_all_its_alerts_is_used(backtest):
    run, warehouse, _, _, _ = backtest
    from event_backtest.warehouse import Warehouse
    stopped, _ = run(special8(), cancel=lambda: True)
    assert stopped['status'] == 'CANCELLED'
    first, replayed = run(special8())
    assert replayed, 'a stopped run is never a source'
    shutil.rmtree(warehouse / 'runs' / first['run_id'])  # deleted from the backtest list
    second, replayed = run(special8())
    assert replayed, 'a deleted run is never a source'
    catalog = Warehouse(warehouse, results=True)
    try:
        catalog.db.execute("DELETE FROM alerts WHERE run_id=? AND recipient='B'", [second['run_id']])
    finally:
        catalog.close()
    third, replayed = run(special8())
    assert replayed, 'a run missing stored alert rows is never a source'
    fourth, replayed = run(special8())
    assert replayed == [] and fourth['replay_reused_from'] == third['run_id']


def test_replay_key_names_every_scenario_field_but_the_entry_ones():
    from event_backtest.runner import replay_key, AFTER_REPLAY
    s = special8(spread_points={'XAUUSD+': 1.0})
    task = {'scenario': s, 'config': {'x': 1}, 'start': BOUNDS[0], 'end': BOUNDS[1], 'warm_start': BOUNDS[0],
            'captures': [{'capture_id': 'c1', 'path': 'captures/a', 'ea_build_hash': 'e'}], 'transport': 'replay',
            'run_id': 'a' * 32, 'out': 'runs/x/chunk_000', 'warehouse': 'w'}
    base = replay_key('code', 'config', [task])
    assert set(AFTER_REPLAY) == {'virtual_entry', 'spread_points', 'result_mode', '_run_id'}
    for field in s:
        changed = dict(s, **{field: ['changed', field]})
        same = replay_key('code', 'config', [dict(task, scenario=changed)]) == base
        assert same == (field in AFTER_REPLAY), field
    for field in ('start', 'end', 'warm_start', 'captures', 'transport'):
        assert replay_key('code', 'config', [dict(task, **{field: 'other'})]) != base, field
    for field in ('run_id', 'out', 'warehouse', 'config'):  # the configuration counts through its hash
        assert replay_key('code', 'config', [dict(task, **{field: 'other'})]) == base, field
    assert replay_key('code2', 'config', [task]) != base and replay_key('code', 'config2', [task]) != base
    assert replay_key('code', 'config', [task, task]) != base


def test_progress_shows_the_reused_replay_once():
    from event_backtest.progress_view import ProgressView
    view = ProgressView(clock=lambda: 0.)
    view.accept({'event': 'RUN_START'})
    view.accept({'event': 'REPLAY_REUSED', 'source': 'a' * 32})
    assert view.replay == 100. and view.lines[-1] == '같은 조건으로 끝난 실행의 알림 재생 결과를 씁니다'
