"""GGUF provider integration contracts without real weights, GPU, or MT5.

The llama.cpp boundary is fake here. The existing Agent, intent validation,
Recipe conversion, settings routes, and model ownership rules remain real.
The separate GGUF engine tests exercise a real loopback subprocess.
"""
from __future__ import annotations

import copy
import errno
import http.client
import json
import struct
import subprocess
import sys
import threading
import time
import types
import urllib.error
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'Part3'))
from lab import server, storage, unified_settings
from lab.ai import provider


GGUF = {'provider': 'local_gguf', 'gguf_model_path': 'models/test-model.gguf',
        'llama_server_path': 'runtime/llama-server.exe', 'gguf_context_size': 4096,
        'gguf_gpu_layers': 0, 'gguf_threads': 2, 'max_new_tokens': 256,
        'timeout': 90}
OUTSIDE = {'supported': False, 'reason': 'MOSES_SCOPE_ONLY', 'message_ko': '시험 응답'}


@pytest.fixture(autouse=True)
def runtime(monkeypatch, tmp_path):
    from lab.ai import model_runtime
    value = model_runtime.ModelRuntime()
    value.lease = model_runtime.MachineLease('test_gguf_' + tmp_path.name + '.lock')
    monkeypatch.setattr(model_runtime, 'RUNTIME', value)
    yield value
    value.close()
    value.lease.release()


@pytest.fixture
def model_files(tmp_path):
    root = tmp_path / 'project'
    model, executable = root / GGUF['gguf_model_path'], root / GGUF['llama_server_path']
    model.parent.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    executable.write_bytes(b'fixture executable; never executed')
    executable.chmod(0o755)
    return root


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    from common_ai.client import Client
    path = tmp_path / 'settings' / 'ai_settings.json'
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', path)
    monkeypatch.setattr(server, '_AI_CLIENT', None)
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {})
    monkeypatch.setattr(server, 'RESEARCH_SESSIONS', {})
    for method in ('_start', '_request'):
        monkeypatch.setattr(Client, method, Mock(side_effect=AssertionError('No common AI service in this fixture')))
    yield path
    server.close_ai_client()
    Client._start.assert_not_called()
    Client._request.assert_not_called()


class Reply:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, *_): return json.dumps(self.value).encode('utf-8')


class OllamaControl:
    def __init__(self):
        self.loaded, self.events = [], []
        self.keep_after_unload = False
        self.before_chat = None

    def __call__(self, request, timeout):
        route = urlparse(request.full_url).path
        if route == '/api/ps':
            self.events.append(('ps', tuple(self.loaded)))
            return Reply({'models': [{'name': name} for name in self.loaded]})
        body = json.loads(request.data)
        if route == '/api/generate':
            assert body['keep_alive'] == 0
            self.events.append(('unload', body['model']))
            if not self.keep_after_unload and body['model'] in self.loaded:
                self.loaded.remove(body['model'])
            return Reply({'done': True})
        assert route == '/api/chat'
        if self.before_chat: self.before_chat()
        self.loaded = [body['model']]
        self.events.append(('chat', body['model']))
        return Reply({'message': {'content': json.dumps(OUTSIDE)}})


@pytest.fixture
def ollama(monkeypatch):
    control = OllamaControl()
    monkeypatch.setattr(provider.urllib.request, 'urlopen', control)
    return control


@pytest.fixture
def fake_engine(monkeypatch):
    from lab.ai import gguf_engine
    state = types.SimpleNamespace(instances=[], calls=[], replies=[], failure=None,
                                  entered=None, release=None, start_failure=None)

    class FakeProcess:
        def __init__(self): self.returncode = None
        def poll(self): return self.returncode

    class FakeServer:
        def __init__(self, settings, root):
            self.settings, self.root = copy.deepcopy(settings), Path(root)
            self.process = None
            self.starts = self.closes = 0
            state.instances.append(self)

        def start(self, deadline):
            # This checks the observable safety property: one active model.
            assert all(item is self or item.process is None or item.process.poll() is not None
                       for item in state.instances)
            assert deadline > time.monotonic()
            self.starts += 1
            self.process = FakeProcess()
            if state.start_failure: raise state.start_failure

        def chat(self, messages, tools, schema, deadline):
            state.calls.append(copy.deepcopy({'messages': messages, 'tools': tools,
                                              'schema': schema}))
            if state.entered:
                state.entered.set()
                assert state.release.wait(3), 'test inference release timeout'
            if state.failure: raise state.failure
            return state.replies.pop(0) if state.replies else {
                'content': json.dumps(OUTSIDE, ensure_ascii=False), 'tool_calls': []}

        def close(self):
            self.closes += 1
            if self.process: self.process.returncode = 0

    monkeypatch.setattr(gguf_engine, 'GGUFServer', FakeServer)
    return state


