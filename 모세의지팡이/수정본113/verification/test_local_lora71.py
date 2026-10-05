"""Local LoRA contracts without model downloads, optional GPU packages, or MT5.

The fake worker is an actual subprocess. It proves process reuse, cancellation,
and model switching without mistaking a mock GPU allocator for a real one.
"""
from __future__ import annotations

import http.client
import contextlib
import copy
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.error
from urllib.parse import urlparse
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'Part3'))
from lab import server, unified_settings
from lab.ai import provider
from common_ai.client import Client


LOCAL = {'provider': 'local_lora', 'base_model': 'unit-test/base-model',
         'adapter_path': 'models/test-adapter', 'load_in_4bit': False,
         'max_new_tokens': 512, 'timeout': 90}
OUTSIDE = {'supported': False, 'reason': 'MOSES_SCOPE_ONLY', 'message_ko': '시험 응답'}


@pytest.fixture(autouse=True)
def isolated_model_runtime(monkeypatch):
    from lab.ai import model_runtime
    # Fake Ollama inventories belong to individual tests. Do not issue model
    # management calls after those tests' transport patches have been restored.
    model_runtime.RUNTIME.ollama_model = model_runtime.RUNTIME.pending_ollama = None
    model_runtime.RUNTIME.close()
    model_runtime.RUNTIME.lease.release()  # The previous test may simulate an unfinished Ollama request.
    runtime = model_runtime.ModelRuntime()
    monkeypatch.setattr(model_runtime, 'RUNTIME', runtime)
    yield runtime
    runtime.ollama_model = runtime.pending_ollama = None
    try:runtime.close()
    finally:runtime.lease.release()  # Failure assertions run before fixture cleanup.


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
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
        self.loaded = []
        self.events = []
        self.keep_after_unload = False
        self.allow_unlisted_unload = False
        self.chat_check = None

    def __call__(self, request, timeout):
        path = urlparse(request.full_url).path
        if path == '/api/ps':
            self.events.append(('ps', tuple(self.loaded)))
            return Reply({'models': [{'name': name} for name in self.loaded]})
        body = json.loads(request.data)
        if path == '/api/generate':
            assert body['keep_alive'] == 0 and body['stream'] is False
            self.events.append(('unload', body['model']))
            if not self.keep_after_unload:
                if not self.allow_unlisted_unload or body['model'] in self.loaded:
                    self.loaded.remove(body['model'])
            return Reply({'done': True})
        assert path == '/api/chat'
        if self.chat_check:
            self.chat_check()
        self.events.append(('chat', body['model']))
        self.loaded = [body['model']]
        return Reply({'message': {'content': json.dumps(OUTSIDE, ensure_ascii=False)}})


@pytest.fixture
def ollama_control(monkeypatch):
    stub = OllamaControl()
    monkeypatch.setattr(provider.urllib.request, 'urlopen', stub)
    return stub


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    from lab.ai import local_lora
    root = tmp_path / 'project'
    adapter = root / LOCAL['adapter_path']
    adapter.mkdir(parents=True)
    (adapter / 'adapter_config.json').write_text('{}', encoding='utf-8')
    (adapter / 'adapter_model.safetensors').write_bytes(b'placeholder, not model weights')
    script = tmp_path / 'fake_worker.py'
    script.write_text('''
import json, os, sys, time
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
loads = 0
for line in sys.stdin:
    request = json.loads(line)
    if request['op'] == 'load':
        settings, root = request['settings'], request['root']
        loads += 1
        result = None
    else:
        text = request['messages'][-1]['content'] if request['messages'] else ''
        if text == 'slow': time.sleep(10)
        if text == 'delay': time.sleep(0.3)
        if text == 'crash': os._exit(7)
        result = {'content': json.dumps({'pid': os.getpid(), 'loads': loads,
            'settings': settings, 'root': root, 'schema': request['response_schema'],
            'messages': request['messages'], 'tools': request['tools']}, ensure_ascii=False),
            'tool_calls': []}
    print(json.dumps({'id': request['id'], 'ok': True, 'result': result}), flush=True)
''', encoding='utf-8')
    command = Mock(return_value=[sys.executable, '-u', str(script)])
    monkeypatch.setattr(local_lora, 'worker_command', command)
    return root, command


def local_model(root, **changes):
    from lab.ai.local_lora import LocalLoRA
    return LocalLoRA({**LOCAL, **changes}, root=root)


def chat_value(model, text='normal', *, schema=None, tools=None):
    reply = model.chat([{'role': 'user', 'content': text}], tools or [], response_schema=schema)
    assert reply['tool_calls'] == []
    return json.loads(reply['content'])


