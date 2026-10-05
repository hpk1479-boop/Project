"""Synthetic error envelopes only; no credentials or external network calls."""
import io
import json
import urllib.error

import pytest


@pytest.mark.parametrize('provider', ['future_external', 'gemini'])
def test_reply_json_syntax_is_distinct_from_original_schema_failure(provider):
    from common_ai.external_errors import reply_error_message
    syntax = reply_error_message(provider, '{broken private-key')
    schema = reply_error_message(provider, '{"wrong":"private-key"}')
    assert '완전한 JSON이 아닙니다' in syntax
    assert 'schema 검증' in schema
    assert 'private-key' not in syntax + schema

from common_ai.external_errors import (
    MAX_ERROR_BYTES, classify_http_error, connection_error_message,
    http_error_message,
)


SECRET = 'synthetic-secret-must-never-be-rendered'


def error(status, body=None):
    raw = json.dumps(body or {}, ensure_ascii=False).encode('utf-8')
    return urllib.error.HTTPError('https://invalid.example/?key=' + SECRET,
                                  status, SECRET, {}, io.BytesIO(raw))


@pytest.mark.parametrize('status,body,category', [
    (400, {'error': {'message': 'API key not valid. Please pass a valid API key.'}}, 'auth_invalid'),
    (400, {'error': {'details': [{'reason': 'API_KEY_EXPIRED'}]}}, 'auth_expired'),
    (401, {}, 'auth_invalid'),
    (400, {'error': {'details': [{'reason': 'API_KEY_SERVICE_BLOCKED'}]}}, 'permission'),
    (403, {}, 'permission'),
    (400, {'error': {'code': 'model_decommissioned', 'message': SECRET}}, 'model_retired'),
    (400, {'error': {'message': 'Unexpected model name format: ' + SECRET}}, 'model_format'),
    (400, {'error': {'code': 'model_not_found'}}, 'model_missing'),
    (404, {}, 'model_missing'),
    (400, {'error': {'code': 'context_length_exceeded'}}, 'context_limit'),
    (400, {'error': {'message': 'Please reduce the length of the messages or completion.'}}, 'context_limit'),
    (400, {'error': {'message': 'The input token count (8200) exceeds the maximum number of tokens allowed (4096).'}}, 'context_limit'),
    (413, {}, 'request_size'),
    (400, {'error': {'message': 'max_completion_tokens exceeds the maximum allowed value'}}, 'output_limit'),
    (400, {'error': {'code': 'json_validate_failed', 'failed_generation': SECRET}}, 'json_generation'),
    (400, {'error': {'message': 'Failed to generate JSON. See failed_generation.'}}, 'json_generation'),
    (400, {'error': {'message': 'response_format: json_object is not supported for this model'}}, 'unsupported_option'),
    (400, {'error': {'message': 'Unknown name "responseJsonSchema" at generation_config.'}}, 'unsupported_option'),
    (400, {'error': {'code': 'unsupported_parameter'}}, 'unsupported_option'),
    (400, {'error': {'message': 'Invalid schema ' + SECRET}}, 'request_schema'),
    (400, {'error': {'message': 'The schema is too complex'}}, 'request_schema'),
    (400, {'error': {'status': 'INVALID_ARGUMENT', 'message': SECRET}}, 'invalid_request'),
    (429, {'error': {'type': 'insufficient_quota'}}, 'quota'),
    (429, {'error': {'message': 'Exceeded your current quota'}}, 'quota'),
    (429, {'error': {'code': 'rate_limit_exceeded'}}, 'rate_limit'),
    (429, {'error': {'message': 'Rate limit reached for tokens per minute'}}, 'rate_limit'),
    (429, {}, 'usage_limit'),
    (408, {}, 'timeout'),
    (504, {}, 'timeout'),
    (503, {}, 'server'),
    (500, {'error': {'message': SECRET}}, 'server'),
    (302, {}, 'redirect'),
    (418, {}, 'unknown'),
])
def test_http_categories_are_distinct_and_never_return_remote_content(status, body, category):
    assert classify_http_error(error(status, body)) == category
    for provider in ('future_external', 'gemini'):
        text = http_error_message(provider, error(status, body))
        assert SECRET not in text and 'https://' not in text and 'failed_generation' not in text
        assert text.startswith(('외부 AI', 'Gemini', '설정한'))


@pytest.mark.parametrize('body', [
    {'error': {'details': [{'reason': {'secret': SECRET}}]}},
    {'error': {'code': [SECRET], 'type': {'secret': SECRET}, 'message': {'secret': SECRET}}},
    {'error': {'failed_generation': 'context_length_exceeded ' + SECRET,
               'metadata': {'reason': 'API_KEY_EXPIRED'}, 'message': SECRET}},
    {'message': 'Please reduce the length of the messages or completion.'},
    {'error': [SECRET]},
])
def test_unapproved_nested_values_cannot_change_classification_or_leak(body):
    assert classify_http_error(error(400, body)) == 'invalid_request'
    assert SECRET not in http_error_message('gemini', error(400, body))


@pytest.mark.parametrize('payload', [b'<html>blocked</html>', b'\xff', b'[]', b'{',
                                     b'[' * 2000 + b']' * 2000])
def test_malformed_remote_bodies_fall_back_to_status_only(payload):
    exc = urllib.error.HTTPError('https://invalid.example', 400, SECRET, {}, io.BytesIO(payload))
    assert classify_http_error(exc) == 'invalid_request'


def test_body_is_read_once_with_bound_and_oversized_response_is_not_classified():
    class BoundedError:
        code = 400
        calls = []
        def read(self, size):
            self.calls.append(size)
            return b'x' * size
    exc = BoundedError()
    assert classify_http_error(exc) == 'invalid_request'
    assert exc.calls == [MAX_ERROR_BYTES + 1]


def test_field_size_is_bounded_and_read_failure_is_safe():
    body = {'error': {'message': 'invalid api key' + 'x' * 9000}}
    assert classify_http_error(error(400, body)) == 'invalid_request'
    class BrokenError:
        code = 401
        def read(self, size):
            raise OSError(SECRET)
    assert SECRET not in http_error_message('gemini', BrokenError())


def test_unknown_provider_labels_are_never_rendered():
    assert SECRET not in http_error_message(SECRET, error(400))
    assert http_error_message({'secret': SECRET}, error(400)).startswith('외부 AI')


@pytest.mark.parametrize('exc,is_timeout', [
    (TimeoutError(SECRET), True),
    (urllib.error.URLError(TimeoutError(SECRET)), True),
    (urllib.error.URLError(SECRET), False),
    (OSError(SECRET), False),
])
def test_timeout_and_connection_failures_have_separate_safe_messages(exc, is_timeout):
    text = connection_error_message('gemini', exc)
    assert SECRET not in text
    assert ('대기 시간이 초과' in text) == is_timeout
    assert ('연결하지 못했습니다' in text) != is_timeout


def test_gemini_key_and_schema_wording_stays_compatible():
    assert 'API 키가 유효하지' in http_error_message('gemini', error(400,
        {'error': {'details': [{'reason': 'API_KEY_EXPIRED'}]}}))
    assert '사용 제한' in http_error_message('gemini', error(400,
        {'error': {'details': [{'reason': 'API_KEY_SERVICE_BLOCKED'}]}}))
    assert '요청 형식' in http_error_message('gemini', error(400,
        {'error': {'message': 'Invalid schema ' + SECRET}}))
