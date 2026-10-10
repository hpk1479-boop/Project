"""167: a backtest runs below normal only while LIVE runs on the PC, at normal otherwise.

The LIVE engine holds a named mark for its lifetime; Windows drops it when the process ends, even
when it is killed. A backtest checks the mark with one system call at most every two seconds and
gives itself and its existing workers the class that applies.
"""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
sys.path[:0] = [str(PROGRAM), str(ROOT / 'Part2')]
import process_priority as p


class Kernel:
    """Records calls; OpenMutexW answers whether LIVE holds its mark."""
    def __init__(self, live=False):
        self.live = live; self.calls = []
    def GetCurrentProcess(self):
        return 'self'
    def SetPriorityClass(self, process, value):
        self.calls.append(('priority', process, value)); return 1
    def SetProcessInformation(self, process, kind, data, size):
        return 1
    def OpenMutexW(self, access, inherit, name):
        assert name == p.LIVE_MARK
        return 'mark' if self.live else None
    def OpenProcess(self, access, inherit, pid):
        return 'pid%d' % pid
    def CloseHandle(self, handle):
        self.calls.append(('close', handle)); return 1


def test_backtest_class_follows_live():
    assert p.backtest_priority(Kernel(live=True)) == {'priority': True, 'throttling_off': True}
    kernel = Kernel(live=True); p.backtest_priority(kernel)
    assert ('priority', 'self', p.BELOW_NORMAL) in kernel.calls and ('close', 'mark') in kernel.calls
    kernel = Kernel(live=False); p.backtest_priority(kernel)
    assert ('priority', 'self', p.NORMAL) in kernel.calls


def test_keeper_changes_itself_and_workers_only_when_live_changes():
    clock = [0.0]; kernel = Kernel(live=False)
    keeper = p.BacktestPriority(2.0, kernel=kernel, clock=lambda: clock[0])
    assert keeper.update([11, 12]) is False
    assert [c for c in kernel.calls if c[0] == 'priority'] == [
        ('priority', 'self', p.NORMAL), ('priority', 'pid11', p.NORMAL), ('priority', 'pid12', p.NORMAL)]
    kernel.calls.clear(); kernel.live = True
    clock[0] = 1.0; keeper.update([11, 12])                  # within the interval: not checked
    assert not kernel.calls
    clock[0] = 2.0; assert keeper.update([11, 12, 13]) is True
    assert [c for c in kernel.calls if c[0] == 'priority'] == [
        ('priority', 'self', p.BELOW_NORMAL), ('priority', 'pid11', p.BELOW_NORMAL),
        ('priority', 'pid12', p.BELOW_NORMAL), ('priority', 'pid13', p.BELOW_NORMAL)]
    kernel.calls.clear(); clock[0] = 4.0; keeper.update([11])   # unchanged: only the check
    assert [c for c in kernel.calls if c[0] == 'priority'] == []


def test_other_systems_change_nothing(monkeypatch):
    monkeypatch.setattr(p.os, 'name', 'posix')
    assert p.mark_live_running() is None and p.live_running() is False
    assert p.backtest_priority() is None
    p.set_priority_of([1], p.NORMAL)


HOLDER = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import process_priority
print(process_priority.mark_live_running(), flush=True)
time.sleep(60)
"""


@pytest.mark.skipif(os.name != 'nt', reason='Windows named mark')
def test_real_mark_is_seen_and_disappears_when_the_live_process_is_killed():
    if p.live_running():
        pytest.skip('a real LIVE engine runs on this PC')
    holder = subprocess.Popen([sys.executable, '-c', HOLDER, str(PROGRAM)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'True'
        assert p.live_running() is True
        assert p.backtest_class() == p.BELOW_NORMAL
    finally:
        holder.kill(); holder.wait(timeout=30)
    deadline = time.monotonic() + 10
    while p.live_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert p.live_running() is False and p.backtest_class() == p.NORMAL


def worker_class(_=None):
    import ctypes
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    return k.GetPriorityClass(k.GetCurrentProcess())


@pytest.mark.skipif(os.name != 'nt', reason='Windows priority classes')
def test_real_workers_follow_live_starting_and_stopping(monkeypatch):
    from concurrent.futures import ProcessPoolExecutor
    from event_backtest import runner
    from event_backtest.worker_schedule import worker_pids
    live = [False]
    monkeypatch.setattr(p, 'live_running', lambda kernel=None: live[0])
    keeper = p.BacktestPriority(0.0)
    try:
        with ProcessPoolExecutor(2, initializer=runner._worker_started) as pool:
            assert set(pool.map(worker_class, range(2))) <= {p.NORMAL, p.BELOW_NORMAL, p.ABOVE_NORMAL, 0x80}
            keeper.update(worker_pids(pool))
            assert set(pool.map(worker_class, range(4))) == {p.NORMAL}
            live[0] = True; keeper.update(worker_pids(pool))
            assert set(pool.map(worker_class, range(4))) == {p.BELOW_NORMAL}
            live[0] = False; keeper.update(worker_pids(pool))
            assert set(pool.map(worker_class, range(4))) == {p.NORMAL}
    finally:
        p._set(None, p.NORMAL)
