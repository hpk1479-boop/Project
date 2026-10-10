"""176: installed SPECIAL definitions are inputs to finished-replay reuse.

Only replay workers are fake. Loading recipes, recording tested settings, hashing inputs,
looking up finished runs and storing/copying their alerts use the real implementation.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import runner
from event_backtest.settings import milliseconds, scenario
from event_backtest.warehouse import FIELDS
from strategy_recipe import registry


def write_recipe(path, entry):
    text = ('PART3_RECIPE = ' + repr(entry['recipe']) + '\nraise RuntimeError("must not execute")\n'
            if path.suffix == '.py' else json.dumps(entry, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


@pytest.fixture
def installed(tmp_path, monkeypatch):
    from event_backtest import build_plan
    # runtime_config adds this fixture's temporary PROGRAM to sys.path; keep it test-local.
    monkeypatch.syspath_prepend(str(ROOT / 'Part1/program'))
    root = tmp_path / 'MOSES'
    for folder in ('Part1/program', 'Part2/event_backtest', 'Part2/generic_backtest',
                   'settings', '스페셜/기본', '스페셜/내 전략'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / 'Part1/program/config.txt').write_text('SYMBOLS=XAUUSD+\n', encoding='utf-8')
    (root / 'Part2/generic_backtest/native_mt5.py').write_text('# fixture\n', encoding='utf-8')
    (root / 'settings/strategy_registry.json').write_text(
        json.dumps({'schema_version': 2, 'presets': []}), encoding='utf-8')
    monkeypatch.setattr(runner, 'PROGRAM', root / 'Part1/program')
    monkeypatch.setattr(registry, 'REGISTRY', root / 'settings/strategy_registry.json')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)
    replayed = []

    def chunk(task):
        name = task['scenario']['strategies'][0]
        period = registry.preset_entry(name)['recipe']['strategy_intent']['steps'][1]['slow_period']
        replayed.append(period)
        out = Path(task['out'])
        out.mkdir(parents=True, exist_ok=True)
        stamp = milliseconds(task['start'])
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow({'run_id': task['run_id'], 'time_ms': stamp, 'strategy': name, 'tf': '1m',
                             'signal_id': 'test-period-' + str(period), 'recipient': 'TEST',
                             'direction': 'LONG', 'message': 'tested period ' + str(period),
                             'signal_source': 'SIGNAL', 'signal_price': '2400'})
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1,
                'approximate': False, 'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {'2026-09': 1}}

    monkeypatch.setattr(runner, 'run_chunk', chunk)
    return root, replayed


def run(root, name):
    value = scenario(symbol='XAUUSD+', start='2026-09-01', end='2026-09-02', strategies=[name],
                     result_mode='ALERT_ONLY', overlap_trading_days=0, cores=1)
    return runner.run(value, root / '데이터창고', captures=[], sequential=True)


def alerts(root, result):
    with (root / '데이터창고' / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return [{key: value for key, value in row.items() if key != 'run_id'} for row in csv.DictReader(handle)]


@pytest.mark.parametrize('folder,number', [('기본', 8), ('내 전략', 100)])
@pytest.mark.parametrize('suffix', ['.recipe.json', '.py'])
def test_changed_installed_recipe_replays_and_unchanged_recipe_reuses(installed, folder, number, suffix):
    root, replayed = installed
    name = 'SPECIAL' + str(number)
    entry = json.loads((ROOT / 'Part1/program/SPECIAL/SPECIAL8.recipe.json').read_text('utf-8'))
    entry['id'] = name
    path = root / '스페셜' / folder / (name + suffix)
    write_recipe(path, entry)
    first = run(root, name)
    first_alerts = alerts(root, first)
    assert replayed == [50] and 'replay_reused_from' not in first

    entry = deepcopy(entry)
    entry['recipe']['strategy_intent']['steps'][1]['slow_period'] = 60
    write_recipe(path, entry)
    second = run(root, name)
    assert replayed == [50, 60] and 'replay_reused_from' not in second
    assert second['code_hash'] != first['code_hash'] and second['replay_key'] != first['replay_key']
    assert alerts(root, second) != first_alerts
    assert second['tested_settings']['strategies'][name]['conditions']['steps'][1]['slow_period'] == 60

    third = run(root, name)
    assert replayed == [50, 60] and third['replay_reused_from'] == second['run_id']
    assert alerts(root, third) == alerts(root, second)


def test_moving_installed_project_and_warehouse_keeps_the_same_replay(installed, monkeypatch):
    root, replayed = installed
    entry = json.loads((ROOT / 'Part1/program/SPECIAL/SPECIAL8.recipe.json').read_text('utf-8'))
    entry['id'] = 'SPECIAL100'
    write_recipe(root / '스페셜/내 전략/SPECIAL100.recipe.json', entry)
    first = run(root, 'SPECIAL100')
    first_alerts = alerts(root, first)
    moved = root.parent / '옮긴 모세'
    assert moved.resolve().parent == root.resolve().parent
    shutil.move(str(root), str(moved))
    monkeypatch.setattr(runner, 'PROGRAM', moved / 'Part1/program')
    monkeypatch.setattr(registry, 'REGISTRY', moved / 'settings/strategy_registry.json')
    second = run(moved, 'SPECIAL100')
    assert replayed == [50] and second['replay_reused_from'] == first['run_id']
    assert second['code_hash'] == first['code_hash'] and second['replay_key'] == first['replay_key']
    assert alerts(moved, second) == first_alerts
