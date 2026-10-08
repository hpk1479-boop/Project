"""수정본133: 같은 PC에서 라이브가 먼저 CPU를 쓰고, 오래 밀리면 화면과 로그에 보인다.

- 라이브 엔진은 '보통 초과' 우선순위와 절전 제한 해제, 백테스트는 '보통 미만'(작업자도 물려받음).
- 엔진 입력이 10초 넘게 기다리면 EVENT를 오류로 보이고, 60초마다 다시 기록하고, 풀리면 해소를 기록.
- 밀림 알림은 기록만 한다: 엔진에 들어가는 입력과 판단은 그대로.
"""
from pathlib import Path
import logging
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
sys.path.insert(0, str(PROGRAM))


class FakeKernel:
    def __init__(self, fail=False):
        self.calls = []; self.fail = fail
    def GetCurrentProcess(self):
        return 'process'
    def SetPriorityClass(self, process, value):
        if self.fail: raise OSError('denied')
        self.calls.append(('priority', process, value)); return 1
    def SetProcessInformation(self, process, kind, data, size):
        self.calls.append(('information', process, kind, size)); return 1


def test_live_goes_above_normal_backtest_below_normal_and_neither_is_throttled():
    import process_priority as p
    for function, priority in ((p.prefer_live, p.ABOVE_NORMAL), (p.yield_to_live, p.BELOW_NORMAL)):
        kernel = FakeKernel()
        assert function(kernel) == {'priority': True, 'throttling_off': True}
        assert kernel.calls == [('priority', 'process', priority), ('information', 'process', 4, 12)]
    kernel = FakeKernel()                                  # 백테스트 작업자: 물려받은 우선순위 그대로
    assert p.full_speed(kernel) == {'throttling_off': True}
    assert kernel.calls == [('information', 'process', 4, 12)]


def test_refused_call_never_stops_live_or_backtest(monkeypatch):
    import process_priority as p
    assert p.prefer_live(FakeKernel(fail=True)) == {'priority': False, 'throttling_off': False}
    assert p.yield_to_live(FakeKernel(fail=True)) == {'priority': False, 'throttling_off': False}
    monkeypatch.setattr(p.os, 'name', 'posix')
    assert p.prefer_live() is None and p.yield_to_live() is None and p.full_speed() is None


def worker_state():
    """Run inside a pool worker: (priority class, throttling control mask, throttling state mask)."""
    import ctypes
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.GetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    state = (ctypes.c_ulong * 3)(1, 0, 0)
    k.GetProcessInformation(k.GetCurrentProcess(), 4, state, 12)
    return k.GetPriorityClass(k.GetCurrentProcess()), state[1], state[2]


@pytest.mark.skipif(os.name != 'nt', reason='Windows 절전 설정')
def test_backtest_worker_process_runs_without_efficiency_throttling():
    sys.path.insert(0, str(ROOT / 'Part2'))
    from concurrent.futures import ProcessPoolExecutor
    from event_backtest import runner
    with ProcessPoolExecutor(1, initializer=runner._worker_started) as pool:
        priority, control, state = pool.submit(worker_state).result(timeout=120)
    assert (control, state) == (1, 0)                      # 실행 속도 직접 지정: 절전 안 함
    with ProcessPoolExecutor(1) as pool:                   # 비교: 초기화 없이 만든 작업자
        assert pool.submit(worker_state).result(timeout=120)[1] == 0


@pytest.mark.skipif(os.name != 'nt', reason='Windows 우선순위')
@pytest.mark.parametrize('function,expected', [('prefer_live', 0x8000), ('yield_to_live', 0x4000)])
def test_real_windows_priority_and_worker_inheritance(function, expected):
    code = f"""
import ctypes, subprocess, sys
sys.path.insert(0, sys.argv[1])
import process_priority
process_priority.{function}()
k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p
k.GetPriorityClass.argtypes = [ctypes.c_void_p]
child = subprocess.run([sys.executable, '-c', "import ctypes;k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p;k.GetPriorityClass.argtypes=[ctypes.c_void_p];print(k.GetPriorityClass(k.GetCurrentProcess()))"],
                       capture_output=True, text=True)
print(k.GetPriorityClass(k.GetCurrentProcess()), child.stdout.strip())
"""
    done = subprocess.run([sys.executable, '-c', code, str(PROGRAM)], capture_output=True, text=True, timeout=60)
    own, child = map(int, done.stdout.split())
    assert own == expected
    if function == 'yield_to_live':
        assert child == expected        # 작업자 프로세스도 '보통 미만'


def test_backtest_entry_yields_before_any_work(monkeypatch, tmp_path):
    # 수정본167: the class follows LIVE (process_priority.backtest_priority); still chosen before any work.
    sys.path.insert(0, str(ROOT / 'Part2'))
    import process_priority
    from event_backtest import workflow
    order = []
    monkeypatch.setattr(process_priority, 'backtest_priority', lambda: order.append('yield') or {'priority': True})
    def stop(*a, **k):
        order.append('work'); raise RuntimeError('stop here')
    monkeypatch.setattr(workflow, 'cleanup_warehouse', stop)
    with pytest.raises(RuntimeError):
        workflow.execute({}, tmp_path)
    assert order == ['yield', 'work']


def test_long_delay_is_shown_repeated_and_cleared(tmp_path):
    from module_diagnostics import Diagnostics
    from module_status_state import display_state
    clock = [1000.]
    sink = Diagnostics(tmp_path, clock=lambda: clock[0])
    def state():
        data = sink.snapshot('ENGINE'); data['pipe_connected'] = True
        return display_state('ENGINE', data), [line for _, line in data['lines'] if '지연' in line]
    sink.engine_delay(1.5)                                   # 짧은 밀림은 평소대로
    assert state() == ('연결중', [])
    sink.engine_delay(12.4)
    shown, lines = state()
    assert shown == '오류' and len(lines) == 1 and '12초 밀림' in lines[0]
    clock[0] += 30; sink.engine_delay(40)
    assert len(state()[1]) == 1                              # 60초 안에는 다시 쓰지 않음
    clock[0] += 31; sink.engine_delay(70)
    shown, lines = state()
    assert shown == '오류' and len(lines) == 2 and '70초 밀림' in lines[1]
    sink.engine_delay(0)
    shown, lines = state()
    assert shown == '연결중' and lines[-1].endswith('엔진 처리 지연 해소')
    log = (tmp_path / 'logs/module_errors/ENGINE.log').read_text('utf-8')
    assert log.count('밀림') == 2 and '해소' in log
    sink.close()


def test_engine_reports_wait_and_empty_queue_without_changing_inputs(tmp_path):
    from module_diagnostics import Diagnostics
    from event_engine.metrics import measured_time
    from test_event_e1 import new_engine, market, Probe
    probe = Probe(); engine = new_engine(probe)
    engine.diagnostics = Diagnostics(tmp_path)
    seen = []
    original = engine.diagnostics.engine_delay
    engine.diagnostics.engine_delay = lambda seconds: (seen.append(seconds), original(seconds))
    item = market(1000)
    engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq, source_time=item.source_time,
                        payload=item.payload, engine_time=measured_time() - 15 * 10**9)
    engine.run()
    from event_engine import Kind
    assert seen[0] >= 15 and seen[-1] == 0                  # 기다린 시간, 그다음 빈 대기열
    assert [row[0] for row in probe.seen].count(Kind.MARKET_BUNDLE) == 1   # 입력은 그대로 한 번 처리
    engine.diagnostics.close()
