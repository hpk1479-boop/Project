"""Schema prompt, original grammar/validator, history/tool and error contracts.

No weights or server process are loaded. Responses cross the real GGUF chat
boundary and the installed JSON Schema validator; only HTTP inference is fake.
The canonical schema is read from this revision, never from old output evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'Part3'))

from common_ai import gguf_engine
from common_ai.lora_worker import reply_schema
from common_ai.schema_prompt import MAX_SUMMARY_CHARS, schema_prompt
from lab.ai.research_chat import chat_schema
from lab.ai.schema import output_schema
from lab.ai.tools import TOOL_SPECS


SETTINGS = {'provider': 'local_gguf', 'gguf_model_path': 'models/configured.gguf',
            'llama_server_path': 'runtime/llama-server.exe', 'timeout': 90,
            'gguf_context_size': 16384, 'gguf_gpu_layers': 0, 'max_new_tokens': 4096}
WATCH_SCHEMA = {'type': 'object', 'properties': {'canonical_text': {'type': 'string'}},
                'required': ['canonical_text'], 'additionalProperties': False}


def _final():
    return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'],
        'steps': [{'kind': 'MA_STATE', 'tfs': ['1m'], 'ma_family': 'EMA',
                   'fast_period': 50, 'slow_period': 200}],
        'order_mode': 'SIMULTANEOUS', 'within_sec': None,
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '요청 조건'}


@pytest.fixture
def transport(monkeypatch):
    engine = gguf_engine.GGUFServer(SETTINGS, PROJECT)
    engine.ready = True
    engine.model = 'configured-model'
    engine.process = SimpleNamespace(poll=lambda: None)
    state = SimpleNamespace(engine=engine, requests=[], replies=[], closed=0)

    def request(route, deadline, body):
        assert route == '/v1/chat/completions' and deadline > time.monotonic()
        state.requests.append(copy.deepcopy(body))
        reply = state.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(reply, ensure_ascii=False)}}]}

    def close():
        state.closed += 1
        engine.ready = False
        engine.process = None

    monkeypatch.setattr(engine, '_request', request)
    monkeypatch.setattr(engine, 'close', close)
    return state


def _chat(state, schema, response, *, messages=None, tools=None):
    state.replies.append(response)
    return state.engine.chat(messages if messages is not None else [
        {'role': 'system', 'content': '기존 계약'}, {'role': 'user', 'content': '입력 내용'}],
        tools or [], schema, time.monotonic() + 20)


def test_large_current_schema_has_short_general_field_outline():
    schema = output_schema()
    # Force the large-input branch independently of whether the current schema
    # factors repeated conditions into compact, reusable definitions.
    schema['$defs']['large_outline_fixture'] = {'type': 'string', 'description': 'format fixture ' * 10_000}
    original = copy.deepcopy(schema)
    full = json.dumps(schema, ensure_ascii=False)
    hint = schema_prompt(schema)
    assert len(full) > 100_000
    assert len(hint) <= MAX_SUMMARY_CHARS and len(hint) < len(full) / 10
    for field in schema['properties']:
        assert json.dumps(field) in hint
    for field in ('direction', 'symbols', 'steps', 'final', 'order_mode', 'branches'):
        assert json.dumps(field) in hint
    assert '"tfs"*' in hint and 'array<object>' in hint
    assert full not in hint and 'additionalProperties' not in hint
    assert schema == original


@pytest.mark.parametrize('mode', ['strategy', 'chat'])
def test_wrapped_research_keeps_required_nested_command_request_in_budget(mode, transport):
    from lab.ai.research_interpreter import response_schema

    schema = response_schema(mode)
    original = copy.deepcopy(schema)
    hint = schema_prompt(schema)
    assert len(hint) <= MAX_SUMMARY_CHARS
    assert '$["plan"]["steps"][]["command"]["request"]' in hint
    for name in ('action', 'target_mode', 'symbol', 'start', 'end', 'specials', 'filename'):
        assert json.dumps(name) in hint
    assert '$["interpretation"]["steps"]' in hint
    assert '$["interpretation"]["branches"]' in hint
    value = _final()
    result = _chat(transport, schema, value, tools=TOOL_SPECS)
    assert json.loads(result['content']) == value
    assert transport.requests[-1]['response_format']['schema'] == reply_schema(original, TOOL_SPECS)
    assert schema == original


def test_current_schema_original_reaches_decoder_and_validator(transport, monkeypatch):
    schema = output_schema()
    tools = copy.deepcopy(TOOL_SPECS)
    original_schema, original_tools = copy.deepcopy(schema), copy.deepcopy(tools)
    calls = []
    real_parser = gguf_engine._parse_reply

    def parse(text, supplied_schema, supplied_tools):
        calls.append((supplied_schema, supplied_tools))
        return real_parser(text, supplied_schema, supplied_tools)

    monkeypatch.setattr(gguf_engine, '_parse_reply', parse)
    result = _chat(transport, schema, _final(), tools=tools)
    body = transport.requests[-1]
    assert body['response_format'] == {'type': 'json_object', 'schema': reply_schema(schema, tools)}
    assert body['parse_tool_calls'] is False and body['chat_template_kwargs'] == {'enable_thinking': False}
    assert body['max_tokens'] == SETTINGS['max_new_tokens']
    assert json.loads(result['content']) == _final() and result['tool_calls'] == []
    assert calls[0][0] is schema and calls[0][1] is tools
    assert schema == original_schema and tools == original_tools and transport.closed == 0
    assert len(body['messages'][0]['content']) < 9000
    assert json.dumps(schema, ensure_ascii=False) not in body['messages'][0]['content']


def test_common_captured_lifecycle_crosses_real_response_validation(transport):
    from lab.ai.schema import validate_intent

    value = _final()
    value['interpretation'].update(order_mode='SEQUENTIAL', steps=[
        {'kind': 'FVG_NEW', 'tfs': ['5m'], 'capture': 'setup'},
        {'kind': 'FVG_TOUCH', 'tfs': ['5m'], 'ref': 'setup'}],
        lifecycle={'expires': {'bars': 3, 'tf': 'FINAL'},
            'snapshots': {'entry': {'tf': '1m', 'field': 'close'},
                          'size': {'tf': '1m', 'field': 'ATR14'}},
            'excursion': {'tf': '1m', 'anchor': 'entry', 'snapshot': 'size',
                          'multiplier': 2, 'direction': 'ADVERSE'},
            'invalidate_refs': True, 'first_success': True})
    value['interpretation']['final']['scope_ref'] = 'setup'
    result = _chat(transport, output_schema(), value, tools=TOOL_SPECS)
    returned = json.loads(result['content'])
    assert returned == value
    validated = validate_intent(returned)['interpretation']
    assert validated['steps'][1]['ref'] == 'setup'
    assert validated['lifecycle']['expires'] == {'bars': 3, 'tf': 'FINAL'}
    assert validated['final']['scope_ref'] == 'setup'


@pytest.mark.parametrize('invalid', [
    lambda value: value['interpretation']['steps'][0].update(fast_period=0),
    lambda value: value['interpretation']['steps'][0].update(side='BULL'),
    lambda value: value['interpretation']['steps'][0].update(unrequested_field=True),
    lambda value: value['interpretation']['steps'][0].pop('tfs'),
    lambda value: value['interpretation']['final'].update(trigger_mode='NORMAL'),
    lambda value: value.update(unsupported_extra=True),
])
def test_short_hint_does_not_weaken_current_schema_validation(transport, invalid):
    value = _final()
    invalid(value)
    with pytest.raises(ValueError, match='응답 형식'):
        _chat(transport, output_schema(), value, tools=TOOL_SPECS)
    assert transport.closed == 1


@pytest.mark.parametrize('schema,value', [
    (chat_schema(), {'kind': 'CHAT', 'message_ko': '설명', 'request': None}),
    (WATCH_SCHEMA, {'canonical_text': '사용자가 입력한 내용'}),
])
def test_small_chat_and_watch_schema_keep_complete_compact_json(transport, schema, value):
    hint = schema_prompt(schema)
    assert json.loads(hint) == schema and '\n' not in hint
    result = _chat(transport, schema, value)
    body = transport.requests[-1]
    assert hint in body['messages'][0]['content']
    assert body['response_format']['schema'] == schema
    assert json.loads(result['content']) == value


def test_generic_large_schema_has_no_strategy_or_model_specific_rules(transport):
    schema = {'description': '형식 설명' * 2000, 'type': 'object',
              'required': ['payload'], 'additionalProperties': False,
              'properties': {'payload': {'type': 'array', 'items': {'type': 'object',
                  'required': ['label'], 'properties': {'label': {'type': 'string'}}}}}}
    hint = schema_prompt(schema)
    assert '"payload"*' in hint and '"label"*' in hint
    assert 'CREATE_STRATEGY' not in hint and 'configured-model' not in hint
    result = _chat(transport, schema, {'payload': [{'label': '데이터'}]})
    assert json.loads(result['content']) == {'payload': [{'label': '데이터'}]}
    assert transport.requests[-1]['response_format']['schema'] == schema


@pytest.mark.parametrize('large', [False, True])
def test_enum_const_objects_are_literal_data_and_remain_unchanged(transport, large):
    literal = {'type': 'literal', 'enum': [False, None, {'properties': {'payload': 'data'}}],
               'anyOf': [{'const': 'payload'}], 'required': ['not_a_field']}
    schema = {'type': 'object', 'required': ['fixed', 'choice'], 'additionalProperties': False,
              'properties': {'fixed': {'const': literal}, 'choice': {'enum': [literal, None]}}}
    if large:
        schema['description'] = 'schema 설명' * 2000
    original = copy.deepcopy(schema)
    hint = schema_prompt(schema)
    if not large:
        assert json.loads(hint) == schema
    else:
        assert '$["fixed"]' not in hint and '$["choice"]' not in hint
        assert '"fixed"*' in hint and '"choice"*' in hint
    result = _chat(transport, schema, {'fixed': literal, 'choice': literal})
    assert json.loads(result['content']) == {'fixed': literal, 'choice': literal}
    assert transport.requests[-1]['response_format']['schema'] == original
    assert schema == original
    altered = copy.deepcopy(literal)
    altered['type'] = 'object'
    with pytest.raises(ValueError, match='응답 형식'):
        _chat(transport, schema, {'fixed': altered, 'choice': literal})


def test_tool_round_keeps_history_arguments_results_and_correlation(transport):
    tools = copy.deepcopy(TOOL_SPECS)
    schema = output_schema()
    messages = [{'role': 'system', 'content': '사용자 지정 system 내용'},
                {'role': 'user', 'content': '사용자 원문: 줄바꿈\n#27 "문장"'}]
    original = copy.deepcopy(messages)
    first = _chat(transport, schema, {'tool_calls': [{'name': 'read_doc',
        'arguments': {'name': 'Part3/docs/MOSES_LANGUAGE.md', 'start': 3, 'length': 77}}]},
        messages=messages, tools=tools)
    assert first == {'content': '', 'tool_calls': [{'id': 'local_0', 'name': 'read_doc',
        'arguments': {'name': 'Part3/docs/MOSES_LANGUAGE.md', 'start': 3, 'length': 77}}]}
    assert messages == original
    messages.extend([{'role': 'assistant', 'content': '', 'tool_calls': [{
        'id': 'local_0', 'type': 'function', 'function': {'name': 'read_doc',
        'arguments': json.dumps(first['tool_calls'][0]['arguments'], ensure_ascii=False)}}]},
        {'role': 'tool', 'tool_call_id': 'local_0', 'name': 'read_doc',
         'content': '{"text":"조회 결과\\n#27 원문"}'}])
    original = copy.deepcopy(messages)
    result = _chat(transport, schema, _final(), messages=messages, tools=tools)
    body = transport.requests[-1]
    history = body['messages']
    assert history[0]['content'].startswith(original[0]['content'] + '\n')
    assert history[1] == original[1]
    encoded_call = json.loads(history[2]['content'])['tool_calls'][0]
    assert encoded_call == {'id': 'local_0', 'name': 'read_doc',
                            'arguments': first['tool_calls'][0]['arguments']}
    assert history[3]['role'] == 'user'
    encoded_result = json.loads(history[3]['content'].split('\n', 1)[1])
    assert encoded_result == {'tool_call_id': 'local_0', 'name': 'read_doc',
                              'content': original[3]['content']}
    assert json.dumps(tools, ensure_ascii=False) in history[0]['content']
    assert body['response_format']['schema'] == reply_schema(schema, tools)
    assert json.loads(result['content']) == _final()
    assert messages == original and tools == TOOL_SPECS and transport.closed == 0


@pytest.mark.parametrize('calls', [[], [{'name': 'execute', 'arguments': {}}],
    [{'name': 'read_doc', 'arguments': {}}], [{'name': 'describe_special', 'arguments': {'number': []}}]])
def test_tool_response_still_enforces_original_tool_contract(transport, calls):
    with pytest.raises(ValueError, match='응답 형식'):
        _chat(transport, output_schema(), {'tool_calls': calls}, tools=TOOL_SPECS)
    assert transport.closed == 1


@pytest.mark.parametrize('error', [
    {'type': 'exceed_context_size_error'}, {'code': 'context_length_exceeded'},
    {'type': 'context_size_exceeded'},
    {'message': 'the request prompt (16400 tokens) exceeds the available context size (16384 tokens)'},
    {'message': 'prompt is too long for the context window'},
    {'message': 'prompt_tokens 16400 exceeds n_ctx 16384'},
    {'message': 'maximum context length is 16384 tokens, but you requested 16400 tokens'},
    'request prompt (16400 tokens) exceeds available context size (16384 tokens)',
])
def test_explicit_prompt_context_overflow_is_classified(transport, error):
    with pytest.raises(ValueError, match='처리 길이'):
        _chat(transport, WATCH_SCHEMA, gguf_engine._HTTPFailure(400, {'error': error}))
    assert transport.closed == 1


@pytest.mark.parametrize('error', [
    {'message': 'context initialization failed'}, {'message': 'grammar is too large'},
    {'message': 'response is too long'}, {'message': 'context template not supported'},
    {'message': 'model has context length 16384'}, {'type': 'context'},
    {'type': 'invalid_request_error', 'message': 'bad context configuration'},
    {'code': 'not_context_length_exceeded'}, {'code': ['context_length_exceeded']},
    {'message': 'prompt is invalid'}, None,
])
def test_context_words_or_size_words_alone_stay_generic_http_errors(transport, error):
    with pytest.raises(ValueError, match='HTTP 400'):
        _chat(transport, WATCH_SCHEMA, gguf_engine._HTTPFailure(400, {'error': error}))
    assert transport.closed == 1


@pytest.mark.parametrize('error', [
    {'message': 'context initialization failed: out of memory'},
    {'message': 'allocation failed for context buffer, too large'},
    {'type': 'context_length_exceeded', 'message': 'outofmemory'},
    {'type': 'exceed_context_size_error', 'message': 'out of memory'},
])
def test_memory_errors_take_priority_over_context_tokens_or_codes(transport, error):
    with pytest.raises(ValueError, match='메모리'):
        _chat(transport, WATCH_SCHEMA, gguf_engine._HTTPFailure(500, {'error': error}))
    assert transport.closed == 1
