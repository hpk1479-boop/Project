"""Gemini request compatibility; original contracts still validate every reply."""
import copy
import io
import json
from pathlib import Path
import sys
import time
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common_ai import gemini, model_runtime

KEY = 'AQ.synthetic.request-key'
SETTINGS = {'provider': 'gemini', 'gemini_model': 'Models/Gemini-Future-Model',
            'gemini_api_key': KEY, 'timeout': 90}
SCHEMA = {'type': 'object', 'properties': {'answer': {'$ref': '#/$defs/answer'}},
          'required': ['answer'], 'additionalProperties': False,
          '$defs': {'answer': {'type': 'string', 'enum': ['valid']}}}
TOOLS = [{'type': 'function', 'function': {'name': 'vocabulary',
          'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]


class Reply:
    def __init__(self, value):
        self.payload = json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': json.dumps(value)}]}}]}).encode()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, limit): return self.payload[:limit]


@pytest.fixture
def remote(monkeypatch):
    sent = []
    answers = []
    monkeypatch.setattr(model_runtime.RUNTIME, 'remote_chat',
        lambda provider, operation: operation(time.monotonic() + provider.timeout))
    def send(request, *, timeout):
        sent.append(request)
        return Reply(answers.pop(0))
    monkeypatch.setattr(gemini, '_open_request', send)
    return sent, answers


def test_display_model_case_is_normalized_without_pinning_model():
    assert gemini.Gemini(SETTINGS).model == 'gemini-future-model'
    for model in ['Gemini-Other-Version', 'MODEL_123.V2']:
        assert gemini.validate_settings({**SETTINGS, 'gemini_model': model})['gemini_model'] == model.lower()


def test_referenced_schema_uses_one_json_request_and_full_contract(remote):
    sent, answers = remote
    before = copy.deepcopy(SCHEMA)
    answers.append({'answer': 'valid'})
    result = gemini.Gemini(SETTINGS).chat([{'role': 'user', 'content': '해석'}], [], response_schema=SCHEMA)
    assert json.loads(result['content']) == {'answer': 'valid'} and SCHEMA == before
    assert len(sent) == 1 and sent[0].full_url.endswith('gemini-future-model:generateContent')
    body = json.loads(sent[0].data)
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    assert 'responseJsonSchema' not in body['generationConfig']
    prompt = body['systemInstruction']['parts'][0]['text']
    assert json.dumps(SCHEMA, ensure_ascii=False, separators=(',', ':')) in prompt
    assert KEY not in prompt and sent[0].get_header('X-goog-api-key') == KEY


@pytest.mark.parametrize('value', [{}, {'answer': 'invented'}, {'answer': 7}, {'answer': 'valid', 'extra': True}])
def test_json_mode_still_rejects_contract_violations(remote, value):
    sent, answers = remote
    answers.append(value)
    with pytest.raises(ValueError, match='JSON 형식을 만족하지'):
        gemini.Gemini(SETTINGS).chat([{'role': 'user', 'content': '해석'}], [], response_schema=SCHEMA)
    assert len(sent) == 1  # No hidden model retries.


def test_simple_watch_contract_keeps_native_schema(remote):
    sent, answers = remote
    schema = {'type': 'object', 'properties': {'canonical_text': {'type': 'string'}},
              'required': ['canonical_text'], 'additionalProperties': False}
    answers.append({'canonical_text': '1분 상승추세'})
    gemini.Gemini(SETTINGS).chat([{'role': 'user', 'content': '해석'}], [], response_schema=schema)
    assert json.loads(sent[0].data)['generationConfig']['responseJsonSchema'] == schema


def test_json_mode_preserves_safe_tool_calls(remote):
    _, answers = remote
    answers.append({'tool_calls': [{'name': 'vocabulary', 'arguments': {}}]})
    result = gemini.Gemini(SETTINGS).chat([{'role': 'user', 'content': '해석'}], TOOLS, response_schema=SCHEMA)
    assert result['tool_calls'] == [{'id': 'local_0', 'name': 'vocabulary', 'arguments': {}}]


def test_json_mode_rejects_forbidden_tool(remote):
    _, answers = remote
    answers.append({'tool_calls': [{'name': 'read_project_code', 'arguments': {}}]})
    with pytest.raises(ValueError, match='JSON 형식을 만족하지'):
        gemini.Gemini(SETTINGS).chat([{'role': 'user', 'content': '해석'}], TOOLS, response_schema=SCHEMA)


def test_model_format_error_has_safe_specific_message():
    body = {'error': {'message': '* GenerateContentRequest.model: unexpected model name format ' + KEY}}
    error = urllib.error.HTTPError('https://example.invalid', 400, KEY, {}, io.BytesIO(json.dumps(body).encode()))
    message = gemini._http_error_message(error)
    assert '모델명 형식' in message and KEY not in message
