"""Compact external context never replaces the authoritative MOSES contract.

All HTTP replies are synthetic. These tests neither invoke a model nor apply
strategies, start jobs, read private project data, or save settings.
"""
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
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai import external_prompt, gemini, model_runtime, reference_pack
from common_ai.reply_format import reply_schema
from common_ai.external_errors import ReplyValidationError
from lab.ai.research_interpreter import response_schema
from lab.ai.schema import output_schema


KEY = 'synthetic.transport-only-key_88'
MODEL = 'arbitrary-future-model-vnext'
TOOLS = [{'type': 'function', 'function': {'name': 'vocabulary',
    'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def transmitted_schema(rows):
    """Read the actual JSON Schema, independent of sharing/definition layout."""
    prefix = 'JSON response schema: '
    content = rows[0]['content']
    assert prefix in content
    result, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
    Draft202012Validator.check_schema(result)
    return result


def agreement(original, sent, values):
    for value, valid in values:
        assert Draft202012Validator(original).is_valid(value) is valid
        assert Draft202012Validator(sent).is_valid(value) is valid


def strategy():
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
            'steps': [
                {'kind': 'MA_CROSS', 'tfs': ['1m'], 'direction': 'LONG',
                 'ma_left': 'SMA50', 'ma_right': 'EMA200', 'bar_state': 'CLOSED'},
                {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'capture': 'gap',
                 'direction': 'SAME_AS_PREVIOUS_DIRECTION', 'bar_state': 'FORMING'},
                {'kind': 'FVG_TOUCH', 'tfs': ['1m'], 'ref': 'gap', 'scope_ref': 'parent',
                 'side': 'BULL'}],
            'order_mode': 'SEQUENTIAL', 'within_sec': 1800,
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'trigger_mode': 'BREAKER',
                'validation_mode': 'NORMAL', 'scope_ref': 'parent'},
            'lifecycle': {'expires': {'bars': 3, 'tf': '15m'},
                'snapshots': {'anchor': {'tf': '15m', 'field': 'close', 'bar_state': 'CLOSED'}},
                'invalidate_refs': True}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '조건 확인'}


def command(symbol):
    return {'supported': True, 'action': 'START',
        'request': {'target_mode': 'GENERATED', 'symbol': symbol,
                    'start': '2025-01-01', 'end': '2026-01-01', 'filename': None,
                    'mode': 'EVENT', 'result_mode': 'ALERT_ONLY', 'build_only': False},
        'job_id': None, 'needs_clarification': False,
        'clarification_question': None, 'message_ko': '확인 후 순차 실행'}


def plan():
    return {'strategy_text': None, 'steps': [
        {'draft': True, 'command': command('XAUUSD+')},
        {'draft': True, 'command': command('USTEC')}],
        'needs_clarification': False, 'clarification_question': None}


def messages(text='15분 상승추세에 1분 올존 전략 만들어줘', *, purpose='strategy', symbol='XAUUSD+', previous=None):
    return [{'role': 'system', 'content': dump({'purpose': purpose, 'moses_contract': {
        'vocabulary': {'ma': {'families': ['SMA', 'WMA', 'EMA', 'HMA'],
            'event_default_bar_state': 'CLOSED'}, 'sequential': {'within_sec': '없으면 무제한'}},
        'presets': [{'id': 'RECIPE_A', 'name': '공통 preset'}],
        'selection': {'today': '2026-10-03', 'options': {'symbol': symbol}}}})},
        {'role': 'user', 'content': dump({'mode': 'strategy', 'message': text,
            'context': {'current_strategy': previous, 'previous_plan': plan() if previous else None}})}]


class Reply:
    def __init__(self, value):
        self.value = value if isinstance(value, bytes) else dump(value).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.value[:limit]


@pytest.fixture
def transport(monkeypatch):
    name = 'gemini'
    module = gemini
    provider = module.Gemini
    settings = {'provider': name, name + '_model': MODEL, name + '_api_key': KEY, 'timeout': 90}
    sent, replies = [], []
    monkeypatch.setattr(model_runtime.RUNTIME, 'remote_chat',
        lambda selected, operation: operation(time.monotonic() + selected.timeout))

    def send(http_request, *, timeout):
        sent.append(SimpleNamespace(body=json.loads(http_request.data),
            url=http_request.full_url, headers=dict(http_request.header_items())))
        value = replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return Reply(value)

    def answer(value=None, *, text=None):
        content = dump(value) if text is None else text
        replies.append({'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': content}]}}]})

    monkeypatch.setattr(module, '_open_request', send)
    return SimpleNamespace(name=name, provider=provider(settings), sent=sent,
        replies=replies, answer=answer)


