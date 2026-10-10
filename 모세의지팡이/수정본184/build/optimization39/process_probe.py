"""Actual run_chunk in a spawn process pool; parent SQL is NOT exercised.

The multi workload uses six fixed intraday output ranges and a shared 00:00
warm start, solely to exercise dispatch with the supplied one-day recording.
It does not change production partition.py, and it is not a one-week test.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import sys
import time

_PROJECT = None
_TRACE = False


def initialize(project, trace):
    global _PROJECT, _TRACE
    _PROJECT, _TRACE = Path(project), trace
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(_PROJECT / 'build/optimization37'))
    from support import install_warehouse
    install_warehouse(_PROJECT)


def execute_task(task):
    """Use the selected revision's unchanged worker body; only observe emissions."""
    from event_backtest.runner import run_chunk
    from event_engine.engine import EventEngine
    from event_engine.domain_support import plain
    trace = hashlib.sha256()
    count = 0
    original = EventEngine._emit
    errors = []
    error_original = EventEngine._error
    def observed(self, strategy, event, output):
        nonlocal count
        row = {'strategy': strategy, 'engine_seq': event.engine_seq,
               'source_time': event.source_time, 'output_type': type(output).__name__,
               'output': plain(vars(output))}
        trace.update(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode('utf-8') + b'\n')
        count += 1
        return original(self, strategy, event, output)
    def error_observed(self, consumer, event, exc):
        errors.append({'consumer': consumer.name, 'time': event.source_time,
                       'error': type(exc).__name__ + ': ' + str(exc)})
        return error_original(self, consumer, event, exc)
    if _TRACE:
        EventEngine._emit = observed
        EventEngine._error = error_observed
    started = time.perf_counter()
    try:
        result = run_chunk(task)
    finally:
        EventEngine._emit = original
        EventEngine._error = error_original
    result['validation'] = {
        'whole_task_seconds': time.perf_counter() - started,
        'csv_sha256': hashlib.sha256((Path(task['out']) / 'alerts.csv').read_bytes()).hexdigest(),
        'trace_sha256': trace.hexdigest() if _TRACE else None,
        'trace_count': count, 'errors': errors,
    }
    return result


