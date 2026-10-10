"""Normal UI close cancels jobs cooperatively and waits for saved results.

Every job, process identity and model worker is isolated. These tests never
read the user's configured warehouse or terminate actual trading processes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs, catalog, desktop_window, live_processes, runtime_lifecycle as lifecycle, unified_live
from lab.ai.model_runtime import RUNTIME, ModelRuntime
from common_ai import client as common_client


@pytest.fixture(autouse=True)
def isolated_close_gates(monkeypatch, tmp_path):
    project = tmp_path / 'isolated-project'
    client = Mock(spec=['shutdown', 'close'])
    factory = Mock(return_value=client)
    monkeypatch.setattr(catalog, 'ROOT', project / 'Part3')
    monkeypatch.setattr(common_client, 'Client', factory)
    monkeypatch.setattr(desktop_window, '_show_close_error', Mock())
    monkeypatch.setattr(desktop_window, '_show_close_warning', Mock())
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SESSION_CONTEXTS', {}, raising=False)
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {}, raising=False)
    monkeypatch.setattr(unified_live, '_closing', False)
    return SimpleNamespace(project=project, client=client, factory=factory)


def joined(closer):
    closer.thread.join(5)
    assert not closer.thread.is_alive(), 'isolated close worker did not finish'


def test_first_close_starts_saving_without_initial_question(monkeypatch):
    window = Mock()
    closer = desktop_window._EngineCloser(window)
    ask = Mock(side_effect=AssertionError('first close must start saving immediately'))
    shutdown = Mock(return_value={'ok': True, 'warnings': []})
    monkeypatch.setattr(desktop_window, '_confirm_force_close', ask)
    monkeypatch.setattr(lifecycle, 'shutdown', shutdown)

    assert closer.closing() is False
    joined(closer)
    assert closer.ready and closer.closing() is True
    ask.assert_not_called()
    shutdown.assert_called_once()
    window.destroy.assert_called_once()


def test_close_waits_for_save_and_repeated_x_no_does_not_duplicate(monkeypatch):
    window = Mock()
    closer = desktop_window._EngineCloser(window)
    entered, saved, release = (threading.Event() for _ in range(3))
    ask = Mock(return_value=False)
    monkeypatch.setattr(desktop_window, '_confirm_force_close', ask)

    def shutdown(**_kwargs):
        entered.set()
        assert release.wait(5)
        saved.set()
        return {'ok': True, 'warnings': []}

    operation = Mock(side_effect=shutdown)
    monkeypatch.setattr(lifecycle, 'shutdown', operation)
    window.destroy.side_effect = lambda: saved.is_set() or pytest.fail('window closed before saved result')
    try:
        assert closer.closing() is False
        assert entered.wait(5)
        assert closer.closing() is False
        closer.choice_thread.join(5)
        assert ask.call_count == 1
        operation.assert_called_once()
        window.destroy.assert_not_called()
        assert not closer.ready
    finally:
        release.set()
        joined(closer)
    assert closer.ready
    window.destroy.assert_called_once()


def test_idle_close_also_starts_without_confirmation(monkeypatch):
    closer = desktop_window._EngineCloser(Mock())
    ask = Mock(side_effect=AssertionError('first close never requires confirmation'))
    monkeypatch.setattr(desktop_window, '_confirm_force_close', ask)
    operation = Mock(return_value={'ok': True, 'warnings': []})
    monkeypatch.setattr(lifecycle, 'shutdown', operation)
    assert closer.closing() is False
    joined(closer)
    assert closer.ready
    ask.assert_not_called()
    operation.assert_called_once()


def test_waiting_blocks_new_jobs_until_normal_close_completes(monkeypatch):
    closer = desktop_window._EngineCloser(Mock())
    entered, release = threading.Event(), threading.Event()
    def shutdown(**kwargs):
        entered.set()
        assert release.wait(5)
        return {'ok': True}

    operation = Mock(side_effect=shutdown)
    monkeypatch.setattr(lifecycle, 'shutdown', operation)
    try:
        assert closer.closing() is False
        assert entered.wait(5)
        with pytest.raises((ValueError, RuntimeError), match='종료'):
            jobs.start({'kind': 'normal', 'scenario': {}})
        with pytest.raises((ValueError, RuntimeError), match='종료'):
            jobs.confirm('a' * 32)
    finally:
        release.set()
        joined(closer)
    assert jobs._closing and closer.ready
    operation.assert_called_once()


def test_close_timeout_keeps_window_and_exposes_error_for_retry(monkeypatch):
    closer = desktop_window._EngineCloser(Mock())
    monkeypatch.setattr(lifecycle, 'shutdown', Mock(side_effect=RuntimeError('백테스트 저장 확인 시간 초과')))
    report = Mock()
    monkeypatch.setattr(desktop_window, '_show_close_error', report)
    assert closer.closing() is False
    joined(closer)
    assert not closer.ready
    closer.window.destroy.assert_not_called()
    report.assert_called_once_with('백테스트 저장 확인 시간 초과')


def test_runtime_shutdown_waits_for_backtest_before_live_and_owned_ai_worker(monkeypatch, isolated_close_gates):
    calls = []
    monkeypatch.setattr(jobs, 'shutdown', lambda **_kwargs: calls.append('backtest-saved') or
                        {'ok': True, 'warnings': ['부분 결과 확인']})
    monkeypatch.setattr(unified_live, 'shutdown', lambda: calls.append('live-stopped') or
                        {'ok': True, 'warnings': ['라이브 기록 확인']})
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **_kwargs: calls.append('owned-ai-stopped'))
    isolated_close_gates.client.shutdown.side_effect = lambda **_kwargs: calls.append('common-ai-stopped')
    isolated_close_gates.client.close.side_effect = lambda: calls.append('common-client-closed')
    result = lifecycle.shutdown(timeout=1)
    assert calls == ['backtest-saved', 'live-stopped', 'common-ai-stopped',
                     'common-client-closed', 'owned-ai-stopped']
    isolated_close_gates.factory.assert_called_once_with(isolated_close_gates.project)
    isolated_close_gates.client.shutdown.assert_called_once_with(timeout=1)
    isolated_close_gates.client.close.assert_called_once_with()
    assert result['ok']
    assert set(result['warnings']) == {'부분 결과 확인', '라이브 기록 확인'}


def test_failed_backtest_shutdown_keeps_live_and_ai_available(monkeypatch, isolated_close_gates):
    monkeypatch.setattr(jobs, 'shutdown', Mock(side_effect=RuntimeError('job remains active')))
    live, model = Mock(), Mock()
    monkeypatch.setattr(unified_live, 'shutdown', live)
    monkeypatch.setattr(RUNTIME, 'shutdown', model)
    with pytest.raises(RuntimeError, match='job remains active'):
        lifecycle.shutdown(timeout=.01)
    live.assert_not_called()
    model.assert_not_called()
    isolated_close_gates.factory.assert_not_called()


def test_common_ai_shutdown_failure_closes_client_and_reopens_start_gates(monkeypatch, isolated_close_gates):
    monkeypatch.setattr(jobs, '_closing', True)
    monkeypatch.setattr(unified_live, '_closing', True)
    monkeypatch.setattr(jobs, 'shutdown', Mock(return_value={'ok': True, 'warnings': []}))
    monkeypatch.setattr(unified_live, 'shutdown', Mock(return_value={'ok': True, 'warnings': []}))
    model = Mock()
    monkeypatch.setattr(RUNTIME, 'shutdown', model)
    isolated_close_gates.client.shutdown.side_effect = RuntimeError('common AI remains active')
    with pytest.raises(RuntimeError, match='common AI remains active'):
        lifecycle.shutdown(timeout=.01)
    isolated_close_gates.client.close.assert_called_once_with()
    model.assert_not_called()
    assert not jobs._closing and not unified_live._closing


def test_model_timeout_after_live_stop_restores_live_start_gate(monkeypatch):
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(unified_live, '_closing', False)
    monkeypatch.setattr(jobs, 'shutdown', lambda **_kwargs: {'ok': True, 'warnings': []})
    monkeypatch.setattr(live_processes, 'stop_all_engines', lambda: {'ok': True, 'warnings': []})
    monkeypatch.setattr(RUNTIME, 'shutdown', Mock(side_effect=RuntimeError('AI 요청 종료 timeout')))
    with pytest.raises(RuntimeError, match='AI 요청 종료 timeout'):
        lifecycle.shutdown(timeout=.01)
    assert not unified_live._closing
    assert not jobs._closing


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'Part2'))
    context = {'warehouse': tmp_path / 'warehouse', 'project_root': tmp_path / 'project',
               'python_executable': sys.executable}
    context['project_root'].mkdir()
    alive = {}
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {}, raising=False)
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, handle=None:
                        None if pid not in alive else {'pid': pid, 'created': alive[pid]})
    no_launch = Mock(side_effect=AssertionError('shutdown tests must not launch a real process'))
    monkeypatch.setattr(jobs.subprocess, 'Popen', no_launch)
    return context, alive


def make_job(context, alive, index=1, *, kind='normal', phase='run', process=True):
    identifier = f'{index:032x}'
    folder = context['warehouse'] / 'runs' / identifier
    folder.mkdir(parents=True)
    pid = 1000 + index
    record = {'version': jobs.VERSION, 'id': identifier, 'kind': kind,
              'scenario': {'symbol': 'TEST', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR'},
              'scenario_file': 'web_scenario.json', 'adapter': {}, 'rebuild': False, 'auto_run': True,
              'phase': phase, 'created_at': time.time(), 'updated_at': time.time(),
              'cancel_requested': False, 'plan': {'approval_token': 'isolated-plan'} if phase == 'confirm' else None,
              'progress': None, 'message': '', 'supervisor': None, 'process': None,
              'returncode': None, 'result_path': 'runs/' + identifier + '/result.json'}
    if process:
        alive[pid] = str(pid * 100)
        record['supervisor'] = {'pid': pid, 'created': alive[pid], 'run_id': 'isolated'}
        if phase == 'run':
            record['process'] = {'pid': pid + 1000, 'created': str((pid + 1000) * 100), 'action': 'run'}
            alive[pid + 1000] = record['process']['created']
    jobs._atomic(folder / 'web_scenario.json', record['scenario'])
    jobs._write(folder, record)
    return folder


def finish_job(folder, alive, *, result=True):
    with jobs.job_lock(folder):
        record = jobs._read(folder)
        for identity in (record.get('supervisor'), record.get('process')):
            if identity:
                alive.pop(identity['pid'], None)
        if result:
            jobs._atomic(folder / 'result.json', {'status': 'CANCELLED', 'scenario': record['scenario']})
        record.update(phase='cancelled', supervisor=None, process=None)
        jobs._write(folder, record)


def test_active_jobs_covers_all_jobs_even_older_than_recent_twenty(isolated_jobs):
    context, alive = isolated_jobs
    expected = set()
    for index in range(1, 26):
        folder = make_job(context, alive, index, kind='generated' if index % 2 else 'normal')
        expected.add(folder.name)
    assert {job['id'] for job in jobs.active_jobs(**context)} == expected


def test_changed_warehouse_still_lists_jobs_started_in_previous_warehouse(isolated_jobs, monkeypatch):
    first, alive = isolated_jobs
    second = {**first, 'warehouse': first['warehouse'].parent / 'second-warehouse'}
    current = first

    def context(warehouse=None, project_root=None, python_executable=None):
        return (Path(warehouse or current['warehouse']).resolve(),
                Path(project_root or current['project_root']).resolve(),
                str(python_executable or current['python_executable']))

    def launch(folder, record, project, warehouse, executable, action):
        pid = 1000 + len(alive)
        alive[pid] = str(pid * 100)
        record['supervisor'] = {'pid': pid, 'created': alive[pid], 'run_id': 'isolated'}
        jobs._write(folder, record)

    monkeypatch.setattr(jobs, '_context', context)
    monkeypatch.setattr(jobs, '_launch', launch)
    a = jobs.start({'kind': 'normal', 'scenario': {'symbol': 'TEST'}, **first})
    current = second
    b = jobs.start({'kind': 'generated', 'scenario': {'symbol': 'TEST'}, **second})
    assert {item['id'] for item in jobs.active_jobs()} == {a['job_id'], b['job_id']}
    assert {item['id'] for item in jobs.active_jobs(**second)} == {b['job_id']}


def test_shutdown_cancels_pending_build_confirmation_without_launch(isolated_jobs):
    context, alive = isolated_jobs
    folder = make_job(context, alive, phase='confirm', process=False)
    result = jobs.shutdown(timeout=1, **context)
    assert result['ok']
    assert not jobs.active_jobs(**context)
    assert jobs._read(folder)['cancel_requested']
    assert jobs._read(folder)['phase'] == 'cancelled'
    jobs.subprocess.Popen.assert_not_called()


def test_shutdown_requests_all_normal_and_generated_jobs_before_waiting_for_save(isolated_jobs):
    context, alive = isolated_jobs
    folders = [make_job(context, alive, 1), make_job(context, alive, 2, kind='generated')]
    errors = []

    def writer():
        try:
            deadline = time.monotonic() + 5
            while not all((folder / 'stop.request').is_file() for folder in folders):
                assert time.monotonic() < deadline, 'not all jobs received stop'
                time.sleep(.01)
            for folder in folders:
                finish_job(folder, alive)
        except BaseException as exc:
            errors.append(exc)

    child = threading.Thread(target=writer)
    child.start()
    try:
        result = jobs.shutdown(timeout=3, **context)
    finally:
        child.join(5)
    assert not errors and not child.is_alive()
    assert result['ok'] and not jobs.active_jobs(**context)
    assert all(json.loads((folder / 'result.json').read_text('utf-8'))['status'] == 'CANCELLED' for folder in folders)
    assert all(jobs._read(folder)['cancel_requested'] for folder in folders)


def test_cancelled_run_without_saved_result_is_not_reported_as_safe_close(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    stop = jobs.stop

    def without_save(identifier, **kwargs):
        result = stop(identifier, **kwargs)
        finish_job(folder, alive, result=False)
        return result

    monkeypatch.setattr(jobs, 'stop', without_save)
    with pytest.raises(RuntimeError):
        jobs.shutdown(timeout=.05, **context)
    assert jobs._closing is False, 'failed close must allow a safe retry'


def test_repeated_close_cannot_bypass_a_previously_missing_partial_result(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    stop = jobs.stop

    def without_save(identifier, **kwargs):
        result = stop(identifier, **kwargs)
        if jobs._read(folder).get('process'):
            finish_job(folder, alive, result=False)
        return result

    monkeypatch.setattr(jobs, 'stop', without_save)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            jobs.shutdown(timeout=.03, **context)
        assert not jobs._closing
    jobs._atomic(folder / 'result.json', {'status': 'CANCELLED', 'scenario': jobs._read(folder)['scenario']})
    assert jobs.shutdown(timeout=1, **context)['ok']


def test_unknown_process_identity_times_out_without_force_kill(isolated_jobs, monkeypatch):
    context, alive = isolated_jobs
    folder = make_job(context, alive)
    def unknown(_pid, handle=None):
        raise OSError('isolated process query denied')
    monkeypatch.setattr(jobs, 'process_identity', unknown)
    force = Mock(side_effect=AssertionError('must preserve result saving'))
    monkeypatch.setattr(jobs.subprocess, 'run', force)
    with pytest.raises(RuntimeError):
        jobs.shutdown(timeout=.05, **context)
    assert (folder / 'stop.request').is_file()
    assert jobs._closing is True, 'pending shutdown must block new jobs until resolved or cancelled'
    force.assert_not_called()


def test_successful_close_blocks_new_start_and_confirm_without_launch(isolated_jobs):
    context, alive = isolated_jobs
    folder = make_job(context, alive, phase='confirm', process=False)
    jobs.shutdown(timeout=1, **context)
    assert jobs._closing is True
    with pytest.raises((ValueError, RuntimeError), match='종료'):
        jobs.start({'kind': 'normal', 'scenario': {}, **context})
    with pytest.raises((ValueError, RuntimeError), match='종료'):
        jobs.confirm(folder.name, **context)
    jobs.subprocess.Popen.assert_not_called()


def test_model_shutdown_waits_for_active_request_then_closes_owned_worker():
    runtime = ModelRuntime()
    runtime.lease = Mock()
    worker = Mock()
    runtime.worker = worker
    runtime.worker_key = 'isolated-model'
    runtime.lock.acquire()  # Simulate an inference already in progress.
    errors = []

    def close():
        try:
            runtime.shutdown(timeout=3)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=close)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while not runtime._closing:
            assert time.monotonic() < deadline
            time.sleep(.005)
        worker.close.assert_not_called()
        assert thread.is_alive()
    finally:
        runtime.lock.release()
        thread.join(5)
    assert not errors and not thread.is_alive()
    worker.close.assert_called_once()
    runtime.lease.release.assert_called_once()
    assert runtime.worker is None and runtime.worker_key is None


def test_model_shutdown_timeout_preserves_request_and_allows_retry():
    runtime = ModelRuntime()
    runtime.lease = Mock()
    worker = Mock()
    runtime.worker = worker
    runtime.lock.acquire()
    try:
        with pytest.raises(RuntimeError, match='AI 요청 종료'):
            runtime.shutdown(timeout=.01)
        assert runtime._closing is False
        worker.close.assert_not_called()
        runtime.lease.release.assert_not_called()
    finally:
        runtime.lock.release()
    runtime.shutdown(timeout=1)
    worker.close.assert_called_once()


def test_completed_model_shutdown_blocks_new_ai_without_loading_any_model():
    runtime = ModelRuntime()
    runtime.lease = Mock()
    runtime.shutdown(timeout=1)
    provider = SimpleNamespace(timeout=1, generation=runtime.generation)
    with pytest.raises(ValueError, match='프로그램 종료 중'):
        with runtime._turn(provider):
            pytest.fail('closed runtime entered a new inference')
    runtime.lease.acquire.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Windows owned-process FILETIME integration')
def test_desktop_close_stops_fake_part2_and_checks_saved_result_before_destroy(tmp_path, monkeypatch):
    """Run real supervisor processes against fake Part2, never user data/MT5."""
    from test_backtest_process67 import fake_project, wait_for, remember_owned, alive, terminate_owned

    project, warehouse = tmp_path / '이동한 가짜 프로젝트', tmp_path / '가짜 창고'
    fake_project(project)
    monkeypatch.setattr(catalog, 'ROOT', project / 'Part3')
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    monkeypatch.setattr(jobs, '_context', lambda warehouse=None, project_root=None, python_executable=None:
                        (Path(warehouse or context['warehouse']).resolve(),
                         Path(project_root or context['project_root']).resolve(),
                         str(python_executable or context['python_executable'])))
    monkeypatch.setattr(unified_live, 'shutdown', lambda: {'ok': True, 'warnings': []})
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **_kwargs: None)
    ask = Mock(side_effect=AssertionError('first close must not open a question'))
    monkeypatch.setattr(desktop_window, '_confirm_force_close', ask)
    monkeypatch.setattr(desktop_window, '_show_close_error', Mock())
    window = Mock()
    closer = desktop_window._EngineCloser(window)
    identities = {}
    verified = threading.Event()
    result = jobs.start({'kind': 'normal', 'scenario': {'symbol': 'FAKE', 'start': '2025-09-01',
                        'end': '2025-09-02', 'mode': 'BAR', 'strategies': ['FAKE']}, **context})
    identifier = result['job_id']
    folder = warehouse / 'runs' / identifier

    def remember_processes():
        # Match the production reader's lock: an unprotected fixture read can
        # prevent the supervisor's atomic job.json replacement on Windows.
        with jobs.job_lock(folder):
            remember_owned(folder, identities)

    try:
        def running():
            remember_processes()
            state = jobs.status(identifier, **context)
            assert state['phase'] != 'error', state.get('message')
            count = ((state.get('progress') or {}).get('replay') or {}).get('processed', 0)
            return state if state['phase'] == 'run' and state['active'] and count > 0 else None

        wait_for(running, 'isolated fake Part2 did not enter replay')

        def destroy_after_save():
            saved = json.loads((folder / 'result.json').read_text('utf-8'))
            assert saved['status'] == 'CANCELLED' and saved['fake_processed'] > 0
            assert not jobs.status(identifier, **context)['active']
            assert all(not alive(row) for row in identities.values())
            verified.set()

        window.destroy.side_effect = destroy_after_save
        assert closer.closing() is False
        joined(closer)
        assert closer.ready
        assert verified.is_set(), 'window destruction happened before verified safe shutdown'
        ask.assert_not_called()
        window.destroy.assert_called_once()
        desktop_window._show_close_error.assert_not_called()
        assert (folder / 'stop.request').is_file()
    finally:
        # Only children recorded in this test's new temporary folder are owned.
        remember_processes()
        (folder / 'stop.request').write_text('STOP\n', encoding='ascii')
        deadline = time.monotonic() + 3
        while any(alive(row) for row in identities.values()) and time.monotonic() < deadline:
            time.sleep(.05)
        for row in identities.values():
            if alive(row):
                terminate_owned(row)