def test_local_settings_save_read_and_factory_use_selected_values(settings_file):
    unified_settings.save_ai(LOCAL)
    saved = json.loads(settings_file.read_text('utf-8'))
    current = server.ai_settings()
    for key, expected in LOCAL.items():
        assert saved[key] == expected
        assert current[key] == expected
    selected = provider.from_settings(current)
    from lab.ai.local_lora import LocalLoRA
    assert isinstance(selected, LocalLoRA)
    assert selected.timeout == 90


def test_partial_local_setting_save_preserves_model_and_clears_both_dialogues(settings_file):
    unified_settings.save_ai(LOCAL)
    server.AI_SESSIONS['strategy'] = object()
    server.BACKTEST_COMMAND_SESSIONS['backtest'] = object()
    unified_settings.save_ai({'timeout': 120})
    assert server.ai_settings() == {**LOCAL, 'timeout': 120, 'model': None, 'watch_enabled': True}
    assert not server.AI_SESSIONS
    assert not server.BACKTEST_COMMAND_SESSIONS


@pytest.mark.parametrize('changes', [
    {'provider': 'cloud'}, {'base_model': ''}, {'adapter_path': ''},
    {'adapter_path': '../outside'}, {'adapter_path': 'models/../../outside'},
    {'adapter_path': 'C:/models/test'}, {'adapter_path': '//server/share'},
    {'load_in_4bit': 'true'}, {'load_in_4bit': 1},
    {'max_new_tokens': 0}, {'max_new_tokens': True}, {'timeout': 9},
    {'base_url': 'https://example.invalid'},
])
def test_invalid_local_settings_do_not_write_or_destroy_sessions(settings_file, changes):
    unified_settings.save_ai(LOCAL)
    before = settings_file.read_bytes()
    old = object()
    server.AI_SESSIONS['keep'] = old
    with pytest.raises(ValueError):
        unified_settings.save_ai(changes)
    assert settings_file.read_bytes() == before
    assert server.AI_SESSIONS['keep'] is old


@pytest.mark.parametrize('missing', ['base_model', 'adapter_path'])
def test_local_settings_require_explicit_model_and_adapter(settings_file, missing):
    values = dict(LOCAL)
    values.pop(missing)
    with pytest.raises((ValueError, RuntimeError)):
        unified_settings.save_ai(values)
    assert not settings_file.exists()


def test_existing_ollama_selection_and_watch_default_are_preserved(settings_file):
    unified_settings.save_ai({'model': 'unit-test:1b', 'timeout': 90})
    assert server.ai_settings() == {'provider': 'ollama', 'model': 'unit-test:1b', 'timeout': 90, 'watch_enabled': True}


def test_ollama_and_local_provider_construction_need_no_optional_model_packages():
    code = '''
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class NoHeavyPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'transformers', 'peft', 'bitsandbytes', 'lmformatenforcer'):
            raise AssertionError('Eager model package import: ' + fullname)
sys.meta_path.insert(0, NoHeavyPackages())
from lab.ai.provider import from_settings
ollama = from_settings({'model': 'unit-test:1b'})
local = from_settings(json.loads(sys.argv[2]))
print(json.dumps({'ollama': ollama.model, 'local': type(local).__name__}))
'''
    finished = subprocess.run([sys.executable, '-I', '-c', code, str(PROJECT / 'Part3'),
                               json.dumps(LOCAL)], text=True, capture_output=True, timeout=10)
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == {'ollama': 'unit-test:1b', 'local': 'LocalLoRA'}


def test_both_settings_routes_accept_local_provider(settings_file):
    saved = server.ai_post('/api/ai/settings', dict(LOCAL))
    assert saved['ok'] is True
    assert saved['settings']['provider'] == 'local_lora'
    unified_settings.save_ai({'adapter_path': 'models/replacement-adapter'})
    assert server.ai_settings()['adapter_path'] == 'models/replacement-adapter'


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