def local_model(root, **changes):
    from lab.ai.local_gguf import LocalGGUF
    return LocalGGUF({**GGUF, **changes}, root=root)


def ask(model, text='전략 해석', *, tools=None, schema=None):
    return model.chat([{'role': 'user', 'content': text}], tools or [], response_schema=schema)


def test_selected_gguf_settings_save_read_and_factory(settings_file):
    from lab.ai.local_gguf import LocalGGUF
    unified_settings.save_ai(GGUF)
    saved = json.loads(settings_file.read_text('utf-8'))
    current = server.ai_settings()
    for key, expected in GGUF.items():
        assert saved[key] == current[key] == expected
    selected = provider.from_settings(current)
    assert isinstance(selected, LocalGGUF)
    assert selected.timeout == GGUF['timeout']


def test_partial_save_preserves_gguf_and_clears_only_setting_dependent_dialogues(settings_file):
    unified_settings.save_ai(GGUF)
    server.AI_SESSIONS['strategy'] = object()
    server.BACKTEST_COMMAND_SESSIONS['backtest'] = object()
    unified_settings.save_ai({'timeout': 120})
    current = server.ai_settings()
    assert current['timeout'] == 120
    assert current['gguf_model_path'] == GGUF['gguf_model_path']
    assert current['llama_server_path'] == GGUF['llama_server_path']
    assert not server.AI_SESSIONS and not server.BACKTEST_COMMAND_SESSIONS


@pytest.mark.parametrize('changes', [
    {'gguf_model_path': ''}, {'llama_server_path': '   '},
    {'gguf_model_path': '../outside.gguf'}, {'gguf_model_path': 'models/../../outside.gguf'},
    {'gguf_model_path': 'C:/models/model.gguf'}, {'gguf_model_path': '//server/share/model.gguf'},
    {'llama_server_path': '../llama-server.exe'}, {'gguf_chat_template_path': '../template.jinja'},
    {'gguf_context_size': 0}, {'gguf_context_size': True},
    {'gguf_gpu_layers': -2}, {'gguf_gpu_layers': True},
    {'gguf_threads': 0}, {'gguf_threads': True},
    {'max_new_tokens': 0}, {'timeout': 9},
])
def test_invalid_settings_leave_saved_file_and_conversation_intact(settings_file, changes):
    unified_settings.save_ai(GGUF)
    before = settings_file.read_bytes()
    previous = object()
    server.AI_SESSIONS['keep'] = previous
    with pytest.raises(ValueError): unified_settings.save_ai(changes)
    assert settings_file.read_bytes() == before
    assert server.AI_SESSIONS['keep'] is previous


@pytest.mark.parametrize('missing', ['gguf_model_path'])
def test_model_must_be_explicitly_configured(settings_file, missing):
    values = dict(GGUF)
    values.pop(missing)
    with pytest.raises((ValueError, RuntimeError)): unified_settings.save_ai(values)
    assert not settings_file.exists()


def test_configuration_is_lazy_and_does_not_import_optional_ml_packages():
    code = '''
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class NoHeavyPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'transformers', 'peft', 'bitsandbytes', 'llama_cpp'):
            raise AssertionError('Eager model package import: ' + fullname)
sys.meta_path.insert(0, NoHeavyPackages())
from lab.ai.provider import from_settings
print(type(from_settings(json.loads(sys.argv[2]))).__name__)
'''
    finished = subprocess.run([sys.executable, '-I', '-c', code, str(PROJECT / 'Part3'),
                               json.dumps(GGUF)], text=True, capture_output=True, timeout=10)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == 'LocalGGUF'