def execute_pool(tasks, workers, optimized, project, trace, context):
    from event_backtest.system import process_memory
    if optimized:
        from event_backtest.worker_schedule import plan_dispatch, BoundedTasks, ProgressRows
        order, schedule = plan_dispatch(tasks, workers)
    else:
        order = list(range(len(tasks)))
        schedule = {'policy': 'ORIGINAL_FIFO', 'task_order': order,
                    'effective_workers': min(workers, len(tasks)), 'pending_limit': len(tasks)}
    results = {}
    started = time.perf_counter()
    with futures.ProcessPoolExecutor(max_workers=min(workers, len(tasks)), mp_context=context,
                                     initializer=initialize, initargs=(str(project), trace)) as pool:
        if optimized:
            queue = BoundedTasks(pool, execute_task, tasks, min(workers, len(tasks)), order)
            reader = ProgressRows(tasks)
            queue.fill()
            while queue.pending:
                done, _ = futures.wait(queue.pending, timeout=2, return_when=futures.FIRST_COMPLETED)
                completed = queue.collect(done)
                queue.fill()
                results.update(completed)
                # Same parent telemetry path as production (not strategy work).
                for row in reader.read(queue.submitted, queue.completed):
                    process_memory(row['pid'], peak=False)
            schedule['max_pending_observed'] = queue.high_water
        else:
            pending = {pool.submit(execute_task, task): i for i, task in enumerate(tasks)}
            while pending:
                done, _ = futures.wait(pending, timeout=2, return_when=futures.FIRST_COMPLETED)
                for future in done:
                    results[pending.pop(future)] = future.result()
                for task in tasks:
                    try:
                        row = json.loads((Path(task['out']) / 'progress.json').read_text('utf-8'))
                        process_memory(row['pid'], peak=False)
                    except (OSError, ValueError, KeyError):
                        pass
    return results, schedule, time.perf_counter() - started


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    p.add_argument('warehouse', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--strategy', choices=['SPECIAL1', 'ALL'], required=True)
    p.add_argument('--layout', choices=['single', 'multi'], default='multi')
    p.add_argument('--optimized', action='store_true')
    p.add_argument('--trace', action='store_true')
    p.add_argument('--stop-before-start', action='store_true')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--context', choices=['spawn', 'fork'], default='spawn')
    p.add_argument('--transport', choices=['live', 'replay'], default='replay')
    p.add_argument('--affinity', default='0,1')
    a = p.parse_args()
    a.project = a.project.resolve()
    a.warehouse = a.warehouse.resolve()
    a.output = a.output.resolve()
    a.output.relative_to(a.warehouse)
    a.output.mkdir(parents=True, exist_ok=True)
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['MOSES_LOG_DIRECTORY'] = str(a.output / 'parent_logs')
    if a.affinity and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, {int(i) for i in a.affinity.split(',')})
    initialize(a.project, a.trace)
    from event_backtest.settings import scenario, digest
    from event_backtest.runner import runtime_config
    from event_backtest.warehouse import FIELDS
    settings = scenario(symbol='XAUUSD+', start='2026-09-01', end='2026-09-02',
                        mode='BAR', strategies=[a.strategy], overlap_trading_days=0,
                        cores=a.workers)
    config = runtime_config(settings)
    storage = json.loads((a.warehouse / 'captures/real_day/storage.json').read_text())
    capture = {'path': 'captures/real_day', 'start': settings['start'], 'end': settings['end'],
               'bundles': storage['bundles']}
    boundaries = ['2026-09-01', '2026-09-02'] if a.layout == 'single' else [
        '2026-09-01', '2026-09-01T03:00:00+00:00', '2026-09-01T05:00:00+00:00',
        '2026-09-01T07:00:00+00:00', '2026-09-01T09:00:00+00:00',
        '2026-09-01T13:00:00+00:00', '2026-09-02']
    tasks = [{'scenario': settings, 'config': config, 'start': start, 'end': end,
              'warm_start': settings['start'], 'captures': [capture], 'run_id': '0' * 32,
              'warehouse': str(a.warehouse), 'out': str(a.output / f'chunk_{i:03d}'),
              'transport': a.transport}
             for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]))]
    input_digest = digest([{k: v for k, v in task.items() if k not in ('out', 'warehouse')}
                           for task in tasks])
    if a.stop_before_start:
        (a.output / 'stop.request').touch()
    results, schedule, elapsed = execute_pool(tasks, a.workers, a.optimized, a.project,
                                              a.trace, multiprocessing.get_context(a.context))
    # Equivalent ORDER BY keys, using stdlib CSV; not represented as a DuckDB test.
    rows = []
    for i in sorted(results):
        with (Path(tasks[i]['out']) / 'alerts.csv').open(encoding='utf-8', newline='') as handle:
            rows.extend(csv.DictReader(handle))
    rows.sort(key=lambda row: (int(row['time_ms']), row['strategy'], row['signal_id'], row['recipient']))
    merged = a.output / 'merged_validation.csv'
    with merged.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {'revision': a.project.name, 'strategy': a.strategy, 'layout': a.layout,
               'optimized': a.optimized, 'traced': a.trace, 'transport': a.transport,
               'workers_requested': a.workers, 'multiprocessing_context': a.context,
               'wall_seconds_including_pool_startup': elapsed, 'input_plan_sha256': input_digest,
               'config_sha256': digest(config), 'scheduling': schedule,
               'csv_sha256': hashlib.sha256(merged.read_bytes()).hexdigest(), 'alert_rows': len(rows),
               'results': {str(i): r for i, r in sorted(results.items())},
               'unique_worker_pids': sorted({r['pid'] for r in results.values()}),
               'total_replayed_bundles': sum(r['bundles'] for r in results.values()),
               'total_warmup_bundles': sum(r['warmup_bundles'] for r in results.values()),
               'all_cancelled': all(r['cancelled'] for r in results.values()),
               'environment': {'python': platform.python_version(), 'system': platform.system(),
                               'affinity': a.affinity, 'cpu_count': os.cpu_count()},
               'limits': ['one supplied real day, not one week',
                          'multi uses fixed validation-only intraday task boundaries',
                          'overlap setting zero in validation; production unchanged',
                          'parent DuckDB planning/merging not tested',
                          'spawn on Linux is not native Windows/MT5 validation']}
    (a.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