def test_local_load_error_is_reported_and_http_server_keeps_running(settings_file, monkeypatch):
    from lab.ai.local_lora import LocalLoRA
    from lab.ai import shared_provider
    monkeypatch.setattr(shared_provider, 'shared_from_settings',
                        lambda settings, **_kwargs: provider.from_settings(settings))
    failure = Mock(side_effect=ValueError('LoRA 파일이 없습니다.'))
    monkeypatch.setattr(LocalLoRA, 'chat', failure)
    host = server.LabServer(0)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    try:
        http_request(host, 'POST', '/api/mo/settings', {'group': 'ai', 'changes': LOCAL})
        error = http_request(host, 'POST', '/api/ai/chat',
                             {'session': 'error', 'message': '전략 해석'}, expected=400)
        assert 'LoRA 파일이 없습니다' in error['error']
        assert http_request(host, 'GET', '/api/ai/settings')['settings']['provider'] == 'local_lora'
        monkeypatch.setattr(LocalLoRA, 'chat', lambda *_args, **_kwargs:
                            {'content': json.dumps(OUTSIDE, ensure_ascii=False), 'tool_calls': []})
        restored = http_request(host, 'POST', '/api/ai/chat',
                                {'session': 'retry', 'message': '다시 해석'})
        assert restored['result']['reason'] == 'MOSES_SCOPE_ONLY'
    finally:
        host.shutdown()
        host.server_close()
        thread.join(2)


def test_lazy_first_load_reuses_one_worker_across_requests_and_sessions(fake_worker, ollama_control):
    root, command = fake_worker
    first = local_model(root)
    second = local_model(root)
    command.assert_not_called()
    one = chat_value(first)
    two = chat_value(first)
    three = chat_value(second)
    assert one['pid'] == two['pid'] == three['pid']
    assert one['loads'] == two['loads'] == three['loads'] == 1
    command.assert_called_once()
    assert one['settings'] == LOCAL


def test_local_protocol_keeps_messages_tools_and_selected_schema(fake_worker, ollama_control):
    from lab.ai.schema import output_schema
    from lab.ai.backtest_commands import command_schema
    root, _ = fake_worker
    model = local_model(root)
    default = chat_value(model)
    assert default['schema'] == output_schema()
    chosen = command_schema()
    tools = [{'type': 'function', 'function': {'name': 'vocabulary', 'parameters': {'type': 'object'}}}]
    result = chat_value(model, '명령 해석', schema=chosen, tools=tools)
    assert result['schema'] == chosen
    assert result['messages'][-1] == {'role': 'user', 'content': '명령 해석'}
    assert result['messages'][0]['role'] == 'system'
    assert 'moses_contract' in json.loads(result['messages'][0]['content'])
    assert {row['function']['name'] for row in result['tools']} == {'vocabulary'}
    assert result['tools'][0]['function']['parameters'] == tools[0]['function']['parameters']


def test_local_loading_unloads_all_ollama_models_and_checks_inventory(fake_worker, ollama_control):
    root, _ = fake_worker
    ollama_control.loaded = ['first:1b', 'second:2b']
    chat_value(local_model(root))
    assert not ollama_control.loaded
    assert ('unload', 'first:1b') in ollama_control.events
    assert ('unload', 'second:2b') in ollama_control.events
    assert ollama_control.events[-1] == ('ps', ())


def test_ollama_switch_terminates_local_worker_before_native_chat(fake_worker, ollama_control,
                                                                 isolated_model_runtime):
    root, _ = fake_worker
    chat_value(local_model(root))
    process = isolated_model_runtime.worker.process
    assert process.poll() is None
    ollama_control.chat_check = lambda: pytest.fail('old local model still alive') if process.poll() is None else None
    provider.from_settings({'model': 'next:2b'}).chat([], [])
    assert process.poll() is not None
    assert isolated_model_runtime.worker is None
    assert ollama_control.events[-1] == ('chat', 'next:2b')


def test_ollama_switch_unloads_other_models_but_keeps_selected_alias(ollama_control):
    ollama_control.loaded = ['selected:latest', 'other:1b']
    provider.from_settings({'model': 'selected'}).chat([], [])
    assert ('unload', 'other:1b') in ollama_control.events
    assert ('unload', 'selected:latest') not in ollama_control.events
    assert ollama_control.events[-1] == ('chat', 'selected')


def test_adapter_switch_waits_for_old_worker_exit_before_loading_replacement(fake_worker, ollama_control,
                                                                          isolated_model_runtime):
    root, command = fake_worker
    original = chat_value(local_model(root))
    previous = isolated_model_runtime.worker.process
    replacement = root / 'models/replacement'
    replacement.mkdir()
    (replacement / 'adapter_config.json').write_text('{}', encoding='utf-8')
    (replacement / 'adapter_model.bin').write_bytes(b'placeholder')
    next_value = chat_value(local_model(root, adapter_path='models/replacement'))
    assert previous.poll() is not None
    assert original['pid'] != next_value['pid']
    assert next_value['settings']['adapter_path'] == 'models/replacement'
    assert command.call_count == 2


