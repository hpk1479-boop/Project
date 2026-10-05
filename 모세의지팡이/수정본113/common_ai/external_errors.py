"""Bounded external API error classification with no remote text in UI messages.

Only documented error-envelope fields inform a category. Credentials, URLs,
failed generation, metadata and arbitrary nested values never become output.
The categories describe protocol failures, not model-name exceptions.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
from itertools import islice
from urllib.parse import unquote


MAX_ERROR_BYTES = 16 * 1024
_MAX_FIELD_CHARS = 8192
_LABELS = {'gemini': 'Gemini'}


class ReplyValidationError(ValueError):
    """Rejected model output for the existing bounded interpretation repair.

    The reply is private repair context, never an exception argument or part
    of the user-facing message/repr. Transport/authentication errors do not
    use this type and therefore cannot trigger response correction.
    """
    def __init__(self, public_message, reply_text):
        super().__init__(public_message)
        self.reply_text = reply_text

    def __repr__(self):
        return type(self).__name__ + '(' + repr(str(self)) + ')'


def _error_fields(exc):
    """Read once, with an explicit bound; malformed bodies give no evidence."""
    try:
        payload = exc.read(MAX_ERROR_BYTES + 1)
        if not isinstance(payload, bytes) or len(payload) > MAX_ERROR_BYTES:
            return frozenset(), ''
        body = json.loads(payload.decode('utf-8'))
        error = body.get('error') if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return frozenset(), ''
        codes = []
        for name in ('code', 'type', 'status', 'reason'):
            value = error.get(name)
            if isinstance(value, str) and len(value) <= 256:
                codes.append(value.casefold())
        details = error.get('details')
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    reason = item.get('reason')
                    if isinstance(reason, str) and len(reason) <= 256:
                        codes.append(reason.casefold())
        message = error.get('message')
        if not isinstance(message, str) or len(message) > _MAX_FIELD_CHARS:
            message = ''
        return frozenset(codes), ' '.join(message.casefold().split())
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return frozenset(), ''


def classify_http_error(exc):
    """Return an internal category; neither body nor authentication is returned."""
    status = getattr(exc, 'code', None)
    codes, message = _error_fields(exc)

    def has(*phrases):
        return any(phrase in message for phrase in phrases)

    # These statuses have an unambiguous transport meaning even with no body.
    if status == 429:
        if codes & {'insufficient_quota', 'quota_exceeded', 'billing_hard_limit_reached'} or has(
                'insufficient quota', 'quota exceeded', 'exceeded your current quota',
                'daily limit', 'daily quota', 'monthly limit', 'billing hard limit',
                'tokens per day', 'requests per day'):
            return 'quota'
        if codes & {'rate_limit_exceeded', 'rate_limit_error'} or has(
                'rate limit', 'rate_limit', 'too many requests',
                'tokens per minute', 'requests per minute'):
            return 'rate_limit'
        return 'usage_limit'
    if status in (408, 504):
        return 'timeout'
    if isinstance(status, int) and status >= 500:
        return 'server'
    if status in (301, 302, 303, 307, 308):
        return 'redirect'

    if codes & {'api_key_expired', 'expired_api_key', 'token_expired'} or has(
            'api key expired', 'api key has expired', 'expired api key', 'token has expired'):
        return 'auth_expired'
    if codes & {'api_key_invalid', 'invalid_api_key', 'invalid_token', 'unauthenticated'} or has(
            'api key not valid', 'invalid api key', 'incorrect api key',
            'api key is invalid', 'invalid authentication credentials'):
        return 'auth_invalid'
    if status == 401:
        return 'auth_invalid'
    if codes & {'api_key_service_blocked', 'api_key_http_referrer_blocked',
                'api_key_ip_address_blocked', 'api_key_android_app_blocked',
                'api_key_ios_app_blocked', 'permission_denied', 'access_denied',
                'forbidden', 'access_not_configured', 'service_disabled'} or has(
            'permission denied', 'access denied', 'api key service blocked',
            'not allowed to access', 'api has not been used', 'api is disabled'):
        return 'permission'
    if status == 403:
        return 'permission'

    if codes & {'model_decommissioned', 'model_deprecated', 'model_retired'} or (
            'model' in message and has(
                'decommissioned', 'deprecated', 'retired',
                'no longer supported', 'no longer available')):
        return 'model_retired'
    if has('unexpected model name format', 'invalid model name', 'invalid model id'):
        return 'model_format'
    if status in (404, 410) or codes & {'model_not_found', 'model_not_available'} or (
            'model' in message and has('does not exist', 'not found', 'not available',
                                      'not supported for generatecontent', 'do not have access')):
        return 'model_missing'

    if codes & {'context_length_exceeded', 'context_window_exceeded',
                'input_too_long', 'token_limit_exceeded'} or has(
            'please reduce the length of the messages or completion',
            'maximum context length', 'context length exceeded',
            'context window', 'input token count exceeds',
            'input token count (', 'input is too long',
            'input exceeds the maximum', 'too many tokens in the input',
            'maximum number of tokens allowed in the input'):
        return 'context_limit'
    if status == 413 or codes & {'request_too_large', 'payload_too_large'} or has(
            'request body is too large', 'request too large', 'payload too large'):
        return 'request_size'
    if codes & {'max_tokens_exceeded', 'max_completion_tokens_exceeded'} or (
            has('max_tokens', 'max_completion_tokens', 'maxoutputtokens') and
            has('exceeds', 'must be less', 'out of range', 'maximum')):
        return 'output_limit'

    if codes & {'json_validate_failed', 'json_generation_failed',
                'failed_generation', 'invalid_json'} or has(
            'failed to generate json', 'generated json is invalid',
            'json generation failed', 'json validation failed'):
        return 'json_generation'
    if codes & {'unsupported_parameter', 'unsupported_value', 'unsupported_option'} or (
            has('not supported', 'unsupported', 'unknown name', 'unrecognized',
                'not permitted') and has(
                    'response_format', 'responsemime', 'responsejsonschema',
                    'response_schema', 'json_schema', 'json object', 'json_object',
                    'temperature', 'max_tokens', 'max_completion_tokens',
                    'maxoutputtokens', 'parameter', 'option')):
        return 'unsupported_option'
    if codes & {'invalid_schema', 'schema_validation_error'} or has(
            'invalid schema', 'schema is invalid', 'schema is too complex',
            'schema too complex', 'schema complexity', 'too many states'):
        return 'request_schema'
    if status in (400, 405, 409, 415, 422):
        return 'invalid_request'
    return 'unknown'


_MESSAGES = {
    'auth_invalid': '{provider} API 키가 유효하지 않습니다. 키를 확인하거나 새 키로 교체하세요.',
    'auth_expired': '{provider} API 키가 유효하지 않거나 만료되었습니다. 새 키로 교체하세요.',
    'permission': '{provider} 서버 접근이 제한되었습니다. 네트워크와 API 키의 접근 권한·사용 제한을 확인하세요.',
    'model_retired': '{provider} 모델의 지원이 종료되었습니다. 사용 가능한 다른 모델을 직접 선택하세요.',
    'model_format': '{provider} 모델명 형식이 올바르지 않습니다. 서비스의 모델 ID를 확인하세요.',
    'model_missing': '설정한 {provider} 모델을 사용할 수 없습니다. 모델 ID와 계정의 모델 접근 권한을 확인하세요.',
    'context_limit': '{provider} 입력 문맥 한도를 초과했습니다. 사용자 문장과 MOSES 계약·대화 문맥을 합친 요청이 모델 한도보다 큽니다. 전송 문맥 또는 응답 길이 설정을 확인하세요.',
    'request_size': '{provider} 요청 크기가 모델·계정의 허용 한도를 초과했습니다. 전송 데이터 크기와 계정의 요청 한도를 확인하세요.',
    'output_limit': '{provider} 요청한 응답 길이가 모델의 허용 한도를 초과했습니다. 응답 길이 설정을 줄이세요.',
    'unsupported_option': '{provider} 모델이 요청 옵션을 지원하지 않습니다. JSON 출력 방식과 응답 옵션의 지원 여부를 확인하세요.',
    'request_schema': '{provider} JSON 요청 형식이 모델에서 지원하는 schema 규칙과 맞지 않습니다. 전송 schema 형식과 복잡도를 확인하세요.',
    'json_generation': '{provider} 모델이 유효한 JSON 응답을 생성하지 못했습니다. 요청을 다시 시도하고 반복되면 모델의 JSON 출력 지원을 확인하세요.',
    'quota': '{provider} 계정의 사용량 한도가 소진되었습니다. 계정의 일별·월별 한도와 결제 설정을 확인하세요.',
    'rate_limit': '{provider} 요청 속도 한도를 초과했습니다. 잠시 후 다시 시도하거나 계정의 분당 요청·토큰 한도를 확인하세요.',
    'usage_limit': '{provider} 사용 한도에 도달했습니다. 계정의 사용량과 요청 속도 한도를 확인하세요.',
    'timeout': '{provider} 서버 응답 대기 시간이 초과되었습니다. 네트워크와 대기 시간 설정을 확인하세요.',
    'server': '{provider} 서버에서 오류가 발생했습니다. 서비스 상태를 확인하고 잠시 후 다시 시도하세요.',
    'redirect': '{provider} API가 다른 응답 경로를 요구했습니다. 인증정보 전송을 차단했습니다. API 연결 설정을 확인하세요.',
    'invalid_request': '{provider} 요청 형식이 올바르지 않습니다. API가 상세 원인을 확인할 수 있는 오류 정보를 반환하지 않았습니다. 요청 설정을 확인하세요.',
    'unknown': '{provider} API 응답 오류의 원인을 확인하지 못했습니다. 연결 상태와 서비스 상태를 확인하세요.',
}


def _label(provider):
    # Never render an arbitrary provider name received from settings or an API.
    return _LABELS.get(provider, '외부 AI') if isinstance(provider, str) else '외부 AI'


def http_error_message(provider, exc):
    """Static Korean UI message; callers may append their validated HTTP code."""
    return _MESSAGES[classify_http_error(exc)].format(provider=_label(provider))


def connection_error_message(provider, exc):
    """Separate timeout from connection failures without rendering exception text."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _MESSAGES['timeout'].format(provider=_label(provider))
    return (_label(provider) + '에 연결하지 못했습니다. 네트워크 연결과 API 서비스 주소를 확인하세요.')