def test_actual_json_schema_retains_types_enums_required_and_lifecycle_oneof():
    schema = output_schema()
    before = copy.deepcopy(schema)
    rows, tools, authoritative = external_prompt.prepare(messages(), TOOLS, schema, {})
    sent = transmitted_schema(rows)
    assert schema == before
    assert authoritative == before
    expected = reply_schema(schema, tools)
    cases = [(strategy(), True)]
    for field in schema['required']:
        missing = strategy()
        del missing[field]
        cases.append((missing, False))
    mutations = [
        ('symbols', [['XAUUSD+', 'NAS100']]),
        ('symbols', 'XAUUSD+'),
        ('symbols', ['NOT_IN_CURRENT_CATALOG']),
        ('direction', 'UP'),
        ('order_mode', 'AFTER'),
        ('preset', None),
    ]
    for field, value in mutations:
        changed = strategy()
        changed['interpretation'][field] = value
        cases.append((changed, False))
    for lifetime, valid in [({'seconds': 30}, True), ({'bars': 3, 'tf': '15m'}, True),
            ({'seconds': 30, 'bars': 3, 'tf': '15m'}, False), ({'bars': 3}, False),
            ({'seconds': 0}, False), ({'seconds': '30'}, False)]:
        changed = strategy()
        changed['interpretation']['lifecycle']['expires'] = lifetime
        cases.append((changed, valid))
    agreement(expected, sent, cases)


def test_shared_closed_objects_do_not_reinterpret_allof():
    # These closed objects cannot both accept the union of their fields.
    # Unlike oneOf/anyOf, allOf must not become closed(base+variant).
    schema = {'allOf': [
        {'type': 'object', 'additionalProperties': False, 'properties': {
            'shared': {'type': 'string'}, 'shared2': {'type': 'boolean'}, 'left': {'type': 'number'}}},
        {'type': 'object', 'additionalProperties': False, 'properties': {
            'shared': {'type': 'string'}, 'shared2': {'type': 'boolean'}, 'right': {'type': 'number'}}}],
        'description': 'x' * 1100}
    assert not Draft202012Validator(schema).is_valid({'shared': 'a', 'shared2': True, 'left': 1, 'right': 2})
    rows, _, _ = external_prompt.prepare(messages(), [], schema, {})
    agreement(schema, transmitted_schema(rows), [
        ({'shared': 'a', 'shared2': True}, True),
        ({'shared': 'a', 'shared2': True, 'left': 1, 'right': 2}, False)])


def test_static_prefix_is_independent_of_selectors_examples_and_history():
    schema = response_schema('chat')
    first, _, _ = external_prompt.prepare(messages(), TOOLS, schema, {})
    changed, _, _ = external_prompt.prepare(messages('골크 이후 새 FVG가 생기면 그 FVG 터치',
        symbol='USTEC', previous=strategy()), TOOLS, schema, {})
    assert first[0] == changed[0]
    assert first[1:] != changed[1:]
    assert 'Current selection:' not in first[0]['content']
    for rows in (first, changed):
        examples = [row for row in rows if row['content'].startswith('Reference examples')]
        assert examples
        selected = json.loads(examples[0]['content'].split(': ', 1)[1])
        assert 1 <= len(selected) <= 2
        assert all(set(example) == {'input', 'meaning'} for example in selected)
        assert len(dump(selected)) <= 900
        assert 'Reference examples' not in rows[0]['content']
    current = json.loads(changed[-1]['content'])['context']
    assert current['current_strategy'] == strategy()
    assert current['previous_plan'] == plan()