@pytest.mark.parametrize('text,error', [('slow', '시간'), ('crash', '응답')])
def test_failed_worker_is_terminated_and_a_new_request_can_recover(fake_worker, ollama_control,
                                                                 isolated_model_runtime, text, error):
    root, command = fake_worker
    model = local_model(root)
    before = chat_value(model)
    previous = isolated_model_runtime.worker.process
    model.timeout = 0.2
    with pytest.raises(ValueError, match=error):
        chat_value(model, text)
    assert previous.poll() is not None
    assert isolated_model_runtime.worker is None
    model.timeout = 5
    recovered = chat_value(model)
    assert recovered['pid'] != before['pid']
    assert command.call_count == 2


def test_unconfirmed_ollama_unload_never_starts_local_worker(fake_worker, ollama_control):
    root, command = fake_worker
    ollama_control.loaded = ['stuck:1b']
    ollama_control.keep_after_unload = True
    model = local_model(root)
    model.timeout = 0.12
    with pytest.raises(ValueError, match='시간'):
        chat_value(model)
    command.assert_not_called()
    assert ollama_control.loaded == ['stuck:1b']


@pytest.mark.parametrize('reply', [{'models': None}, {'models': [{}]}, {'wrong': []}])
def test_invalid_ollama_inventory_blocks_new_model_loading(fake_worker, monkeypatch, reply):
    root, command = fake_worker
    monkeypatch.setattr(provider.urllib.request, 'urlopen', lambda *_args, **_kwargs: Reply(reply))
    with pytest.raises(ValueError, match='목록 응답'):
        chat_value(local_model(root))
    command.assert_not_called()


def test_local_provider_works_when_ollama_listener_is_refused(fake_worker, monkeypatch):
    import errno
    root, _ = fake_worker
    refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))
    monkeypatch.setattr(provider.urllib.request, 'urlopen', Mock(side_effect=refused))
    assert chat_value(local_model(root))['loads'] == 1


def test_missing_adapter_returns_clear_error_without_worker_or_network(tmp_path, monkeypatch):
    from lab.ai import local_lora
    command = Mock(side_effect=AssertionError('worker must not start'))
    connection = Mock(side_effect=AssertionError('network must not open'))
    monkeypatch.setattr(local_lora, 'worker_command', command)
    monkeypatch.setattr(provider.urllib.request, 'urlopen', connection)
    with pytest.raises(ValueError, match='LoRA 학습 파일이 없습니다'):
        chat_value(local_model(tmp_path))
    command.assert_not_called()
    connection.assert_not_called()


def test_moved_project_keeps_relative_model_settings_working(fake_worker, ollama_control,
                                                            isolated_model_runtime):
    root, _ = fake_worker
    base = root / 'models/base'
    base.mkdir()
    settings = {**LOCAL, 'base_model': 'models/base'}
    first = chat_value(local_model(root, base_model='models/base'))
    isolated_model_runtime.close()
    moved = root.with_name('moved-project')
    root.rename(moved)
    second = chat_value(local_model(moved, base_model='models/base'))
    assert first['settings'] == second['settings'] == settings
    assert Path(first['root']) == root
    assert Path(second['root']) == moved


def test_machine_lease_blocks_second_owner_until_first_releases():
    from lab.ai.model_runtime import MachineLease
    first, second = MachineLease(), MachineLease()
    try:
        first.acquire()
        with pytest.raises(ValueError, match='다른 MOSES 창'):
            second.acquire()
        first.release()
        second.acquire()
    finally:
        first.release()
        second.release()


def test_parallel_requests_serialize_and_waiting_timeout_does_not_kill_active_model(
        fake_worker, ollama_control, isolated_model_runtime):
    root, command = fake_worker
    model = local_model(root)
    ready = chat_value(model)
    current = isolated_model_runtime.worker.process
    result = []
    thread = threading.Thread(target=lambda: result.append(chat_value(model, 'delay')))
    thread.start()
    limit = time.monotonic() + 2
    while not isolated_model_runtime.lock.locked() and time.monotonic() < limit:
        time.sleep(0.005)
    assert isolated_model_runtime.lock.locked()
    waiting = local_model(root)
    waiting.timeout = 0.05
    try:
        with pytest.raises(ValueError, match='다른 AI 요청'):
            chat_value(waiting)
    finally:
        thread.join(3)
    assert result[0]['pid'] == ready['pid']
    assert current.poll() is None
    command.assert_called_once()


