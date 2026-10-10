"""Parent-only worker dispatch; task contents and replay boundaries never change.

An estimated-work order is used only when it predicts at least a 5% shorter
finish time than FIFO. The estimate is a scheduling hint, not a replay filter.
No strategy, engine, input, or mutable worker state is shared by this module.
"""
from __future__ import annotations

import heapq
import json
import math
from contextlib import contextmanager, ExitStack
from itertools import cycle
from pathlib import Path
import sys

from .settings import milliseconds

_MIN_PREDICTED_GAIN = 0.05
_PENDING_PER_WORKER = 2


class _WorkerPools:
    def __init__(self, pools, sizes):
        self.pools = pools
        self.order = cycle(index for slot in range(max(sizes))
                           for index, size in enumerate(sizes) if slot < size)

    def submit(self, function, *args, **kwargs):
        return self.pools[next(self.order)].submit(function, *args, **kwargs)


@contextmanager
def worker_pool(workers, *, factory, platform=None):
    """Keep the requested total, including >61 physical cores on Windows.

    Python's Windows ProcessPoolExecutor supports at most 61 per pool (wait
    handles). This is a per-pool platform limit, not a total worker cap.
    Tasks, futures, cancellation and result ordering stay with the runner.
    """
    if workers < 1:
        raise ValueError('workers must be positive')
    count = (workers + 60) // 61 if (platform or sys.platform) == 'win32' else 1
    size, extra = divmod(workers, count)
    sizes = [size + (index < extra) for index in range(count)]
    with ExitStack() as stack:
        pools = [stack.enter_context(factory(max_workers=size)) for size in sizes]
        yield pools[0] if count == 1 else _WorkerPools(pools, sizes)


def worker_pids(pool):
    """Process IDs of the workers of a pool from worker_pool (one ProcessPoolExecutor or several)."""
    pools = getattr(pool, 'pools', None) or [pool]
    return [pid for each in pools for pid in tuple(getattr(each, '_processes', None) or {})]


def priority_keeper():
    """The backtest's CPU class following LIVE on this PC (수정본167); a no-op where it cannot apply."""
    from .settings import PROGRAM
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    from process_priority import BacktestPriority
    return BacktestPriority()


def _capture_work(task):
    """Estimate bundles in the EXISTING warm-start/end range, without file I/O."""
    start = milliseconds(task['warm_start'])
    end = milliseconds(task['end'])
    pieces = task.get('captures')
    if not pieces:
        return None
    total = 0.0
    for capture in pieces:
        try:
            first = milliseconds(capture['start'])
            last = milliseconds(capture['end'])
            count = capture.get('bundles', capture.get('records'))
            if isinstance(count, bool):
                return None
            count = float(count)
            if last <= first or not math.isfinite(count) or count < 0:
                return None
            overlap = max(0, min(end, last) - max(start, first))
            total += count * (overlap / (last - first))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    return total if math.isfinite(total) else None


def _finish_estimate(costs, order, workers):
    loads = [0.0] * workers
    for index in order:
        heapq.heapreplace(loads, loads[0] + costs[index])
    return max(loads, default=0.0)


def plan_dispatch(tasks, workers):
    """Return original task indices and portable, non-semantic diagnostics.

    All estimates use the same unit. Missing/invalid capture metadata anywhere
    falls back to warm-start-inclusive time spans for the WHOLE task list.
    A single worker and a one-wave workload retain their existing FIFO order.
    """
    if workers < 1:
        raise ValueError('workers must be positive')
    order = list(range(len(tasks)))
    active = min(workers, len(tasks))
    details = {'policy': 'FIFO', 'effective_workers': active,
               'pending_limit': min(len(tasks), _PENDING_PER_WORKER * active),
               'task_order': order.copy(), 'estimate_unit': 'not_needed',
               'minimum_predicted_gain': _MIN_PREDICTED_GAIN}
    if active <= 1 or len(tasks) <= active:
        return order, details
    costs = [_capture_work(task) for task in tasks]
    if any(cost is None for cost in costs):
        costs = [float(max(1, milliseconds(task['end']) - milliseconds(task['warm_start'])))
                 for task in tasks]
        unit = 'warm_span_ms'
    else:
        costs = [max(1.0, cost) for cost in costs]
        unit = 'estimated_bundles'
    longest_first = sorted(order, key=lambda i: (-costs[i], i))
    before = _finish_estimate(costs, order, active)
    after = _finish_estimate(costs, longest_first, active)
    gain = 1.0 - after / before if before else 0.0
    if gain >= _MIN_PREDICTED_GAIN:
        order = longest_first
        details['policy'] = 'ESTIMATED_LONGEST_FIRST'
    details.update(task_order=order.copy(), estimate_unit=unit, task_estimates=costs,
                   fifo_finish_estimate=before, candidate_finish_estimate=after,
                   predicted_gain=gain)
    return order, details


class BoundedTasks:
    """Keep up to two unchanged tasks per worker submitted to the process pool.

    The runner may stop submissions and cancel futures that have not begun.
    Running jobs retain their cooperative stop path and result files. Never
    retry, combine, or edit task inputs here.
    """
    def __init__(self, pool, function, tasks, workers, order):
        if workers < 1:
            raise ValueError('workers must be positive')
        order = list(order)
        if sorted(order) != list(range(len(tasks))):
            raise ValueError('dispatch order must contain each task exactly once')
        self.pool = pool
        self.function = function
        self.tasks = tasks
        self.limit = min(len(tasks), _PENDING_PER_WORKER * workers)
        self._order = iter(order)
        self.pending = {}
        self.submitted = set()
        self.completed = set()
        self.cancelled = set()
        self.stopped = False
        self.high_water = 0

    def fill(self):
        if self.stopped:return
        while len(self.pending) < self.limit:
            index = next(self._order, None)
            if index is None:
                break
            future = self.pool.submit(self.function, self.tasks[index])
            self.pending[future] = index
            self.submitted.add(index)
        self.high_water = max(self.high_water, len(self.pending))

    def stop(self):
        """Leave executing futures alone; never submit more after this boundary."""
        self.stopped = True
        for future, index in list(self.pending.items()):
            if future.cancel():
                self.cancelled.add(index)
                del self.pending[future]

    def collect(self, done):
        rows = []
        for future in sorted(done, key=self.pending.__getitem__):
            index = self.pending[future]
            value = future.result()  # Preserve worker errors; never hide/retry them.
            del self.pending[future]
            self.completed.add(index)
            rows.append((index, value))
        return rows


class ProgressRows:
    """Read submitted jobs only; reuse a finished job's last readable JSON.

    Missing/locked/invalid progress files remain best-effort telemetry and are
    retried on the next poll, including when a finished job was unreadable.
    Worker output CSVs and result.json are never cached or changed here.
    """
    def __init__(self, tasks):
        self.paths = [Path(task['out']) / 'progress.json' for task in tasks]
        self.finished = {}

    def read(self, submitted, completed):
        rows = []
        for index in sorted(submitted):
            if index in self.finished:
                rows.append(self.finished[index])
                continue
            try:
                row = json.loads(self.paths[index].read_text('utf-8'))
                # Do not cache malformed/incomplete progress records permanently.
                if not isinstance(row, dict) or 'pid' not in row or 'percent' not in row:
                    continue
                if index in completed:
                    self.finished[index] = row
                rows.append(row)
            except (OSError, ValueError):
                pass
        return rows
