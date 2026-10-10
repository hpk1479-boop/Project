"""Real launch identities, intentional shutdown and bounded status-file races."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program')]
from Part1 import engine_processes as core
import event_lifecycle as lifecycle
import module_diagnostics as diagnostics
import module_status_state as ui


def record(root, pid=202, created='200', run_id='current', state='ready'):
    row = {'version': 1, 'pid': pid, 'created': created, 'run_id': run_id,
           'state': state, 'pipe_bound': True, 'pipe_connected': True,
           'state_saved': False, 'error': 'startup failed' if state == 'failed' else ''}
    lifecycle._atomic_json(root / 'runtime/engines' / f'{pid}.json', row)
    return row


def child_launch(monkeypatch, root, *, parents=None, active=True):
    monkeypatch.setattr(core, 'lifecycle', lambda: lifecycle)
    monkeypatch.setattr(core, 'spawned_process_identity', lambda _process: {'pid': 101, 'created': '100'})
    monkeypatch.setattr(core, '_process_parents', lambda: {202: 101} if parents is None else parents)
    monkeypatch.setattr(core, 'process_active', lambda pid, created: active and (pid, created) == (202, '200'))
    return SimpleNamespace(pid=101, poll=lambda: None)


@pytest.mark.parametrize('state,expected', [('ready', True), ('failed', False)])
def test_redirecting_launcher_resolves_the_actual_engine(monkeypatch, tmp_path, state, expected):
    root = tmp_path / 'moved project/Part1'
    record(root, state=state)
    process = child_launch(monkeypatch, root)
    ok, message, instance = core.wait_until_ready(process, root, 'current', timeout=0)
    assert ok is expected
    assert instance == {'pid': 202, 'created': '200', 'root': root}
    assert ('MT5 연결됨' in message) if expected else message == 'startup failed'


@pytest.mark.parametrize('changes,parents,active', [
    ({'run_id': 'old'}, {202: 101}, True),
    ({}, {202: 999}, True),
    ({}, {202: 101}, False),
    ({'created': '90'}, {202: 101}, True),
    ({}, {202: 303, 303: 202}, True),
])
def test_unrelated_stale_reused_or_cyclic_children_are_never_accepted(monkeypatch, tmp_path, changes, parents, active):
    record(tmp_path, **changes)
    process = child_launch(monkeypatch, tmp_path, parents=parents, active=active)
    ok, message, instance = core.wait_until_ready(process, tmp_path, 'current', timeout=0)
    assert not ok and '제한시간' in message
    assert instance['pid'] == 101


def test_failed_launch_cleanup_requests_the_actual_child_not_its_wrapper(monkeypatch, tmp_path):
    record(tmp_path)
    process = child_launch(monkeypatch, tmp_path)
    stopped = Mock(return_value={'ok': True})
    monkeypatch.setattr(core, 'stop_engine_instances', stopped)
    assert core.cleanup_spawned_process(process, tmp_path, 'current')['ok']
    assert stopped.call_args.args[0][0]['pid'] == 202


def test_engine_query_hides_a_launcher_only_when_its_child_has_runtime_identity(monkeypatch):
    script = r'D:\move\Part1\program\event_host.py'
    import subprocess
    command = subprocess.list2cmdline([sys.executable, script])
    rows = [{'ProcessId': 101, 'ParentProcessId': 9, 'Created': '100', 'CommandLine': command},
            {'ProcessId': 202, 'ParentProcessId': 101, 'Created': '200', 'CommandLine': command},
            {'ProcessId': 303, 'ParentProcessId': 9, 'Created': '300', 'CommandLine': command}]
    monkeypatch.setattr(core.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(rows))))
    api = SimpleNamespace(read_status=lambda root, pid, **kwargs: {'run_id': 'current'} if pid == 202 else None)
    monkeypatch.setattr(core, 'lifecycle', lambda: api)
    assert [row['pid'] for row in core.find_engine_instances()] == [202, 303]


def test_force_does_not_wait_for_the_graceful_stop_mutex_or_touch_other_roots(monkeypatch, tmp_path):
    own = {'pid': 101, 'created': '100', 'root': tmp_path / 'own'}
    other = {'pid': 202, 'created': '200', 'root': tmp_path / 'other'}
    monkeypatch.setattr(core, 'find_engine_instances', lambda: [own, other])
    monkeypatch.setattr(core, 'lifecycle_lock', Mock(side_effect=AssertionError('must interrupt instead of waiting')))
    stopped = Mock(return_value={'ok': True})
    monkeypatch.setattr(core, 'stop_engine_instances', stopped)
    assert core.force_all_engines(root=own['root'])['ok']
    stopped.assert_called_once_with([own], timeout=3, force=True)


def test_force_skips_a_reused_process_identity(monkeypatch):
    @contextmanager
    def reused(pid, created):
        yield False
    monkeypatch.setattr(core, '_hold_process', reused)
    kill = Mock(side_effect=AssertionError('unrelated process must live'))
    monkeypatch.setattr(core.subprocess, 'run', kill)
    assert core.force_engine_instances([{'pid': 101, 'created': '100', 'root': ROOT / 'Part1'}])['ok']
    kill.assert_not_called()


@pytest.mark.parametrize('state', ['starting', 'stopping', 'stopped'])
def test_intentional_shutdown_never_displays_pipe_disconnect_or_backlog_as_error(tmp_path, state):
    sink = diagnostics.Diagnostics(tmp_path, clock=lambda: 100)
    try:
        sink.lifecycle_state = lambda: state
        sink.pipe_seen = True
        sink.pipe_connected = False
        sink.last_staff_input = 0
        sink.backlog = True
        sink.telegram_attempted = True
        data = sink.snapshot('ALL')
        assert data['modules']['STAFF']['status'] == '오류·끊김'
        assert data['modules']['ENGINE']['status'] == '지연'
        assert all(ui.display_state(name, data) == '연결 대기' for name in ui.MAIN_MODULES)
    finally:
        sink.close()


def test_real_connection_errors_are_still_visible():
    data = {'engine_state': 'ready', 'pipe_seen': True, 'pipe_connected': False,
            'modules': {'STAFF': {'status': '정상'}, 'ENGINE': {'status': '지연'}}}
    assert ui.display_state('STAFF', data) == '오류'
    assert ui.display_state('ENGINE', data) == '연결 대기'
    data['pipe_connected'] = True
    assert ui.display_state('ENGINE', data) == '연결중'
    data['modules']['ENGINE']['status'] = '오류·끊김'
    assert ui.display_state('ENGINE', data) == '오류'
    data['engine_state'] = 'failed'
    assert ui.display_state('ENGINE', data) == '오류'


def test_windows_reader_collision_retries_and_publishes_the_same_complete_record(monkeypatch, tmp_path):
    original = lifecycle.os.replace
    calls = []
    def replace(source, target):
        calls.append(1)
        if len(calls) == 1:
            error = PermissionError('reader sharing violation'); error.winerror = 32
            raise error
        return original(source, target)
    monkeypatch.setattr(lifecycle.os, 'replace', replace)
    row = record(tmp_path)
    assert len(calls) == 2
    assert lifecycle.read_status(tmp_path, 202) == row
    assert not list((tmp_path / 'runtime/engines').glob('*.tmp'))


def test_persistent_status_write_error_keeps_its_os_reason_in_the_log(monkeypatch, tmp_path, caplog):
    import threading
    host = lifecycle.HostLifecycle(tmp_path, threading.Event(), pid=99, created='100', run_id='test')
    monkeypatch.setattr(lifecycle, '_atomic_json', Mock(side_effect=PermissionError('disk access denied detail')))
    host.services_started()
    assert host.stop.is_set() and host.row['state'] == 'failed'
    assert 'disk access denied detail' in caplog.text


def test_economy_receipt_wait_is_released_on_stop_without_claiming_delivery():
    import threading
    import time
    import event_host
    condition = threading.Condition()
    posted = threading.Event()
    # No output path at all: nothing can deliver the notice once stopping (164: notice_attempts, delivering).
    host = SimpleNamespace(inputs=SimpleNamespace(symbols=()),config={},
        engine=SimpleNamespace(ingress=SimpleNamespace(post=lambda *a,**kw:posted.set())),
        delivery_condition=condition,output=SimpleNamespace(receipts={}),stop=threading.Event(),
        notice_attempts={},delivering=lambda:False)
    result=[]
    worker=threading.Thread(target=lambda:result.append(event_host.EconomyOutputPort(host).send('notice')))
    worker.start()
    try:
        assert posted.wait(1)
        with condition:
            host.stop.set(); condition.notify_all()
        worker.join(1)
        assert not worker.is_alive() and result==[False]
    finally:
        with condition:host.stop.set();condition.notify_all()
        worker.join(1)
