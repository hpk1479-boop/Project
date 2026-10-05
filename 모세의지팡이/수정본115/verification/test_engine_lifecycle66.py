"""Lifecycle boundaries without MT5, Telegram, GUI, or user configuration I/O."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import event_lifecycle as lifecycle
import event_host
import event_pipe_host


def runtime(tmp_path, **kwargs):
    return lifecycle.HostLifecycle(tmp_path / 'Part1', threading.Event(), pid=99,
                                   created='100001', run_id='current_run', **kwargs)


@pytest.mark.parametrize('services_first', [False, True])
def test_ready_requires_bound_pipe_and_all_started_services(tmp_path, services_first):
    host = runtime(tmp_path)
    first, second = ((host.services_started, lambda: host.pipe_bound(True))
                     if services_first else (lambda: host.pipe_bound(True), host.services_started))
    first()
    assert lifecycle.read_status(tmp_path / 'Part1', 99)['state'] == 'starting'
    second()
    row = lifecycle.read_status(tmp_path / 'Part1', 99, created='100000', run_id='current_run')
    assert row['state'] == 'ready' and row['pipe_bound'] and not row['pipe_connected']
    assert not row['state_saved']


def test_connected_flag_does_not_claim_initial_ready(tmp_path):
    host = runtime(tmp_path)
    host.pipe_connected(True)
    assert host.row['state'] == 'starting'
    host.begin_stopping()
    host.pipe_bound(True)
    host.services_started()
    assert host.row['state'] == 'stopping'


def test_stop_request_validates_creation_identity_and_run_token(tmp_path):
    host = runtime(tmp_path)
    root = tmp_path / 'Part1'
    assert not lifecycle.request_stop(root, 99, '200001', 'current_run')
    assert not lifecycle.request_stop(root, 99, '100001', 'previous_run')
    assert not host.stop_path.exists() and not host.stop.is_set()
    # CIM rounds FILETIME to microseconds; the exact current identity is written.
    assert lifecycle.request_stop(root, 99, '100000', 'current_run')
    request = json.loads(host.stop_path.read_text('utf-8'))
    assert request['created'] == '100001' and request['run_id'] == 'current_run'
    assert host.check_requests() and host.stop.is_set()
    assert host.row['state'] == 'stopping' and not host.row['state_saved']
    host.finish(True)
    assert host.row['state'] == 'stopped' and host.row['state_saved']


@pytest.mark.parametrize('changes', [
    {'pid': 98}, {'created': '200001'}, {'run_id': 'previous_run'},
    {'action': 'start'}, {'version': 2},
])
def test_old_or_unrelated_stop_files_never_stop_current_engine(tmp_path, changes):
    host = runtime(tmp_path)
    request = {'version': 1, 'pid': 99, 'created': '100001',
               'run_id': 'current_run', 'action': 'stop', **changes}
    host.stop_path.write_text(json.dumps(request), encoding='utf-8')
    assert not host.check_requests() and not host.stop.is_set()


@pytest.mark.parametrize('content', ['', '{', '[]', 'null', '{"version": 1}'])
def test_invalid_runtime_records_are_not_ready_or_controllable(tmp_path, content):
    host = runtime(tmp_path)
    host.path.write_text(content, encoding='utf-8')
    assert lifecycle.read_status(tmp_path / 'Part1', 99) is None
    assert not lifecycle.request_stop(tmp_path / 'Part1', 99, '100001')
    assert not host.stop_path.exists()


def test_saved_acknowledgement_never_precedes_finish_and_failure_stays_visible(tmp_path):
    host = runtime(tmp_path)
    host.begin_stopping()
    assert not lifecycle.read_status(tmp_path / 'Part1', 99)['state_saved']
    host.fail('입출력 작업이 종료되지 않았습니다.')
    host.finish(False)
    row = lifecycle.read_status(tmp_path / 'Part1', 99)
    assert row['state'] == 'failed' and not row['state_saved'] and row['error']
    assert not lifecycle.request_stop(tmp_path / 'Part1', 99, '100001')


@pytest.mark.parametrize('actual', [None, '900000'])
def test_web_owner_exit_or_pid_reuse_requests_orderly_shutdown(tmp_path, actual):
    host = runtime(tmp_path, owner_pid=7, owner_created='700000', identity=lambda _pid: actual)
    assert host.check_requests() and host.stop.is_set()
    assert host.row['state'] == 'stopping' and not host.row['state_saved']


def test_web_owner_alive_or_temporarily_unqueryable_is_not_declared_dead(tmp_path, caplog):
    host = runtime(tmp_path, owner_pid=7, owner_created='700000', identity=lambda _pid: '700009')
    assert not host.check_requests() and not host.stop.is_set()
    def denied(_pid):
        raise PermissionError('identity query unavailable')
    host.identity = denied
    host.check_requests()
    host.check_requests()
    assert not host.stop.is_set()
    assert len([r for r in caplog.records if '생존 상태 조회' in r.message]) == 1


def test_independent_cli_never_watches_its_short_lived_starter(tmp_path, monkeypatch):
    for name in ('MOSES_UI_OWNER_PID', 'MOSES_UI_OWNER_CREATED'):
        monkeypatch.delenv(name, raising=False)
    host = runtime(tmp_path, identity=lambda _pid: pytest.fail('no owner query expected'))
    assert host.owner is None and not host.check_requests() and not host.stop.is_set()


def test_runtime_contract_survives_project_move_and_has_no_stored_paths(tmp_path):
    host = runtime(tmp_path)
    moved = tmp_path / 'moved' / 'Part1'
    moved.parent.mkdir()
    shutil.move(str(tmp_path / 'Part1'), moved)
    row = lifecycle.read_status(moved, 99, created='100001', run_id='current_run')
    assert row and str(tmp_path) not in json.dumps(row)
    assert lifecycle.request_stop(moved, 99, '100001', 'current_run')
    assert (moved / 'runtime/engines/99.stop.json').is_file()


def test_control_record_write_failure_stops_host_without_false_saved_claim(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    def failed_write(*_args):
        raise PermissionError('readonly')
    monkeypatch.setattr(lifecycle, '_atomic_json', failed_write)
    host.services_started()
    assert host.stop.is_set() and host.row['state'] == 'failed'
    host.finish(True)
    assert not host.row['state_saved']


def test_worker_shutdown_uses_one_deadline_and_reports_unfinished_output(monkeypatch):
    clock = [0.0]
    seen = []
    class Worker:
        def __init__(self):
            self.remaining = 20.0
        def join(self, timeout):
            seen.append(timeout)
            spent = min(timeout, self.remaining)
            clock[0] += spent
            self.remaining -= spent
        def is_alive(self):
            return self.remaining > 0
    from event_engine import EventEngine, IngressSequencer
    host = event_host.EventHost(EventEngine(IngressSequencer()), {}, SimpleNamespace(),
                                transport=lambda _data: pytest.fail('unexpected output'))
    host.workers = [Worker(), Worker()]
    monkeypatch.setattr(event_host.time, 'monotonic', lambda: clock[0])
    assert host.close() is False and host.stop.is_set()
    assert clock[0] == pytest.approx(30.0)
    assert host.workers[1].is_alive()
    assert all(0 <= wait <= 30.0 for wait in seen)


class FakeFunction:
    def __init__(self, function):
        self.function = function
    def __call__(self, *args):
        return self.function(*args)


def fake_receiver(monkeypatch, tmp_path, *, busy=False):
    stop = threading.Event()
    stop.wait = lambda _timeout: stop.is_set()
    calls = []
    def create(*args):
        calls.append(('create', args))
        return ctypes.c_void_p(-1).value if busy else 123
    functions = {
        'CreateNamedPipeW': create,
        'ConnectNamedPipe': lambda handle, _overlap: calls.append(('connect', handle)) or 1,
        'DisconnectNamedPipe': lambda handle: calls.append(('disconnect', handle)) or 1,
        'CloseHandle': lambda handle: calls.append(('close', handle)) or 1,
        'ReadFile': lambda *a: 1, 'WriteFile': lambda *a: 1,
        'CancelIoEx': lambda *a: 1, 'OpenThread': lambda *a: 1,
        'CancelSynchronousIo': lambda *a: 1,
    }
    kernel = SimpleNamespace(**{name: FakeFunction(fn) for name, fn in functions.items()})
    monkeypatch.setattr(event_pipe_host.ctypes, 'WinDLL', lambda *a, **k: kernel)
    monkeypatch.setattr(event_pipe_host.ctypes, 'get_last_error', lambda: 231)
    @contextmanager
    def security():
        yield ctypes.c_int(0)
    adapter = SimpleNamespace(health=lambda **k: calls.append(('health', k['status'])))
    bound, fatal = [], []
    receiver = event_pipe_host.PipeReceiver(SimpleNamespace(staff_pipe_security=security),
        SimpleNamespace(pipe_name='test-only-pipe', reconnect=lambda: calls.append(('reconnect', None))),
        adapter, ('BTCUSD',), stop, on_bound=bound.append, on_fatal=fatal.append)
    return receiver, calls, bound, fatal


def test_pipe_busy_fails_once_without_health_flood_or_retry(tmp_path, monkeypatch, caplog):
    receiver, calls, bound, fatal = fake_receiver(monkeypatch, tmp_path, busy=True)
    receiver._run()
    assert receiver.stop.is_set() and fatal and bound == [False]
    assert len([x for x in calls if x[0] == 'create']) == 1
    assert not any(x[0] in ('health', 'connect') for x in calls)
    assert len([r for r in caplog.records if '파이프를 준비하지 못해' in r.message]) == 1


def test_ea_reconnect_keeps_pipe_ownership_and_existing_reconnect_events(tmp_path, monkeypatch):
    receiver, calls, bound, fatal = fake_receiver(monkeypatch, tmp_path)
    count = [0]
    def receive(*_args):
        count[0] += 1
        if count[0] == 1:
            raise OSError('client disconnected')
        receiver.stop.set()
    receiver.receive = receive
    receiver._run()
    assert not fatal and bound == [True, False]
    assert len([x for x in calls if x[0] == 'create']) == 1
    assert [x for x in calls if x[0] == 'connect'] == [('connect', 123), ('connect', 123)]
    assert len([x for x in calls if x[0] == 'reconnect']) == 2
    assert len([x for x in calls if x == ('health', 'RECONNECT')]) == 2
    assert [x for x in calls if x[0] == 'close'] == [('close', 123)]
    mode = next(x[1][1] for x in calls if x[0] == 'create')
    assert mode & 0x00080000


@pytest.mark.skipif(os.name != 'nt', reason='Windows 로컬 Named Pipe 전용 검사')
def test_real_local_pipe_reconnect_never_releases_name_to_another_engine():
    """A random local pipe cannot be addressed by the user's configured EA."""
    from ctypes import wintypes
    staff = event_host.load_staff()
    pipe_name = r'\\.\pipe\MOSES_VERIFY_' + uuid.uuid4().hex
    bound, disconnected, received = threading.Event(), threading.Event(), threading.Event()
    counts = {'frames': 0, 'reconnects': 0}
    active_stop = threading.Event()
    def cache():
        def reconnect():
            counts['reconnects'] += 1
        return SimpleNamespace(pipe_name=pipe_name, reconnect=reconnect, keys=lambda: ())
    def receive_one(read, **_kwargs):
        assert read(1) == b'x'
        counts['frames'] += 1
        received.set()
        return b''
    adapter = SimpleNamespace(receive_one=receive_one, health=lambda **_kwargs: None)
    owner = event_pipe_host.PipeReceiver(staff, cache(), adapter, (), active_stop,
        on_bound=lambda value: bound.set() if value else None,
        on_connected=lambda value: disconnected.clear() if value else disconnected.set())
    receivers, clients = [owner], []
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    def another_engine_must_fail():
        failed = threading.Event()
        duplicate = event_pipe_host.PipeReceiver(staff, cache(), adapter, (), threading.Event(),
                                                 on_fatal=lambda _error: failed.set())
        receivers.append(duplicate)
        duplicate.start()
        assert failed.wait(3), '다른 엔진은 파이프 점유를 확인하면 유한 종료해야 합니다.'
        assert duplicate.stop.is_set() and duplicate.close()
        assert not owner.stop.is_set()
    def send_frame():
        received.clear()
        assert kernel.WaitNamedPipeW(pipe_name, 3000)
        handle = kernel.CreateFileW(pipe_name, 0xC0000000, 0, None, 3, 0, None)
        assert handle and handle != ctypes.c_void_p(-1).value
        clients.append(handle)
        written = wintypes.DWORD()
        assert kernel.WriteFile(handle, ctypes.create_string_buffer(b'x'), 1, ctypes.byref(written), None)
        assert written.value == 1 and received.wait(3)
        kernel.CloseHandle(handle)
        clients.remove(handle)
        assert disconnected.wait(3)
    try:
        owner.start()
        assert bound.wait(3), '실제 파이프 생성 완료를 기다립니다.'
        another_engine_must_fail()
        send_frame()
        # Ownership is retained while no EA client is connected, too.
        another_engine_must_fail()
        send_frame()
        assert counts == {'frames': 2, 'reconnects': 2}
    finally:
        for handle in clients:
            kernel.CloseHandle(handle)
        for receiver in receivers:
            receiver.close()
    assert owner.handle is None and not owner.thread.is_alive()


