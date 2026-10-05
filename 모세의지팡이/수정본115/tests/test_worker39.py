"""Worker39 scheduling invariants. No strategy decisions are replaced here."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.worker_schedule import (
    BoundedTasks, ProgressRows, plan_dispatch, _capture_work, _finish_estimate,
)


def tasks(costs, root=Path('run')):
    start = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    return [{'start': start.isoformat(),
             'end': (start + dt.timedelta(minutes=cost)).isoformat(),
             'warm_start': start.isoformat(), 'scenario': {'strategies': ['SPECIAL1']},
             'config': {'sentinel': ['do not edit']}, 'captures': [],
             'out': str(root / f'chunk_{i:03d}'), 'identity': i}
            for i, cost in enumerate(costs)]


def test_largest_first_preserves_every_task_and_warmup():
    jobs = tasks([2, 4, 6, 8, 12, 24])
    before = deepcopy(jobs)
    order, info = plan_dispatch(jobs, 2)
    assert order == [5, 4, 3, 2, 1, 0]
    assert info['policy'] == 'ESTIMATED_LONGEST_FIRST'
    assert info['predicted_gain'] >= 0.05
    assert info['pending_limit'] == 4
    assert jobs == before
    assert all(jobs[i] is jobs[order[order.index(i)]] for i in range(len(jobs)))


@pytest.mark.parametrize('costs,workers', [([], 2), ([1], 4), ([1, 10], 2),
                                         ([1, 10, 2], 1), ([2, 2, 2, 2], 2)])
def test_fifo_when_no_useful_reordering(costs, workers):
    order, info = plan_dispatch(tasks(costs), workers)
    assert order == list(range(len(costs)))
    assert info['policy'] == 'FIFO'


def test_negligible_predicted_improvement_keeps_fifo():
    jobs = tasks([10, 10, 10, 11])
    order, info = plan_dispatch(jobs, 2)
    assert order == [0, 1, 2, 3]
    assert info['policy'] == 'FIFO'


def test_equal_costs_have_stable_index_ties():
    order, info = plan_dispatch(tasks([1, 1, 10, 10, 20]), 2)
    assert order == [4, 2, 3, 0, 1]


def test_estimate_includes_warmup_not_just_output_window():
    jobs = tasks([2, 4, 6, 8, 12, 24])
    jobs[0]['warm_start'] = '2026-08-31T00:00:00+00:00'
    order, info = plan_dispatch(jobs, 2)
    assert info['task_estimates'][0] > info['task_estimates'][-1]
    assert order[0] == 0


def test_capture_count_estimate_respects_intersection_and_input_unchanged():
    job = tasks([60])[0]
    job['captures'] = [{'start': '2026-09-01', 'end': '2026-09-02',
                        'bundles': 1440, 'path': 'never/open/me'}]
    original = deepcopy(job)
    assert _capture_work(job) == 60
    job['warm_start'] = '2026-08-31'
    assert _capture_work(job) == 60
    job['warm_start'] = original['warm_start']
    assert job == original


def test_capture_records_legacy_count_supported():
    job = tasks([60])[0]
    job['captures'] = [{'start': '2026-09-01', 'end': '2026-09-02', 'records': 1440}]
    assert _capture_work(job) == 60


@pytest.mark.parametrize('bad', [None, '', 'invalid', -1, float('nan'), float('inf'), True, {}, []])
def test_unusable_capture_count_falls_back_for_entire_plan(bad):
    jobs = tasks([2, 4, 6, 8, 12, 24])
    for job in jobs:
        job['captures'] = [{'start': '2026-09-01', 'end': '2026-09-02', 'bundles': 1440}]
    jobs[-1]['captures'][0]['bundles'] = bad
    _, info = plan_dispatch(jobs, 2)
    assert info['estimate_unit'] == 'warm_span_ms'
    assert info['task_estimates'] == [v * 60000 for v in (2, 4, 6, 8, 12, 24)]


def test_capture_estimate_units_are_not_mixed():
    jobs = tasks([2, 4, 6, 8, 12, 24])
    for job in jobs:
        job['captures'] = [{'start': '2026-09-01', 'end': '2026-09-02', 'bundles': 1440}]
    _, info = plan_dispatch(jobs, 2)
    assert info['estimate_unit'] == 'estimated_bundles'
    assert info['task_estimates'] == [2, 4, 6, 8, 12, 24]


@pytest.mark.parametrize('capture', [
    {'start': 'bad', 'end': '2026-09-02', 'bundles': 10},
    {'start': '2026-09-02', 'end': '2026-09-01', 'bundles': 10},
    {'start': '2026-09-01', 'end': '2026-09-01', 'bundles': 10},
    {'bundles': 10},
])
def test_bad_capture_ranges_do_not_change_task(capture):
    job = tasks([2])[0]
    job['captures'] = [capture]
    original = deepcopy(job)
    assert _capture_work(job) is None
    assert job == original


@pytest.mark.parametrize('workers', [0, -1])
def test_invalid_worker_count(workers):
    with pytest.raises(ValueError, match='positive'):
        plan_dispatch(tasks([1]), workers)


def test_estimate_simulation_known_parallel_load():
    assert _finish_estimate([2, 4, 6, 8, 12, 24], range(6), 2) == 36
    assert _finish_estimate([2, 4, 6, 8, 12, 24], reversed(range(6)), 2) == 28


class FakePool:
    def __init__(self):
        self.calls = []
    def submit(self, function, job):
        future = Future()
        self.calls.append((function, job, future))
        return future


def test_bounded_refill_executes_every_original_object_exactly_once():
    jobs = tasks(range(1, 21))
    pool = FakePool()
    queue = BoundedTasks(pool, id, jobs, 2, reversed(range(len(jobs))))
    queue.fill()
    assert len(pool.calls) == len(queue.pending) == 4
    seen = []
    while queue.pending:
        # Finish out of submit order to exercise the future->task mapping.
        future = next(reversed(queue.pending))
        index = queue.pending[future]
        future.set_result(id(jobs[index]))
        completed = queue.collect({future})
        assert completed == [(index, id(jobs[index]))]
        seen.append(index)
        queue.fill()
        assert len(queue.pending) <= 4
    assert sorted(seen) == list(range(20))
    assert len(pool.calls) == 20
    assert queue.high_water == 4
    assert queue.completed == queue.submitted == set(range(20))
    for index, (_, job, _) in zip(reversed(range(20)), pool.calls):
        assert job is jobs[index]


@pytest.mark.parametrize('order', [[0, 0], [0], [0, 2], [-1, 0]])
def test_invalid_schedule_rejected_before_submitting(order):
    pool = FakePool()
    with pytest.raises(ValueError, match='exactly once'):
        BoundedTasks(pool, id, tasks([1, 2]), 2, order)
    assert not pool.calls


def test_worker_error_is_not_swallowed_or_resubmitted():
    pool = FakePool()
    queue = BoundedTasks(pool, id, tasks(range(1, 11)), 2, range(10))
    queue.fill()
    failed = next(iter(queue.pending))
    failed.set_exception(RuntimeError('original worker error'))
    with pytest.raises(RuntimeError, match='original worker error'):
        queue.collect({failed})
    assert len(pool.calls) == 4 and not queue.completed


def test_simultaneous_completion_results_keep_original_indices():
    jobs = tasks([1, 2, 3, 4])
    pool = FakePool()
    queue = BoundedTasks(pool, id, jobs, 2, [3, 2, 1, 0])
    queue.fill()
    for future, index in queue.pending.items():
        future.set_result(index)
    assert queue.collect(set(queue.pending)) == [(i, i) for i in range(4)]


def test_cancelled_result_is_preserved_and_does_not_drop_other_tasks():
    jobs = tasks(range(1, 11))
    with ThreadPoolExecutor(max_workers=2) as pool:
        queue = BoundedTasks(pool, lambda task: {'id': task['identity'], 'cancelled': True},
                             jobs, 2, range(10))
        queue.fill()
        results = []
        while queue.pending:
            done, _ = wait(queue.pending, return_when=FIRST_COMPLETED)
            results.extend(queue.collect(done))
            queue.fill()
    assert sorted(i for i, _ in results) == list(range(10))
    assert all(row['cancelled'] for _, row in results)


def put_progress(job, value):
    path = Path(job['out']) / 'progress.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_completed_progress_read_once_active_read_each_time(tmp_path, monkeypatch):
    jobs = tasks([1, 2, 3], tmp_path)
    for i, job in enumerate(jobs):
        put_progress(job, {'pid': i + 1, 'percent': 100 if i == 0 else 10})
    reader = ProgressRows(jobs)
    original = Path.read_text
    reads = []
    def tracked(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', tracked)
    assert len(reader.read({0, 1}, {0})) == 2
    assert len(reader.read({0, 1}, {0})) == 2
    assert reads.count(reader.paths[0]) == 1
    assert reads.count(reader.paths[1]) == 2
    assert reader.paths[2] not in reads  # Unsubmitted tasks have no progress I/O.


@pytest.mark.parametrize('initial', ['missing', '{bad', '[]', '{"pid": 1}'])
def test_unreadable_finished_progress_retried(tmp_path, initial):
    jobs = tasks([1], tmp_path)
    path = Path(jobs[0]['out']) / 'progress.json'
    path.parent.mkdir(parents=True)
    if initial != 'missing':
        path.write_text(initial)
    reader = ProgressRows(jobs)
    assert reader.read({0}, {0}) == []
    assert not reader.finished
    put_progress(jobs[0], {'pid': 1, 'percent': 100})
    assert reader.read({0}, {0}) == [{'pid': 1, 'percent': 100}]


def test_locked_progress_retry(tmp_path, monkeypatch):
    jobs = tasks([1], tmp_path)
    put_progress(jobs[0], {'pid': 1, 'percent': 0})
    original = Path.read_text
    def locked(*args, **kwargs):
        raise PermissionError('reader lock')
    reader = ProgressRows(jobs)
    monkeypatch.setattr(Path, 'read_text', locked)
    assert reader.read({0}, {0}) == []
    monkeypatch.setattr(Path, 'read_text', original)
    assert reader.read({0}, {0}) == [{'pid': 1, 'percent': 0}]


def test_progress_reader_portability(tmp_path):
    import shutil
    old = tmp_path / 'old'
    jobs = tasks([1, 2], old)
    for i, job in enumerate(jobs):
        put_progress(job, {'pid': i + 1, 'percent': 100})
    new = tmp_path / 'moved'
    shutil.copytree(old, new)
    moved = tasks([1, 2], new)
    assert ProgressRows(jobs).read({0, 1}, {0, 1}) == ProgressRows(moved).read({0, 1}, {0, 1})


@pytest.fixture
def parent_runner(monkeypatch, tmp_path):
    """Real parent control flow, explicit in-memory SQL substitute, fake workers."""
    import csv
    import os
    import time
    from types import SimpleNamespace
    # Optional dependency absence is isolated to tests, never production.
    sys.path.insert(0, str(ROOT / 'build/optimization37'))
    from support import install_warehouse
    install_warehouse(ROOT)
    from event_backtest import runner, build_plan, partition
    from event_backtest.warehouse import FIELDS
    from event_backtest.settings import milliseconds
    from event_backtest.portable import validate
    stages = []
    imported = []
    class MemoryCatalog:
        def __init__(self, root, **kwargs):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)
            self.db = SimpleNamespace(execute=lambda *args, **kwargs: None)
            self.rows = []
        def close(self):
            pass
        def run(self, run_id, status, data):
            validate(data)
            stages.append(status)
        def import_results(self, path):
            imported.append(Path(path).parent.name)
            with Path(path).open(encoding='utf-8', newline='') as handle:
                self.rows.extend(csv.DictReader(handle))
        def export_results(self, run_id, path):
            self.rows.sort(key=lambda row:(int(row['time_ms']),row['strategy'],row['signal_id'],row['recipient']))
            with Path(path).open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(self.rows)
    bounds = ['2026-09-01','2026-09-01T03:00:00+00:00','2026-09-01T05:00:00+00:00',
              '2026-09-01T07:00:00+00:00','2026-09-01T09:00:00+00:00',
              '2026-09-01T13:00:00+00:00','2026-09-02']
    monkeypatch.setattr(runner, 'Warehouse', MemoryCatalog)
    monkeypatch.setattr(runner, 'runtime_config', lambda s:{})
    monkeypatch.setattr(runner, 'code_hash', lambda:'test-code')
    monkeypatch.setattr(build_plan, 'current_build', lambda root:'test-ea')
    monkeypatch.setattr(partition, 'plan_periods', lambda *args:list_and_label(bounds))
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)
    def fake_chunk(task):
        out = Path(task['out'])
        out.mkdir(parents=True, exist_ok=True)
        time.sleep(0.01)
        cancelled = (out.parent / 'stop.request').exists()
        stamp = milliseconds(task['start'])
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            if not cancelled:
                writer.writerow({'run_id':task['run_id'],'time_ms':stamp,'strategy':'SPECIAL1',
                                 'signal_id':out.name,'recipient':'OFFLINE','message':'worker test'})
        put_progress(task, {'pid':os.getpid(),'percent':0 if cancelled else 100})
        return {'pid':os.getpid(),'bundles':1,'processor_timings':{},'max_memory_bytes':1,
                'approximate':False,'alerts_csv':(out/'alerts.csv').relative_to(task['warehouse']).as_posix(),
                'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
                'warmup_bundles':0,'elapsed_seconds':0.01,'cancelled':cancelled,
                'processed_start_ms':None if cancelled else stamp,
                'processed_end_ms':None if cancelled else stamp,
                'alert_months':{} if cancelled else {'2026-09':1}}
    monkeypatch.setattr(runner, 'run_chunk', fake_chunk)
    return runner, stages, imported


def list_and_label(bounds):
    return list(zip(bounds, bounds[1:])), 'VALIDATION_ONLY_FIXED_RANGES'


def test_real_parent_control_flow_keeps_tasks_and_chronological_merge(parent_runner, tmp_path):
    import csv
    from event_backtest.settings import scenario
    runner, stages, imported = parent_runner
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=2)
    result = runner.run(s, tmp_path/'warehouse', captures=[])
    assert result['status'] == 'COMPLETE' and stages == ['RUNNING','COMPLETE']
    assert len(result['chunks']) == 6
    assert result['worker_scheduling']['max_pending_observed'] == 4
    assert result['worker_scheduling']['policy'] == 'ESTIMATED_LONGEST_FIRST'
    assert imported == [f'chunk_{i:03d}' for i in range(6)]
    assert result['processed_periods'] == sorted(result['processed_periods'], key=lambda row:row['start'])
    with (tmp_path/'warehouse'/result['alerts_csv']).open(encoding='utf-8',newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6 and [row['signal_id'] for row in rows] == imported


def test_real_parent_cancellation_retains_partial_result_contract(parent_runner, tmp_path):
    from event_backtest.settings import scenario
    runner, stages, imported = parent_runner
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=2)
    result = runner.run(s, tmp_path/'warehouse', captures=[], cancel=lambda:True)
    assert result['status'] == 'CANCELLED'
    assert stages == ['RUNNING','CANCELLED']
    assert result['chunks'] == []
    assert result['processed_periods'] == []
    assert result['alert_statistics']['total'] == 0
    assert imported == []
    assert result['worker_scheduling']['submitted_chunks'] == 0
    assert len(result['unprocessed_periods']) == 6
    assert all(row['reason'] == 'NOT_STARTED' for row in result['unprocessed_periods'])


def test_real_parent_stop_after_first_result_preserves_completed_rows(parent_runner, tmp_path):
    import csv
    from event_backtest.settings import scenario
    runner, stages, imported = parent_runner
    cancelled = [False]
    def progress(kind, data):
        if kind == 'CHUNK_COMPLETE':cancelled[0] = True
    request = scenario(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL1'], overlap_trading_days=0, cores=2)
    result = runner.run(request, tmp_path/'warehouse', captures=[], cancel=lambda: cancelled[0], emit=progress)
    assert result['status'] == 'CANCELLED' and stages == ['RUNNING', 'CANCELLED']
    assert 1 <= len(result['chunks']) <= 4
    assert result['worker_scheduling']['submitted_chunks'] <= 4
    assert result['unprocessed_periods']
    assert len(imported) == len(result['chunks'])
    with (tmp_path/'warehouse'/result['alerts_csv']).open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == result['alert_statistics']['total'] >= 1
    assert len(rows) == sum(not row['cancelled'] for row in result['chunks'])


def test_stop_cancels_unstarted_futures_and_keeps_running_result():
    jobs = tasks(range(1, 11))
    pool = FakePool()
    queue = BoundedTasks(pool, id, jobs, 2, range(10))
    queue.fill()
    running = pool.calls[0][2]
    assert running.set_running_or_notify_cancel()
    queue.stop()
    queue.fill()
    assert len(pool.calls) == 4 and len(queue.pending) == 1
    assert queue.cancelled == {1, 2, 3}
    running.set_result('partial result')
    assert queue.collect({running}) == [(0, 'partial result')]
    assert queue.completed == {0} and queue.pending == {}


def test_real_parent_failure_is_recorded_not_retried(parent_runner, tmp_path, monkeypatch):
    from event_backtest.settings import scenario
    runner, stages, imported = parent_runner
    called = []
    def failed(task):
        called.append(Path(task['out']).name)
        raise RuntimeError('controlled worker failure')
    monkeypatch.setattr(runner, 'run_chunk', failed)
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=2)
    with pytest.raises(RuntimeError, match='controlled worker failure'):
        runner.run(s, tmp_path/'warehouse', captures=[])
    assert stages == ['RUNNING','FAILED']
    assert not imported and len(called) <= 4 and len(set(called)) == len(called)
    assert len(list((tmp_path/'warehouse').glob('runs/*/errors/*.log'))) == 1


def test_real_parent_one_worker_keeps_original_task_order(parent_runner, tmp_path):
    from event_backtest.settings import scenario
    runner, _, imported = parent_runner
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=1)
    result = runner.run(s, tmp_path/'warehouse', captures=[])
    assert result['worker_scheduling']['task_order'] == list(range(6))
    assert result['worker_scheduling']['policy'] == 'FIFO'
    assert len(result['chunks']) == 6


def test_real_parent_sequential_preserves_one_existing_period(parent_runner, tmp_path):
    from event_backtest.settings import scenario
    runner, _, imported = parent_runner
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=12)
    result = runner.run(s, tmp_path/'warehouse', captures=[],sequential=True)
    assert result['cores'] == 1 and result['partition'] == 'SEQUENTIAL'
    assert len(result['chunks']) == 1 and imported == ['chunk_000']
    assert result['worker_scheduling']['effective_workers'] == 1


def test_parent_dispatch_error_is_recorded_as_failure(parent_runner, tmp_path, monkeypatch):
    from event_backtest.settings import scenario
    from event_backtest import worker_schedule
    runner, stages, imported = parent_runner
    def bad_plan(*args):
        raise ValueError('controlled dispatch failure')
    monkeypatch.setattr(worker_schedule, 'plan_dispatch', bad_plan)
    s = scenario(start='2026-09-01',end='2026-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,cores=2)
    with pytest.raises(ValueError, match='controlled dispatch failure'):
        runner.run(s, tmp_path/'warehouse', captures=[])
    assert stages == ['RUNNING','FAILED'] and not imported