def test_project_relocation_resolves_model_executable_and_template(model_files):
    from lab.ai.local_gguf import check_files
    template = model_files / 'models/template.jinja'
    template.write_text('{{ messages }}', encoding='utf-8')
    settings = {**GGUF, 'gguf_chat_template_path': 'models/template.jinja'}
    initial = check_files(settings, model_files)
    moved = model_files.with_name('relocated-project')
    model_files.rename(moved)
    relocated = check_files(settings, moved)
    assert [path.relative_to(model_files).as_posix() for path in initial] == [
        path.relative_to(moved).as_posix() for path in relocated]
    assert settings['gguf_model_path'] == 'models/test-model.gguf'
    assert all(path.is_file() for path in relocated)


@pytest.mark.parametrize('missing', ['gguf_model_path', 'llama_server_path'])
def test_missing_file_is_clear_error_before_launch(model_files, fake_engine, ollama, missing):
    (model_files / GGUF[missing]).unlink()
    with pytest.raises(ValueError): ask(local_model(model_files))
    assert not fake_engine.instances


@pytest.mark.parametrize('bad_bytes', [b'', b'not a GGUF model', b'GGUF'])
def test_invalid_model_header_is_rejected_before_launch(model_files, fake_engine, ollama, bad_bytes):
    (model_files / GGUF['gguf_model_path']).write_bytes(bad_bytes)
    with pytest.raises(ValueError, match='GGUF'): ask(local_model(model_files))
    assert not fake_engine.instances


def test_first_request_loads_once_and_reuses_model_across_sessions(model_files, fake_engine, ollama):
    first, second = local_model(model_files), local_model(model_files)
    assert not fake_engine.instances
    assert ask(first)['tool_calls'] == []
    ask(first)
    ask(second)
    assert len(fake_engine.instances) == 1
    assert fake_engine.instances[0].starts == 1
    assert len(fake_engine.calls) == 3


def test_gguf_inference_works_with_no_ollama_listener(model_files, fake_engine, monkeypatch):
    def absent_ollama(request, timeout):
        assert urlparse(request.full_url).path == '/api/ps'
        raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'fixture: Ollama absent'))
    monkeypatch.setattr(provider.urllib.request, 'urlopen', absent_ollama)
    assert ask(local_model(model_files))['tool_calls'] == []
    assert len(fake_engine.instances) == 1


def test_unexpected_cached_server_exit_reloads_on_next_request(model_files, fake_engine, ollama):
    selected = local_model(model_files)
    ask(selected)
    previous = fake_engine.instances[0]
    previous.process.returncode = 7
    ask(selected)
    assert len(fake_engine.instances) == 2
    assert fake_engine.instances[-1].process.poll() is None


