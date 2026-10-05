"""Key replacement/deletion and safe authentication errors; synthetic HTTP only."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai import client, gemini, model_runtime
from common_ai.provider import from_settings
from common_ai.settings import masked_settings
from lab import server, unified_settings

OLD = 'synthetic_old_key'
NEW = 'AQ.synthetic.new_key-86'
SETTINGS = {'provider': 'gemini', 'gemini_model': 'selected-model',
            'gemini_api_key': OLD, 'timeout': 90}


class Reply:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _): return b'{"models":[{"name":"models/selected-model"}]}'


@pytest.fixture
def saved(monkeypatch, tmp_path):
    path = tmp_path / 'settings/ai_settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps(SETTINGS), encoding='utf-8')
    monkeypatch.setattr(server, 'AI_SETTINGS', path)
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, '_AI_CLIENT', None)
    for name in ('AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS', 'RESEARCH_SESSIONS'):
        monkeypatch.setattr(server, name, {'existing': object()})
    monkeypatch.setattr(client.Client, '_lookup', Mock(return_value=None))
    monkeypatch.setattr(client.Client, '_connect', Mock(side_effect=AssertionError('No AI connection')))
    monkeypatch.setattr(client.Client, '_start', Mock(side_effect=AssertionError('No AI service startup')))
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', lambda: None)
    yield path
    server.close_ai_client()


@pytest.mark.parametrize('key', [NEW, 'future.format/+==key'])
def test_visible_key_characters_are_not_provider_prefix_hardcoded(key):
    assert gemini.validate_settings({**SETTINGS, 'gemini_api_key': key}, required=True)['gemini_api_key'] == key


@pytest.mark.parametrize('key', ['bad key', 'bad\tkey', 'bad\nHeader: injection', 'bad\rkey', 'bad\x00key', 'bad\x7fkey'])
def test_key_header_injection_is_still_rejected(key):
    with pytest.raises(ValueError, match='공백·제어 문자'):
        gemini.validate_settings({**SETTINGS, 'gemini_api_key': key}, required=True)


def test_new_key_is_checked_before_save_and_hidden_afterwards(saved, monkeypatch):
    requests = []
    def send(request, *, timeout):
        # Old key is still present while the replacement is being verified.
        assert json.loads(saved.read_text('utf-8'))['gemini_api_key'] == OLD
        requests.append((request, timeout))
        return Reply()
    monkeypatch.setattr(gemini, '_open_request', send)
    result = unified_settings.save_ai({'gemini_api_key': NEW})
    updated = json.loads(saved.read_text('utf-8'))
    assert updated['gemini_api_key'] == NEW and result['ok']
    request, timeout = requests[0]
    assert request.method == 'GET' and request.full_url == gemini.API_ROOT + '?pageSize=1'
    assert request.data is None and request.get_header('X-goog-api-key') == NEW
    assert NEW not in request.full_url and timeout <= 15
    public = masked_settings(updated)
    assert public['gemini_api_key_configured'] and NEW not in json.dumps(public)
    assert not server.AI_SESSIONS and not server.RESEARCH_SESSIONS


def test_invalid_new_key_does_not_overwrite_old_key_or_conversation(saved, monkeypatch):
    before = saved.read_bytes()
    existing = dict(server.RESEARCH_SESSIONS)
    def send(request, *, timeout):
        body = {'error': {'message': 'API key not valid. ' + NEW,
                          'details': [{'reason': 'API_KEY_INVALID'}]}}
        raise urllib.error.HTTPError(request.full_url, 400, NEW, {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(gemini, '_open_request', send)
    with pytest.raises(ValueError, match='API 키가 유효하지') as error:
        unified_settings.save_ai({'gemini_api_key': NEW})
    assert NEW not in str(error.value)
    assert saved.read_bytes() == before and server.RESEARCH_SESSIONS == existing


def test_deleted_key_saves_incomplete_settings_without_network(saved, monkeypatch):
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: pytest.fail('Deletion must not call Google'))
    assert unified_settings.save_ai({'gemini_api_key': None})['ok']
    updated = server.ai_settings()
    assert updated['provider'] == 'gemini' and 'gemini_api_key' not in updated
    assert not masked_settings(updated)['gemini_api_key_configured']
    with pytest.raises(ValueError, match='연결 키를 입력'):
        from_settings(updated)
    with pytest.raises(ValueError, match='연결 키를 입력'):
        server.ai_post('/api/ai/chat', {'session':'no-key','message':'전략 해석'})
    client.Client._connect.assert_not_called()
    client.Client._start.assert_not_called()
    assert server.AI_SESSIONS['no-key'].last_intent is None
    assert server.AI_SESSIONS['no-key'].candidate is None
    # Re-entering a key works after deletion, with authentication before save.
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: Reply())
    unified_settings.save_ai({'gemini_api_key': NEW})
    assert server.ai_settings()['gemini_api_key'] == NEW


def test_network_failure_does_not_save_or_echo_key(saved, monkeypatch):
    before = saved.read_bytes()
    def offline(*a, **k): raise urllib.error.URLError(NEW)
    monkeypatch.setattr(gemini, '_open_request', offline)
    with pytest.raises(ValueError, match='네트워크') as error:
        unified_settings.save_ai({'gemini_api_key': NEW})
    assert saved.read_bytes() == before and NEW not in str(error.value)


@pytest.mark.parametrize('body,expected', [
    ({'error': {'message': 'API key not valid. Please pass a valid API key.'}}, 'API 키가 유효하지'),
    ({'error': {'details': [{'reason': 'API_KEY_EXPIRED'}]}}, 'API 키가 유효하지'),
    ({'error': {'details': [{'reason': 'API_KEY_SERVICE_BLOCKED'}]}}, '사용 제한'),
    ({'error': {'message': 'Invalid schema ' + NEW}}, '요청 형식'),
    ({'error': {'details': [{'reason': {'secret': NEW}}]}}, '요청 형식'),
])
def test_http_400_classification_never_returns_google_body(body, expected):
    error = urllib.error.HTTPError('https://example.invalid', 400, NEW, {}, io.BytesIO(json.dumps(body).encode()))
    text = gemini._http_error_message(error)
    assert expected in text and NEW not in text and 'Invalid schema' not in text