def test_every_condition_keeps_its_own_allowed_fields_and_required_parameters():
    schema = output_schema()
    before = copy.deepcopy(schema)
    variants = schema['$defs']['step']['anyOf']
    rows, tools, _ = external_prompt.prepare(messages(), TOOLS, schema, {})
    sent = transmitted_schema(rows)
    assert schema == before
    expected = reply_schema(schema, tools)
    for variant in variants:
        kind = variant['properties']['kind']['const']
        step = {'kind': kind, 'tfs': ['1m']}
        # These are ordinary legal values for each required field; no
        # condition variant is removed from the external language contract.
        required_values = {'ma_family': 'SMA', 'fast_period': 50, 'slow_period': 200,
            'relation': variant['properties'].get('relation', {}).get('enum', ['IN'])[0],
            'ma_left': 'SMA50', 'ma_right': 'EMA200', 'metric_operator': 'LTE',
            'metric_value': 30, 'regime_families': ['RSI'], 'families': ['RSI'],
            'validation_mode': 'NORMAL', 'trigger_mode': 'OZ', 'level': 1}
        for field in ('metric', 'session', 'side', 'bar_state', 'shape'):
            if field in variant['properties']:
                required_values[field] = variant['properties'][field]['enum'][0]
        for field in variant['required']:
            if field not in step:
                step[field] = required_values[field]
        value = strategy()
        value['interpretation']['steps'] = [step]
        cases = [(value, True)]
        for field in variant['required']:
            missing = copy.deepcopy(value)
            del missing['interpretation']['steps'][0][field]
            cases.append((missing, False))
        extra = copy.deepcopy(value)
        extra['interpretation']['steps'][0]['not_a_recipe_parameter'] = 1
        cases.append((extra, False))
        if 'side' in variant['properties']:
            wrong = copy.deepcopy(value)
            wrong['interpretation']['steps'][0]['side'] = 'INVALID_SIDE'
            cases.append((wrong, False))
        agreement(expected, sent, cases)


def test_existing_intent_kinds_keep_their_details_during_unrelated_revision():
    schema = response_schema('chat')
    original = copy.deepcopy(schema)
    rows, _, authoritative = external_prompt.prepare(
        messages('종목만 바꿔줘', previous=strategy()), TOOLS, schema, {})
    sent = transmitted_schema(rows)
    for kind in ('MA_CROSS', 'FVG_NEW', 'FVG_TOUCH'):
        assert dump(kind) in dump(sent)
    assert json.loads(rows[-1]['content'])['context']['current_strategy'] == strategy()
    agreement(reply_schema(original, TOOLS), sent, [(strategy(), True)])
    assert authoritative == original and schema == original


def test_unknown_condition_phrase_can_still_query_public_vocabulary():
    schema = response_schema('chat')
    rows, tools, authoritative = external_prompt.prepare(messages('새 조건의 뜻이 무엇인가요'),
        TOOLS, schema, {})
    assert tools[0]['function']['name'] == 'vocabulary'
    assert 'vocabulary' in rows[0]['content']
    assert authoritative == schema
    agreement(reply_schema(schema, tools), transmitted_schema(rows), [(strategy(), True)])


def test_static_condition_prefix_is_same_with_or_without_retrieved_details():
    schema = response_schema('chat')
    known, _, _ = external_prompt.prepare(messages(), TOOLS, schema, {})
    unknown, _, _ = external_prompt.prepare(messages('새 조건의 뜻이 무엇인가요'), TOOLS, schema, {})
    assert known[0] == unknown[0]


def test_watch_has_its_own_contract_without_example_injection(monkeypatch):
    schema = {'type': 'object', 'additionalProperties': False,
        'properties': {'canonical_text': {'type': 'string'}}, 'required': ['canonical_text']}
    monkeypatch.setattr(reference_pack, 'select_examples',
        lambda *_args, **_kwargs: pytest.fail('WATCH must not select strategy examples'))
    rows, tools, original = external_prompt.prepare(messages(purpose='watch'), [], schema, {})
    assert 'canonical_text' in rows[0]['content']
    agreement(schema, transmitted_schema(rows), [
        ({'canonical_text': '15분 상승추세'}, True), ({'canonical_text': 15}, False),
        ({'canonical_text': '15분 상승추세', 'steps': []}, False)])
    assert 'MOSES rules:' not in rows[0]['content']
    assert all('Reference examples' not in row['content'] for row in rows)
    assert tools == [] and original == schema


