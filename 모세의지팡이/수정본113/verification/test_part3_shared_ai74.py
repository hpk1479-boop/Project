"""Part3 uses the independent shared AI client while keeping its own contracts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Part3'))
from common_ai import client, gemini, provider as common_provider
from lab import runtime_lifecycle, server, storage, unified_settings
from lab.ai import model_runtime, shared_provider
from lab.ai.schema import output_schema


class FakeClient:
    instances = []
    replies = []

    def __init__(self, root):
        self.root = Path(root)
        self.calls = []
        self.instances.append(self)

    def chat(self, messages, tools, **kwargs):
        self.calls.append(('chat', messages, tools, kwargs))
        return self.replies.pop(0) if self.replies else {'content': '{}', 'tool_calls': []}

    def invalidate(self, settings=None):
        self.calls.append(('invalidate', settings))

    def shutdown(self, **kwargs):
        self.calls.append(('shutdown', kwargs))

    def close(self):
        self.calls.append(('close',))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / 'portable_project'
    root.mkdir()
    monkeypatch.setattr(server, 'ROOT', root / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', root / 'settings/ai_settings.json')
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {})
    monkeypatch.setattr(client, 'Client', FakeClient)
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', Mock())
    monkeypatch.setattr(FakeClient, 'instances', [])
    monkeypatch.setattr(FakeClient, 'replies', [])
    return root


@pytest.fixture
def checked_api_key(monkeypatch):
    """Isolate only Google's connectivity check; exercise real settings saves."""
    checked = Mock()
    monkeypatch.setattr(gemini, 'check_api_key', checked)
    return checked


def test_adapter_validates_without_constructing_low_level_provider(isolated, monkeypatch):
    monkeypatch.setattr(common_provider, 'from_settings', Mock(side_effect=AssertionError('loaded a model')))
    selected = shared_provider.shared_from_settings({'provider': 'ollama', 'model': 'chosen:4b'}, root=isolated)
    assert selected.model == 'chosen:4b'
    assert selected.timeout == 90
    assert selected.client.root == isolated
    assert selected.client.calls == []
    common_provider.from_settings.assert_not_called()


def test_default_and_custom_schema_pass_to_shared_client_without_mutating_messages(isolated):
    selected = shared_provider.shared_from_settings({'provider': 'ollama', 'model': 'chosen:4b'},
                                                    root=isolated, role='backtest')
    messages = [{'role': 'user', 'content': '백테스트'}]
    tools = [{'type': 'function', 'function': {'name': 'query'}}]
    selected.chat(messages, tools)
    assert selected.client.calls[-1][1] is messages
    assert selected.client.calls[-1][2] is tools
    assert selected.client.calls[-1][3] == {'response_schema': output_schema(), 'role': 'backtest'}
    custom = {'type': 'object', 'required': ['run']}
    selected.chat(messages, tools, response_schema=custom)
    assert selected.client.calls[-1][3]['response_schema'] is custom
    assert messages == [{'role': 'user', 'content': '백테스트'}]
    assert tools == [{'type': 'function', 'function': {'name': 'query'}}]
    selected.close()
    assert selected.client.calls[-1] == ('close',)


@pytest.mark.parametrize('settings', [
    {}, {'provider': 'ollama', 'model': None},
    {'provider': 'local_gguf', 'gguf_model_path': '../outside.gguf', 'llama_server_path': 'runtime/server.exe'},
    {'provider': 'gemini', 'gemini_model': 'chosen-gemini-model'},
])
def test_incomplete_or_invalid_selection_never_constructs_client(isolated, settings):
    with pytest.raises((ValueError, RuntimeError)):
        shared_provider.shared_from_settings(settings, root=isolated)
    assert FakeClient.instances == []


def test_server_strategy_and_backtest_use_same_root_and_distinct_roles(isolated):
    unified_settings.save_ai({'provider': 'ollama', 'model': 'common:4b'})
    strategy = server.ai_agent('strategy-chat')
    backtest = server.backtest_command_session('backtest-chat')
    assert strategy.provider.client.root == backtest.provider.client.root == isolated
    assert strategy.provider.role == 'strategy'
    assert backtest.provider.role == 'backtest'
    assert strategy.provider.client.calls == backtest.provider.client.calls == []
    assert server.ai_agent('strategy-chat') is strategy
    assert server.backtest_command_session('backtest-chat') is backtest


