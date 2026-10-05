"""Owned llama-server transport without downloading weights or running MT5.

The subprocess is a small real loopback HTTP server. It exercises readiness,
authentication, deadlines and process lifetime. The semantic JSON validator is
tested separately when its optional pure Python dependency is installed.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'Part3'))
from lab.ai import gguf_engine

SETTINGS = {'provider': 'local_gguf', 'gguf_model_path': 'models/model.gguf',
            'llama_server_path': 'runtime/llama-server.exe', 'timeout': 90,
            'gguf_context_size': 8192, 'gguf_gpu_layers': 0, 'max_new_tokens': 128}
SCHEMA = {'type': 'object', 'required': ['answer'], 'additionalProperties': False,
          'properties': {'answer': {'type': 'string'}}}
TOOLS = [{'type': 'function', 'function': {'name': 'read_text',
          'parameters': {'type': 'object', 'required': ['path'], 'additionalProperties': False,
                         'properties': {'path': {'type': 'string'}}}}}]
MESSAGES = [{'role': 'system', 'content': '계약을 유지하세요.'}, {'role': 'user', 'content': '전략 시험'}]

FAKE_SERVER = r'''
import argparse, contextlib, http.server, json, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--config', required=True)
p.add_argument('--port', type=int, required=True)
p.add_argument('--api-key', required=True)
p.add_argument('--host', required=True)
args, unknown = p.parse_known_args()
config = json.loads(Path(args.config).read_text('utf-8'))
if config.get('exit_code'):
    print('model load failed ' + str(config.get('diagnostic', '')), flush=True)
    sys.exit(config['exit_code'])
class Handler(http.server.BaseHTTPRequestHandler):
    count = 0
    chats = 0
    def log_message(self, *a): pass
    def reply(self, value, status=200, raw=None):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8') if raw is None else raw
        if self.path == '/v1/chat/completions':
            time.sleep(config.get('header_delay', 0))
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.path == '/v1/chat/completions':
            time.sleep(config.get('body_delay', 0))
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)
    def note(self, payload=None):
        with Path(config['requests']).open('a', encoding='utf-8') as file:
            file.write(json.dumps({'path': self.path, 'host': self.headers['Host'],
                'auth': self.headers.get('Authorization'), 'payload': payload}, ensure_ascii=False) + '\n')
    def do_GET(self):
        self.note()
        if self.path == '/health':
            Handler.count += 1
            time.sleep(config.get('health_delay', 0))
            if Handler.count <= config.get('loading_count', 0):
                return self.reply({'error': {'message': 'Loading model'}}, 503)
            return self.reply(config.get('health', {'status': 'ok'}), config.get('health_status', 200))
        if self.headers.get('Authorization') != 'Bearer ' + args.api_key or config.get('deny_models'):
            return self.reply({'error': {'message': 'Invalid API Key'}}, 401)
        if self.path == '/v1/models':
            return self.reply(config.get('models', {'data': [{'id': 'fake-model'}]}))
        self.reply({}, 404)
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.note(payload)
        if self.headers.get('Authorization') != 'Bearer ' + args.api_key:
            return self.reply({'error': {'message': 'Invalid API Key'}}, 401)
        if self.path != '/v1/chat/completions':
            return self.reply({}, 404)
        Handler.chats += 1
        time.sleep(config.get('chat_delay', 0))
        if config.get('chat_error'):
            return self.reply({'error': config['chat_error']}, config.get('chat_status', 400))
        if config.get('invalid_json'):
            return self.reply({}, raw=b'not-json')
        if 'wire_reply' in config:
            return self.reply(config['wire_reply'])
        content = json.dumps(config.get('content', {'answer': '완료'}), ensure_ascii=False)
        self.reply({'choices': [{'finish_reason': config.get('finish_reason', 'stop'),
                    'message': {'content': content}}], 'truncated': config.get('truncated', False)})
http.server.ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
'''


@pytest.fixture
def harness(tmp_path, monkeypatch):
    (tmp_path / 'models').mkdir()
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'models/model.gguf').write_bytes(b'GGUF' + bytes(20))
    (tmp_path / 'runtime/llama-server.exe').write_bytes(b'fake executable fixture')
    fake = tmp_path / 'fake_server.py'
    fake.write_text(FAKE_SERVER, 'utf-8')
    config_file = tmp_path / 'fake_config.json'
    records = tmp_path / 'requests.jsonl'
    config = {'requests': str(records)}
    captured = []
    children = []
    original_popen = subprocess.Popen
    def spawn(command, **kwargs):
        config_file.write_text(json.dumps(config), 'utf-8')
        captured.append((command, kwargs))
        child = original_popen([sys.executable, '-u', str(fake), '--config', str(config_file), *command[1:]], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(gguf_engine.subprocess, 'Popen', spawn)
    engine = gguf_engine.GGUFServer(SETTINGS, tmp_path)
    yield {'engine': engine, 'root': tmp_path, 'config': config, 'captured': captured,
           'children': children, 'records': records, 'original_popen': original_popen}
    engine.close()
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


def _records(harness):
    return [json.loads(line) for line in harness['records'].read_text('utf-8').splitlines()]


def _wire_parser(monkeypatch):
    seen = []
    def parse(text, schema, tools):
        seen.append((text, schema, tools))
        return {'content': text, 'tool_calls': []}
    # Isolate HTTP/lifetime from the separately tested optional validator;
    # this fixture never claims to validate a canonical intent.
    monkeypatch.setattr(gguf_engine, '_parse_reply', parse)
    return seen


def test_settings_command_loopback_only_and_environment(harness, monkeypatch):
    engine = harness['engine']
    engine.settings.update({'gguf_threads': 4, 'gguf_gpu_layers': 17,
                            'gguf_chat_template_path': 'runtime/template.jinja'})
    (harness['root'] / 'runtime/template.jinja').write_text('{{ messages }}', 'utf-8')
    monkeypatch.setenv('LLAMA_ARG_HOST', '0.0.0.0')
    monkeypatch.setenv('LLAMA_ARG_MODEL_URL', 'https://invalid.example/model.gguf')
    monkeypatch.setenv('LLAMA_API_KEY', 'old-test-key')
    engine.start(time.monotonic() + 4)
    command, kwargs = harness['captured'][0]
    for flag, value in [('--model', str(harness['root'] / 'models/model.gguf')),
                        ('--ctx-size', '8192'), ('--n-gpu-layers', '17'), ('--threads', '4'),
                        ('--host', '127.0.0.1'), ('--parallel', '1'),
                        ('--chat-template-file', str(harness['root'] / 'runtime/template.jinja'))]:
        assert command[command.index(flag) + 1] == value
    assert command[command.index('--api-key') + 1] == engine.api_key
    assert len(engine.api_key) >= 32
    assert all(flag in command for flag in ('--jinja', '--offline', '--no-context-shift', '--no-webui'))
    assert not any(key.startswith('LLAMA_ARG_') or key == 'LLAMA_API_KEY' for key in kwargs['env'])
    assert kwargs.get('shell', False) is False


def test_ready_waits_for_health_and_authenticated_owned_listener(harness):
    harness['config']['loading_count'] = 2
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    records = _records(harness)
    assert [r['path'] for r in records] == ['/health', '/health', '/health', '/v1/models']
    assert all(r['host'] == '127.0.0.1:' + str(engine.port) for r in records)
    assert all(r['auth'] == 'Bearer ' + engine.api_key for r in records)
    assert engine.ready and engine.model == 'fake-model'


def test_server_and_secret_reused_across_requests(harness, monkeypatch):
    seen = _wire_parser(monkeypatch)
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    first_pid, first_key = engine.process.pid, engine.api_key
    first = engine.chat(MESSAGES, TOOLS, SCHEMA, time.monotonic() + 4)
    engine.start(time.monotonic() + 4)
    second = engine.chat(MESSAGES, [], SCHEMA, time.monotonic() + 4)
    assert json.loads(first['content']) == {'answer': '완료'}
    assert first == second
    assert engine.process.pid == first_pid and engine.api_key == first_key
    assert len(harness['captured']) == 1
    assert seen[0][1:] == (SCHEMA, TOOLS) and seen[1][1:] == (SCHEMA, [])
    bodies = [r['payload'] for r in _records(harness) if r['path'] == '/v1/chat/completions']
    assert bodies[0]['stream'] is False and bodies[0]['max_tokens'] == 128
    assert bodies[0]['chat_template_kwargs']['enable_thinking'] is False
    assert bodies[0]['response_format']['schema']['anyOf'][0] == SCHEMA
    assert bodies[1]['response_format']['schema'] == SCHEMA
    assert 'tools' not in bodies[0] and bodies[0]['parse_tool_calls'] is False
    assert 'tool_calls' in bodies[0]['messages'][0]['content']


def test_history_conversion_retains_original_intent_and_tool_output():
    history = [*MESSAGES, {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'abc', 'type': 'function', 'function': {'name': 'read_text', 'arguments': '{"path":"설명.md"}'}}]},
        {'role': 'tool', 'name': 'read_text', 'tool_call_id': 'abc', 'content': '{"ok":true,"result":"전체 문서"}'},
        {'role': 'user', 'content': '직전 canonical intent를 수정하세요.'}]
    original = copy.deepcopy(history)
    normalized = gguf_engine._history(history)
    assert history == original
    assert json.loads(normalized[2]['content']) == {'tool_calls': [{'id': 'abc', 'name': 'read_text', 'arguments': {'path': '설명.md'}}]}
    result = json.loads(normalized[3]['content'].split('\n', 1)[1])
    assert result == {'tool_call_id': 'abc', 'name': 'read_text', 'content': history[3]['content']}
    assert normalized[3]['role'] == 'user' and normalized[4] == history[4]


def test_multiple_calls_to_same_tool_preserve_correlation_ids():
    history = [{'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'first', 'function': {'name': 'read_text', 'arguments': '{"path":"첫째.md"}'}},
        {'id': 'second', 'function': {'name': 'read_text', 'arguments': '{"path":"둘째.md"}'}}]},
        {'role': 'tool', 'name': 'read_text', 'tool_call_id': 'first', 'content': '첫째 문서'},
        {'role': 'tool', 'name': 'read_text', 'tool_call_id': 'second', 'content': '둘째 문서'}]
    result = gguf_engine._history(history)
    calls = json.loads(result[0]['content'])['tool_calls']
    outputs = [json.loads(message['content'].split('\n', 1)[1]) for message in result[1:]]
    assert [call['id'] for call in calls] == [output['tool_call_id'] for output in outputs] == ['first', 'second']
    assert calls[0]['arguments']['path'] == '첫째.md' and outputs[0]['content'] == '첫째 문서'
    assert calls[1]['arguments']['path'] == '둘째.md' and outputs[1]['content'] == '둘째 문서'


@pytest.mark.parametrize('history', [[{'role': 'system', 'content': ['unsupported']}],
                                    [{'role': 'invalid', 'content': 'x'}],
                                    [{'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_text', 'arguments': 'bad'}}]}],
                                    [{'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_text', 'arguments': '[]'}}]}]])
def test_malformed_history_cannot_silently_lose_data(history):
    with pytest.raises(ValueError, match='GGUF'):
        gguf_engine._history(history)


@pytest.mark.parametrize('patch,message', [({'health': {'status': 'wrong'}}, '준비 상태'),
                                         ({'models': {'data': []}}, '모델 목록'),
                                         ({'models': {'data': [{'id': None}]}}, '모델 식별자'),
                                         ({'deny_models': True}, 'HTTP 401'),
                                         ({'health_status': 302}, 'HTTP 302')])
def test_unready_or_foreign_server_fails_without_sending_strategy(harness, patch, message):
    harness['config'].update(patch)
    with pytest.raises(ValueError, match=message):
        harness['engine'].start(time.monotonic() + 4)
    assert harness['engine'].process is None
    assert harness['children'][0].poll() is not None
    assert not any(r['path'] == '/v1/chat/completions' for r in _records(harness))


def test_loading_timeout_stops_exact_owned_process(harness):
    harness['config']['loading_count'] = 9999
    start = time.monotonic()
    with pytest.raises(ValueError, match='대기 시간'):
        harness['engine'].start(start + 0.5)
    assert time.monotonic() - start < 3
    assert harness['engine'].process is None and harness['children'][0].poll() is not None


def test_slow_health_probe_uses_overall_load_deadline(harness):
    harness['config']['health_delay'] = 1.2
    start = time.monotonic()
    with pytest.raises(ValueError, match='대기 시간'):
        harness['engine'].start(start + 2.5)
    elapsed = time.monotonic() - start
    assert 2.3 <= elapsed < 4
    assert harness['engine'].process is None


def test_dll_or_incompatible_model_exit_is_readable_and_sanitized(harness):
    harness['config'].update({'exit_code': 17, 'diagnostic': str(harness['root'])})
    with pytest.raises(ValueError, match='종료 코드 17') as error:
        harness['engine'].start(time.monotonic() + 4)
    assert str(harness['root']) not in str(error.value)
    assert harness['engine'].process is None


@pytest.mark.parametrize('patch,message', [({'invalid_json': True}, '올바른 JSON'),
                                         ({'wire_reply': []}, 'JSON 객체'),
                                         ({'wire_reply': {'choices': []}}, '응답 목록'),
                                         ({'wire_reply': {'choices': [{'message': {'content': None}}]}}, '응답 형식'),
                                         ({'finish_reason': 'length'}, '길이 제한'),
                                         ({'truncated': True}, '길이 제한'),
                                         ({'chat_error': {'type': 'exceed_context_size_error'}}, '처리 길이'),
                                         ({'chat_error': {'message': 'out of memory'}}, '메모리'),
                                         ({'chat_error': {'message': 'unsupported grammar'}}, 'HTTP 400')])
def test_http_and_incomplete_output_errors_release_owned_server(harness, patch, message, monkeypatch):
    _wire_parser(monkeypatch)
    harness['config'].update(patch)
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    child = engine.process
    with pytest.raises(ValueError, match=message):
        engine.chat(MESSAGES, [], SCHEMA, time.monotonic() + 4)
    assert child.poll() is not None and engine.process is None


def test_chat_timeout_and_large_reply_do_not_leave_server_running(harness, monkeypatch):
    harness['config']['chat_delay'] = 2
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    start = time.monotonic()
    with pytest.raises(ValueError, match='대기 시간'):
        engine.chat(MESSAGES, [], SCHEMA, start + 0.25)
    assert time.monotonic() - start < 3 and engine.process is None
    harness['config'].pop('chat_delay')
    engine.start(time.monotonic() + 4)
    monkeypatch.setattr(gguf_engine, 'MAX_REPLY_BYTES', 20)
    with pytest.raises(ValueError, match='너무 큽니다'):
        engine.chat(MESSAGES, [], SCHEMA, time.monotonic() + 4)
    assert engine.process is None


def test_deadline_covers_body_after_http10_headers_detach_socket(harness):
    harness['config'].update({'header_delay': 0.25, 'body_delay': 1})
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    start = time.monotonic()
    # Measure transport separately from the subsequent OS process wait.
    with pytest.raises((ValueError, OSError)):
        engine._request('/v1/chat/completions', start + 0.4, {'messages': MESSAGES})
    assert time.monotonic() - start < 0.6
    engine.close()
    assert engine.process is None


def test_close_is_idempotent_and_does_not_touch_unrelated_process(harness):
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    other = harness['original_popen']([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
    try:
        engine = harness['engine']
        engine.start(time.monotonic() + 4)
        owned = engine.process
        engine.close()
        engine.close()
        assert owned.poll() is not None and other.poll() is None
    finally:
        other.terminate()
        other.wait(timeout=3)


def test_windows_job_setup_failure_stops_spawned_server(harness, monkeypatch):
    class FailingJob:
        def assign(self, process): raise ValueError('종료 보호 장치 시험 오류')
        def close(self): pass
    monkeypatch.setattr(gguf_engine, '_WindowsJob', FailingJob)
    with pytest.raises(ValueError, match='종료 보호'):
        harness['engine'].start(time.monotonic() + 4)
    assert harness['children'][0].poll() is not None and harness['engine'].process is None


def test_root_relocation_changes_runtime_paths_without_changing_settings(harness, tmp_path):
    import shutil
    moved = tmp_path / 'moved'
    moved.mkdir()
    shutil.copytree(harness['root'] / 'models', moved / 'models')
    shutil.copytree(harness['root'] / 'runtime', moved / 'runtime')
    engine = gguf_engine.GGUFServer(SETTINGS, moved)
    try:
        engine.start(time.monotonic() + 4)
        command = harness['captured'][0][0]
        assert command[command.index('--model') + 1] == str(moved / 'models/model.gguf')
        assert engine.settings['gguf_model_path'] == 'models/model.gguf'
        assert command[0] == str(moved / 'runtime/llama-server.exe')
    finally:
        engine.close()


def test_actual_json_schema_accepts_final_or_readonly_tool_and_rejects_invalid():
    validation = pytest.importorskip('jsonschema', reason='GGUF 선택 응답 검증 의존성 jsonschema가 설치되지 않음')
    if not hasattr(validation, 'Draft202012Validator'):
        pytest.skip('GGUF 선택 응답 검증 의존성 jsonschema가 현재 실행 권한에서 읽히지 않음')
    final = gguf_engine._parse_reply('{"answer":"완료"}', SCHEMA, TOOLS)
    assert json.loads(final['content']) == {'answer': '완료'} and final['tool_calls'] == []
    lookup = gguf_engine._parse_reply('{"tool_calls":[{"name":"read_text","arguments":{"path":"설명.md"}}]}', SCHEMA, TOOLS)
    assert lookup['content'] == '' and lookup['tool_calls'][0]['arguments'] == {'path': '설명.md'}
    for text in ('{"answer":3}', '{"extra":1}', '{"tool_calls":[{"name":"execute","arguments":{}}]}',
                 '{"tool_calls":[]}', '{"answer":', '[]'):
        with pytest.raises(ValueError, match='GGUF'):
            gguf_engine._parse_reply(text, SCHEMA, TOOLS)


def test_missing_response_validator_is_explicit_and_stops_owned_server(harness, monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'jsonschema':
            raise ImportError('missing optional validator fixture')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    engine = harness['engine']
    engine.start(time.monotonic() + 4)
    child = engine.process
    with pytest.raises(ValueError, match='GGUF 응답 검증 패키지'):
        engine.chat(MESSAGES, [], SCHEMA, time.monotonic() + 4)
    assert child.poll() is not None and engine.process is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object 강제 부모 종료 계약')
def test_windows_parent_death_kills_only_its_managed_child(tmp_path):
    import ctypes
    from ctypes import wintypes
    root = tmp_path / 'portable'
    (root / 'models').mkdir(parents=True)
    (root / 'runtime').mkdir()
    (root / 'models/model.gguf').write_bytes(b'GGUF' + bytes(20))
    (root / 'runtime/llama-server.exe').write_bytes(b'fixture')
    fake = root / 'fake_server.py'
    fake.write_text(FAKE_SERVER, 'utf-8')
    config = root / 'fake_config.json'
    config.write_text(json.dumps({'requests': str(root / 'requests.jsonl')}), 'utf-8')
    ready = root / 'ready.pid'
    parent_script = root / 'parent.py'
    parent_script.write_text('''import json, subprocess, sys, time\nfrom pathlib import Path\n'''
        + '''sys.path.insert(0, sys.argv[1])\nfrom lab.ai import gguf_engine\noriginal = subprocess.Popen\n'''
        + '''root = Path(sys.argv[2])\ndef spawn(command, **kwargs):\n    return original([sys.executable, '-u', str(root / 'fake_server.py'), '--config', str(root / 'fake_config.json'), *command[1:]], **kwargs)\n'''
        + '''gguf_engine.subprocess.Popen = spawn\nengine = gguf_engine.GGUFServer(json.loads(sys.argv[3]), root)\nengine.start(time.monotonic() + 8)\n(root / 'ready.pid').write_text(str(engine.process.pid))\ntime.sleep(60)\n''', 'utf-8')
    parent = subprocess.Popen([sys.executable, '-u', str(parent_script), str(PROJECT / 'Part3'), str(root), json.dumps(SETTINGS)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            parent.kill(); parent.wait(timeout=3)
            raise AssertionError('시험 부모가 준비되지 않음: ' + parent.stderr.read().decode('utf-8', 'replace'))
        handle = kernel.OpenProcess(0x00100000, False, int(ready.read_text()))
        assert handle and kernel.WaitForSingleObject(handle, 0) == 0x102
        parent.kill()
        parent.wait(timeout=3)
        assert kernel.WaitForSingleObject(handle, 3000) == 0
    finally:
        if parent.poll() is None:
            parent.kill(); parent.wait(timeout=3)
        if handle:
            kernel.CloseHandle(handle)
        parent.stdout.close(); parent.stderr.close()