def test_settings_change_during_inference_unloads_worker_before_next_turn(
        fake_worker, ollama_control, isolated_model_runtime):
    root, _ = fake_worker
    model = local_model(root)
    chat_value(model)
    previous = isolated_model_runtime.worker.process
    responses = []
    errors = []
    def request():
        try:
            responses.append(chat_value(model, 'delay'))
        except ValueError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=request)
    thread.start()
    limit = time.monotonic() + 2
    while not isolated_model_runtime.lock.locked() and time.monotonic() < limit:
        time.sleep(0.005)
    assert isolated_model_runtime.lock.locked()
    isolated_model_runtime.invalidate()
    thread.join(3)
    assert not responses
    assert len(errors) == 1 and 'AI 설정이 변경' in errors[0]
    assert previous.poll() is not None
    with pytest.raises(ValueError, match='AI 설정이 변경'):
        chat_value(model)
    replacement = chat_value(local_model(root))
    assert replacement['pid'] != previous.pid


@pytest.fixture
def model_stubs(monkeypatch):
    """Only ML boundaries are simulated; existing Agent/intent validation is real."""
    from lab.ai import lora_worker
    state = types.SimpleNamespace(gpu=True, output=json.dumps(OUTSIDE, ensure_ascii=False),
                                  context=8192, architectures=['UnitCausalLM'])
    class InputTensor:
        shape = (1, 2)
    class Batch(dict):
        def to(self, device):
            state.device = device
            return self
    class Sent:
        def tolist(self): return [1, 2]
    class Tokenizer:
        chat_template = 'unit-test-template'
        all_special_ids = [2]
        eos_token_id = 2
        pad_token_id = 0
        model_max_length = 8192
        def __len__(self): return 3
        def encode(self, *_args, **_kwargs): return [0]
        def decode(self, ids, **kwargs):
            if kwargs.get('skip_special_tokens'): return state.output
            return ''.join(str(item) for item in ids)
        def apply_chat_template(self, history, **kwargs):
            state.history = copy.deepcopy(history)
            state.template_kwargs = kwargs
            return '한글 요청과 출력 계약'
        def __call__(self, text, **kwargs):
            state.tokenizer_kwargs = kwargs
            return Batch(input_ids=InputTensor())
    class FakeModel:
        config = types.SimpleNamespace(max_position_embeddings=8192)
        def eval(self): state.eval_called = True
        def get_input_embeddings(self):
            return types.SimpleNamespace(weight=types.SimpleNamespace(device='unit-device'))
        def generate(self, **kwargs):
            state.generated = kwargs
            state.allowed = kwargs['prefix_allowed_tokens_fn'](0, Sent())
            return [[1, 2, 3, 4]]
    state.tokenizer = Tokenizer()
    state.model = FakeModel()
    torch = types.ModuleType('torch')
    torch.cuda = types.SimpleNamespace(is_available=lambda: state.gpu)
    torch.inference_mode = contextlib.nullcontext
    transformers = types.ModuleType('transformers')
    transformers.AutoConfig = types.SimpleNamespace(from_pretrained=Mock(side_effect=lambda *_args, **_kwargs:
        types.SimpleNamespace(architectures=state.architectures)))
    transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=Mock(return_value=state.model))
    transformers.AutoModelForMultimodalLM = types.SimpleNamespace(from_pretrained=Mock(return_value=state.model))
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=Mock(return_value=state.tokenizer))
    transformers.BitsAndBytesConfig = Mock(side_effect=lambda **kwargs: kwargs)
    peft = types.ModuleType('peft')
    peft.PeftModel = types.SimpleNamespace(from_pretrained=Mock(return_value=state.model))
    enforcer = types.ModuleType('lmformatenforcer')
    def tokenizer_data(*args):
        state.tokenizer_data_args = args
        return types.SimpleNamespace(tokenizer_alphabet='abc')
    enforcer.TokenEnforcerTokenizerData = Mock(side_effect=tokenizer_data)
    def schema_parser(schema, config):
        state.decode_schema = schema
        state.parser_config = config
        return types.SimpleNamespace(config=config)
    enforcer.JsonSchemaParser = Mock(side_effect=schema_parser)
    enforcer.TokenEnforcer = Mock(return_value=types.SimpleNamespace(
        get_allowed_tokens=lambda _tokens: types.SimpleNamespace(allowed_tokens=[3, 4])))
    parser = types.ModuleType('lmformatenforcer.characterlevelparser')
    parser.CharacterLevelParserConfig = lambda: types.SimpleNamespace(alphabet='abc', max_json_array_length=20)
    schema = types.ModuleType('jsonschema')
    schema.Draft202012Validator = Mock(return_value=types.SimpleNamespace(iter_errors=lambda _data: iter([])))
    for name, module in {'torch': torch, 'transformers': transformers, 'peft': peft,
                         'lmformatenforcer': enforcer,
                         'lmformatenforcer.characterlevelparser': parser, 'jsonschema': schema}.items():
        monkeypatch.setitem(sys.modules, name, module)
    state.transformers, state.peft, state.schema = transformers, peft, schema
    state.worker = lora_worker.Worker()
    yield state
    if state.worker.lease is not None:
        state.worker.lease.release()