def mocked_main(tmp_path, monkeypatch, *, scenario='normal'):
    import command_interpreter
    import event_startup
    import event_composer_domain
    import event_economy_host
    import live_alert_recording
    import module_diagnostics
    part1 = tmp_path / 'Part1'
    program, state = part1 / 'program', part1 / 'event_state'
    program.mkdir(parents=True)
    host_lifecycle = runtime(tmp_path)
    events = []
    engine = SimpleNamespace(startup_config={'ECONOMY_ENABLED': 'true' if scenario == 'economy_start_failure' else 'false'},
        event_state_directory=str(state), startup_enabled_specials=(), startup_aliases={}, ingress=object())
    monkeypatch.setattr(lifecycle, 'HostLifecycle', lambda *_args: host_lifecycle)
    monkeypatch.setattr(host_lifecycle, 'start', lambda: None)
    monkeypatch.setattr(event_composer_domain, 'load_config', lambda *_args: {})
    monkeypatch.setattr(event_startup, 'create_live_event_engine', lambda *_args, **_kwargs: engine)
    def export(_engine):
        row = lifecycle.read_status(part1, 99)
        assert row['state'] in ('stopping', 'failed') and not row['state_saved']
        events.append('export')
        return {'trend_watch_state.json': '{"version":3,"watches":{}}'}
    monkeypatch.setattr(event_startup, 'export_engine_state', export)
    real_write = event_startup.write_event_state_files
    def write(files, **kwargs):
        assert not lifecycle.read_status(part1, 99)['state_saved']
        events.append('write')
        if scenario == 'save_failure':
            raise PermissionError('state write failed')
        return real_write(files, **kwargs)
    monkeypatch.setattr(event_startup, 'write_event_state_files', write)
    diagnostics = SimpleNamespace(configure_specials=lambda *_args: None, close=lambda: events.append('diagnostics_close'))
    monkeypatch.setattr(module_diagnostics, 'configure', lambda *_args: diagnostics)
    def status_server(_sink):
        if scenario == 'early_start_failure':
            raise RuntimeError('diagnostic server failed')
        return SimpleNamespace(close=lambda: events.append('status_close'))
    monkeypatch.setattr(module_diagnostics, 'DiagnosticServer', status_server)
    monkeypatch.setattr(command_interpreter, 'CommandInterpreter', lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(event_host, 'HTTPServices', lambda *_args, **_kwargs: SimpleNamespace(send=lambda *_a: None))
    monkeypatch.setattr(live_alert_recording, 'LiveAlertRecorder', lambda *_args: object())
    monkeypatch.setattr(event_host, 'load_staff', lambda: SimpleNamespace(StaffPipeCache=lambda *_args, **_kwargs: object()))
    monkeypatch.setattr(event_host, 'StaffIngressAdapter', lambda *_args: object())
    class Host:
        def __init__(self, *_args, stop, **_kwargs):
            self.stop = stop
            self.inputs = SimpleNamespace(seed_symbols=())
            self.output = SimpleNamespace(receipts={})
            self.workers = []
        def start_io(self):
            events.append('host_start')
        def run(self):
            if self.stop.is_set():
                return
            assert host_lifecycle.row['state'] == 'ready'
            events.append('run')
            self.stop.set()
        def close(self):
            events.append('host_close')
            self.stop.set()
            self.output.receipts['last_delivery'] = 'recorded_before_close'
            return scenario != 'worker_timeout'
    monkeypatch.setattr(event_host, 'EventHost', Host)
    class Receiver:
        symbols = ()
        def __init__(self, *_args, on_bound, on_connected, on_fatal, **_kwargs):
            self.bound, self.connected, self.fatal = on_bound, on_connected, on_fatal
        def start(self):
            events.append('receiver_start')
            if scenario == 'pipe_busy':
                self.fatal('MT5 파이프가 이미 사용 중입니다.')
            else:
                self.bound(True)
        def close(self):
            events.append('receiver_close')
            self.bound(False)
            self.connected(False)
            return True
    monkeypatch.setattr(event_pipe_host, 'PipeReceiver', Receiver)
    class Economy:
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            raise RuntimeError('economy worker failed to start')
    monkeypatch.setattr(event_economy_host, 'EconomyWorker', Economy)
    return program, state, host_lifecycle, events


def test_real_main_writes_state_and_last_receipts_before_saved_ack(tmp_path, monkeypatch):
    program, state, host, events = mocked_main(tmp_path, monkeypatch)
    assert event_host.main(['--program', str(program)]) == 0
    row = lifecycle.read_status(program.parent, 99)
    assert row['state'] == 'stopped' and row['state_saved'] and not row['pipe_bound']
    assert json.loads((state / 'signal_receipts.json').read_text('utf-8')) == {
        'last_delivery': 'recorded_before_close'}
    assert events.index('host_close') < events.index('export') < events.index('write')
    assert events[-2:] == ['status_close', 'diagnostics_close']


@pytest.mark.parametrize('scenario', ['early_start_failure', 'economy_start_failure',
                                    'pipe_busy', 'save_failure', 'worker_timeout'])
def test_real_main_start_and_shutdown_failures_cleanup_and_never_report_success(tmp_path, monkeypatch, scenario):
    program, state, host, events = mocked_main(tmp_path, monkeypatch, scenario=scenario)
    assert event_host.main(['--program', str(program)]) == 1
    row = lifecycle.read_status(program.parent, 99)
    assert row['state'] == 'failed' and row['error'] and 'diagnostics_close' in events
    if scenario != 'early_start_failure':
        assert 'host_close' in events and 'receiver_close' in events and 'status_close' in events
    if scenario in ('early_start_failure', 'worker_timeout', 'save_failure'):
        assert not row['state_saved']
    if scenario in ('early_start_failure', 'worker_timeout'):
        assert 'export' not in events and not (state / 'signal_receipts.json').exists()
    if scenario == 'pipe_busy':
        assert 'run' not in events