def _revision_fixture():
    path = ROOT / 'Part3/tests/test_ai_revision59.py'
    spec = importlib.util.spec_from_file_location('shared_revision74', path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture


def test_shared_transport_preserves_interpret_modify_reconfirm_apply_flow(isolated, monkeypatch):
    fixture = _revision_fixture()
    FakeClient.replies = [fixture.reply(fixture.original()), fixture.reply(fixture.changed())]
    monkeypatch.setattr(storage, 'ROOT', isolated / 'Part3')
    unified_settings.save_ai({'provider': 'ollama', 'model': 'common:4b'})
    first = server.ai_post('/api/ai/chat', {'session': 'flow', 'message': '15분 추세와 3분 원비'})
    assert not (isolated / 'Part3/generated').exists()
    revised = server.ai_post('/api/ai/chat', {'session': 'flow', 'message': '아니 원비는 15분이야'})
    assert revised['is_revision'] is True
    assert revised['result'] == fixture.changed()
    assert not (isolated / 'Part3/generated').exists()
    with pytest.raises(ValueError, match='최신 해석'):
        server.ai_post('/api/ai/apply', {'session': 'flow', 'revision': first['revision']})
    applied = server.ai_post('/api/ai/apply', {'session': 'flow', 'revision': revised['revision']})
    assert applied['recipe']['strategy_intent'] == revised['result']['interpretation']
    assert not (isolated / 'Part3/generated').exists()
    assert len(server.ai_agent('flow').provider.client.calls) == 2


def test_gemini_api_save_masks_key_but_internal_service_settings_keep_it(isolated, checked_api_key):
    settings_path = isolated / 'settings/ai_settings.json'
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({'provider': 'gemini', 'gemini_model': 'previous-model',
                                        'gemini_api_key': 'previous-secret-74'}), encoding='utf-8')
    response = server.ai_post('/api/ai/settings', {'provider': 'gemini', 'gemini_model': 'selected-model',
                                                 'gemini_api_key': 'synthetic-secret-74', 'watch_enabled': False})
    assert response['settings']['provider'] == 'gemini'
    assert response['settings']['gemini_api_key_configured'] is True
    assert 'gemini_api_key' not in response['settings']
    assert 'synthetic-secret-74' not in json.dumps(response)
    assert server.ai_settings()['gemini_api_key'] == 'synthetic-secret-74'
    saved = json.loads((isolated / 'settings/ai_settings.json').read_text('utf-8'))
    assert saved['gemini_api_key'] == 'synthetic-secret-74'
    assert 'previous-secret-74' not in json.dumps(saved)
    assert saved['watch_enabled'] is False
    checked_api_key.assert_called_once_with(saved)
    assert [row[0] for row in FakeClient.instances[-1].calls] == ['invalidate', 'close']
    model_runtime.RUNTIME.invalidate.assert_called_once()


def test_common_save_preserves_inactive_models_and_clears_both_session_types(isolated, checked_api_key):
    initial = {'provider': 'ollama', 'model': 'previous:4b', 'gemini_model': 'chosen-external',
               'gemini_api_key': 'synthetic-secret-74', 'gguf_model_path': 'models/gguf/selected.gguf',
               'llama_server_path': 'runtime/llama-server.exe', 'base_model': 'saved/base',
               'adapter_path': 'models/adapter'}
    unified_settings.save_ai(initial)
    server.AI_SESSIONS['old'] = object()
    server.BACKTEST_COMMAND_SESSIONS['old'] = object()
    unified_settings.save_ai({'provider': 'gemini'})
    checked_api_key.assert_called_once()
    assert checked_api_key.call_args.args[0]['gemini_api_key'] == initial['gemini_api_key']
    current = server.ai_settings()
    assert current['provider'] == 'gemini'
    for key, value in initial.items():
        if key != 'provider':
            assert current[key] == value
    assert server.AI_SESSIONS == server.BACKTEST_COMMAND_SESSIONS == {}


def test_first_common_partial_save_preserves_legacy_selection_and_original_file(isolated):
    legacy = isolated / 'Part3/projects/ai_settings.json'
    legacy.parent.mkdir(parents=True)
    before = b'{"provider":"ollama","model":"already-selected:4b","timeout":90}'
    legacy.write_bytes(before)
    assert server.ai_settings()['model'] == 'already-selected:4b'
    assert not (isolated / 'settings/ai_settings.json').exists()
    unified_settings.save_ai({'timeout': 120})
    current = server.ai_settings()
    assert current['provider'] == 'ollama'
    assert current['model'] == 'already-selected:4b'
    assert current['timeout'] == 120
    assert legacy.read_bytes() == before


def test_live_settings_only_reference_common_ai_and_never_modify_legacy_config(isolated, monkeypatch, checked_api_key):
    from lab import unified_backtest
    config = isolated / 'Part1/program/config.txt'
    config.parent.mkdir(parents=True)
    before = b'TARGET_SYMBOLS=XAUUSD+\nGEMINI_API_KEY=old-secret\nGEMINI_MODEL=old-model\nGEMINI_TIMEOUT_SEC=15\nGEMINI_FALLBACK_ENABLED=true\n'
    config.write_bytes(before)
    monkeypatch.setattr(unified_settings, 'ROOT', isolated)
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    monkeypatch.setattr(unified_settings, '_machine_roots', lambda: SimpleNamespace(load_live_root=lambda: None))
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None, None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    unified_settings.save_ai({'provider': 'gemini', 'gemini_model': 'chosen-model', 'gemini_api_key': 'new-secret'})
    checked_api_key.assert_called_once()
    assert checked_api_key.call_args.args[0]['gemini_api_key'] == 'new-secret'
    data = unified_settings.read()
    assert [row['key'] for row in data['live']] == ['SYMBOLS']
    assert data['ai']['gemini_api_key_configured'] is True
    assert 'gemini_api_key' not in data['ai']
    assert 'new-secret' not in json.dumps(data)
    with pytest.raises(ValueError, match='공통 AI 설정'):
        unified_settings.save_live({'GEMINI_MODEL': 'wrong-place'})
    assert config.read_bytes() == before


def test_shutdown_order_waits_for_backtests_and_live_before_common_model(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_lifecycle.backtest_jobs, 'shutdown', lambda **_: calls.append('backtest') or {'warnings': []})
    monkeypatch.setattr(runtime_lifecycle.unified_live, 'shutdown', lambda: calls.append('live') or {'warnings': []})
    monkeypatch.setattr(FakeClient, 'shutdown', lambda self, **_: calls.append('common-ai'))
    monkeypatch.setattr(model_runtime.RUNTIME, 'shutdown', lambda **_: calls.append('legacy-ai'))
    assert runtime_lifecycle.shutdown(timeout=1)['ok'] is True
    assert calls == ['backtest', 'live', 'common-ai', 'legacy-ai']
