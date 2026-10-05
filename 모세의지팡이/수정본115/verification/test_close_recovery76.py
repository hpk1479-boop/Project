"""Shutdown recovery uses isolated jobs and children, never user MT5 or data."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs, desktop_window, runtime_lifecycle as lifecycle, unified_live
from lab.ai.model_runtime import RUNTIME
from common_ai.client import Client
from test_close_runtime72 import isolated_jobs as seed_jobs, make_job, finish_job

NATIVE_IDENTITY = jobs.process_identity
NATIVE_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SESSION_CONTEXTS', {})
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {})
    monkeypatch.setattr(unified_live, '_closing', False)
    monkeypatch.setattr(unified_live, 'shutdown', lambda: {'ok': True})
    monkeypatch.setattr(Client, 'shutdown', lambda *a, **k: None)
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **k: None)
    def no_user_context(*args, **kwargs):
        raise AssertionError('test must supply an isolated warehouse context')
    monkeypatch.setattr(jobs, '_context', no_user_context)


@pytest.fixture
def isolated_jobs(seed_jobs, monkeypatch):
    context, alive = seed_jobs
    monkeypatch.setattr(jobs, '_context', lambda warehouse=None, project_root=None, python_executable=None:
                        (Path(warehouse or context['warehouse']).resolve(),
                         Path(project_root or context['project_root']).resolve(),
                         str(python_executable or context['python_executable'])))
    return context, alive


def denied_api(*args):
    ctypes.set_last_error(5)
    return 0


def kernel_proxy(monkeypatch, **overrides):
    native = ctypes.WinDLL('kernel32', use_last_error=True)

    class Proxy:
        def __getattr__(self, key):
            return getattr(native, key)

    proxy = Proxy()
    for name, function in overrides.items():
        setattr(proxy, name, Mock(side_effect=function))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: proxy)
    return proxy


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_native_process_list_finds_self_and_excludes_exited_child():
    assert jobs._windows_pid_present(os.getpid())
    with NATIVE_POPEN([sys.executable, '-B', '-c', 'pass']) as child:
        child.wait(timeout=5)
        assert not jobs._windows_pid_present(child.pid)


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_exited_process_query_denied_is_confirmed_absent(monkeypatch):
    with NATIVE_POPEN([sys.executable, '-B', '-c', 'pass']) as child:
        child.wait(timeout=5)
        kernel_proxy(monkeypatch, OpenProcess=denied_api)
        assert jobs._alive({'pid': child.pid, 'created': 'old'}) is False


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_running_process_query_denied_stays_unknown(monkeypatch):
    kernel_proxy(monkeypatch, OpenProcess=denied_api)
    assert jobs._alive({'pid': os.getpid(), 'created': 'unknown'}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_failed_snapshot_does_not_report_exit(monkeypatch):
    def no_snapshot(*args):
        ctypes.set_last_error(5)
        return ctypes.c_void_p(-1).value

    kernel_proxy(monkeypatch, OpenProcess=denied_api, CreateToolhelp32Snapshot=no_snapshot)
    assert jobs._alive({'pid': os.getpid(), 'created': 'unknown'}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_incomplete_snapshot_does_not_report_exit_and_releases_handle(monkeypatch):
    proxy = kernel_proxy(monkeypatch, Process32NextW=denied_api)
    close = proxy.CloseHandle = Mock(wraps=proxy.CloseHandle)
    with pytest.raises(OSError, match='조회가 중단'):
        jobs._windows_pid_present(0xffffffff)
    close.assert_called_once()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process identity')
def test_failed_wait_does_not_report_process_exit(monkeypatch):
    def failed_wait(*args):
        ctypes.set_last_error(5)
        return 0xffffffff

    kernel_proxy(monkeypatch, WaitForSingleObject=failed_wait)
    assert jobs._alive({'pid': os.getpid(), 'created': 'unknown'}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process identity')
def test_failed_creation_time_does_not_report_process_exit(monkeypatch):
    kernel_proxy(monkeypatch, GetProcessTimes=denied_api)
    assert jobs._alive({'pid': os.getpid(), 'created': 'unknown'}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process enumeration')
def test_saved_cancelled_job_with_dead_supervisor_no_longer_blocks_close(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    finish_job(folder, alive)
    with NATIVE_POPEN([sys.executable, '-B', '-c', 'pass']) as child:
        child.wait(timeout=5)
        record = jobs._read(folder)
        record.update(supervisor={'pid': child.pid, 'created': 'old', 'run_id': 'isolated'},
                      run_started=True)
        jobs._write(folder, record)
        monkeypatch.setattr(jobs, 'process_identity', NATIVE_IDENTITY)
        kernel_proxy(monkeypatch, OpenProcess=denied_api)
        assert not jobs.active_jobs(**context)
        assert jobs.shutdown(timeout=.1, **context)['ok']
        assert json.loads((folder / 'result.json').read_text('utf-8'))['status'] == 'CANCELLED'


@pytest.mark.parametrize('unknown', [False, True])
def test_saved_result_cannot_hide_remaining_child(isolated_jobs, monkeypatch, unknown):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    record = jobs._read(folder)
    alive.pop(record['supervisor']['pid'])
    record.update(phase='cancelled', run_started=True)
    jobs._write(folder, record)
    jobs._atomic(folder / 'result.json', {'status': 'CANCELLED'})
    if unknown:
        def identity(pid, handle=None):
            if pid in alive:
                raise PermissionError('isolated child cannot be queried')
            return None
        monkeypatch.setattr(jobs, 'process_identity', identity)
    with pytest.raises(jobs.ShutdownPending):
        jobs.shutdown(timeout=0, **context)
    assert jobs._closing and jobs._SHUTDOWN_PENDING


def test_pending_runtime_keeps_start_gate_closed_and_other_engines_alive(monkeypatch):
    jobs.begin_shutdown()
    monkeypatch.setattr(jobs, 'shutdown', Mock(side_effect=jobs.ShutdownPending('재확인')))
    cancel = Mock()
    live = Mock()
    monkeypatch.setattr(lifecycle, 'cancel_shutdown', cancel)
    monkeypatch.setattr(unified_live, 'shutdown', live)
    with pytest.raises(jobs.ShutdownPending):
        lifecycle.shutdown(timeout=.01)
    assert jobs._closing
    cancel.assert_not_called()
    live.assert_not_called()


def test_desktop_auto_closes_after_late_save_with_one_shutdown_worker(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    original_shutdown = jobs.shutdown
    monkeypatch.setattr(jobs, 'shutdown', lambda **kwargs: original_shutdown(timeout=.02))
    ask, error = Mock(return_value=False), Mock()
    monkeypatch.setattr(desktop_window, '_confirm_force_close', ask)
    monkeypatch.setattr(desktop_window, '_show_close_error', error)
    window = Mock()
    closer = desktop_window._EngineCloser(window)
    pending = threading.Event()
    messages = []

    def notify(message, **kwargs):
        messages.append(message)
        if '자동으로 창을 닫습니다' in message:
            pending.set()
    monkeypatch.setattr(closer, '_notify', notify)

    def verify_saved_before_close():
        assert json.loads((folder / 'result.json').read_text('utf-8'))['status'] == 'CANCELLED'
        assert not jobs.active_jobs(**context)
    window.destroy.side_effect = verify_saved_before_close
    try:
        assert closer.closing() is False
        assert pending.wait(5), 'pending close did not switch to automatic recheck'
        assert jobs._closing and not closer.ready
        assert closer.closing() is False
        closer.choice_thread.join(5)
        assert ask.call_count == 1
        error.assert_not_called()
        window.destroy.assert_not_called()
        with pytest.raises(ValueError, match='종료'):
            jobs.start({'kind': 'normal', 'scenario': {}})
    finally:
        finish_job(folder, alive)
        closer.thread.join(5)
    assert not closer.thread.is_alive() and closer.ready
    window.destroy.assert_called_once()
    assert ask.call_count == 1
    error.assert_not_called()
    assert jobs._closing and not jobs._SHUTDOWN_PENDING


def test_unknown_identity_recovers_without_extra_x_or_error(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    original_shutdown = jobs.shutdown
    monkeypatch.setattr(jobs, 'shutdown', lambda **kwargs: original_shutdown(timeout=.02))
    known_identity = jobs.process_identity
    recovered = threading.Event()
    unknown_seen = threading.Event()
    def identity(pid, handle=None):
        if not recovered.is_set():
            raise OSError('isolated identity query unavailable')
        return known_identity(pid, handle)
    monkeypatch.setattr(jobs, 'process_identity', identity)
    monkeypatch.setattr(jobs, '_windows_identity_fallback', lambda _pid: (_ for _ in ()).throw(OSError('isolated snapshot unavailable')))
    monkeypatch.setattr(desktop_window, '_confirm_force_close', Mock(return_value=False))
    error = Mock()
    monkeypatch.setattr(desktop_window, '_show_close_error', error)
    closer = desktop_window._EngineCloser(Mock())
    monkeypatch.setattr(closer, '_notify', lambda message, **kwargs:
                        unknown_seen.set() if '프로세스 상태를 재확인' in message else None)
    try:
        assert closer.closing() is False
        assert unknown_seen.wait(5)
        assert not closer.ready
        error.assert_not_called()
    finally:
        finish_job(folder, alive)
        recovered.set()
        closer.thread.join(5)
    assert closer.ready and not closer.thread.is_alive()
    closer.window.destroy.assert_called_once()
    error.assert_not_called()