@pytest.mark.parametrize('conditional', [False, True])
def test_worker_uses_configured_base_adapter_and_architecture_once(fake_worker, model_stubs, conditional):
    root, _ = fake_worker
    state = model_stubs
    state.architectures = ['UnitForConditionalGeneration'] if conditional else ['UnitCausalLM']
    state.worker.load(LOCAL, root)
    state.worker.load(LOCAL, root)
    chosen = (state.transformers.AutoModelForMultimodalLM if conditional
              else state.transformers.AutoModelForCausalLM).from_pretrained
    chosen.assert_called_once()
    assert chosen.call_args.args == (LOCAL['base_model'],)
    assert chosen.call_args.kwargs['trust_remote_code'] is False
    assert chosen.call_args.kwargs['local_files_only'] is True
    assert 'quantization_config' not in chosen.call_args.kwargs
    state.peft.PeftModel.from_pretrained.assert_called_once_with(
        state.model, str(root / LOCAL['adapter_path']), local_files_only=True, is_trainable=False)
    state.transformers.AutoTokenizer.from_pretrained.assert_called_once_with(
        LOCAL['base_model'], local_files_only=True, trust_remote_code=False)
    assert state.eval_called


def test_worker_four_bit_setting_reaches_quantization_loader(fake_worker, model_stubs):
    root, _ = fake_worker
    state = model_stubs
    state.worker.load({**LOCAL, 'load_in_4bit': True}, root)
    state.transformers.BitsAndBytesConfig.assert_called_once_with(load_in_4bit=True)
    assert state.transformers.AutoModelForCausalLM.from_pretrained.call_args.kwargs[
        'quantization_config'] == {'load_in_4bit': True}


def test_worker_four_bit_without_supported_gpu_fails_before_model_load(fake_worker, model_stubs):
    root, _ = fake_worker
    state = model_stubs
    state.gpu = False
    with pytest.raises(ValueError, match='CUDA GPU'):
        state.worker.load({**LOCAL, 'load_in_4bit': True}, root)
    state.transformers.AutoModelForCausalLM.from_pretrained.assert_not_called()
    state.peft.PeftModel.from_pretrained.assert_not_called()


def test_worker_local_base_path_resolves_after_project_move(fake_worker, model_stubs):
    root, _ = fake_worker
    (root / 'models/base').mkdir()
    moved = root.with_name('moved-loader-project')
    root.rename(moved)
    state = model_stubs
    state.worker.load({**LOCAL, 'base_model': 'models/base'}, moved)
    assert state.transformers.AutoModelForCausalLM.from_pretrained.call_args.args == (str(moved / 'models/base'),)