def test_history_readonly_tools_and_exact_requested_schema_are_preserved(model_files, fake_engine, ollama):
    from lab.ai.schema import output_schema
    from lab.ai.backtest_commands import command_schema
    from lab.ai.agent import external_system_prompt
    from lab.ai.tools import ReadOnlyWorkspace, TOOL_SPECS
    model = local_model(model_files)
    ask(model)
    assert fake_engine.calls[-1]['schema'] == output_schema()
    workspace = ReadOnlyWorkspace()
    messages = [{'role': 'system', 'content': external_system_prompt(workspace)},
                {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'v', 'type': 'function', 'function': {'name': 'vocabulary', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'v', 'name': 'vocabulary',
                 'content': json.dumps({'ok': True, 'result': workspace.vocabulary()}, ensure_ascii=False)},
                {'role': 'user', 'content': '백테스트 명령'}]
    original = copy.deepcopy(messages)
    chosen_schema = command_schema()
    model.chat(messages, TOOL_SPECS, response_schema=chosen_schema)
    observed = fake_engine.calls[-1]
    assert observed['schema'] == chosen_schema
    assert observed['messages'][-1] == original[-1]
    contract = json.loads(observed['messages'][0]['content'])['moses_contract']
    assert contract['presets'] == workspace.list_specials()
    assert {row['function']['name'] for row in observed['tools']} == {
        'vocabulary', 'list_specials', 'describe_special'}
    tool_result = json.loads(next(row['content'] for row in observed['messages'] if row['role'] == 'tool'))
    assert tool_result['ok'] and 'TREND' in tool_result['result']['intent_kinds']
    assert not {'source_text', 'source_code', 'data', 'runtime'} & tool_result['result'].keys()
    assert 'read_project_code' not in json.dumps(observed, ensure_ascii=False)
    assert 'search_project_code' not in json.dumps(observed, ensure_ascii=False)
    assert 'read_doc' not in json.dumps(observed, ensure_ascii=False)
    assert messages == original


def test_switching_models_stops_old_model_before_new_load(model_files, fake_engine, ollama):
    ask(local_model(model_files))
    previous = fake_engine.instances[0]
    other = model_files / 'models/another.gguf'
    other.write_bytes((model_files / GGUF['gguf_model_path']).read_bytes())
    ask(local_model(model_files, gguf_model_path='models/another.gguf'))
    assert previous.process.poll() is not None
    assert len(fake_engine.instances) == 2
    assert fake_engine.instances[-1].settings['gguf_model_path'] == 'models/another.gguf'


def test_settings_invalidation_stops_cached_model_and_rejects_stale_provider(
        model_files, fake_engine, ollama, runtime):
    previous_provider = local_model(model_files)
    ask(previous_provider)
    previous = fake_engine.instances[0]
    runtime.invalidate()
    assert previous.process.poll() is not None
    with pytest.raises(ValueError, match='설정이 변경'): ask(previous_provider)
    ask(local_model(model_files))
    assert len(fake_engine.instances) == 2


# 수정본149: GGUF requests no longer probe or unload Ollama models they do not own (tests/test_ollama_selected149.py).


def test_switching_to_ollama_terminates_gguf_before_native_inference(model_files, fake_engine, ollama, runtime):
    ask(local_model(model_files))
    previous = fake_engine.instances[0]
    def no_old_model(): assert previous.process.poll() is not None
    ollama.before_chat = no_old_model
    provider.from_settings({'model': 'next:2b'}).chat([], [])
    assert runtime.worker is None
    assert ollama.events[-1] == ('chat', 'next:2b')


# 수정본138 removed the LoRA provider and its switching test.


@pytest.mark.parametrize('during_start', [False, True])
def test_failed_gguf_worker_is_closed_and_retry_recovers(
        model_files, fake_engine, ollama, runtime, during_start):
    error = ValueError('GGUF 로딩·추론 오류')
    if during_start: fake_engine.start_failure = error
    else: fake_engine.failure = error
    with pytest.raises(ValueError, match='GGUF'): ask(local_model(model_files))
    assert fake_engine.instances[0].process.poll() is not None
    assert runtime.worker is None
    fake_engine.start_failure = fake_engine.failure = None
    ask(local_model(model_files))
    assert len(fake_engine.instances) == 2


def test_configuration_change_during_request_discards_result_and_stops_worker(
        model_files, fake_engine, ollama, runtime):
    selected = local_model(model_files)
    fake_engine.entered, fake_engine.release = threading.Event(), threading.Event()
    received, errors = [], []
    def infer():
        try: received.append(ask(selected))
        except ValueError as exc: errors.append(str(exc))
    thread = threading.Thread(target=infer)
    thread.start()
    try:
        assert fake_engine.entered.wait(2)
        runtime.invalidate()
    finally:
        fake_engine.release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not received
    assert len(errors) == 1 and '설정이 변경' in errors[0]
    assert fake_engine.instances[0].process.poll() is not None


def test_generation_change_with_close_failure_releases_request_lock_and_keeps_model_lease(
        model_files, fake_engine, ollama, monkeypatch, runtime):
    selected = local_model(model_files)
    ask(selected)
    previous = fake_engine.instances[0]
    close_successfully = previous.close
    monkeypatch.setattr(previous, 'close', Mock(side_effect=ValueError('GGUF 종료를 확인하지 못했습니다.')))
    try:
        with pytest.raises(ValueError, match='종료를 확인'):
            with runtime._turn(selected):
                runtime.invalidate()
        assert not runtime.lock.locked(), 'cleanup failure must not permanently block every retry'
        assert runtime.worker is previous
        assert previous.process.poll() is None
        assert runtime.lease.handle is not None, 'an unconfirmed live model must retain its machine lease'
    finally:
        monkeypatch.setattr(previous, 'close', close_successfully)
        # Keep failure diagnostics safe to run against the unfixed code too.
        if runtime.lock.locked(): runtime.lock.release()
    # A new configuration retries cleanup before loading its replacement.
    assert ask(local_model(model_files, gguf_threads=3))['tool_calls'] == []
    assert previous.process.poll() is not None
    assert len(fake_engine.instances) == 2
    assert fake_engine.instances[-1].settings['gguf_threads'] == 3


def strategy(tf='3m'):
    return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [
            {'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG', 'bar_state': 'CLOSED'},
            {'kind': 'WONBI_TOUCH', 'tfs': [tf], 'side': 'LOWER', 'direction': 'LONG'}],
        'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None,
        'final_window_sec': 600, 'persistent': True,
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '조건 충족 시 브레이커 감시'}


