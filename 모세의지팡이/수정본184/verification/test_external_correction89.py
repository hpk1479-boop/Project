"""Existing interpretation correction is bounded and keeps external permissions.

All model replies are scripted. No remote API, strategy application, generated
file, or backtest job is used by these regressions.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from test_research79 import fixture, research_reply, snapshot, storage, sequences
from test_research_modes80 import conversation
from common_ai import external_prompt
from common_ai.external_errors import ReplyValidationError, reply_error_message
from common_ai.reply_format import parse_reply
from lab.ai.intent import validate_intent
from lab.ai.research import DiscussionError
from lab.ai.research_interpreter import response_schema


SECRET = 'synthetic-setting-key-must-not-enter-external-context'
PRIVATE = 'synthetic-project-source-must-not-enter-external-context'


def external_conversation(script, monkeypatch, tmp_path):
    research, agent, provider, apply = conversation(script)
    provider.permission_scope = 'external'
    provider.settings = {'provider': 'gemini', 'gemini_api_key': SECRET}
    provider.wire = []
    native = provider.chat

    def remote_boundary(messages, tools, *, response_schema):
        # Use the same outbound security/schema preparation as the real remote
        # providers and their original strict reply parser, without HTTP.
        rows, allowed, original = external_prompt.prepare(messages, tools, response_schema, provider.settings)
        assert original == response_schema
        provider.wire.append(copy.deepcopy({'messages': rows, 'tools': allowed,
            'schema': original, 'provider': provider.settings['provider']}))
        reply = native(messages, tools, response_schema=response_schema)
        if isinstance(reply, BaseException):
            raise reply
        if reply.get('tool_calls'):
            text = json.dumps({'tool_calls': [{'name': call['name'], 'arguments': call.get('arguments', {})}
                for call in reply['tool_calls']]})
        else:
            text = reply.get('content') or ''
        try:
            return parse_reply(text, original, allowed)
        except ValueError:
            raise ReplyValidationError(reply_error_message('gemini', text, original, allowed), text) from None

    provider.chat = remote_boundary
    root = tmp_path / 'Part3'
    root.mkdir()
    monkeypatch.setattr(storage, 'ROOT', root)
    generate, start = Mock(), Mock()
    monkeypatch.setattr(storage, 'generate', generate)
    monkeypatch.setattr(sequences, 'start', start)
    forbidden_execution = Mock(side_effect=AssertionError('A failed model candidate cannot execute a tool'))
    monkeypatch.setattr(agent, 'call_tool', forbidden_execution)
    research.context_loader = lambda: {**snapshot(), 'connections': {'password': SECRET},
        'source_code': PRIVATE, 'logs': PRIVATE}
    return research, agent, provider, apply, generate, start, forbidden_execution, root


def untouched(apply, generate, start, tool, root):
    apply.assert_not_called()
    generate.assert_not_called()
    start.assert_not_called()
    tool.assert_not_called()
    assert not any(path.is_file() for path in root.rglob('*'))


def test_valid_first_reply_uses_one_call_and_still_waits_for_confirmation(monkeypatch, tmp_path):
    canonical = fixture().original()
    research, agent, provider, *effects = external_conversation(
        [research_reply('STRATEGY', strategy=canonical)], monkeypatch, tmp_path)
    result = research.send('15분 확정봉 상승추세에 3분 하단 원비, 1분 브레이커 올존 전략')
    assert result['can_apply'] and agent.last_intent == canonical
    assert len(provider.exchanges) == len(provider.wire) == 1
    untouched(*effects)


def test_schema_correction_preserves_full_meaning_context_contract_and_external_filter(monkeypatch, tmp_path):
    f = fixture()
    malformed = f.changed()
    malformed['reason'] = None
    malformed['source_code'] = PRIVATE
    research, agent, provider, *effects = external_conversation([
        research_reply('STRATEGY', strategy=malformed),
        research_reply('STRATEGY', strategy=f.changed())], monkeypatch, tmp_path)
    agent.last_intent = f.original()
    original = copy.deepcopy(response_schema('strategy'))
    request = '직전 전략에서 원비 시간봉만 15분으로 수정해. 나머지 조건은 그대로야.'
    result = research.send(request)
    assert result['can_apply'] and agent.last_intent == f.changed()
    assert len(provider.exchanges) == len(provider.wire) == 2
    initial, correction = provider.exchanges
    assert correction[:len(initial)] == initial
    first_request = json.loads(initial[1]['content'])
    assert first_request['message'] == request
    assert first_request['context']['current_strategy'] == f.original()
    assert json.loads(correction[-2]['content']) == malformed
    assert 'schema 검증' in json.loads(correction[-1]['content'])['message']
    first_wire, second_wire = provider.wire
    assert first_wire['schema'] == second_wire['schema'] == original
    assert first_wire['messages'][0] == second_wire['messages'][0]
    assert first_wire['provider'] == second_wire['provider'] == 'gemini'
    for wire in provider.wire:
        body = json.dumps(wire, ensure_ascii=False)
        assert SECRET not in body and PRIVATE not in body
        names = {spec['function']['name'] for spec in wire['tools']}
        assert names <= {'vocabulary', 'list_specials', 'describe_special'}
    corrected_copy = f.changed()
    corrected_copy['interpretation']['steps'][1]['tfs'] = ['3m']
    assert corrected_copy == f.original()
    assert response_schema('strategy') == original
    untouched(*effects)


def test_second_schema_failure_stops_at_two_calls_without_committing_state(monkeypatch, tmp_path):
    invalid = fixture().original()
    invalid['reason'] = None
    unused = research_reply('STRATEGY', strategy=fixture().original())
    research, agent, provider, *effects = external_conversation([
        research_reply('STRATEGY', strategy=invalid),
        research_reply('STRATEGY', strategy=invalid), unused], monkeypatch, tmp_path)
    before = copy.deepcopy(agent.checkpoint())
    with pytest.raises(DiscussionError) as failure:
        research.send('15분 상승추세 전략 만들어줘')
    assert failure.value.pending_preserved
    assert len(provider.exchanges) == 2 and provider.script == [unused]
    assert agent.checkpoint() == before and research.pending is None
    untouched(*effects)


@pytest.mark.parametrize('error', [ValueError('HTTP 400 요청 오류'), ValueError('API 키 인증 실패')])
def test_transport_or_authentication_failure_does_not_use_correction(monkeypatch, tmp_path, error):
    unused = research_reply('STRATEGY', strategy=fixture().original())
    research, agent, provider, *effects = external_conversation([error, unused], monkeypatch, tmp_path)
    before = copy.deepcopy(agent.checkpoint())
    with pytest.raises(DiscussionError):
        research.send('15분 상승추세 전략 만들어줘')
    assert len(provider.exchanges) == 1 and provider.script == [unused]
    assert agent.checkpoint() == before and research.pending is None
    untouched(*effects)


def test_schema_and_semantic_failures_share_one_correction_allowance(monkeypatch, tmp_path):
    bad_shape = fixture().original()
    bad_shape['reason'] = None
    bad_meaning = fixture().original()
    bad_meaning['interpretation']['steps'][0]['ref'] = 'not_captured'
    assert Draft202012Validator(response_schema('strategy')).is_valid(bad_meaning)
    with pytest.raises(ValueError):
        validate_intent(bad_meaning)
    unused = research_reply('STRATEGY', strategy=fixture().original())
    research, agent, provider, *effects = external_conversation([
        research_reply('STRATEGY', strategy=bad_shape),
        research_reply('STRATEGY', strategy=bad_meaning), unused], monkeypatch, tmp_path)
    before = copy.deepcopy(agent.checkpoint())
    with pytest.raises(DiscussionError):
        research.send('15분 상승추세 전략 만들어줘')
    assert len(provider.exchanges) == 2 and provider.script == [unused]
    assert agent.checkpoint() == before and research.pending is None
    untouched(*effects)


def test_forbidden_tool_candidate_is_rejected_before_correction_or_tool_execution(monkeypatch, tmp_path):
    canonical = fixture().original()
    forbidden = {'content': '', 'tool_calls': [{'id': 'synthetic-private-tool',
        'name': 'read_project_code', 'arguments': {'path': PRIVATE}}]}
    research, agent, provider, *effects = external_conversation([
        forbidden, research_reply('STRATEGY', strategy=canonical)], monkeypatch, tmp_path)
    result = research.send('15분 상승추세 전략 만들어줘')
    assert result['can_apply'] and agent.last_intent == canonical
    assert len(provider.exchanges) == 2
    for wire in provider.wire:
        body = json.dumps(wire, ensure_ascii=False)
        assert PRIVATE not in body and 'read_project_code' not in body and SECRET not in body
    untouched(*effects)
