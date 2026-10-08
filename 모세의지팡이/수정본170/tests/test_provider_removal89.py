"""AI provider selection, the shared adapter and Gemini settings.

(수정본162 deleted the checks that only looked for the removed Groq provider.)
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai import client, gemini, model_runtime
from common_ai.provider import configured_settings, from_settings
from common_ai.security import provider_scope
from common_ai.settings import masked_settings, read_settings
from lab import server, unified_settings
from lab.ai.shared_provider import SharedProvider, validate_selection


def _handler(path, data=None):
    request = object.__new__(server.Handler)
    request.server = SimpleNamespace(token='test-token', server_port=8763, last_seen=None)
    request.path = path
    payload = json.dumps(data).encode() if data is not None else b''
    request.headers = {'X-Lab-Token': 'test-token', 'Content-Length': str(len(payload))}
    request.rfile = io.BytesIO(payload)
    request.replies = []
    request.send = lambda code, body, *args: request.replies.append((code, body))
    return request


@pytest.mark.parametrize('factory', [configured_settings, from_settings, validate_selection])
@pytest.mark.parametrize('name', ['unknown-provider'])
def test_unsupported_provider_is_rejected_before_loading(factory, name):
    with pytest.raises(ValueError, match='AI 실행 방식'):
        factory({'provider': name, 'model': 'valid-model'})


@pytest.mark.parametrize('change', [{'unknown_model': 'x'}, {'unknown_api_key': 'synthetic-key'}])
def test_unknown_settings_cannot_be_saved(change, monkeypatch):
    read = Mock(side_effect=AssertionError('Unknown settings must fail before reading current state'))
    monkeypatch.setattr(server, 'ai_settings', read)
    with pytest.raises(ValueError, match='AI 설정 항목'):
        unified_settings.save_ai(change)
    read.assert_not_called()


@pytest.mark.parametrize('settings,model_field', [
    ({'provider': 'ollama', 'model': 'arbitrary-model'}, 'model'),
    ({'provider': 'local_gguf', 'gguf_model_path': 'models/arbitrary.gguf'}, 'gguf_model_path'),
    ({'provider': 'gemini', 'gemini_model': 'arbitrary-model', 'gemini_api_key': 'synthetic-key'}, 'gemini_model'),
])
def test_supported_selection_and_shared_adapter_are_preserved(settings, model_field, tmp_path, monkeypatch):
    checked = configured_settings(settings)
    assert checked['provider'] == settings['provider']
    assert validate_selection(checked)[model_field] == settings[model_field]
    connect = Mock(return_value=SimpleNamespace(chat=Mock(return_value={'content': '{}', 'tool_calls': []})))
    monkeypatch.setattr(client, 'Client', connect)
    selected = SharedProvider(checked, root=tmp_path)
    assert selected.model == settings[model_field]
    schema = {'type': 'object'}
    messages = [{'role': 'user', 'content': '전략 해석'}]
    assert selected.chat(messages, [], response_schema=schema) == {'content': '{}', 'tool_calls': []}
    selected.client.chat.assert_called_once_with(messages, [], response_schema=schema, role='strategy')
    assert provider_scope(checked) == ('external' if settings['provider'] == 'gemini' else 'local')


def test_gemini_factory_key_masking_and_model_routes_remain(monkeypatch):
    settings = configured_settings({'provider': 'gemini', 'gemini_model': 'arbitrary-model',
                                    'gemini_api_key': 'synthetic.gemini-key', 'timeout': 90})
    selected = from_settings(settings)
    assert isinstance(selected, gemini.Gemini)
    assert selected.model == settings['gemini_model']
    public = masked_settings(settings)
    assert public['gemini_api_key_configured']
    assert 'gemini_api_key' not in public
    assert settings['gemini_api_key'] not in json.dumps(public)
    listing = Mock(return_value={'available': True, 'models': ['arbitrary-model'], 'error': None})
    monkeypatch.setattr(gemini, 'list_models', listing)
    monkeypatch.setattr(server, 'ai_settings', lambda: settings)
    for method, data in [('GET', None), ('POST', {'gemini_api_key': 'synthetic.new-key'})]:
        request = _handler('/api/ai/gemini-models', data)
        getattr(request, 'do_' + method)()
        assert request.replies[-1][0] == 200
        assert request.replies[-1][1]['models'] == ['arbitrary-model']
    assert listing.call_count == 2


def test_gemini_settings_save_and_key_deletion_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / 'settings/ai_settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'provider': 'gemini', 'gemini_model': 'first-model',
                                'gemini_api_key': 'synthetic.key', 'timeout': 90}), encoding='utf-8')
    monkeypatch.setattr(server, 'AI_SETTINGS', path)
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(client.Client, '_lookup', Mock(return_value=None))
    monkeypatch.setattr(client.Client, '_connect', Mock(side_effect=AssertionError('No AI service connection')))
    monkeypatch.setattr(client.Client, '_start', Mock(side_effect=AssertionError('No AI service startup')))
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', lambda: None)
    for name in ('AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS', 'RESEARCH_SESSIONS'):
        monkeypatch.setattr(server, name, {})
    auth = Mock()
    monkeypatch.setattr(gemini, 'check_api_key', auth)
    assert unified_settings.save_ai({'gemini_model': 'second-model'})['ok']
    assert server.ai_settings()['gemini_model'] == 'second-model'
    auth.assert_not_called()
    assert unified_settings.save_ai({'gemini_api_key': 'synthetic.replacement-key'})['ok']
    auth.assert_called_once()
    assert server.ai_settings()['gemini_api_key'] == 'synthetic.replacement-key'
    assert unified_settings.save_ai({'gemini_api_key': None})['ok']
    assert 'gemini_api_key' not in server.ai_settings()
    assert not masked_settings(server.ai_settings())['gemini_api_key_configured']
    assert read_settings(tmp_path)['gemini_model'] == 'second-model'
    client.Client._connect.assert_not_called()
    client.Client._start.assert_not_called()