def structured(value):
    return {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}


def test_existing_agent_tool_loop_interpret_modify_reconfirm_apply_stays_intact(
        model_files, fake_engine, ollama, monkeypatch, tmp_path):
    from lab.ai.agent import Agent
    generated_root = tmp_path / 'output'
    generated_root.mkdir()
    monkeypatch.setattr(storage, 'ROOT', generated_root)
    fake_engine.replies = [
        {'content': '', 'tool_calls': [{'id': 'lookup', 'name': 'vocabulary', 'arguments': {}}]},
        structured(strategy()), structured(strategy('15m'))]
    agent = Agent(local_model(model_files))
    monkeypatch.setitem(server.AI_SESSIONS, 'gguf-flow', agent)
    first = server.ai_post('/api/ai/chat', {'session': 'gguf-flow', 'message': '15분 추세와 3분 하단 원비'})
    assert first['can_apply'] and not first['is_revision']
    assert first['actions'] == [{'tool': 'vocabulary', 'ok': True, 'error': None}]
    assert fake_engine.calls[1]['messages'][-1]['role'] == 'tool'
    assert {row['function']['name'] for row in fake_engine.calls[1]['tools']} == {
        'vocabulary', 'list_specials', 'describe_special'}
    assert list(generated_root.iterdir()) == []
    second = server.ai_post('/api/ai/chat', {'session': 'gguf-flow', 'message': '아니 원비는 15분이야'})
    assert second['can_apply'] and second['is_revision']
    expected = copy.deepcopy(first['result'])
    expected['interpretation']['steps'][1]['tfs'] = ['15m']
    assert second['result'] == expected
    correction = json.loads(fake_engine.calls[-1]['messages'][-1]['content'])
    assert correction['message'] == '아니 원비는 15분이야'
    assert correction['context']['current_strategy'] == first['result']
    assert list(generated_root.iterdir()) == []
    with pytest.raises(ValueError, match='최신 해석'):
        server.ai_post('/api/ai/apply', {'session': 'gguf-flow', 'revision': first['revision']})
    applied = server.ai_post('/api/ai/apply', {'session': 'gguf-flow', 'revision': second['revision']})
    assert applied['recipe']['strategy_intent'] == second['result']['interpretation']
    assert list(generated_root.iterdir()) == []
    assert agent.candidate is None
    assert len(fake_engine.instances) == 1


def http_request(host, method, route, data=None, expected=200):
    client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
    try:
        client.request(method, route, None if data is None else json.dumps(data),
                       {'Content-Type': 'application/json', 'X-Lab-Token': host.token})
        response = client.getresponse()
        result = json.loads(response.read())
        assert response.status == expected, result
        return result
    finally:
        client.close()


