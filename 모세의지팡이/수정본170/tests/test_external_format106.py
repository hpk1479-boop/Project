"""Schema field reminders and bounded correction use synthetic HTTP replies only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai import external_prompt, gemini, model_runtime
from common_ai.external_errors import ReplyValidationError
from lab.ai import research_interpreter


SETTINGS = {'provider': 'gemini', 'gemini_model': 'synthetic-future-model',
            'gemini_api_key': 'synthetic-format106-key', 'timeout': 90}
REQUEST = '15분 상승추세에서 하단 원비 터치 시 1분 올존으로 골드 1년 백테스트'
GUIDE_PREFIX = 'Outer JSON object field guide (derived from the original schema; not a response example): '


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def canonical():
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
            'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'},
                      {'kind': 'WONBI_TOUCH', 'tfs': ['15m'], 'side': 'LOWER'}],
            'order_mode': 'SIMULTANEOUS',
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '15분 상승추세와 하단 원비 터치 조건에서 1분 올존 알림'}


def backtest():
    return {'kind': 'BACKTEST', 'strategy': canonical(), 'message_ko': '골드 1년 백테스트 계획',
        'plan': {'strategy_text': None, 'needs_clarification': False, 'clarification_question': None,
            'steps': [{'draft': True, 'command': {'supported': True, 'action': 'START',
                'request': {'target_mode': 'GENERATED', 'symbol': 'XAUUSD+', 'filename': None,
                            'start': '2025-10-04', 'end': '2026-10-04', 'result_mode': 'VIRTUAL_ENTRY'},
                'job_id': None, 'needs_clarification': False, 'clarification_question': None,
                'message_ko': '선택한 기간을 실행'}}]}}


def misplaced_plan_fields():
    value = backtest()
    for name in ('needs_clarification', 'clarification_question'):
        value[name] = value['plan'].pop(name)
    return value


def messages():
    return [{'role': 'system', 'content': dump({'moses_contract': {}})},
            {'role': 'user', 'content': dump({'mode': 'strategy', 'message': REQUEST,
                'context': {'selection': {'today': '2026-10-04'}, 'current_strategy': None}})}]


def field_guide(prompt):
    value, _ = json.JSONDecoder().raw_decode(prompt.split(GUIDE_PREFIX, 1)[1])
    return value


class Reply:
    def __init__(self, value):
        self.payload = dump({'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': dump(value)}]}}]}).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.payload[:limit]


@pytest.fixture
def remote(monkeypatch):
    sent, answers = [], []
    monkeypatch.setattr(model_runtime.RUNTIME, 'remote_chat',
        lambda provider, operation: operation(time.monotonic() + provider.timeout))

    def send(request, *, timeout):
        sent.append(json.loads(request.data))
        return Reply(answers.pop(0))

    monkeypatch.setattr(gemini, '_open_request', send)
    monkeypatch.setattr(research_interpreter, 'external_system_prompt',
        lambda _workspace: dump({'moses_contract': {}}))
    agent = SimpleNamespace(workspace=object(), call_tool=lambda *_: pytest.fail('No tool was requested'))
    return SimpleNamespace(sent=sent, answers=answers, provider=gemini.Gemini(SETTINGS), agent=agent)


def test_object_guide_uses_arbitrary_schema_fields_without_supplied_values():
    schema = {'anyOf': [{'type': 'object', 'additionalProperties': False,
        'required': ['operation', 'payload'], 'properties': {
            'operation': {'const': 'ARBITRARY_OPERATION'},
            'payload': {'type': 'object', 'additionalProperties': False,
                'required': ['acknowledged', 'note'], 'properties': {
                    'acknowledged': {'type': 'boolean'}, 'note': {'type': ['string', 'null']},
                    'optional_limit': {'type': 'integer', 'minimum': 1}}}}}]}
    before = copy.deepcopy(schema)
    rows, _, original = external_prompt.prepare(messages(), [], schema, {})
    guide = field_guide(rows[0]['content'])
    assert guide == [
        {'path': '$', 'required': ['operation', 'payload'], 'when': {'$.operation': 'ARBITRARY_OPERATION'},
         'allowed_fields': ['operation', 'payload'], 'field_types': {'payload': ['object']}},
        {'path': '$.payload', 'required': ['acknowledged', 'note'],
         'when': {'$.operation': 'ARBITRARY_OPERATION'},
         'allowed_fields': ['acknowledged', 'note', 'optional_limit'],
         'field_types': {'acknowledged': ['boolean'], 'note': ['string', 'null'], 'optional_limit': ['integer']}}]
    assert original == before and schema == before
    assert 'Include every required field at its own object path' in rows[0]['content']
    assert 'this guide does not supply values or override any constraint' in rows[0]['content']


def test_gemini_wire_exposes_complete_outer_plan_fields_from_original_schema(remote):
    value = backtest()
    schema = research_interpreter.response_schema('strategy')
    before = copy.deepcopy((schema, value))
    remote.answers.append(value)
    reply = remote.provider.chat(messages(), [], response_schema=schema)
    assert json.loads(reply['content']) == value
    assert (schema, value) == before
    assert len(remote.sent) == 1
    body = remote.sent[0]
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    assert 'responseJsonSchema' not in body['generationConfig']
    guide = field_guide(body['systemInstruction']['parts'][0]['text'])
    backtest_schema = schema['anyOf'][1]
    outer = next(row for row in guide if row['path'] == '$' and row.get('when') == {'$.kind': 'BACKTEST'})
    plan = next(row for row in guide if row['path'] == '$.plan')
    assert outer['required'] == backtest_schema['required']
    assert outer['allowed_fields'] == list(backtest_schema['properties'])
    assert plan['required'] == backtest_schema['properties']['plan']['required']
    assert plan['allowed_fields'] == list(backtest_schema['properties']['plan']['properties'])
    assert plan['field_types']['strategy_text'] == ['null']
    assert plan['field_types']['needs_clarification'] == ['boolean']
    assert plan['field_types']['clarification_question'] == ['string', 'null']
    # The natural-language request stays intact beside schema-only reminders.
    wire_users = [part['text'] for row in body['contents'] if row['role'] == 'user' for part in row['parts']]
    assert any(json.loads(text).get('message') == REQUEST for text in wire_users if text.startswith('{'))


@pytest.mark.parametrize('missing', ['needs_clarification', 'clarification_question'])
def test_missing_plan_fields_remain_rejected_without_local_defaults_or_transport_retry(remote, missing):
    value = backtest()
    del value['plan'][missing]
    before = copy.deepcopy(value)
    schema = research_interpreter.response_schema('strategy')
    assert not Draft202012Validator(schema).is_valid(value)
    remote.answers.append(value)
    with pytest.raises(ReplyValidationError) as failure:
        remote.provider.chat(messages(), [], response_schema=schema)
    assert '$.plan.' + missing in str(failure.value)
    assert json.loads(failure.value.reply_text) == before and value == before
    assert len(remote.sent) == 1


def test_misplaced_envelope_fields_are_rejected_without_relocating_them(remote):
    value = misplaced_plan_fields()
    before = copy.deepcopy(value)
    remote.answers.append(value)
    with pytest.raises(ReplyValidationError) as failure:
        remote.provider.chat(messages(), [], response_schema=research_interpreter.response_schema('strategy'))
    assert '$: 불필요한 필드' in str(failure.value)
    assert '$.plan.needs_clarification' in str(failure.value)
    assert '$.plan.clarification_question' in str(failure.value)
    assert json.loads(failure.value.reply_text) == before and value == before
    assert len(remote.sent) == 1


def test_invalid_plan_is_corrected_by_existing_single_allowance_with_original_meaning(remote):
    invalid, valid = misplaced_plan_fields(), backtest()
    remote.answers.extend([invalid, valid])
    result, actions = research_interpreter.interpret(remote.provider, remote.agent,
        'strategy', [], {}, REQUEST)
    assert result == valid and actions == []
    assert len(remote.sent) == 2
    first_guide = field_guide(remote.sent[0]['systemInstruction']['parts'][0]['text'])
    assert field_guide(remote.sent[1]['systemInstruction']['parts'][0]['text']) == first_guide
    text = dump(remote.sent[1]['contents'])
    assert REQUEST in text
    assert '$.plan.needs_clarification' in text and '$.plan.clarification_question' in text
    assert '조건·방향·시간봉·순서·객체 참조를 추가하거나 바꾸지 마세요' in text
    assert result['strategy']['interpretation'] == canonical()['interpretation']
    assert result['plan'] == valid['plan']


def test_second_invalid_reply_stops_at_existing_correction_limit(remote):
    invalid = misplaced_plan_fields()
    before = copy.deepcopy(invalid)
    remote.answers.extend([invalid, copy.deepcopy(invalid), backtest()])
    with pytest.raises(ReplyValidationError) as failure:
        research_interpreter.interpret(remote.provider, remote.agent, 'strategy', [], {}, REQUEST)
    assert '$.plan.needs_clarification' in str(failure.value)
    assert '$.plan.clarification_question' in str(failure.value)
    assert len(remote.sent) == 2 and len(remote.answers) == 1
    assert invalid == before


@pytest.mark.parametrize('value', [canonical(), backtest(),
    {'kind': 'CHAT', 'strategy': None, 'plan': None, 'message_ko': '한국어 연구 설명'}],
    ids=['canonical', 'backtest', 'chat'])
def test_valid_original_reply_variants_are_kept_without_new_calls(remote, value):
    before = copy.deepcopy(value)
    remote.answers.append(value)
    result = remote.provider.chat(messages(), [], response_schema=research_interpreter.response_schema('chat'))
    assert json.loads(result['content']) == before and value == before
    assert len(remote.sent) == 1
