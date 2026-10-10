"""Saved shutdown, global restart confirmation and genuine startup readiness."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Part1' / 'program'))
from Part1 import engine_processes as core


def instance(pid=101, created='100', root=None):
    root = Path(root or ROOT / 'Part1')
    return {'pid': pid, 'created': created, 'root': root, 'script': root / 'program' / 'event_host.py'}


@contextmanager
def active_process(*args):
    yield True


@pytest.fixture
def lifecycle(monkeypatch):
    api = SimpleNamespace(read_status=Mock(return_value=None), request_stop=Mock(return_value=False))
    monkeypatch.setattr(core, 'lifecycle', lambda: api)
    monkeypatch.setattr(core, '_hold_process', active_process)
    return api


@pytest.fixture
def clock(monkeypatch):
    value = [0.0]
    monkeypatch.setattr(core.time, 'monotonic', lambda: value[0])
    monkeypatch.setattr(core.time, 'sleep', lambda seconds: value.__setitem__(0, value[0] + seconds))
    return value


@pytest.mark.parametrize('arguments,expected', [
    (['-B', '-X', 'pycache_prefix=cache', r'D:\이동 폴더\수정본66\Part1\program\event_host.py'], True),
    (['-Wignore', '--', r'E:\copy\Part1\program\event_host.py'], True),
    (['-c', "print('C:/copy/Part1/program/event_host.py')"], False),
    (['-m', 'unittest', r'C:\copy\Part1\program\event_host.py'], False),
    ([r'C:\other.py', r'C:\copy\Part1\program\event_host.py'], False),
    ([r'C:\copy\Part2\program\event_host.py'], False),
    (['Part1/program/event_host.py'], False),
    (['-X', r'C:\copy\Part1\program\event_host.py'], False),
])
def test_query_matches_only_the_executed_live_engine_script(arguments, expected):
    assert core._is_engine(subprocess.list2cmdline([sys.executable, *arguments])) is expected


def test_global_query_includes_other_copies_without_substring_matches(monkeypatch):
    def command(script):
        return subprocess.list2cmdline([sys.executable, '-B', script])
    rows = [{'ProcessId': 101, 'Created': '100', 'CommandLine': command(r'D:\copyA\Part1\program\event_host.py')},
            {'ProcessId': 202, 'Created': '200', 'CommandLine': command(r'E:\copyB\Part1\program\event_host.py')},
            {'ProcessId': 303, 'Created': '300', 'CommandLine': command(r'E:\other.py') +
             ' E:\\copyB\\Part1\\program\\event_host.py'}]
    monkeypatch.setattr(core.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(rows))))
    found = core.find_engine_instances()
    assert [(row['pid'], row['created']) for row in found] == [(101, '100'), (202, '200')]


@pytest.mark.parametrize('response', [
    SimpleNamespace(returncode=1, stdout=''),
    SimpleNamespace(returncode=0, stdout='bad JSON'),
    SimpleNamespace(returncode=0, stdout='{"bad":"record"}'),
    SimpleNamespace(returncode=0, stdout='[{"ProcessId":101,"Created":"100","CommandLine":null}]'),
    OSError('denied'), subprocess.TimeoutExpired('powershell', 10),
])
def test_query_failure_blocks_start_and_shutdown(monkeypatch, response):
    run = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(core.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='조회에 실패'):
        core.find_engine_instances()


def test_public_list_is_safe_after_relocation(lifecycle, tmp_path):
    root = tmp_path / '이동한 수정본' / 'Part1'
    lifecycle.read_status.return_value = {'state': 'ready', 'run_id': 'nonce', 'pipe_connected': False}
    output = core.public_instances([instance(root=root)], root)
    assert output == [{'pid': 101, 'created': '100', 'copy_name': '이동한 수정본',
                       'current_copy': True, 'state': 'ready', 'pipe_connected': False,
                       'supports_shutdown': True}]
    assert str(tmp_path) not in json.dumps(output)
    lifecycle.read_status.assert_called_once_with(root, 101, created='100', run_id=None)


def test_running_engine_requires_explicit_confirmation(lifecycle):
    with pytest.raises(core.EngineConflictError) as caught:
        core.confirm_restart([instance()], ROOT / 'Part1')
    assert caught.value.engines[0]['pid'] == 101
    assert '다시 시작하시겠습니까' in str(caught.value)


@pytest.mark.parametrize('actual,expected', [
    ([instance(101, '100'), instance(202, '200')], [{'pid': 101, 'created': '100'}]),
    ([instance(101, '200')], [{'pid': 101, 'created': '100'}]),
    ([], [{'pid': 101, 'created': '100'}]),
])
def test_changed_identity_needs_new_confirmation(lifecycle, actual, expected):
    with pytest.raises(core.EngineConflictError, match='목록이 변경'):
        core.confirm_restart(actual, ROOT / 'Part1', restart=True, expected_engines=expected)


def test_confirmation_matches_cim_precision_and_order(lifecycle):
    core.confirm_restart([instance(202, '200'), instance(101, '100')], ROOT / 'Part1',
                         restart=True, expected_engines=[{'pid': 101, 'created': '109'},
                                                         {'pid': 202, 'created': '209'}])


@pytest.mark.parametrize('expected', [None, {}, [{'pid': True, 'created': '100'}],
                                      [{'pid': 101, 'created': True}],
                                      [{'pid': 101, 'created': 'no'}],
                                      [{'pid': 101, 'created': '100'}, {'pid': 101, 'created': '100'}]])
def test_invalid_confirmation_is_never_used(lifecycle, expected):
    with pytest.raises(ValueError):
        core.confirm_restart([instance()], ROOT / 'Part1', restart=True, expected_engines=expected)


def test_all_requests_precede_wait_and_saves_finish_without_force(monkeypatch, lifecycle, clock):
    rows = [instance(101, '100'), instance(202, '200')]
    active = {101: True, 202: True}
    order = []
    def request(root, pid, created, run_id=None):
        order.append(('request', pid))
        return True
    def running(pid, created):
        assert order[:2] == [('request', 101), ('request', 202)]
        order.append(('wait', pid))
        active[pid] = False
        return False
    lifecycle.request_stop.side_effect = request
    lifecycle.read_status.side_effect = lambda root, pid, **kwargs: {
        'run_id': f'nonce{pid}', 'state_saved': not active[pid]}
    monkeypatch.setattr(core, 'process_active', running)
    kill = Mock(side_effect=AssertionError('a normal stop must never force'))
    monkeypatch.setattr(core.subprocess, 'run', kill)
    result = core.stop_engine_instances(rows, timeout=45)
    assert result['ok'] is True and result['saved_pids'] == [101, 202]
    assert '저장 완료 2개' in result['message']
    assert result['forced_pids'] == [] and result['warnings'] == []
    assert clock[0] == 0
    kill.assert_not_called()


def test_timeout_is_shared_and_force_follows_deadline(monkeypatch, lifecycle, clock):
    alive = {101: True, 202: True}
    monkeypatch.setattr(core, 'process_active', lambda pid, created: alive[pid])
    def force(command, **kwargs):
        assert clock[0] >= 0.5
        alive[int(command[2])] = False
        return SimpleNamespace(returncode=0)
    kill = Mock(side_effect=force)
    monkeypatch.setattr(core.subprocess, 'run', kill)
    result = core.stop_engine_instances([instance(101, '100'), instance(202, '200')], timeout=0.5)
    assert result['ok'] is True and result['forced_pids'] == [101, 202]
    assert len(result['warnings']) == 2 and '저장' in result['warnings'][0]
    assert 0.5 <= clock[0] < 0.6
    assert [call.args[0] for call in kill.call_args_list] == [
        ['taskkill', '/PID', '101', '/T', '/F'], ['taskkill', '/PID', '202', '/T', '/F']]


def test_managed_exit_without_saved_ack_is_failure(monkeypatch, lifecycle):
    lifecycle.read_status.return_value = {'run_id': 'nonce', 'state_saved': False, 'state': 'failed'}
    lifecycle.request_stop.return_value = True
    monkeypatch.setattr(core, 'process_active', lambda *args: False)
    monkeypatch.setattr(core.subprocess, 'run', Mock(side_effect=AssertionError('already exited')))
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is False and '저장 완료' in result['message']


def test_legacy_natural_exit_reports_that_save_cannot_be_verified(monkeypatch, lifecycle):
    monkeypatch.setattr(core, 'process_active', lambda *args: False)
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is True and result['saved_pids'] == []
    assert len(result['warnings']) == 1 and '저장 완료를 확인할 수 없습니다' in result['message']


def test_graceful_exit_racing_failed_taskkill_is_saved_not_forced(monkeypatch, lifecycle, clock):
    lifecycle.read_status.side_effect = [{'run_id': 'nonce', 'state': 'stopping'},
                                        {'run_id': 'nonce', 'state_saved': True}]
    lifecycle.request_stop.return_value = True
    active = iter([True, True, False])
    monkeypatch.setattr(core, 'process_active', lambda *args: next(active))
    monkeypatch.setattr(core.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=1)))
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is True and result['saved_pids'] == [101]
    assert result['forced_pids'] == [] and result['warnings'] == []


def test_stop_request_write_failure_waits_then_forces(monkeypatch, lifecycle, clock):
    alive = [True]
    lifecycle.read_status.return_value = {'run_id': 'original nonce', 'state_saved': False}
    lifecycle.request_stop.side_effect = OSError('disk read-only')
    monkeypatch.setattr(core, 'process_active', lambda *args: alive[0])
    def force(*args, **kwargs):
        assert clock[0] >= 0.2
        alive[0] = False
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(core.subprocess, 'run', Mock(side_effect=force))
    result = core.stop_engine_instances([instance()], timeout=0.2)
    assert result['ok'] is True and result['forced_pids'] == [101]
    assert len(result['warnings']) == 2
    assert '정상 종료 요청' in result['warnings'][0]


def test_saved_ack_is_checked_against_original_launch_nonce(monkeypatch, lifecycle):
    lifecycle.read_status.side_effect = [{'run_id': 'original nonce', 'state_saved': False},
                                        {'run_id': 'original nonce', 'state_saved': True}]
    lifecycle.request_stop.return_value = True
    monkeypatch.setattr(core, 'process_active', lambda *args: False)
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['saved_pids'] == [101]
    assert lifecycle.read_status.call_args.kwargs['run_id'] == 'original nonce'


def test_rejected_stop_file_for_running_managed_engine_is_visible(monkeypatch, lifecycle, clock):
    lifecycle.read_status.return_value = {'run_id': 'nonce', 'state': 'ready', 'state_saved': False}
    lifecycle.request_stop.return_value = False
    alive = [True]
    monkeypatch.setattr(core, 'process_active', lambda *args: alive[0])
    monkeypatch.setattr(core.subprocess, 'run', lambda *args, **kwargs:
                        alive.__setitem__(0, False) or SimpleNamespace(returncode=0))
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is True and result['forced_pids'] == [101]
    assert '정상 종료 요청' in result['warnings'][0]


def test_read_failure_after_exit_does_not_claim_pid_is_still_running(monkeypatch, lifecycle):
    lifecycle.read_status.side_effect = [{'run_id': 'nonce', 'state': 'ready'}, OSError('unreadable saved status')]
    lifecycle.request_stop.return_value = True
    monkeypatch.setattr(core, 'process_active', lambda *args: False)
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is False and result['remaining_pids'] == []
    assert '기록을 읽지 못했습니다' in result['message']


def test_pid_reuse_or_exit_is_never_terminated(monkeypatch, lifecycle):
    @contextmanager
    def reused(*args):
        yield False
    monkeypatch.setattr(core, '_hold_process', reused)
    kill = Mock(side_effect=AssertionError('PID reused'))
    monkeypatch.setattr(core.subprocess, 'run', kill)
    result = core.stop_engine_instances([instance()], timeout=0)
    assert result['ok'] is True and result['forced_pids'] == []
    lifecycle.request_stop.assert_not_called()
    kill.assert_not_called()


def test_failed_force_does_not_prevent_other_engine_shutdown(monkeypatch, lifecycle, clock):
    alive = {101: True, 202: True}
    monkeypatch.setattr(core, 'process_active', lambda pid, created: alive[pid])
    def force(command, **kwargs):
        pid = int(command[2])
        if pid == 101:
            raise subprocess.TimeoutExpired('taskkill', 10)
        alive[pid] = False
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(core.subprocess, 'run', Mock(side_effect=force))
    result = core.stop_engine_instances([instance(101, '100'), instance(202, '200')], timeout=0)
    assert result['ok'] is False and result['remaining_pids'] == [101]
    assert result['forced_pids'] == [202]


def test_final_query_catches_an_engine_started_by_older_controller(monkeypatch):
    monkeypatch.setattr(core, 'lifecycle_lock', lambda: nullcontext())
    monkeypatch.setattr(core, 'find_engine_instances', Mock(side_effect=[[], [instance()]]))
    monkeypatch.setattr(core, 'stop_engine_instances', Mock(return_value={
        'ok': True, 'message': 'done', 'remaining_pids': [], 'saved_pids': [], 'forced_pids': [], 'warnings': []}))
    result = core.stop_all_engines()
    assert result['ok'] is False and result['remaining_pids'] == [101]


def test_ready_requires_completed_startup_and_bound_pipe(monkeypatch, lifecycle, clock):
    monkeypatch.setattr(core, 'current_process_identity', lambda pid: {'pid': pid, 'created': '100'})
    lifecycle.read_status.side_effect = [{'state': 'starting', 'pipe_bound': True},
                                         {'state': 'ready', 'pipe_bound': False},
                                         {'state': 'ready', 'pipe_bound': True, 'pipe_connected': False}]
    process = SimpleNamespace(pid=101, poll=lambda: None)
    ok, message, row = core.wait_until_ready(process, ROOT / 'Part1', 'nonce', timeout=1)
    assert ok is True and 'MT5 연결 대기' in message
    assert clock[0] >= 0.2
    for call in lifecycle.read_status.call_args_list:
        assert call.kwargs == {'created': '100', 'run_id': 'nonce'}


@pytest.mark.parametrize('state,exit_code,text', [
    ({'state': 'ready', 'pipe_bound': True}, 1, '종료'),
    ({'state': 'failed', 'error': '파이프가 이미 사용 중입니다.'}, None, '사용 중'),
])
def test_dead_process_or_startup_failure_is_not_ready(monkeypatch, lifecycle, state, exit_code, text):
    monkeypatch.setattr(core, 'current_process_identity', lambda pid: {'pid': pid, 'created': '100'})
    lifecycle.read_status.return_value = state
    ok, message, _row = core.wait_until_ready(SimpleNamespace(pid=101, poll=lambda: exit_code),
                                             ROOT / 'Part1', 'nonce', timeout=0)
    assert ok is False and text in message


def test_stale_or_missing_ready_times_out(monkeypatch, lifecycle, clock):
    monkeypatch.setattr(core, 'current_process_identity', lambda pid: {'pid': pid, 'created': '100'})
    lifecycle.read_status.return_value = None
    ok, message, _row = core.wait_until_ready(SimpleNamespace(pid=101, poll=lambda: None),
                                             ROOT / 'Part1', 'new nonce', timeout=0.2)
    assert ok is False and '제한시간' in message


def test_ready_read_failure_returns_own_identity_for_cleanup(monkeypatch, lifecycle):
    monkeypatch.setattr(core, 'current_process_identity', lambda pid: {'pid': pid, 'created': '100'})
    lifecycle.read_status.side_effect = OSError('disk inaccessible')
    ok, message, row = core.wait_until_ready(SimpleNamespace(pid=101, poll=lambda: None),
                                             ROOT / 'Part1', 'nonce', timeout=0)
    assert ok is False and '준비 상태' in message
    assert row == {'pid': 101, 'created': '100', 'root': ROOT / 'Part1'}


def test_child_identity_uses_its_popen_handle(monkeypatch):
    process = SimpleNamespace(pid=101, _handle='owned handle')
    kernel = object()
    monkeypatch.setattr(core, '_kernel_process_api', lambda: kernel)
    created = Mock(return_value=100)
    monkeypatch.setattr(core, '_created', created)
    monkeypatch.setattr(core, 'current_process_identity', Mock(side_effect=OSError('OpenProcess denied')))
    assert core.spawned_process_identity(process) == {'pid': 101, 'created': '100'}
    created.assert_called_once_with(kernel, 'owned handle')


def test_failed_child_identity_uses_own_popen_only_after_deadline(monkeypatch, lifecycle, clock):
    monkeypatch.setattr(core, 'spawned_process_identity', Mock(side_effect=OSError('denied')))
    alive = [True]
    def kill():
        assert clock[0] >= 0.2
        alive[0] = False
    process = SimpleNamespace(pid=101, poll=lambda: None if alive[0] else 1,
                              kill=Mock(side_effect=kill), wait=Mock())
    result = core.cleanup_spawned_process(process, ROOT / 'Part1', 'private nonce', timeout=0.2)
    assert result['ok'] is True and result['forced_pids'] == [101]
    process.kill.assert_called_once()
    lifecycle.read_status.assert_called_once_with(ROOT / 'Part1', 101, run_id='private nonce')
    lifecycle.request_stop.assert_not_called()


def test_failed_child_identity_can_still_request_matching_nonce_shutdown(monkeypatch, lifecycle, clock):
    monkeypatch.setattr(core, 'spawned_process_identity', Mock(side_effect=OSError('denied')))
    alive = [True]
    lifecycle.read_status.side_effect = [{'created': '100', 'run_id': 'private nonce'},
                                        {'created': '100', 'run_id': 'private nonce', 'state_saved': True}]
    lifecycle.request_stop.side_effect = lambda *args, **kwargs: alive.__setitem__(0, False) or True
    process = SimpleNamespace(pid=101, poll=lambda: None if alive[0] else 0,
                              kill=Mock(side_effect=AssertionError('normal stop')), wait=Mock())
    result = core.cleanup_spawned_process(process, ROOT / 'Part1', 'private nonce', timeout=0.2)
    assert result['ok'] is True and result['forced_pids'] == []
    lifecycle.request_stop.assert_called_once_with(ROOT / 'Part1', 101, '100', run_id='private nonce')


@pytest.fixture
def controller(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('process_controller_test66', ROOT / 'Part1' / 'live_control.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    base = tmp_path / '이동된 프로젝트' / 'Part1'
    script = base / 'program' / 'event_host.py'
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(owner, 'BASE_DIR', base)
    monkeypatch.setattr(owner, 'LOG_DIR', base / 'logs')
    monkeypatch.setattr(owner, 'PYCACHE_DIR', base / 'logs' / 'pycache')
    monkeypatch.setattr(owner, 'resolve_script_path', lambda name: script)
    monkeypatch.setattr(owner, 'resolve_manager_modules', lambda: ({}, []))
    api = SimpleNamespace(EngineConflictError=core.EngineConflictError,
        lifecycle_lock=Mock(side_effect=lambda: nullcontext()),
        find_engine_instances=Mock(return_value=[]),
        confirm_restart=Mock(),
        public_instances=Mock(return_value=[{'pid': 202, 'created': '200'}]),
        wait_until_ready=Mock(return_value=(True, '준비 완료 · MT5 연결 대기', instance(root=base))),
        cleanup_spawned_process=Mock(return_value={'ok': True, 'message': 'saved', 'forced_pids': [], 'warnings': []}),
        stop_engine_instances=Mock(return_value={'ok': True, 'message': 'saved', 'warnings': []}))
    monkeypatch.setattr(owner, 'process_control', lambda: api)
    process = SimpleNamespace(pid=101, poll=Mock(return_value=None))
    spawn = Mock(return_value=process)
    monkeypatch.setattr(owner.subprocess, 'Popen', spawn)
    return owner, api, spawn, base


def test_start_waits_for_readiness_and_preserves_saved_selection(controller):
    owner, api, spawn, base = controller
    result = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'},
        special_triggers='saved OZ', special_times='saved times')
    assert result[0] is True and 'MT5 연결 대기' in result[1]
    env = spawn.call_args.kwargs['env']
    assert env['OZ_ENABLED_SPECIALS'] == 'SPECIAL1'
    assert env['OZ_SPECIAL_TRIGGERS'] == 'saved OZ' and env['OZ_SPECIAL_TIME_FILTERS'] == 'saved times'
    assert len(env['MOSES_ENGINE_RUN_ID']) == 48
    assert 'MOSES_UI_OWNER_PID' not in env and 'MOSES_UI_OWNER_CREATED' not in env
    assert api.wait_until_ready.call_args.args == (spawn.return_value, base, env['MOSES_ENGINE_RUN_ID'])
    api.lifecycle_lock.assert_called_once()


def test_conflict_never_launches_and_exposes_public_engine_list(controller):
    owner, api, spawn, base = controller
    api.confirm_restart.side_effect = core.EngineConflictError([{'pid': 202, 'created': '200'}])
    with pytest.raises(owner.EngineConflictError) as caught:
        owner.start_program('event_host.py', enabled_specials={'SPECIAL1'})
    assert caught.value.engines == [{'pid': 202, 'created': '200'}]
    spawn.assert_not_called()
    api.stop_engine_instances.assert_not_called()


def test_confirmed_restart_saves_before_launch_and_reports_force_warning(controller):
    owner, api, spawn, base = controller
    old = instance(202, '200')
    api.find_engine_instances.side_effect = [[old], []]
    warning = 'PID 202: 제한시간 초과 강제 종료 · 최신 기록 저장 미확인'
    api.stop_engine_instances.return_value = {'ok': True, 'message': 'forced', 'warnings': [warning]}
    order = []
    api.stop_engine_instances.side_effect = lambda rows: order.append('stop') or {
        'ok': True, 'message': 'forced', 'warnings': [warning]}
    spawn.side_effect = lambda *args, **kwargs: order.append('start') or SimpleNamespace(pid=101, poll=lambda: None)
    expected = [{'pid': 202, 'created': '200'}]
    result = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'}, restart=True, expected_engines=expected)
    assert order == ['stop', 'start']
    assert result.warnings == [warning] and warning in result[1]
    api.confirm_restart.assert_called_once_with([old], base, restart=True, expected_engines=expected)


def test_new_engine_during_old_engine_shutdown_requires_confirmation_again(controller):
    owner, api, spawn, base = controller
    api.find_engine_instances.side_effect = [[instance()], [instance(202, '200')]]
    with pytest.raises(owner.EngineConflictError, match='새 엔진'):
        owner.start_program('event_host.py', enabled_specials={'SPECIAL1'}, restart=True,
                            expected_engines=[{'pid': 101, 'created': '100'}])
    spawn.assert_not_called()
    api.stop_engine_instances.assert_called_once()


def test_failed_saved_shutdown_prevents_restart(controller):
    owner, api, spawn, base = controller
    api.find_engine_instances.return_value = [instance()]
    api.stop_engine_instances.return_value = {'ok': False, 'message': '저장 완료 미확인'}
    ok, message = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'}, restart=True,
                                     expected_engines=[{'pid': 101, 'created': '100'}])
    assert ok is False and '저장' in message
    spawn.assert_not_called()


def test_startup_failure_cleans_up_only_the_spawned_engine(controller):
    owner, api, spawn, base = controller
    own = instance(101, '100', base)
    api.wait_until_ready.return_value = (False, '파이프 사용 중', own)
    ok, message = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'})
    assert ok is False and '파이프' in message
    api.stop_engine_instances.assert_called_once_with([own])


def test_unexpected_readiness_error_cleans_the_exact_spawned_process(controller):
    owner, api, spawn, base = controller
    api.wait_until_ready.side_effect = OSError('identity/readiness unavailable')
    ok, message = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'})
    assert ok is False and '준비 상태' in message
    env = spawn.call_args.kwargs['env']
    api.cleanup_spawned_process.assert_called_once_with(spawn.return_value, base, env['MOSES_ENGINE_RUN_ID'])


def test_ui_owner_is_explicit_and_independent_cli_clears_inherited_owner(controller, monkeypatch):
    owner, api, spawn, base = controller
    monkeypatch.setenv('MOSES_UI_OWNER_PID', '999')
    monkeypatch.setenv('MOSES_UI_OWNER_CREATED', '999')
    assert owner.start_program('event_host.py', enabled_specials={'SPECIAL1'})[0]
    assert 'MOSES_UI_OWNER_PID' not in spawn.call_args.kwargs['env']
    assert owner.start_program('event_host.py', enabled_specials={'SPECIAL1'},
                               ui_owner={'pid': 77, 'created': '770'})[0]
    env = spawn.call_args.kwargs['env']
    assert env['MOSES_UI_OWNER_PID'] == '77' and env['MOSES_UI_OWNER_CREATED'] == '770'


def test_stop_current_copy_does_not_touch_other_copy(controller):
    owner, api, spawn, base = controller
    own = instance(101, '100', base)
    other = instance(202, '200', ROOT / 'other-copy' / 'Part1')
    api.find_engine_instances.return_value = [own, other]
    assert owner.stop_program('event_host.py') == (True, 'saved')
    api.stop_engine_instances.assert_called_once_with([own])


def test_query_error_never_launches(controller):
    owner, api, spawn, base = controller
    api.find_engine_instances.side_effect = RuntimeError('엔진 조회에 실패')
    ok, message = owner.start_program('event_host.py', enabled_specials={'SPECIAL1'})
    assert ok is False and '조회' in message
    spawn.assert_not_called()


def test_two_controllers_cannot_both_launch_while_first_is_preparing(controller):
    owner, api, spawn, base = controller
    shared_lock = threading.RLock()
    preparing, release = threading.Event(), threading.Event()
    current = []
    api.lifecycle_lock.side_effect = lambda: shared_lock
    api.find_engine_instances.side_effect = lambda: list(current)
    api.confirm_restart.side_effect = lambda rows, root, **options: core.confirm_restart(rows, root, **options)
    api.public_instances.return_value = [{'pid': 101, 'created': '100'}]
    # No status files or processes are touched: only the independent
    # controllers' competing launch decisions are exercised.
    old_public = core.public_instances
    core.public_instances = lambda rows, root: [{'pid': row['pid'], 'created': row['created']} for row in rows]
    try:
        def start(*args, **kwargs):
            current.append(instance(101, '100', base))
            return SimpleNamespace(pid=101, poll=lambda: None)
        def ready(*args):
            preparing.set()
            assert release.wait(5)
            return True, '준비 완료', current[0]
        spawn.side_effect = start
        api.wait_until_ready.side_effect = ready
        outcomes = []
        def launch():
            try:
                outcomes.append(owner.start_program('event_host.py', enabled_specials={'SPECIAL1'}))
            except owner.EngineConflictError as exc:
                outcomes.append(exc)
        first, second = threading.Thread(target=launch), threading.Thread(target=launch)
        first.start()
        assert preparing.wait(5)
        second.start()
        release.set()
        first.join(5)
        second.join(5)
        assert not first.is_alive() and not second.is_alive()
        spawn.assert_called_once()
        assert sum(isinstance(outcome, owner.EngineConflictError) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, tuple) and outcome[0] is True for outcome in outcomes) == 1
    finally:
        release.set()
        core.public_instances = old_public