def test_gguf_error_does_not_kill_web_server_and_new_request_can_retry(
        model_files, settings_file, fake_engine, ollama, monkeypatch):
    from lab.ai import local_gguf, shared_provider
    monkeypatch.setattr(shared_provider, 'shared_from_settings',
                        lambda settings, **_kwargs: provider.from_settings(settings))
    monkeypatch.setattr(local_gguf, 'PROJECT_ROOT', model_files)
    unified_settings.save_ai(GGUF)
    fake_engine.failure = ValueError('GGUF 모델을 로드하지 못했습니다.')
    host = server.LabServer(0)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    try:
        error = http_request(host, 'POST', '/api/ai/chat',
                             {'session': 'error', 'message': '전략 해석'}, expected=400)
        assert 'GGUF' in error['error']
        assert http_request(host, 'GET', '/api/ai/settings')['settings']['provider'] == 'local_gguf'
        fake_engine.failure = None
        restored = http_request(host, 'POST', '/api/ai/chat',
                                {'session': 'retry', 'message': '다시 해석'})
        assert restored['result']['reason'] == 'MOSES_SCOPE_ONLY'
    finally:
        host.shutdown()
        host.server_close()
        thread.join(2)


def test_program_shutdown_stops_cached_gguf_and_refuses_new_inference(model_files, fake_engine, ollama, runtime):
    selected = local_model(model_files)
    ask(selected)
    previous = fake_engine.instances[0]
    runtime.shutdown()
    assert previous.process.poll() is not None
    assert runtime.worker is None
    with pytest.raises(ValueError, match='종료 중'): ask(selected)


def catalog_file(root, name):
    folder = root / 'models' / 'gguf'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    return path


def serve_in_thread():
    host = server.LabServer(0)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    return host, thread


def test_model_catalog_is_readonly_and_does_not_select_or_load_a_model(
        model_files, settings_file, fake_engine, monkeypatch, runtime):
    from lab.ai import gguf_catalog
    catalog_file(model_files, 'MOSES-Qwen3.5-4B-Q4_K_M.gguf')
    catalog_file(model_files, 'MOSES-Qwen3.5-9B-Q4_K_M.gguf')
    read_models = gguf_catalog.models
    monkeypatch.setattr(gguf_catalog, 'models', lambda: read_models(model_files))
    host, thread = serve_in_thread()
    try:
        result = http_request(host, 'GET', '/api/ai/gguf-models')
        assert result['directory'] == 'models/gguf'
        assert result['selected'] is None
        listed = {row['name']: row for row in result['models']}
        assert set(listed) == {'MOSES-Qwen3.5-4B-Q4_K_M.gguf', 'MOSES-Qwen3.5-9B-Q4_K_M.gguf'}
        for name, row in listed.items():
            assert row['name'] == name
            assert row['path'] == 'models/gguf/' + name
            assert set(row) == {'name', 'path', 'bytes'}, 'list only the original filename, path and file size'
        assert not settings_file.exists()
        assert not fake_engine.instances and runtime.worker is None
        assert server.ai_settings()['provider'] == 'gemini'
        assert not server.ai_settings().get('gemini_model') and not server.ai_settings().get('gemini_api_key')
        assert server.ai_settings()['model'] is None
        with pytest.raises(RuntimeError, match='모델이 설정되지'):
            provider.from_settings(server.ai_settings())
    finally:
        host.shutdown(); host.server_close(); thread.join(2)


