"""The Gemini key's own [확인], the bot token's contract (수정본177); synthetic HTTP only.

A typed key is checked with Google and saved, null deletes without a request, and an empty value
checks the saved key again and writes nothing.
"""
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai import client, gemini, model_runtime
from lab import server, unified_settings

OLD = 'synthetic_old_key_177'
NEW = 'AIza.synthetic.new_key-177'
SETTINGS = {'provider': 'gemini', 'gemini_model': 'selected-model', 'gemini_api_key': OLD, 'timeout': 90}


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


def google(monkeypatch):
    sent = []
    def send(request, *, timeout):
        sent.append(request.get_header('X-goog-api-key'))
        return Reply()
    monkeypatch.setattr(gemini, '_open_request', send)
    return sent


def stored(path):
    return json.loads(path.read_text('utf-8'))


def test_typed_key_is_checked_then_saved(saved, monkeypatch):
    sent = google(monkeypatch)
    result = unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': ' ' + NEW + ' '})
    assert result == {'ok': True, 'key': 'gemini_api_key', 'configured': True, 'message': '확인하고 저장했습니다'}
    assert sent == [NEW] and stored(saved)['gemini_api_key'] == NEW
    assert NEW not in json.dumps(result)


def test_empty_value_checks_the_saved_key_and_writes_nothing(saved, monkeypatch):
    sent = google(monkeypatch)
    before = saved.read_bytes()
    result = unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': '  '})
    assert result == {'ok': True, 'key': 'gemini_api_key', 'configured': True, 'message': '확인했습니다'}
    assert sent == [OLD] and saved.read_bytes() == before
    # A check changes nothing, so open conversations stay.
    assert 'existing' in server.AI_SESSIONS and 'existing' in server.RESEARCH_SESSIONS


def test_empty_value_without_a_saved_key_asks_for_one(saved, monkeypatch):
    saved.write_text(json.dumps({key: value for key, value in SETTINGS.items() if key != 'gemini_api_key'}), encoding='utf-8')
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: pytest.fail('Nothing to check'))
    with pytest.raises(ValueError, match='확인할 값을 입력하세요'):
        unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': ''})


def test_null_deletes_without_a_request(saved, monkeypatch):
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: pytest.fail('Deletion must not call Google'))
    result = unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': None})
    assert result == {'ok': True, 'key': 'gemini_api_key', 'configured': False, 'message': '저장값을 지웠습니다'}
    assert 'gemini_api_key' not in stored(saved) and stored(saved)['provider'] == 'gemini'


def test_refused_key_keeps_the_saved_one_and_never_echoes_it(saved, monkeypatch):
    before = saved.read_bytes()
    def send(request, *, timeout):
        body = {'error': {'message': 'API key not valid. ' + NEW, 'details': [{'reason': 'API_KEY_INVALID'}]}}
        raise urllib.error.HTTPError(request.full_url, 400, NEW, {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(gemini, '_open_request', send)
    with pytest.raises(ValueError, match='API 키가 유효하지') as error:
        unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': NEW})
    assert NEW not in str(error.value) and saved.read_bytes() == before


def test_failed_recheck_reports_and_changes_nothing(saved, monkeypatch):
    before = saved.read_bytes()
    def offline(*a, **k): raise urllib.error.URLError(OLD)
    monkeypatch.setattr(gemini, '_open_request', offline)
    with pytest.raises(ValueError, match='네트워크') as error:
        unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': ''})
    assert OLD not in str(error.value) and saved.read_bytes() == before


@pytest.mark.parametrize('data', [
    None, [], {'key': 'gemini_api_key'}, {'key': 'TELEGRAM_TOKEN', 'value': NEW},
    {'key': 'gemini_api_key', 'value': NEW, 'token': NEW}, {'key': 'gemini_api_key', 'value': 177},
])
def test_malformed_requests_are_refused_before_anything_happens(saved, monkeypatch, data):
    before = saved.read_bytes()
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: pytest.fail('Refused before Google'))
    with pytest.raises(ValueError):
        unified_settings.confirm_gemini_key(data)
    assert saved.read_bytes() == before


def test_http_route_answers_like_the_bot_token_route(saved, monkeypatch):
    google(monkeypatch)
    def post(payload):
        raw = json.dumps(payload).encode('utf-8')
        handler = server.Handler.__new__(server.Handler)
        handler.path = '/api/mo/settings/gemini_key'
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.authorized = lambda: True
        captured = []
        handler.send = lambda status, body, *args: captured.append((status, body))
        handler.do_POST()
        assert len(captured) == 1
        return captured[0]
    status, body = post({'key': 'gemini_api_key', 'value': NEW})
    assert status == 200 and body['configured'] is True and stored(saved)['gemini_api_key'] == NEW
    status, body = post({'key': 'gemini_api_key', 'value': None})
    assert status == 200 and body['configured'] is False and 'gemini_api_key' not in stored(saved)
    status, body = post({'key': 'gemini_api_key', 'value': ''})
    assert status == 400 and '확인할 값을 입력하세요' in json.dumps(body, ensure_ascii=False)
