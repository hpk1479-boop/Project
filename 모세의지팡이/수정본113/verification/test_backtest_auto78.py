"""Physical-core scheduling and AUTO partitions; no resource-usage sampling."""
from __future__ import annotations

from concurrent.futures import Future
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program'), str(ROOT / 'Part3')]
from event_backtest import settings, runner, build_plan
from event_backtest.partition import plan_periods
from event_backtest.worker_schedule import worker_pool, BoundedTasks
from lab import unified_settings


@pytest.mark.parametrize('count', [1, 4, 8, 16, 24, 32, 64, 96, 128])
def test_empty_worker_setting_uses_all_physical_cores(monkeypatch, count):
    measured = Mock(return_value=count)
    monkeypatch.setattr(runner, 'physical_cores', measured)
    assert runner.worker_count({'cores': None}) == count
    measured.assert_called_once_with()


@pytest.mark.parametrize('scenario_cores,override,sequential,expected', [(7, None, False, 7),
    (7, 19, False, 19), (None, 32, False, 32), (64, 96, True, 1)])
def test_explicit_and_sequential_settings_keep_precedence(monkeypatch, scenario_cores, override, sequential, expected):
    measured = Mock(side_effect=AssertionError('explicit override must not query hardware'))
    monkeypatch.setattr(runner, 'physical_cores', measured)
    assert runner.worker_count({'cores': scenario_cores}, override, sequential) == expected
    measured.assert_not_called()


@pytest.mark.parametrize('workers,expected', [(1, 'MONTH'), (4, 'WEEK'), (24, 'DAY'), (64, 'DAY')])
def test_auto_partitions_cover_the_entire_period_once(workers, expected):
    periods, actual = plan_periods('2026-09-01', '2026-10-01', workers, 'AUTO')
    assert actual == expected
    assert periods[0][0] == '2026-09-01' and periods[-1][1] == '2026-10-01'
    assert all(first[1] == second[0] for first, second in zip(periods, periods[1:]))
    assert all(settings.milliseconds(start) < settings.milliseconds(end) for start, end in periods)
    assert sum(settings.milliseconds(end) - settings.milliseconds(start) for start, end in periods) == 30 * 86400000
    assert plan_periods('2026-09-01', '2026-10-01', workers)[0] == periods


def test_auto_is_automatic_even_if_legacy_adaptive_flag_is_disabled():
    assert plan_periods('2026-09-01', '2026-10-01', 24, 'AUTO', False)[1] == 'DAY'


@pytest.mark.parametrize('mode,number', [('MONTH', 1), ('FORTNIGHT', 3), ('WEEK', 5), ('DAY', 30)])
def test_manual_choices_are_preserved(mode, number):
    periods, actual = plan_periods('2026-09-01', '2026-10-01', 24, mode, False)
    assert actual == mode and len(periods) == number


def test_settings_and_scenario_default_to_auto_without_overwriting_saved_manual_choice(tmp_path):
    config = tmp_path / 'settings.json'
    assert settings.settings(config)['work_size'] == 'AUTO'
    assert settings.scenario(start='2026-09-01', end='2026-10-01')['work_size'] == 'AUTO'
    config.write_text(json.dumps({'work_size': 'FORTNIGHT', 'cores': 7}), encoding='utf-8')
    assert settings.settings(config)['work_size'] == 'FORTNIGHT'
    assert settings.settings(config)['cores'] == 7


@pytest.mark.parametrize('mode', ['AUTO', 'MONTH', 'FORTNIGHT', 'WEEK', 'DAY'])
def test_settings_save_and_execution_scenario_roundtrip(monkeypatch, tmp_path, mode):
    config = tmp_path / 'settings.json'
    monkeypatch.setattr(unified_settings, 'PART2_CONFIG', config)
    assert unified_settings.save_part2({'work_size': mode, 'cores': None})['ok']
    current = settings.settings(config)
    assert current['work_size'] == mode and current['cores'] is None
    assert settings.scenario(defaults={'work_size': current['work_size']}, start='2026-09-01', end='2026-10-01')['work_size'] == mode


def test_invalid_partition_setting_is_still_rejected(monkeypatch, tmp_path):
    config = tmp_path / 'settings.json'
    monkeypatch.setattr(unified_settings, 'PART2_CONFIG', config)
    with pytest.raises(ValueError): unified_settings.save_part2({'work_size': 'INVALID'})
    assert not config.exists()
    with pytest.raises(ValueError): settings.scenario(work_size='INVALID')


def test_auto_partition_does_not_change_capture_fragments_or_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(build_plan, 'current_build', lambda _: 'isolated-build')
    monkeypatch.setattr(build_plan, 'schema_id', lambda: 50)
    monkeypatch.setattr(build_plan, 'compatible_hashes', lambda _: {'isolated-build'})
    catalog = SimpleNamespace(root=tmp_path, available=lambda *args: [])
    base = settings.scenario(start='2026-08-15', end='2026-10-01', overlap_trading_days=0)
    plans = [build_plan.make_plan({**base, 'work_size': mode}, catalog, today=dt.date(2026, 10, 2))
             for mode in ['AUTO', 'MONTH', 'FORTNIGHT', 'WEEK', 'DAY']]
    assert all(plan == plans[0] for plan in plans)
    assert plans[0]['record'] == [{'start': '2026-08-01', 'end': '2026-09-01', 'unit': 'MONTH'},
                                  {'start': '2026-09-01', 'end': '2026-10-01', 'unit': 'MONTH'}]


class InstantPool:
    def __init__(self, *, max_workers, created):
        self.workers = max_workers; self.closed = False; created.append(self)
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def submit(self, function, *args, **kwargs):
        future = Future()
        try: future.set_result(function(*args, **kwargs))
        except BaseException as error: future.set_exception(error)
        return future


@pytest.mark.parametrize('workers', [1, 24, 61, 64, 96, 128])
def test_high_core_windows_pool_executes_every_task_without_a_total_cap(workers):
    created = []
    factory = lambda **kwargs: InstantPool(created=created, **kwargs)
    tasks = list(range(workers * 3)); completed = []
    with worker_pool(workers, factory=factory, platform='win32') as pool:
        queue = BoundedTasks(pool, lambda value: value * 2, tasks, workers, range(len(tasks)))
        while len(queue.completed) < len(tasks):
            queue.fill(); completed.extend(queue.collect(set(queue.pending)))
        assert sum(part.workers for part in created) == workers
        assert all(part.workers <= 61 for part in created)
    assert sorted(completed) == [(index, index * 2) for index in tasks]
    assert all(part.closed for part in created)


def test_all_worker_pools_close_and_worker_failure_is_not_hidden():
    created = []
    with pytest.raises(RuntimeError, match='calculation failure'):
        with worker_pool(64, factory=lambda **kwargs: InstantPool(created=created, **kwargs), platform='win32') as pool:
            future = pool.submit(lambda: (_ for _ in ()).throw(RuntimeError('calculation failure')))
            future.result()
    assert len(created) == 2 and all(part.closed for part in created)


def test_partial_pool_start_failure_closes_already_started_pool():
    created = []
    def factory(**kwargs):
        if created: raise OSError('pool startup failure')
        return InstantPool(created=created, **kwargs)
    with pytest.raises(OSError, match='startup failure'):
        with worker_pool(64, factory=factory, platform='win32'): pass
    assert created[0].closed
