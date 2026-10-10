"""External authentication must never become model context or browser output."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common_ai import gemini, model_runtime
from common_ai.gemini import Gemini
from common_ai.settings import masked_settings


KEY = 'synthetic_external_key_86'
SETTINGS = {'provider': 'gemini', 'gemini_model': 'selected-model',
            'gemini_api_key': KEY, 'timeout': 90}
SCHEMA = {'type': 'object', 'properties': {'canonical_text': {'type': 'string'}},
          'required': ['canonical_text'], 'additionalProperties': False}


class Reply:
    def __init__(self, value):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.data[:limit]


def packet(value):
    return {'candidates': [{'finishReason': 'STOP', 'content': {
        'parts': [{'text': json.dumps(value, ensure_ascii=False)}]}}]}


@pytest.fixture
def remote(monkeypatch):
    sent = []
    replies = []
    monkeypatch.setattr(model_runtime.RUNTIME, 'remote_chat',
        lambda provider, operation: operation(time.monotonic() + provider.timeout))

    def send(request, *, timeout):
        sent.append({'url': request.full_url, 'headers': dict(request.header_items()),
                     'body': json.loads(request.data), 'timeout': timeout})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply)

    monkeypatch.setattr(gemini, '_open_request', send)
    return SimpleNamespace(sent=sent, replies=replies)


def test_credentials_are_authentication_header_only(remote, capsys):
    remote.replies.append(packet({'canonical_text': '15분 상승추세'}))
    result = Gemini(SETTINGS).chat([{'role': 'user', 'content': '15분 상승추세'}], [],
                                  response_schema=SCHEMA)
    request = remote.sent[0]
    assert request['headers']['X-goog-api-key'] == KEY
    assert KEY not in request['url'] + json.dumps(request['body'])
    assert json.loads(result['content']) == {'canonical_text': '15분 상승추세'}
    captured = capsys.readouterr()
    assert KEY not in repr(result) + captured.out + captured.err


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 400, 401, 403, 404, 429, 500])
def test_http_body_and_reason_are_never_forwarded_to_ui(remote, capsys, status):
    echo = KEY + ' confidential account password and raw response'
    remote.replies.append(urllib.error.HTTPError('https://untrusted.invalid/?key=' + KEY,
        status, echo, {}, io.BytesIO(echo.encode())))
    with pytest.raises(ValueError) as error:
        Gemini(SETTINGS).chat([{'role': 'user', 'content': '조건 해석'}], [], response_schema=SCHEMA)
    assert KEY not in str(error.value)
    assert 'confidential' not in str(error.value)
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


@pytest.mark.parametrize('problem', [urllib.error.URLError(KEY), TimeoutError(KEY), OSError(KEY)])
def test_connection_errors_hide_exception_secret(remote, problem):
    remote.replies.append(problem)
    with pytest.raises(ValueError) as error:
        Gemini(SETTINGS).chat([{'role': 'user', 'content': '조건 해석'}], [], response_schema=SCHEMA)
    assert KEY not in str(error.value)


def test_even_schema_valid_key_echo_is_rejected(remote, capsys):
    remote.replies.append(packet({'canonical_text': '응답에 ' + KEY + ' 포함'}))
    with pytest.raises(ValueError) as error:
        Gemini(SETTINGS).chat([{'role': 'user', 'content': '조건 해석'}], [], response_schema=SCHEMA)
    assert KEY not in str(error.value)
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


def test_json_escaped_key_echo_is_also_rejected(remote):
    escaped = ''.join('\\u%04x' % ord(char) for char in KEY)
    remote.replies.append({'candidates': [{'finishReason': 'STOP', 'content': {
        'parts': [{'text': '{"canonical_text":"' + escaped + '"}'}]}}]})
    with pytest.raises(ValueError) as error:
        Gemini(SETTINGS).chat([{'role': 'user', 'content': '조건 해석'}], [], response_schema=SCHEMA)
    assert KEY not in str(error.value)


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirect_cannot_forward_authentication_header(status):
    request = gemini.urllib.request.Request(gemini.API_ROOT + 'chosen:generateContent',
        data=b'{}', headers={'x-goog-api-key': KEY}, method='POST')
    handler = gemini._NoRedirect()
    # Redirect handler never constructs a second request, including same-host redirects.
    assert handler.redirect_request(request, None, status, 'redirect',
        {'location': 'https://untrusted.invalid/'}, 'https://untrusted.invalid/') is None


def test_real_transport_installs_redirect_blocker(monkeypatch):
    observed = []
    request = gemini.urllib.request.Request(gemini.API_ROOT + 'chosen:generateContent',
        data=b'{}', headers={'x-goog-api-key': KEY}, method='POST')
    sentinel = object()

    def build(handler):
        assert isinstance(handler, gemini._NoRedirect)
        return SimpleNamespace(open=lambda value, *, timeout:
            observed.append((value, timeout)) or sentinel)

    monkeypatch.setattr(gemini.urllib.request, 'build_opener', build)
    assert gemini._open_request(request, timeout=12) is sentinel
    assert observed == [(request, 12)]


def test_public_settings_mask_nested_future_credentials_without_mutation():
    original = {**SETTINGS, 'max_new_tokens': 256, 'gguf_gpu_layers': -1,
        'future_api_key': 'future-secret', 'access_token': 'access-secret',
        'password': 'password-secret', 'connections': {'mt5': {'password': 'mt5-secret'}},
        'metadata': {'safe': 'model description', 'token': 'nested-secret'},
        'description': 'accidental key echo: ' + KEY}
    before = copy.deepcopy(original)
    public = masked_settings(original)
    assert original == before
    assert public['gemini_api_key_configured'] is True
    assert public['max_new_tokens'] == 256 and public['gguf_gpu_layers'] == -1
    assert public['metadata']['safe'] == 'model description'
    serialized = json.dumps(public)
    for value in (KEY, 'future-secret', 'access-secret', 'password-secret', 'mt5-secret', 'nested-secret'):
        assert value not in serialized