def _schema_reply_issues(value, schema, tools):
    """Describe original-schema failures without rendering response contents.

    Union diagnostics choose the structurally closest branch. Paths contain
    only public schema property names and numeric array positions; arbitrary
    object keys, offending values, validation messages, and enums stay private.
    This helper reports errors and never changes or revalidates permissively.
    """
    from jsonschema import Draft202012Validator
    from .lora_worker import reply_schema

    expected = reply_schema(schema, tools or [])
    names = set()
    def collect(node):
        if isinstance(node, dict):
            fields = node.get('properties')
            if isinstance(fields, dict):
                names.update(name for name in fields if isinstance(name, str)
                             and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name))
            for key, child in node.items():
                if key not in {'const', 'enum', 'default', 'examples'}:
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(expected)

    def resolve(node, active=()):
        ref = node.get('$ref') if isinstance(node, dict) else None
        if not isinstance(ref, str) or not ref.startswith('#/') or ref in active:
            return node
        target = expected
        try:
            for part in unquote(ref[2:]).split('/'):
                target = target[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            return node
        target = resolve(target, (*active, ref))
        rest = {key: child for key, child in node.items() if key != '$ref'}
        return {'allOf': [target, rest]} if rest else target

    def kind(item):
        return ('null' if item is None else 'boolean' if isinstance(item, bool) else
                'object' if isinstance(item, dict) else 'array' if isinstance(item, list) else
                'string' if isinstance(item, str) else 'integer' if isinstance(item, int) else 'number')

    def score(node, item, depth=0):
        if depth > 16 or not isinstance(node, dict):
            return (0, 0, 0, 0)
        node = resolve(node)
        alternatives = node.get('anyOf') or node.get('oneOf')
        if alternatives:
            return min(score(branch, item, depth + 1) for branch in alternatives)
        if isinstance(node.get('allOf'), list):
            parts = [score(part, item, depth + 1) for part in node['allOf']]
            return tuple(sum(part[index] for part in parts) for index in range(4))
        allowed = node.get('type', [])
        allowed = [allowed] if isinstance(allowed, str) else allowed
        item_kind = kind(item)
        wrong_type = int(bool(allowed) and item_kind not in allowed and
                         not (item_kind == 'integer' and 'number' in allowed))
        wrong_literal = int('const' in node and item != node['const'])
        if not isinstance(item, dict):
            return (wrong_type, wrong_literal, 0, 0)
        fields = node.get('properties', {})
        required = node.get('required', [])
        missing = sum(name not in item for name in required)
        extra = sum(name not in fields for name in item) if node.get('additionalProperties') is False else 0
        overlap = len(set(item) & (set(fields) | set(required)))
        for name, child in fields.items():
            child = resolve(child)
            if name in item and isinstance(child, dict) and 'const' in child:
                wrong_literal += int(item[name] != child['const'])
        return (wrong_type, wrong_literal, missing + extra, -overlap)

    def leaves(error, depth=0):
        if depth < 20 and error.validator in {'anyOf', 'oneOf'} and error.context:
            groups = {}
            for child in error.context:
                branch = next((part for part in child.schema_path if isinstance(part, int)), None)
                if branch is not None:
                    groups.setdefault(branch, []).append(child)
            variants = error.validator_value
            if groups and isinstance(variants, list):
                branch = min(groups, key=lambda index:
                    (*score(variants[index], error.instance), len(groups[index])))
                for child in groups[branch]:
                    yield from leaves(child, depth + 1)
                return
        yield error

    def path(parts):
        result = '$'
        for part in parts:
            if isinstance(part, int):
                result += '[' + str(part) + ']'
            elif part in names:
                result += '.' + part
            else:
                result += '[항목]'
        return result

    issues = []
    for error in islice(Draft202012Validator(expected).iter_errors(value), 32):
        for leaf in leaves(error):
            location = list(leaf.absolute_path)
            if leaf.validator == 'required' and isinstance(leaf.instance, dict):
                candidates = [(path([*location, name]), '필수 필드 누락')
                              for name in leaf.validator_value if name not in leaf.instance]
            else:
                label = ('자료형 오류' if leaf.validator == 'type' else
                         '허용값 오류' if leaf.validator in {'enum', 'const'} else
                         '불필요한 필드' if leaf.validator in {'additionalProperties', 'unevaluatedProperties'} else
                         '형식 오류')
                candidates = [(path(location), label)]
            for issue in candidates:
                # A wrong JSON type often also fails the same field's enum.
                # Show its first cause once so other affected fields remain
                # visible within the three-issue limit.
                if not any(previous[0] == issue[0] for previous in issues):
                    issues.append(issue)
                if len(issues) == 3:
                    return issues
    return issues


def reply_error_message(provider, text, schema=None, tools=None):
    """Distinguish JSON syntax and bounded, safe original-schema failures."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return _label(provider) + ' 모델 응답이 완전한 JSON이 아닙니다. JSON 생성 실패 또는 응답 잘림을 확인하세요.'
    message = _label(provider) + ' JSON 형식을 만족하지 못했습니다. JSON은 받았지만 MOSES schema 검증을 통과하지 못했습니다.'
    if isinstance(schema, dict):
        try:
            issues = _schema_reply_issues(value, schema, tools)
        except (TypeError, ValueError, KeyError, RecursionError):
            issues = []
        if issues:
            return message + ' ' + '; '.join(location + ': ' + label for location, label in issues)
    return message + ' 필수 필드와 조건 값을 확인하세요.'
