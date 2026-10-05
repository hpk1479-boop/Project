"""Shared AI connection contracts, using real IPC and fake model weights.

Only the model inference boundary is fake. Independent Python clients connect
to the authenticated common service; no Ollama, GPU, MT5, or network outside
loopback is used. Existing provider suites verify actual worker ownership.
"""
from __future__ import annotations

import copy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from common_ai.client import Client
from common_ai.service import Service


GGUF = {'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/first-4b.gguf',
        'llama_server_path': 'runtime/llama.cpp/llama-server.exe', 'timeout': 90}
SCHEMA = {'type': 'object', 'required': ['echo', 'model', 'owner_pid'],
          'properties': {'echo': {'type': 'string'}, 'model': {'type': 'string'},
                         'owner_pid': {'type': 'integer'}}}


class ModelState:
    """Observe inference entry, residency, and cleanup without loading weights."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = self.maximum_active = 0
        self.resident = None
        self.loads = self.shutdown_calls = self.invalidations = 0
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail_next = None

    def factory(self, settings):
        owner = self
        selected = copy.deepcopy(settings)

        class Provider:
            timeout = float(selected.get('timeout', 90))

            def chat(self, messages, tools, *, response_schema=None):
                text = messages[-1]['content'] if messages else ''
                key = selected.get('gguf_model_path') or selected.get('model') or selected.get('base_model')
                with owner.lock:
                    owner.active += 1
                    owner.maximum_active = max(owner.maximum_active, owner.active)
                    if owner.active != 1:
                        owner.active -= 1
                        raise AssertionError('WATCH and strategy inference overlapped')
                    if owner.resident != key:
                        owner.resident = key
                        owner.loads += 1
                    owner.calls.append(copy.deepcopy({'messages': messages, 'tools': tools,
                        'schema': response_schema, 'settings': selected}))
                try:
                    if text == 'hold':
                        owner.entered.set()
                        if not owner.release.wait(5):
                            raise ValueError('isolated fake inference timed out')
                    if owner.fail_next:
                        error, owner.fail_next = owner.fail_next, None
                        raise error
                    if text == 'lookup':
                        return {'content': '', 'tool_calls': [{'id': 'query-1',
                            'name': 'vocabulary', 'arguments': {}}]}
                    return {'content': json.dumps({'echo': text, 'model': key,
                        'owner_pid': os.getpid()}, ensure_ascii=False), 'tool_calls': []}
                finally:
                    with owner.lock:
                        owner.active -= 1

        return Provider()


class Runtime:
    def __init__(self, state):
        self.state = state
        self.generation = 0

    def invalidate(self):
        self.generation += 1
        self.state.invalidations += 1
        self.state.resident = None

    def shutdown(self, timeout=45):
        self.state.shutdown_calls += 1
        self.state.resident = None


def save_settings(root, settings=GGUF):
    path = root / 'settings' / 'ai_settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.fixture
def shared(tmp_path):
    root = tmp_path / 'portable-project'
    root.mkdir()
    settings_file = save_settings(root)
    state = ModelState()
    runtime = Runtime(state)
    service = Service(root, provider_factory=state.factory, runtime=runtime)
    service.start()
    value = SimpleNamespace(root=root, state=state, runtime=runtime,
                            service=service, settings_file=settings_file,
                            clients=[])
    yield value
    state.release.set()
    for client in value.clients:
        try:
            client.close()
        except (ValueError, RuntimeError, OSError):
            pass
    service.close()


def client(shared):
    selected = Client(shared.root)
    shared.clients.append(selected)
    return selected


def ask(selected, text='해석', *, role='strategy', schema=SCHEMA, tools=None,
        generation=None):
    kwargs = {'response_schema': schema, 'role': role}
    if generation is not None:
        kwargs['generation'] = generation
    return selected.chat([{'role': 'user', 'content': text}], tools or [], **kwargs)


def eventually(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    assert check(), 'shared service did not reach its expected observable state'


def test_listing_service_and_registering_clients_do_not_load_weights(shared):
    first, second = client(shared), client(shared)
    assert first is not second
    assert shared.state.loads == 0 and shared.state.resident is None
    assert not shared.state.calls
    record = json.loads((shared.root / 'runtime' / 'ai_service.json').read_text('utf-8'))
    assert record['port'] == shared.service.server.server_port
    assert record['pid'] == os.getpid()
    assert record['token'] and record['service_id']
    assert str(shared.root).replace('\\', '/') not in json.dumps(record).replace('\\\\', '/')


def test_watch_strategy_and_backtest_share_the_selected_resident_model(shared):
    watch, strategy = client(shared), client(shared)
    for selected, role in ((watch, 'watch'), (strategy, 'strategy'), (strategy, 'backtest')):
        reply = ask(selected, role=role)
        assert json.loads(reply['content'])['model'] == GGUF['gguf_model_path']
        assert reply['tool_calls'] == []
    assert shared.state.loads == 1
    assert len(shared.state.calls) == 3
    assert shared.state.maximum_active == 1


def test_safe_contract_tools_requested_schema_and_tool_calls_cross_ipc_intact(shared):
    selected = client(shared)
    vocabulary = {'intent_kinds': ['TREND'], 'intent_parameters': {'TREND': ['tf', 'direction']},
                  'symbols': ['XAUUSD+']}
    presets = [{'id': 'PRESET_A', 'name': '기본 전략'}]
    messages = [{'role': 'system', 'content': json.dumps({'moses_contract': {
                    'vocabulary': vocabulary, 'presets': presets}})},
        {'role': 'system', 'content': 'private-source-74'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'read-1',
            'function': {'name': 'read_text', 'arguments': '{"path":"문서.md"}'}}]},
        {'role': 'tool', 'name': 'read_text', 'content': 'private-source-74', 'tool_call_id': 'read-1'},
        {'role': 'user', 'content': 'lookup'}]
    tools = [{'type': 'function', 'function': {'name': 'vocabulary',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}},
        {'type': 'function', 'function': {'name': 'read_text',
        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}}]
    before = copy.deepcopy((messages, tools, SCHEMA))
    reply = selected.chat(messages, tools, response_schema=SCHEMA, role='strategy')
    assert reply == {'content': '', 'tool_calls': [{'id': 'query-1',
        'name': 'vocabulary', 'arguments': {}}]}
    captured = shared.state.calls[-1]
    contract = json.loads(captured['messages'][0]['content'])['moses_contract']
    assert contract['vocabulary'] == vocabulary and contract['presets'] == presets
    assert captured['messages'][-1] == messages[-1]
    assert captured['schema'] == SCHEMA
    assert {row['function']['name'] for row in captured['tools']} == {'vocabulary'}
    assert captured['tools'][0]['function']['parameters'] == tools[0]['function']['parameters']
    encoded = json.dumps(captured, ensure_ascii=False)
    assert 'private-source-74' not in encoded and 'read_text' not in encoded
    assert (messages, tools, SCHEMA) == before


def test_overlapping_watch_and_strategy_are_processed_in_sequence(shared):
    first, second = client(shared), client(shared)
    responses, errors = [], []

    def run(selected, text, role):
        try:
            responses.append(ask(selected, text, role=role))
        except Exception as error:
            errors.append(error)

    watch = threading.Thread(target=run, args=(first, 'hold', 'watch'))
    strategy = threading.Thread(target=run, args=(second, '뒤 요청', 'strategy'))
    watch.start()
    try:
        assert shared.state.entered.wait(3)
        strategy.start()
        time.sleep(0.1)
        assert len(shared.state.calls) == 1
        assert shared.state.maximum_active == 1
    finally:
        shared.state.release.set()
        watch.join(5)
        if strategy.ident is not None:
            strategy.join(5)
    assert not watch.is_alive() and not strategy.is_alive()
    assert not errors
    assert {json.loads(row['content'])['echo'] for row in responses} == {'hold', '뒤 요청'}
    assert shared.state.maximum_active == 1


def test_saved_model_switch_clears_old_residency_before_next_request(shared):
    selected = client(shared)
    ask(selected)
    previous = shared.service.settings_generation
    changed = {**GGUF, 'gguf_model_path': 'models/gguf/renamed-9b.gguf'}
    save_settings(shared.root, changed)
    selected.invalidate(changed)
    assert shared.service.settings_generation > previous
    assert shared.state.resident is None
    reply = ask(selected)
    assert json.loads(reply['content'])['model'] == changed['gguf_model_path']
    assert shared.state.resident == changed['gguf_model_path']
    assert shared.state.loads == 2


def test_explicit_stale_generation_is_rejected_before_inference(shared):
    selected = client(shared)
    old_generation = shared.service.settings_generation
    selected.invalidate(GGUF)
    with pytest.raises((ValueError, RuntimeError), match='설정.*변경|변경.*설정'):
        ask(selected, generation=old_generation)
    assert not shared.state.calls


def test_configuration_change_while_inference_runs_discards_old_response(shared):
    selected = client(shared)
    updater = client(shared)
    responses, errors = [], []

    def infer():
        try:
            responses.append(ask(selected, 'hold'))
        except Exception as error:
            errors.append(error)

    pending = threading.Thread(target=infer)
    pending.start()
    try:
        assert shared.state.entered.wait(3)
        changed = {**GGUF, 'gguf_model_path': 'models/gguf/second-9b.gguf'}
        save_settings(shared.root, changed)
        updater.invalidate(changed)
    finally:
        shared.state.release.set()
        pending.join(5)
    assert not pending.is_alive() and not responses
    assert len(errors) == 1 and '설정' in str(errors[0]) and '변경' in str(errors[0])
    assert json.loads(ask(updater)['content'])['model'] == changed['gguf_model_path']


def test_failed_model_request_is_clear_and_service_accepts_retry(shared):
    selected = client(shared)
    shared.state.fail_next = ValueError('GGUF 모델 로딩 실패: 시험 파일')
    with pytest.raises((ValueError, RuntimeError), match='GGUF 모델 로딩 실패'):
        ask(selected, role='watch')
    recovered = ask(selected, '재시도', role='strategy')
    assert json.loads(recovered['content'])['echo'] == '재시도'
    assert shared.state.maximum_active == 1


def test_invalid_token_cannot_read_service_state_or_invoke_model(shared):
    connection = http.client.HTTPConnection('127.0.0.1', shared.service.server.server_port, timeout=3)
    try:
        connection.request('GET', '/status', headers={'X-AI-Token': 'invalid-token'})
        response = connection.getresponse()
        response.read()
        assert response.status in (401, 403)
    finally:
        connection.close()
    assert not shared.state.calls


def test_close_waits_for_active_inference_before_unloading_or_removing_endpoint(shared):
    selected = client(shared)
    failures, responses = [], []

    def infer():
        try:
            responses.append(ask(selected, 'hold'))
        except Exception as error:
            failures.append(error)

    request = threading.Thread(target=infer)
    closing = threading.Thread(target=shared.service.close, kwargs={'timeout': 3})
    request.start()
    try:
        assert shared.state.entered.wait(3)
        closing.start()
        eventually(lambda: shared.service.closing)
        assert shared.state.shutdown_calls == 0
        assert (shared.root / 'runtime' / 'ai_service.json').exists()
        assert closing.is_alive()
    finally:
        shared.state.release.set()
        request.join(5)
        if closing.ident is not None:
            closing.join(5)
    assert not request.is_alive() and not closing.is_alive()
    assert not responses and len(failures) == 1 and '종료' in str(failures[0])
    assert shared.state.shutdown_calls == 1 and shared.state.resident is None
    assert not (shared.root / 'runtime' / 'ai_service.json').exists()


def test_close_timeout_keeps_model_connection_and_allows_safe_retry(shared):
    selected = client(shared)
    errors = []

    def infer():
        try:
            ask(selected, 'hold')
        except Exception as error:
            errors.append(error)

    request = threading.Thread(target=infer)
    request.start()
    try:
        assert shared.state.entered.wait(3)
        with pytest.raises(RuntimeError, match='AI 요청 종료'):
            shared.service.close(timeout=0.03)
        assert not shared.service.closing and not shared.service.closed.is_set()
        assert shared.state.shutdown_calls == 0
        assert (shared.root / 'runtime' / 'ai_service.json').exists()
    finally:
        shared.state.release.set()
        request.join(5)
    assert not request.is_alive() and not errors
    assert json.loads(ask(selected, '계속 사용')['content'])['echo'] == '계속 사용'
    shared.service.close(timeout=1)
    assert shared.state.resident is None


def test_new_registration_after_idle_observation_prevents_idle_close(shared):
    selected = client(shared)
    ask(selected)
    # Explicitly reach the watchdog's stale-observation branch: a registration
    # already exists when an earlier "idle" observation reaches close().
    shared.service.close(only_if_idle=True)
    assert not shared.service.closed.is_set()
    assert shared.state.shutdown_calls == 0
    assert json.loads(ask(selected)['content'])['model'] == GGUF['gguf_model_path']


def test_closing_owner_does_not_spawn_second_service(shared, monkeypatch):
    selected = client(shared)
    ask(selected)
    shared.service.closing = True
    attempted = []
    def forbidden_spawn(*args, **kwargs):
        attempted.append(args)
        raise AssertionError('a second service was spawned while its owner was closing')
    monkeypatch.setattr('common_ai.client.subprocess.Popen', forbidden_spawn)
    try:
        with pytest.raises(ValueError, match='종료'):
            ask(client(shared))
        assert not attempted
    finally:
        shared.service.closing = False


def test_common_providers_can_construct_without_part3_or_heavy_model_packages():
    source = '''
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class BlockPart3(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('lab', 'Part3', 'torch', 'transformers', 'peft', 'bitsandbytes'):
            raise AssertionError('forbidden eager dependency: ' + fullname)
sys.meta_path.insert(0, BlockPart3())
from common_ai.provider import from_settings
settings = json.loads(sys.argv[2])
models = [from_settings(settings), from_settings({'provider':'ollama','model':'test:4b'}),
          from_settings({'provider':'local_lora','base_model':'unit/model',
                         'adapter_path':'models/adapter','timeout':90})]
print(json.dumps([type(item).__name__ for item in models]))
'''
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', source,
                             str(PROJECT), json.dumps(GGUF)], capture_output=True,
                            text=True, encoding='utf-8', timeout=10, creationflags=flags)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['LocalGGUF', 'GuardedOllama', 'LocalLoRA']


def test_shutdown_uses_owner_record_even_if_current_settings_are_corrupt(shared):
    selected = client(shared)
    ask(selected)
    shared.settings_file.write_text('{broken settings', encoding='utf-8')
    selected.shutdown(timeout=1)
    assert shared.service.closed.is_set()
    assert shared.state.resident is None and shared.state.shutdown_calls == 1
    assert not (shared.root / 'runtime' / 'ai_service.json').exists()


def test_shutdown_cleanup_error_is_not_mistaken_for_success(shared, monkeypatch):
    selected = client(shared)
    ask(selected)
    with monkeypatch.context() as scoped:
        scoped.setattr(shared.runtime, 'shutdown', Mock(side_effect=ValueError('모델 종료 확인 실패')))
        with pytest.raises(ValueError, match='모델 종료 확인 실패'):
            selected.shutdown(timeout=1)
        assert not shared.service.closed.is_set() and not shared.service.closing
        assert shared.state.resident is not None
        assert (shared.root / 'runtime' / 'ai_service.json').exists()


def test_closing_watch_client_interrupts_waiter_and_preserves_other_client_model(shared):
    watch, strategy = client(shared), client(shared)
    ask(strategy)
    errors, responses = [], []
    def infer():
        try:
            responses.append(ask(watch, 'hold', role='watch'))
        except Exception as error:
            errors.append(error)
    waiter = threading.Thread(target=infer)
    waiter.start()
    try:
        assert shared.state.entered.wait(3)
        started = time.monotonic()
        watch.close()
        waiter.join(1)
        assert not waiter.is_alive(), 'WATCH socket remained blocked after its client closed'
        assert time.monotonic() - started < 1
        assert not responses and len(errors) == 1 and isinstance(errors[0], ValueError)
        assert shared.state.active == 1, 'closing a client must not terminate shared inference'
        assert shared.state.shutdown_calls == 0 and not shared.service.closing
        assert watch.client_id not in shared.service.clients
        assert strategy.client_id in shared.service.clients
    finally:
        shared.state.release.set()
        waiter.join(5)
    assert json.loads(ask(strategy, '계속 연구')['content'])['echo'] == '계속 연구'
    assert shared.state.loads == 1 and shared.state.shutdown_calls == 0
    watch.close()  # Explicitly prove idempotence without another model operation.


def test_closing_queued_watch_client_prevents_its_inference(shared, monkeypatch):
    watch, strategy = client(shared), client(shared)
    errors = []
    queued = threading.Event()
    original = shared.service.chat
    def observe(payload):
        if payload['client_id'] == watch.client_id:
            queued.set()
        return original(payload)
    monkeypatch.setattr(shared.service, 'chat', observe)
    def infer(selected, text, role):
        try:
            ask(selected, text, role=role)
        except Exception as error:
            errors.append((role, error))
    running = threading.Thread(target=infer, args=(strategy, 'hold', 'strategy'))
    pending = threading.Thread(target=infer, args=(watch, '닫힌 WATCH', 'watch'))
    running.start()
    try:
        assert shared.state.entered.wait(3)
        pending.start()
        assert queued.wait(3)
        watch.close()
        pending.join(1)
        assert not pending.is_alive()
        assert shared.state.active == 1 and shared.state.shutdown_calls == 0
    finally:
        shared.state.release.set()
        running.join(5)
        if pending.ident is not None:
            pending.join(5)
    # Wait until the old queued handler releases the common turn lock.
    assert json.loads(ask(strategy, '정상 연구')['content'])['echo'] == '정상 연구'
    assert [row['messages'][-1]['content'] for row in shared.state.calls] == ['hold', '정상 연구']
    assert len(errors) == 1 and errors[0][0] == 'watch'


def test_registration_finishing_after_client_close_cannot_leave_a_client_lease(shared, monkeypatch):
    watch, strategy = client(shared), client(shared)
    ask(strategy)
    entered, release = threading.Event(), threading.Event()
    original = shared.service.dispatch
    def dispatch(route, payload):
        if route == '/register' and payload['client_id'] == watch.client_id:
            entered.set()
            assert release.wait(5)
        return original(route, payload)
    monkeypatch.setattr(shared.service, 'dispatch', dispatch)
    errors = []
    def infer():
        try:
            ask(watch, '늦은 등록', role='watch')
        except Exception as error:
            errors.append(error)
    pending = threading.Thread(target=infer)
    pending.start()
    try:
        assert entered.wait(3)
        watch.close()
        assert watch.client_id not in shared.service.clients
    finally:
        release.set()
        pending.join(5)
    assert not pending.is_alive() and len(errors) == 1 and isinstance(errors[0], ValueError)
    assert watch.client_id not in shared.service.clients
    assert strategy.client_id in shared.service.clients
    assert len(shared.state.calls) == 1, 'a closed client must not send a chat after late registration'
    assert json.loads(ask(strategy)['content'])['model'] == GGUF['gguf_model_path']


def test_client_close_does_not_cancel_shutdown_management_ack(shared, monkeypatch):
    selected = client(shared)
    ask(selected)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = shared.runtime.shutdown
    def shutdown(timeout=45):
        entered.set()
        assert release.wait(5)
        return original(timeout=timeout)
    monkeypatch.setattr(shared.runtime, 'shutdown', shutdown)
    errors = []
    def close_project():
        try:
            selected.shutdown(timeout=3)
            finished.set()
        except Exception as error:
            errors.append(error)
    closing = threading.Thread(target=close_project)
    closing.start()
    try:
        assert entered.wait(3)
        selected.close()
        assert closing.is_alive(), 'closing the client must preserve its explicit shutdown acknowledgement'
    finally:
        release.set()
        closing.join(5)
    assert not closing.is_alive() and not errors and finished.is_set()
    assert shared.service.closed.is_set() and shared.state.resident is None


def test_client_close_interrupts_body_read_after_connection_close_header(tmp_path, monkeypatch):
    """A response owns its socket even after HTTPConnection drops .sock."""
    ready, release = threading.Event(), threading.Event()
    payload = json.dumps({'content': '대기 응답', 'tool_calls': []}, ensure_ascii=False).encode('utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(payload[:5])
            self.wfile.flush()
            ready.set()
            assert release.wait(5)
            try:
                self.wfile.write(payload[5:])
            except OSError:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    host = threading.Thread(target=server.serve_forever, daemon=True)
    host.start()
    selected = Client(tmp_path)
    record = {'port': server.server_port, 'token': 'test-token'}
    monkeypatch.setattr(selected, '_connect', lambda: (record, {'generation': 0}))
    errors = []
    def read():
        try:
            selected.chat([{'role': 'user', 'content': '본문 대기'}], [], response_schema=SCHEMA)
        except Exception as error:
            errors.append(error)
    request = threading.Thread(target=read)
    request.start()
    try:
        assert ready.wait(3)
        eventually(lambda: any(connection.sock is None for connection in selected._chat_connections))
        selected.close()
        request.join(1)
        assert not request.is_alive(), 'body read ignored client cancellation'
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
    finally:
        release.set()
        request.join(5)
        server.shutdown()
        server.server_close()
        host.join(5)


CHILD = '''
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from common_ai.client import Client
selected = Client(pathlib.Path(sys.argv[2]))
schema = json.loads(sys.argv[3])
def chat(text):
    result = selected.chat([{'role':'user','content':text}], [],
        response_schema=schema, role=sys.argv[4])
    assert not any(name == 'lab' or name.startswith('lab.') or name == 'Part3' or name.startswith('Part3.')
                   for name in sys.modules), 'common client imported Part3'
    print(json.dumps(result, ensure_ascii=False), flush=True)
chat('첫 요청')
for line in sys.stdin:
    command = json.loads(line)
    if command == 'close':
        selected.close()
        print('closed', flush=True)
        break
    chat(command)
'''


class Child:
    def __init__(self, root, role, *, script=CHILD, package_root=PROJECT):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-c', script,
            str(package_root), str(root), json.dumps(SCHEMA), role], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            creationflags=flags)
        self.lines = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.lines.put(line.rstrip('\n'))
            self.lines.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def receive(self):
        try:
            value = self.lines.get(timeout=5)
        except queue.Empty:
            raise AssertionError('isolated shared AI child did not respond') from None
        if value is None:
            raise AssertionError('isolated child stopped: ' + self.process.stderr.read())
        return value

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        return self.receive()

    def cleanup(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize('last_exit', ['normal', 'forced'])
def test_independent_processes_share_model_and_only_last_client_exit_stops_service(shared, last_exit):
    watch = strategy = None
    try:
        watch = Child(shared.root, 'watch')
        initial = json.loads(watch.receive())
        strategy = Child(shared.root, 'strategy')
        second = json.loads(strategy.receive())
        assert json.loads(initial['content'])['owner_pid'] == os.getpid()
        assert json.loads(second['content'])['owner_pid'] == os.getpid()
        assert shared.state.loads == 1
        assert watch.send('close') == 'closed'
        watch.process.wait(timeout=5)
        assert shared.state.shutdown_calls == 0
        assert json.loads(json.loads(strategy.send('다음 전략'))['content'])['echo'] == '다음 전략'
        assert shared.state.loads == 1
        if last_exit == 'normal':
            assert strategy.send('close') == 'closed'
            strategy.process.wait(timeout=5)
        else:
            strategy.process.kill()
            strategy.process.wait(timeout=5)
        eventually(lambda: shared.state.shutdown_calls > 0)
        assert shared.state.resident is None
        eventually(lambda: not (shared.root / 'runtime' / 'ai_service.json').exists())
    finally:
        if watch:
            watch.cleanup()
        if strategy:
            strategy.cleanup()


STARTUP_CHILD = '''
import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from common_ai.client import Client
root = pathlib.Path(sys.argv[2])
selected = Client(root)
(root / ('ready-' + sys.argv[4])).write_text('ready')
deadline = time.monotonic() + 8
while not (root / 'start-requests').exists():
    assert time.monotonic() < deadline, 'test startup barrier timed out'
    time.sleep(0.01)
try:
    selected.chat([{'role':'user','content':'설정 없는 요청'}], [],
                  response_schema={'type':'object'}, role=sys.argv[4])
    raise AssertionError('unconfigured model was unexpectedly loaded')
except ValueError as error:
    assert 'AI 모델이 설정되지' in str(error), str(error)
assert not any(name == 'lab' or name.startswith('lab.') for name in sys.modules)
print(json.dumps({'owner':selected.record['pid'], 'created':selected.record['created'],
                  'service_id':selected.record['service_id'], 'port':selected.record['port']}), flush=True)
for line in sys.stdin:
    if json.loads(line) == 'close':
        selected.close()
        print('closed', flush=True)
        break
'''


DROPPED_SHUTDOWN_CHILD = '''
import http.server, json, os, pathlib, secrets, socket, sys, uuid
sys.path.insert(0, sys.argv[1])
from common_ai.client import project_id
from common_ai.process_identity import identity
root, mode = pathlib.Path(sys.argv[2]), sys.argv[4]
endpoint = root / 'runtime' / 'ai_service.json'
token, service_id = secrets.token_hex(32), uuid.uuid4().hex
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        assert self.path == '/shutdown' and self.headers.get('X-AI-Token') == token
        self.rfile.read(int(self.headers['Content-Length']))
        if mode == 'changed':
            record = json.loads(endpoint.read_text())
            record['service_id'] = uuid.uuid4().hex
            endpoint.write_text(json.dumps(record), encoding='utf-8')
        else:
            endpoint.unlink()
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()
        if mode != 'alive':
            os._exit(0)
server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
endpoint.parent.mkdir(parents=True)
record = {'pid':os.getpid(), 'created':identity(os.getpid()), 'project':project_id(root),
          'port':server.server_port, 'token':token, 'service_id':service_id}
endpoint.write_text(json.dumps(record), encoding='utf-8')
print(json.dumps(record), flush=True)
server.serve_forever()
'''


@pytest.mark.parametrize('mode', ['exit', 'changed'])
def test_missing_shutdown_ack_is_success_only_when_exact_owner_exited(tmp_path, mode):
    root = tmp_path / 'owned-reset-service'
    child = Child(root, mode, script=DROPPED_SHUTDOWN_CHILD)
    try:
        record = json.loads(child.receive())
        selected = Client(root)
        selected.shutdown(timeout=1)
        child.process.wait(timeout=5)
        from common_ai.process_identity import identity
        assert identity(record['pid']) != record['created']
        if mode == 'exit':
            assert not selected.endpoint.exists()
        else:
            assert json.loads(selected.endpoint.read_text())['service_id'] != record['service_id']
    finally:
        child.cleanup()


def test_dropped_shutdown_ack_with_live_owner_remains_an_error(tmp_path, monkeypatch):
    root = tmp_path / 'live-reset-service'
    child = Child(root, 'alive', script=DROPPED_SHUTDOWN_CHILD)
    monkeypatch.setattr('common_ai.client.SHUTDOWN_CONFIRM_TIMEOUT', 0.1)
    try:
        record = json.loads(child.receive())
        selected = Client(root)
        with pytest.raises(RuntimeError, match='공통 AI 종료를 확인하지 못했습니다'):
            selected.shutdown(timeout=1)
        from common_ai.process_identity import identity
        assert identity(record['pid']) == record['created']
        assert not selected.endpoint.exists(), 'endpoint deletion alone must not prove shutdown'
    finally:
        child.cleanup()


def test_simultaneous_independent_startup_creates_one_service_and_relocates_cleanly(tmp_path):
    """Two real processes race to start a copied service without any model."""
    from common_ai.process_identity import identity
    root = tmp_path / '다른 위치의 독립 프로젝트'
    for package in ('common_ai', 'moses_language'):
        shutil.copytree(PROJECT / package, root / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    save_settings(root, {})
    watch = strategy = None
    owned = None
    try:
        watch = Child(root, 'watch', script=STARTUP_CHILD, package_root=root)
        strategy = Child(root, 'strategy', script=STARTUP_CHILD, package_root=root)
        eventually(lambda: (root / 'ready-watch').exists() and (root / 'ready-strategy').exists())
        (root / 'start-requests').write_text('go', encoding='utf-8')
        first, second = json.loads(watch.receive()), json.loads(strategy.receive())
        assert first == second
        assert first['owner'] not in (os.getpid(), watch.process.pid, strategy.process.pid)
        owned = first
        record = json.loads((root / 'runtime' / 'ai_service.json').read_text('utf-8'))
        assert record['pid'] == first['owner']
        assert record['service_id'] == first['service_id']
        assert watch.send('close') == 'closed'
        watch.process.wait(timeout=5)
        assert identity(first['owner']) == first['created'], 'the other process still has a live connection'
        assert strategy.send('close') == 'closed'
        strategy.process.wait(timeout=5)
        eventually(lambda: identity(first['owner']) != first['created'], timeout=8)
        assert not (root / 'runtime' / 'ai_service.json').exists()
        assert not any((root / 'models').glob('**/*.gguf')), 'this startup test must not load real weights'
    finally:
        if watch:
            watch.cleanup()
        if strategy:
            strategy.cleanup()
        # Only this temporary project is eligible for cleanup. Do not inspect
        # or terminate any running service in the user's actual project.
        if (root / 'runtime' / 'ai_service.json').exists():
            try:
                Client(root).shutdown(timeout=3)
            except (ValueError, RuntimeError, OSError):
                pass
        if owned:
            eventually(lambda: identity(owned['owner']) != owned['created'], timeout=8)