def test_worker_missing_optional_package_returns_installation_error(fake_worker, monkeypatch):
    import builtins
    from lab.ai.lora_worker import Worker
    root, _ = fake_worker
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'torch': raise ImportError('test: torch absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    worker = Worker()
    try:
        with pytest.raises(ValueError, match='실행 패키지가 없습니다'):
            worker.load(LOCAL, root)
    finally:
        if worker.lease is not None:
            worker.lease.release()


def test_real_worker_returns_korean_optional_package_error_over_utf8_json_lines(
        tmp_path, monkeypatch, ollama_control, isolated_model_runtime):
    from lab.ai import local_lora
    root = tmp_path / 'real-worker-project'
    adapter = root / LOCAL['adapter_path']
    adapter.mkdir(parents=True)
    (adapter / 'adapter_config.json').write_text('{}', encoding='utf-8')
    (adapter / 'adapter_model.bin').write_bytes(b'placeholder')
    code = '''
import importlib.abc, runpy, sys
class NoTorch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'torch': raise ImportError('test: optional torch absent')
sys.meta_path.insert(0, NoTorch())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
    command = [sys.executable, '-u', '-c', code,
               str(Path(local_lora.__file__).with_name('lora_worker.py')),
               '--parent-pid', str(os.getpid())]
    monkeypatch.setattr(local_lora, 'worker_command', lambda: command)
    with pytest.raises(ValueError, match='로컬 LoRA 실행 패키지가 없습니다'):
        chat_value(local_model(root))
    assert isolated_model_runtime.worker is None


def test_worker_inference_preserves_history_and_uses_selected_generation_length(fake_worker, model_stubs):
    from lab.ai.schema import output_schema
    root, _ = fake_worker
    state = model_stubs
    state.worker.load(LOCAL, root)
    messages = [{'role': 'system', 'content': '기존 계약'}, {'role': 'user', 'content': '전략 해석'}]
    original = copy.deepcopy(messages)
    result = state.worker.chat(messages, [], output_schema())
    assert messages == original
    assert state.history[-1] == original[-1]
    assert state.history[0]['content'].startswith('기존 계약')
    assert state.template_kwargs == {'tokenize': False, 'add_generation_prompt': True, 'enable_thinking': False}
    assert state.tokenizer_kwargs == {'return_tensors': 'pt', 'add_special_tokens': False}
    assert state.generated['max_new_tokens'] == LOCAL['max_new_tokens']
    assert state.generated['do_sample'] is False
    assert state.generated['pad_token_id'] == 0
    assert state.device == 'unit-device'
    assert state.allowed == [3, 4]
    assert state.parser_config.max_json_array_length == sys.maxsize
    assert '한' in state.parser_config.alphabet
    assert json.loads(result['content']) == OUTSIDE
    assert result['tool_calls'] == []
    state.schema.Draft202012Validator.assert_called_once_with(output_schema())


def test_worker_context_overflow_is_reported_before_generation(fake_worker, model_stubs):
    from lab.ai.schema import output_schema
    root, _ = fake_worker
    state = model_stubs
    state.worker.load(LOCAL, root)
    state.tokenizer.model_max_length = 4
    with pytest.raises(ValueError, match='처리 길이를 초과'):
        state.worker.chat([{'role': 'user', 'content': 'long'}], [], output_schema())
    assert not hasattr(state, 'generated')


def test_tool_history_conversion_does_not_mutate_existing_agent_messages():
    from lab.ai.lora_worker import normalize_messages
    messages = [{'role': 'assistant', 'tool_calls': [{'id': 'v', 'type': 'function',
        'function': {'name': 'vocabulary', 'arguments': '{"number": 2}'}}]},
        {'role': 'tool', 'name': 'vocabulary', 'tool_call_id': 'v', 'content': '{}'}]
    original = copy.deepcopy(messages)
    normalized = normalize_messages(messages)
    assert normalized[0]['tool_calls'][0]['function']['arguments'] == {'number': 2}
    assert normalized[1] == original[1]
    assert messages == original


@pytest.mark.parametrize('text,error', [('not JSON', '완전한 JSON'), ('[]', 'JSON 객체')])
def test_worker_parser_rejects_non_json_object_output(model_stubs, text, error):
    from lab.ai.lora_worker import parse_reply
    from lab.ai.schema import output_schema
    with pytest.raises(ValueError, match=error):
        parse_reply(text, output_schema(), [])


def test_worker_parser_enforces_schema_validation_boundary(model_stubs):
    from lab.ai.lora_worker import parse_reply
    from lab.ai.schema import output_schema
    state = model_stubs
    state.schema.Draft202012Validator.return_value.iter_errors = lambda _data: iter([ValueError('invalid fixture')])
    with pytest.raises(ValueError, match='응답 형식'):
        parse_reply(json.dumps(OUTSIDE), output_schema(), [])
    state.schema.Draft202012Validator.assert_called_once_with(output_schema())


def test_worker_tool_calls_rejoin_existing_agent_loop_before_user_confirmation(model_stubs):
    from lab.ai.agent import Agent
    from lab.ai.lora_worker import parse_reply
    from lab.ai.schema import output_schema
    intent = {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'],
        'steps': [{'kind': 'TREND', 'tf': '15m', 'direction': 'LONG'},
                  {'kind': 'WONBI_TOUCH', 'tf': '15m', 'side': 'LOWER'}],
        'order_mode': 'SIMULTANEOUS', 'within_sec': None, 'final_window_sec': None,
        'final': {'kind': 'OZ', 'tf': '1m', 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '전략 의도'}
    outputs = iter([{'tool_calls': [{'name': 'vocabulary', 'arguments': {}}]}, intent])
    class ParsedProvider:
        def chat(self, messages, tools, *, response_schema=None):
            assert {row['function']['name'] for row in tools} == {
                'vocabulary', 'list_specials', 'describe_special'}
            return parse_reply(json.dumps(next(outputs), ensure_ascii=False),
                               response_schema or output_schema(), tools)
    agent = Agent(ParsedProvider())
    response = agent.send('15분 상승추세에서 하단 원비를 터치하면 1분 브레이커 올존')
    assert response['can_apply'] is True
    assert response['actions'] == [{'tool': 'vocabulary', 'ok': True, 'error': None}]
    assert agent.messages[-2]['role'] == 'tool'
    assert agent.apply()['strategy_intent']['steps'][1]['kind'] == 'WONBI_TOUCH'


def test_decoder_schema_view_preserves_authoritative_schema():
    from lab.ai.lora_worker import decoding_schema
    from lab.ai.backtest_commands import command_schema
    current = command_schema()
    original = copy.deepcopy(current)
    decoded = decoding_schema(current)
    assert current == original
    assert decoded['properties']['action']['anyOf'][-1] == {'type': 'null'}
    assert decoded['properties']['request']['properties']['mode']['anyOf'][-1] == {'type': 'null'}


def failed_ollama_request(monkeypatch, error):
    """The inference request fails after its model inventory check succeeded."""
    with monkeypatch.context() as scoped:
        scoped.setattr(provider.OpenAICompatible, 'chat', Mock(side_effect=error))
        with pytest.raises(type(error)):
            provider.from_settings({'model': 'pending:1b'}).chat([], [])


@pytest.mark.parametrize('error', [ValueError('mapped Ollama connection failure'),
                                 TimeoutError('Ollama inference deadline')])
def test_invalidate_confirms_pending_ollama_unload_before_releasing_machine_lease(
        monkeypatch, ollama_control, isolated_model_runtime, error):
    from lab.ai.model_runtime import ModelRuntime
    runtime = isolated_model_runtime
    failed_ollama_request(monkeypatch, error)
    assert runtime.pending_ollama == 'pending:1b'
    assert runtime.lease.handle is not None
    ollama_control.allow_unlisted_unload = True
    runtime.invalidate()
    assert runtime.pending_ollama is None
    assert runtime.ollama_model is None
    assert runtime.lease.handle is None
    contender = ModelRuntime()
    try:
        contender.lease.acquire()
        assert contender.lease.handle is not None
    finally:
        contender.close()
        contender.lease.release()


@pytest.mark.parametrize('target_kind', ['local_lora', 'ollama'])
def test_followup_explicitly_unloads_pending_model_even_when_inventory_is_empty(
        fake_worker, monkeypatch, ollama_control, isolated_model_runtime, target_kind):
    root, command = fake_worker
    runtime = isolated_model_runtime
    failed_ollama_request(monkeypatch, ValueError('previous request failed'))
    assert ollama_control.loaded == []
    ollama_control.allow_unlisted_unload = True
    def assert_pending_confirmed():
        index = ollama_control.events.index(('unload', 'pending:1b'))
        assert ollama_control.events[index + 1] == ('ps', ())
        assert runtime.pending_ollama is None
    if target_kind == 'local_lora':
        original_command = command.return_value
        def start_after_confirmation():
            assert_pending_confirmed()
            return original_command
        command.side_effect = start_after_confirmation
        assert chat_value(local_model(root))['loads'] == 1
        command.assert_called_once()
    else:
        ollama_control.chat_check = assert_pending_confirmed
        provider.from_settings({'model': 'replacement:2b'}).chat([], [])
        assert ollama_control.events[-1] == ('chat', 'replacement:2b')
    assert_pending_confirmed()


@pytest.mark.parametrize('failure', ['unload_request', 'unload_confirmation'])
def test_pending_request_that_cannot_be_confirmed_blocks_followup_inference(
        fake_worker, monkeypatch, ollama_control, isolated_model_runtime, failure):
    root, command = fake_worker
    runtime = isolated_model_runtime
    failed_ollama_request(monkeypatch, ValueError('previous request failed'))
    ollama_control.allow_unlisted_unload = True
    if failure == 'unload_request':
        def rejected_unload(request, timeout):
            if request.full_url.endswith('/api/generate'):
                raise urllib.error.URLError('simulated unload connection failure')
            return ollama_control(request, timeout)
        monkeypatch.setattr(provider.urllib.request, 'urlopen', rejected_unload)
    else:
        ollama_control.loaded = ['pending:1b']
        ollama_control.keep_after_unload = True
    model = local_model(root)
    model.timeout = 0.12
    with pytest.raises(ValueError, match='이전 Ollama 요청의 종료를 확인하지 못했습니다'):
        chat_value(model)
    command.assert_not_called()
    assert runtime.worker is None
    assert runtime.pending_ollama == 'pending:1b'
    assert runtime.lease.handle is not None