@pytest.mark.parametrize('purpose', ['strategy', 'watch', 'backtest'])
def test_gemini_transmits_the_unchanged_central_contract(monkeypatch, purpose):
    """Compare actual wire text, including rules, examples and schema fields."""
    from common_ai.security import external_payload
    rows = messages(purpose=purpose)
    schema = response_schema('strategy')
    tools = TOOLS
    value = strategy()
    if purpose == 'watch':
        schema = {'type': 'object', 'additionalProperties': False,
            'properties': {'canonical_text': {'type': 'string'}}, 'required': ['canonical_text']}
        tools, value = [], {'canonical_text': '15분 상승추세'}
    elif purpose == 'backtest':
        value = {'kind': 'BACKTEST', 'strategy': None, 'plan': plan(), 'message_ko': '확인 후 실행'}
    before = copy.deepcopy((rows, schema, tools))
    settings = {'provider': 'gemini', 'gemini_model': MODEL,
        'gemini_api_key': KEY, 'timeout': 90}
    wire = {}
    def send(request, *, timeout):
        wire.update(json.loads(request.data))
        return Reply({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': dump(value)}]}}]})
    monkeypatch.setattr(gemini, '_open_request', send)
    result = gemini.Gemini(settings)._chat(rows, tools, schema, time.monotonic() + 90)
    assert json.loads(result['content']) == value
    google = [('system', wire['systemInstruction']['parts'][0]['text'])]
    for row in wire['contents']:
        google.extend(('assistant' if row['role'] == 'model' else row['role'], part['text'])
                      for part in row['parts'])
    prepared, _, _ = external_prompt.prepare(rows, tools, schema, settings)
    assert google[1:] == [(row['role'], row['content']) for row in prepared[1:]]
    # The existing Gemini transport appends its JSON-only format instruction;
    # the complete shared rules/schema/examples preceding it stay unchanged.
    assert google[0][1] == prepared[0]['content'] + '\n' + (
        'Return one JSON object. Use tool_calls only for the listed read-only tools; '
        'otherwise return the original result object. No Markdown or XML.')
    safe, _, _ = external_payload(rows, tools, schema, settings={})
    central_rules = json.loads(safe[0]['content'])['instructions']
    assert google[0][1].startswith(central_rules)
    assert (rows, schema, tools) == before
    assert KEY not in dump(wire)


@pytest.mark.parametrize('value', [strategy(),
    {'kind': 'BACKTEST', 'strategy': strategy(), 'plan': plan(), 'message_ko': '골드 후 나스닥'},
    {'kind': 'CHAT', 'strategy': None, 'plan': None, 'message_ko': '조건을 함께 논의해요.'}],
    ids=['complete-strategy', 'sequential-backtest-plan', 'chat'])
def test_transport_keeps_original_results_and_arbitrary_models(transport, value):
    schema = response_schema('chat')
    original_messages = messages()
    before = copy.deepcopy((schema, original_messages))
    transport.answer(value)
    result = transport.provider.chat(original_messages, TOOLS, response_schema=schema)
    assert json.loads(result['content']) == value
    assert (schema, original_messages) == before
    assert len(transport.sent) == 1
    sent = transport.sent[0]
    assert '/' + MODEL + ':generateContent' in sent.url
    prompt = sent.body['systemInstruction']['parts'][0]['text']
    transmitted_schema([{'content': prompt}])
    assert KEY not in sent.url + dump(sent.body)


@pytest.mark.parametrize('bad', ['not-json', 'missing-ma', 'invalid-kind-side',
    'both-lifetime-units', 'closed-only-snapshot', 'forbidden-tool', 'bad-backtest', 'chat-in-strategy'])
def test_original_schema_still_rejects_invalid_outputs_without_retry(transport, bad):
    schema = response_schema('strategy' if bad == 'chat-in-strategy' else 'chat')
    value = strategy()
    if bad == 'missing-ma':
        del value['interpretation']['steps'][0]['ma_left']
    elif bad == 'invalid-kind-side':
        value['interpretation']['steps'][1]['side'] = 'LOWER'
    elif bad == 'both-lifetime-units':
        value['interpretation']['lifecycle']['expires']['seconds'] = 60
    elif bad == 'closed-only-snapshot':
        value['interpretation']['lifecycle']['snapshots']['anchor']['bar_state'] = 'FORMING'
    elif bad == 'forbidden-tool':
        value = {'tool_calls': [{'name': 'read_project_code', 'arguments': {}}]}
    elif bad == 'bad-backtest':
        value = {'kind': 'BACKTEST', 'strategy': strategy(), 'plan': plan(), 'message_ko': '실행'}
        value['plan']['steps'][0]['command']['request']['target_mode'] = 'SPECIAL'
    elif bad == 'chat-in-strategy':
        value = {'kind': 'CHAT', 'strategy': None, 'plan': None, 'message_ko': '대화'}
    transport.answer(value, text='not JSON' if bad == 'not-json' else None)
    with pytest.raises(ValueError, match='JSON|검증|형식'):
        transport.provider.chat(messages(), TOOLS, response_schema=schema)
    assert len(transport.sent) == 1


def test_external_filter_still_blocks_source_credentials_and_tools(transport):
    schema = response_schema('chat')
    rows = messages(previous=strategy())
    rows.insert(0, {'role': 'system', 'content': 'PRIVATE_SOURCE_MANUAL ' + KEY})
    user = json.loads(rows[-1]['content'])
    user['message'] += ' ' + KEY
    user['context'].update({'source_code': 'PRIVATE_SOURCE_TEXT', 'runtime': 'PRIVATE_RUNTIME',
        'connections': {'password': 'PRIVATE_PASSWORD'}, 'path': 'Part3/private.py'})
    rows[-1]['content'] = dump(user)
    rows.append({'role': 'tool', 'name': 'read_project_code', 'content': 'PRIVATE_TOOL_TEXT'})
    forbidden = {'type': 'function', 'function': {'name': 'read_project_code',
        'description': 'PRIVATE_TOOL_DESCRIPTION', 'parameters': {'type': 'object'}}}
    transport.answer(strategy())
    transport.provider.chat(rows, [*TOOLS, forbidden], response_schema=schema)
    body = dump(transport.sent[0].body)
    for value in (KEY, 'PRIVATE_SOURCE_MANUAL', 'PRIVATE_SOURCE_TEXT', 'PRIVATE_RUNTIME',
                  'PRIVATE_PASSWORD', 'Part3/private.py', 'PRIVATE_TOOL_TEXT',
                  'PRIVATE_TOOL_DESCRIPTION', 'read_project_code'):
        assert value not in body
    assert 'vocabulary' in body and '[REDACTED]' in body


def test_http_failure_never_switches_model_or_retries(transport):
    body = dump({'error': {'message': 'maximum context length exceeded',
        'code': 'context_length_exceeded'}}).encode()
    transport.replies.append(urllib.error.HTTPError('https://synthetic.invalid', 400,
        'synthetic', {}, io.BytesIO(body)))
    with pytest.raises(ValueError) as failure:
        transport.provider.chat(messages(), [], response_schema=response_schema('chat'))
    assert not isinstance(failure.value, ReplyValidationError)
    assert len(transport.sent) == 1


def test_transport_schema_error_reports_only_safe_paths_without_retry(transport):
    value = strategy()
    value['interpretation']['symbols'] = [['XAUUSD+', 'NAS100']]
    value['interpretation']['preset'] = None
    value['reason'] = None
    value['message_ko'] = 'PRIVATE_REPLY_MARKER'
    transport.answer(value)
    with pytest.raises(ValueError) as failure:
        transport.provider.chat(messages(), TOOLS, response_schema=response_schema('chat'))
    assert isinstance(failure.value, ReplyValidationError)
    assert json.loads(failure.value.reply_text) == value
    message = str(failure.value)
    for field in ('$.interpretation.symbols[0]', '$.interpretation.preset', '$.reason'):
        assert field in message
    assert message.count(': ') == 3
    assert KEY not in message and 'PRIVATE_REPLY_MARKER' not in message
    assert 'PRIVATE_REPLY_MARKER' not in repr(failure.value)
    assert 'XAUUSD+' not in message and 'NAS100' not in message
    assert len(transport.sent) == 1


def test_authentication_echo_failure_cannot_trigger_reply_correction(transport):
    value = strategy()
    value['message_ko'] = KEY
    transport.answer(value)
    with pytest.raises(ValueError) as failure:
        transport.provider.chat(messages(), TOOLS, response_schema=response_schema('chat'))
    assert not isinstance(failure.value, ReplyValidationError)
    assert not hasattr(failure.value, 'reply_text')
    assert KEY not in str(failure.value) + repr(failure.value)
    assert len(transport.sent) == 1

