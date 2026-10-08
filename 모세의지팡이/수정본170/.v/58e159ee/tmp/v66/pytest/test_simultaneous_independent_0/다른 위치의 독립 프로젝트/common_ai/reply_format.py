"""The JSON reply contract shared by every AI provider: schema, decoder view and checked parse."""
from __future__ import annotations

import copy
import json


def normalize_messages(messages):
    """Translate the existing Agent history without mutating its conversation."""
    result = copy.deepcopy(messages)
    for message in result:
        for call in message.get('tool_calls') or []:
            function = call.get('function', call)
            args = function.get('arguments')
            if isinstance(args, str):
                function['arguments'] = json.loads(args)
    return result


def decoding_schema(value):
    """Decoder-compatible view; authoritative schema is checked after generation."""
    if isinstance(value, list):
        return [decoding_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: decoding_schema(item) for key, item in value.items()}
    if 'enum' in result:
        groups = {}
        for item in result['enum']:
            kind = ('null' if item is None else 'boolean' if isinstance(item, bool) else
                    'string' if isinstance(item, str) else 'number')
            groups.setdefault(kind, []).append(item)
        if len(groups) > 1 or 'null' in groups or 'boolean' in groups:
            result.pop('enum')
            result.pop('type', None)
            result['anyOf'] = [({'type': kind} if kind in ('null', 'boolean') else {'type': kind, 'enum': items})
                               for kind, items in groups.items()]
    return result


def reply_schema(schema, tools):
    if not tools:
        return schema
    calls = []
    for tool in tools:
        function = tool['function']
        calls.append({'type': 'object', 'additionalProperties': False,
            'required': ['name', 'arguments'], 'properties': {
                'name': {'type': 'string', 'enum': [function['name']]},
                'arguments': function['parameters']}})
    result = {'anyOf': [schema, {'type': 'object', 'additionalProperties': False,
        'required': ['tool_calls'], 'properties': {'tool_calls': {
            'type': 'array', 'minItems': 1, 'items': {'anyOf': calls}}}}]}
    # Local JSON pointers resolve from the document root, including when a
    # canonical response is nested alongside the tool-call alternative.
    if '$defs' in schema:
        result['$defs'] = schema['$defs']
    return result


def parse_reply(text, schema, tools):
    from jsonschema import Draft202012Validator
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError('AI 출력이 완전한 JSON이 아닙니다. 응답 길이를 확인하세요.') from None
    if not isinstance(data, dict):
        raise ValueError('AI 출력은 JSON 객체여야 합니다.')
    expected = reply_schema(schema, tools)
    if next(Draft202012Validator(expected).iter_errors(data), None) is not None:
        raise ValueError('AI 출력이 요청한 AI 응답 형식을 만족하지 못했습니다.')
    if 'tool_calls' in data:
        calls = [{'id': 'local_' + str(i), 'name': call['name'], 'arguments': call['arguments']}
                 for i, call in enumerate(data['tool_calls'])]
        return {'content': '', 'tool_calls': calls}
    return {'content': json.dumps(data, ensure_ascii=False), 'tool_calls': []}