def test_catalog_selection_save_uses_selected_model_and_switch_closes_cached_model(
        model_files, settings_file, fake_engine, ollama, monkeypatch):
    from lab.ai import gguf_catalog, local_gguf
    small = catalog_file(model_files, 'MOSES-Qwen3.5-4B-Q4_K_M.gguf')
    large = catalog_file(model_files, 'MOSES-Qwen3.5-9B-Q4_K_M.gguf')
    small_path, large_path = small.relative_to(model_files).as_posix(), large.relative_to(model_files).as_posix()
    read_models = gguf_catalog.models
    monkeypatch.setattr(gguf_catalog, 'models', lambda: read_models(model_files))
    monkeypatch.setattr(local_gguf, 'PROJECT_ROOT', model_files)
    host, thread = serve_in_thread()
    try:
        result = http_request(host, 'GET', '/api/ai/gguf-models')
        assert {row['path'] for row in result['models']} == {small_path, large_path}
        http_request(host, 'POST', '/api/mo/settings', {
            'group': 'ai', 'changes': {**GGUF, 'gguf_model_path': small_path}})
        assert http_request(host, 'GET', '/api/ai/gguf-models')['selected'] == small_path
        assert not fake_engine.instances, 'listing and saving must not load weights'
        ask(provider.from_settings(server.ai_settings()))
        old = fake_engine.instances[0]
        assert old.settings['gguf_model_path'] == small_path
        # An invalid new path cannot destroy a working selected model.
        http_request(host, 'POST', '/api/mo/settings', {
            'group': 'ai', 'changes': {'gguf_model_path': '../outside.gguf'}}, expected=400)
        assert server.ai_settings()['gguf_model_path'] == small_path
        ask(provider.from_settings(server.ai_settings()))
        assert len(fake_engine.instances) == 1 and old.process.poll() is None
        http_request(host, 'POST', '/api/mo/settings', {
            'group': 'ai', 'changes': {'gguf_model_path': large_path}})
        assert old.process.poll() is not None
        assert len(fake_engine.instances) == 1, 'saving prepares the next request without eager loading'
        assert http_request(host, 'GET', '/api/ai/gguf-models')['selected'] == large_path
        ask(provider.from_settings(server.ai_settings()))
        assert len(fake_engine.instances) == 2
        assert fake_engine.instances[-1].settings['gguf_model_path'] == large_path
        assert json.loads(settings_file.read_text('utf-8'))['gguf_model_path'] == large_path
    finally:
        host.shutdown(); host.server_close(); thread.join(2)


def test_empty_catalog_and_noise_files_do_not_create_a_default_model(
        model_files, settings_file, fake_engine, monkeypatch):
    from lab.ai import gguf_catalog
    folder = model_files / 'models' / 'gguf'
    folder.mkdir(parents=True)
    (folder / 'readme.txt').write_text('모델 설명', encoding='utf-8')
    (folder / 'adapter.safetensors').write_bytes(b'not gguf')
    read_models = gguf_catalog.models
    monkeypatch.setattr(gguf_catalog, 'models', lambda: read_models(model_files))
    host, thread = serve_in_thread()
    try:
        listed = http_request(host, 'GET', '/api/ai/gguf-models')
        assert listed['models'] == [] and listed['selected'] is None
        assert not settings_file.exists() and not fake_engine.instances
    finally:
        host.shutdown(); host.server_close(); thread.join(2)


def test_catalog_excludes_later_split_parts_and_corrupt_headers_without_loading(
        model_files, fake_engine):
    from lab.ai import gguf_catalog
    first = catalog_file(model_files, 'Qwen3.5-4B-00001-of-00003.gguf')
    catalog_file(model_files, 'Qwen3.5-4B-00002-of-00003.gguf')
    catalog_file(model_files, 'Qwen3.5-4B-00003-of-00003.gguf')
    invalid = catalog_file(model_files, 'broken-9B.gguf')
    invalid.write_bytes(b'not a valid GGUF')
    result = gguf_catalog.models(model_files)
    assert [row['path'] for row in result['models']] == [first.relative_to(model_files).as_posix()]
    assert result.get('warnings')
    serialized = json.dumps(result, ensure_ascii=False).replace('\\\\', '/')
    assert str(model_files).replace('\\', '/') not in serialized
    assert not fake_engine.instances


def test_catalog_remains_portable_after_project_folder_move(model_files, fake_engine):
    from lab.ai import gguf_catalog
    catalog_file(model_files, 'MOSES-Qwen3.5-4B-Q4_K_M.gguf')
    catalog_file(model_files, 'MOSES-Qwen3.5-9B-Q4_K_M.gguf')
    before = gguf_catalog.models(model_files)
    moved = model_files.with_name('relocated-model-catalog')
    model_files.rename(moved)
    after = gguf_catalog.models(moved)
    assert before == after
    assert {row['path'] for row in after['models']} == {
        'models/gguf/MOSES-Qwen3.5-4B-Q4_K_M.gguf', 'models/gguf/MOSES-Qwen3.5-9B-Q4_K_M.gguf'}
    assert not fake_engine.instances

