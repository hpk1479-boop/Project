"""169: a backtest keeps what it tested, so later recipe or setting changes never change what it shows.

Each run keeps the plan every strategy ran (its recipe as written then, on the base frame of the test
and with the OZ trigger and alert times applied, or the conditions edited for a virtual entry test),
the virtual entry and the configuration values its decisions read. The run folder has it from the
start, before the replay runs.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import runner
from event_backtest.base_frames import engine_plugins, recipe_entry
from event_backtest.portable import validate
from event_backtest.settings import digest, scenario, variant_scenarios
from event_backtest.tested_settings import CONFIG_KEYS, FILE, tested_settings as keep
from strategy_recipe.registry import builtin_sources, default_settings

PERIOD = dict(symbol='XAUUSD+', start='2026-09-01', end='2026-09-02')
# Replays in a run: no warm-up (no recordings here), two fixed blocks.
RUN = dict(PERIOD, overlap_trading_days=0, cores=2)
BOUNDS = ['2026-09-01', '2026-09-01T12:00:00+00:00', '2026-09-02']


def kept(s):
    config = runner.runtime_config(s)
    return keep(s, config, runner.applied_strategy_settings(s, config)), config


def frames(plan):
    return {tf for step in plan['steps'] + plan.get('cancel_conditions', []) for tf in step['tfs']}


def test_a_run_keeps_the_plan_its_engine_runs_with_the_trigger_and_times_applied():
    times = {'MAIN_ASIA': {'enabled': True}, 'MAIN_NEWYORK': {'enabled': True, 'start': '2200', 'end': '0100'}}
    s = scenario(strategies=['SPECIAL2'], triggers={'SPECIAL2': '무지성 브레이커 올존'},
                 special_time_filters={'SPECIAL2': times}, **PERIOD)
    data, config = kept(s)
    row = data['strategies']['SPECIAL2']
    plan = row['conditions']
    assert plan == engine_plugins(config, s)['SPECIAL2'].recipe['strategy_intent']
    # The recipe is written with a normal OZ and no final alert times; the run applied the chosen ones.
    written = recipe_entry('SPECIAL2')['recipe']['strategy_intent']
    assert written['final']['validation_mode'] == 'NORMAL' and 'final_time_filters' not in written
    assert (plan['final']['validation_mode'], plan['final']['trigger_mode']) == ('BLIND', 'BREAKER')
    assert plan['final_time_filters'] == times
    assert (row['trigger'], row['time_filters'], row['time_source']) == ('무지성 브레이커 올존', times, 'override')
    entry = recipe_entry('SPECIAL2')
    assert row['title'] == entry['name'] and row['recipe_sha256'] == digest(entry)
    assert row['folder'] == builtin_sources()['SPECIAL2'] and row['edited'] is False and row['base_frame'] is None
    assert list(data['config']) == [*CONFIG_KEYS, 'POINT_XAUUSD+']
    assert data['config'] == {key: config[key] for key in data['config']} and data['virtual_entry'] is None
    validate(data)  # only relative paths: it is stored in the warehouse catalog


def test_without_a_choice_the_recipe_defaults_are_kept():
    s = scenario(strategies=['SPECIAL1'], **PERIOD)
    row = kept(s)[0]['strategies']['SPECIAL1']
    trigger, times = default_settings('SPECIAL1')
    assert (row['trigger'], row['time_filters'], row['time_source']) == (trigger, times, 'preset_default')
    assert row['conditions']['final_time_filters'] == times


def test_a_run_on_another_base_frame_keeps_its_conditions_on_that_frame():
    one, three = variant_scenarios(scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY',
                                            virtual_bases=['1m', '3m'], **PERIOD))
    for s, tf in ((one, '1m'), (three, '3m')):
        data, config = kept(s)
        row = data['strategies']['SPECIAL8']
        assert row['conditions'] == engine_plugins(config, s)['SPECIAL8'].recipe['strategy_intent']
        assert frames(row['conditions']) == {tf} and row['base_frame'] == tf and row['frames']['timeframes'] == [tf]
        # Each run of the request keeps its own virtual entry.
        assert data['virtual_entry'] == s['virtual_entry'] and row['edited'] is False
    assert kept(three)[0]['virtual_entry']['tf'] == '3m'


def test_conditions_edited_for_a_virtual_entry_test_are_kept_instead_of_the_recipe():
    from event_backtest.virtual_defaults import strategy_on_base, strategy_profile
    intent = strategy_on_base(strategy_profile('SPECIAL8'), 'SIGNAL')
    intent['steps'][1]['slow_period'] = 60
    s = scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY', virtual_strategy=intent, **PERIOD)
    assert s['virtual_strategy'] is not None
    data, config = kept(s)
    row = data['strategies']['SPECIAL8']
    assert row['edited'] is True and row['conditions']['steps'][1]['slow_period'] == 60
    assert row['conditions'] == engine_plugins(config, s)['SPECIAL8'].recipe['strategy_intent']
    assert recipe_entry('SPECIAL8')['recipe']['strategy_intent']['steps'][1]['slow_period'] == 50


@pytest.mark.parametrize('changes', [
    dict(strategies=['WATCH'], commands=[{'strategy': 'WATCH', 'text': '1분 HMA17 골든크로스', 'chat_id': 'BACKTEST'}]),
    dict(strategies=[], build_only=True)])
def test_runs_without_a_recipe_keep_no_strategy(changes):
    data, config = kept(scenario(**PERIOD, **changes))
    assert data['strategies'] == {} and data['config']['WONBI_SIGMA'] == config['WONBI_SIGMA']


def test_all_keeps_every_strategy_it_runs():
    s = scenario(strategies=['ALL'], **PERIOD)
    data, config = kept(s)
    loaded = engine_plugins(config, s)
    assert set(data['strategies']) == {name for name in s['enabled_specials'] if name in loaded} and data['strategies']
    for name, row in data['strategies'].items():
        assert row['conditions'] == loaded[name].recipe['strategy_intent']


def test_a_strategy_not_loaded_is_not_kept(monkeypatch):
    # A strategy watching the configured symbols runs nothing when none are configured.
    s = scenario(strategies=['SPECIAL8'], **PERIOD)
    config = {**runner.runtime_config(s), 'SYMBOLS': ''}
    assert 'SPECIAL8' not in engine_plugins(config, s)
    assert keep(s, config, runner.applied_strategy_settings(s, config))['strategies'] == {}


@pytest.mark.parametrize('error', [ValueError('기준 프레임으로 바꿀 수 없습니다'), KeyError('SPECIAL8')])
def test_a_plan_the_replay_cannot_prepare_keeps_nothing_and_leaves_the_run_to_report_it(monkeypatch, error):
    from event_backtest import base_frames

    def refused(config, s):
        raise error
    monkeypatch.setattr(base_frames, 'engine_plugins', refused)
    s = scenario(strategies=['SPECIAL8'], **PERIOD)
    data, config = kept(s)
    assert data['strategies'] == {} and data['config']['WONBI_SIGMA'] == config['WONBI_SIGMA']


def test_a_generated_strategy_keeps_its_library_folder(monkeypatch):
    from event_backtest import tested_settings as module
    from strategy_recipe.user_catalog import ROOT as PROGRAM_ROOT, test_directory
    entry = {'generated': True, 'name': 'AI 전략'}
    assert module._folder('Test_SPECIAL001', entry, {}) == test_directory(PROGRAM_ROOT).relative_to(PROGRAM_ROOT).as_posix()
    assert module._folder('SPECIAL2', {'name': 'x'}, {'SPECIAL2': '스페셜/기본'}) == '스페셜/기본'


# ---- in a run (fake replay chunks, as tests/test_variant_runs161.py) ------------------------------

@pytest.fixture
def backtest(monkeypatch, tmp_path):
    from event_backtest import build_plan, joins, virtual_entry
    from event_backtest.settings import milliseconds
    from event_backtest.warehouse import FIELDS
    started = []
    monkeypatch.setattr(runner, 'code_hash', lambda: 'code-169')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(joins, 'plan_periods', lambda *a: (list(zip(BOUNDS, BOUNDS[1:])), [[i] for i in range(len(BOUNDS) - 1)]))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)

    def lane_result(task, lane):
        out = Path(lane['out'])
        # The run folder already holds what this run tests when its replay starts.
        started.append(json.loads((out.parent / FILE).read_text('utf-8')))
        out.mkdir(parents=True, exist_ok=True)
        stamp = milliseconds(task['start'])
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            csv.DictWriter(handle, fieldnames=FIELDS).writeheader()
        return {'pid': os.getpid(), 'bundles': 1, 'processor_timings': {}, 'max_memory_bytes': 1, 'approximate': False,
                'alerts_csv': (out / 'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start': task['start'], 'task_end': task['end'], 'warm_start': task['warm_start'],
                'warmup_bundles': 0, 'elapsed_seconds': .01, 'cancelled': False,
                'processed_start_ms': stamp, 'processed_end_ms': stamp, 'alert_months': {}}

    def chunk(task):
        results = [lane_result(task, lane) for lane in runner.lanes_of(task)]
        return results if task.get('lanes') else results[0]
    monkeypatch.setattr(runner, 'run_chunk', chunk)
    monkeypatch.setattr(virtual_entry, 'calculate',
                        lambda export, captures, root, s, config, out, **k: {'cancelled': False, 'summary': [], 'policy': s['virtual_entry']})
    warehouse = tmp_path / 'warehouse'
    return (lambda scenarios: runner.run_many(scenarios, warehouse, captures=[])), warehouse, started


def test_a_run_writes_what_it_tests_before_its_replay_and_keeps_it_with_its_result(backtest):
    run_many, warehouse, started = backtest
    s = scenario(strategies=['SPECIAL2'], triggers={'SPECIAL2': '무지성 브레이커 올존'}, _run_id='d' * 32, **RUN)
    (result,) = run_many([s])
    expected, _ = kept(s)
    assert started and all(item == expected for item in started)
    folder = warehouse / 'runs' / ('d' * 32)
    assert json.loads((folder / FILE).read_text('utf-8')) == expected
    assert json.loads((folder / 'result.json').read_text('utf-8'))['tested_settings'] == expected
    assert result['tested_settings'] == expected


def test_each_base_frame_of_one_request_keeps_its_own(backtest):
    run_many, warehouse, _ = backtest
    results = run_many(variant_scenarios(scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY',
                                                  virtual_bases=['1m', '3m'], **RUN)))
    kept_frames = [json.loads((warehouse / 'runs' / r['run_id'] / FILE).read_text('utf-8'))['strategies']['SPECIAL8']['base_frame']
                   for r in results]
    assert kept_frames == ['1m', '3m']
