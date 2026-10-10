"""Validated shared intents and bounded per-turn strategy context."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'Part3'))

from lab.ai import agent as agent_module
from lab.ai.agent import Agent
from lab.ai.intent import validate_intent


def intent():
    return {
        'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {
            'direction': 'LONG', 'symbols': ['XAUUSD+'],
            'steps': [
                {'kind': 'MA_CROSS', 'tf': '1m', 'ma_left': 'WMA17',
                 'ma_right': 'EMA50', 'direction': 'LONG', 'bar_state': 'FORMING'},
                {'kind': 'FVG_NEW', 'tf': '5m', 'side': 'BULL', 'direction': 'LONG'},
            ],
            'order_mode': 'SEQUENTIAL', 'within_sec': 600,
            'final_window_sec': 3600, 'final_after': 'FVG_NEW',
            'final': {'kind': 'OZ', 'tf': '1m', 'validation_mode': 'BLIND',
                      'trigger_mode': 'BREAKER'},
        },
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '교차 후 FVG가 생기면 올존',
    }


def reply(value):
    return {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}


class Provider:
    permission_scope = 'local'  # This fixture verifies the retained local diagnostic context.

    def __init__(self, script=()):
        self.script = list(script)
        self.exchanges = []

    def chat(self, messages, tools):
        self.exchanges.append(copy.deepcopy(messages))
        if not self.script:
            raise AssertionError('accept must not call AI')
        return self.script.pop(0)


@pytest.fixture(autouse=True)
def bounded_system_prompt(monkeypatch):
    # Production prompt stays unchanged; these tests isolate turn ownership.
    monkeypatch.setattr(agent_module, 'system_prompt', lambda: 'current system contract')


def test_accept_validates_without_ai_or_apply_and_preserves_timeframes(monkeypatch):
    from lab import storage

    provider = Provider()
    agent = Agent(provider)
    apply = Mock()
    generate = Mock()
    monkeypatch.setattr(agent, 'apply', apply)
    monkeypatch.setattr(storage, 'generate', generate)
    proposed = intent()
    actions = [{'tool': 'vocabulary', 'ok': True, 'error': None}]
    out = agent.accept(proposed, '진행봉 WMA17 EMA50 교차 후 5분 새 FVG', actions)
    expected = validate_intent(intent())
    assert out['result'] == expected and out['can_apply']
    assert out['revision'] == 1 and not out['is_revision']
    assert out['actions'] == actions and out['actions'] is not actions
    assert agent.candidate == expected and agent.last_intent == expected
    assert agent.first_interpretation == intent()['interpretation']
    assert agent.pending_clarification is None
    assert provider.exchanges == []
    apply.assert_not_called()
    generate.assert_not_called()
    proposed['interpretation']['steps'][1]['tf'] = '1m'
    out['result']['interpretation']['steps'][0]['ma_left'] = 'EMA1'
    assert agent.candidate['interpretation']['steps'][1]['tfs'] == ['5m']
    assert agent.last_intent['interpretation']['steps'][0]['ma_left'] == 'WMA17'
    assert agent.candidate['interpretation']['steps'][0]['bar_state'] == 'FORMING'
    assert agent.candidate['interpretation']['final']['tfs'] == ['1m']


def test_accept_revision_preserves_original_draft_and_only_latest_can_apply():
    agent = Agent(Provider())
    first = agent.accept(intent(), '처음 전략')
    first_interpretation = copy.deepcopy(agent.first_interpretation)
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    second = agent.accept(changed, 'FVG만 15분으로 변경')
    assert second['revision'] == 2 and second['is_revision']
    assert agent.first_interpretation == first_interpretation
    assert agent.original_text == '처음 전략\n[수정 요청] FVG만 15분으로 변경'
    with pytest.raises(ValueError, match='최신 해석'):
        agent.apply(first['revision'])
    recipe = agent.apply(second['revision'])
    assert recipe['strategy_intent']['steps'][1]['tfs'] == ['15m']
    assert recipe['strategy_intent']['steps'][0] == first['result']['interpretation']['steps'][0]
    assert recipe['strategy_intent']['final'] == first['result']['interpretation']['final']
    agent.cancel()
    assert agent.candidate is None and agent.last_intent == second['result']
    with pytest.raises(ValueError, match='확정 전략'):
        agent.apply(second['revision'])


def test_accept_clarification_blocks_apply_until_valid_answer():
    agent = Agent(Provider())
    first = intent()
    first['needs_clarification'] = True
    first['clarification_question'] = 'FVG는 5분 시간봉인가요?'
    out = agent.accept(first, '전략 확인')
    assert not out['can_apply']
    assert agent.pending_clarification == out['result']
    assert agent.last_intent == out['result']
    with pytest.raises(ValueError, match='확정 전략'):
        agent.apply(out['revision'])
    answered = agent.accept(intent(), '네 5분입니다')
    assert answered['can_apply'] and answered['is_revision']
    assert agent.pending_clarification is None
    assert '[확인 답변] 네 5분입니다' in agent.original_text
    assert agent.first_interpretation == first['interpretation']


@pytest.mark.parametrize('damage', [
    'not_object', 'encoded_json', 'missing_supported', 'wrong_intent',
    'source_text', 'unknown_kind', 'bad_tf', 'kind_list', 'missing_question',
])
def test_accept_invalid_result_rejects_without_second_ai_call(damage):
    proposed = intent()
    if damage == 'not_object': proposed = None
    elif damage == 'encoded_json': proposed = json.dumps(proposed)
    elif damage == 'missing_supported': proposed.pop('supported')
    elif damage == 'wrong_intent': proposed['intent'] = 'create_strategy'
    elif damage == 'source_text': proposed['source_text'] = 'print(1)'
    elif damage == 'unknown_kind': proposed['interpretation']['steps'][0]['kind'] = 'NEW_MAGIC'
    elif damage == 'bad_tf': proposed['interpretation']['steps'][0]['tf'] = '9h'
    elif damage == 'kind_list': proposed['interpretation']['steps'][0]['kind'] = []
    elif damage == 'missing_question': proposed['needs_clarification'] = True
    provider = Provider()
    agent = Agent(provider)
    previous = agent.accept(intent(), '전략')
    out = agent.accept(proposed, '수정 결과')
    assert not out['can_apply'] and out['result']['reason'] == 'MODEL_OUTPUT_INVALID'
    assert agent.candidate is None and agent.pending_clarification is None
    assert agent.last_intent == previous['result']
    assert out['revision'] == 2 and out['is_revision']
    assert provider.exchanges == []


def test_accept_unsupported_keeps_schema_scope_decision():
    agent = Agent(Provider())
    out = agent.accept({'supported': False, 'reason': 'MOSES_SCOPE_ONLY',
                        'message_ko': '범위 밖입니다.'}, '날씨')
    assert not out['can_apply'] and out['result']['reason'] == 'MOSES_SCOPE_ONLY'
    assert agent.last_intent is None and agent.first_interpretation is None


def test_accept_recipe_validation_failure_cannot_apply(monkeypatch):
    agent = Agent(Provider())
    monkeypatch.setattr(agent_module, 'recipe_from_intent', Mock(side_effect=ValueError('실행 계약 불가')))
    out = agent.accept(intent(), '전략')
    assert out['result']['supported'] and not out['can_apply']
    assert out['application_error'] == '실행 계약 불가'
    assert agent.last_intent == out['result'] and agent.candidate is None


def test_send_keeps_tools_and_json_correction_within_current_turn():
    script = [
        {'content': '', 'tool_calls': [{'id': 'lookup', 'name': 'vocabulary', 'arguments': {}}]},
        {'content': 'lookup summary', 'tool_calls': []},
        reply(intent()),
    ]
    provider = Provider(script)
    workspace = Mock()
    workspace.list_specials.return_value = []
    workspace.vocabulary.return_value = {'timeframes': ['1m', '5m']}
    agent = Agent(provider, workspace)
    out = agent.send('첫 요청')
    assert out['can_apply'] and len(provider.exchanges) == 3
    assert out['actions'] == [{'tool': 'vocabulary', 'ok': True, 'error': None}]
    workspace.vocabulary.assert_called_with()
    second = provider.exchanges[1]
    assert [m['role'] for m in second] == ['system', 'user', 'assistant', 'tool']
    assert second[2]['tool_calls'][0]['id'] == second[3]['tool_call_id'] == 'lookup'
    third = provider.exchanges[2]
    assert third[:len(second)] == second
    assert '원래 전략 요청' in third[-1]['content']


def test_send_revision_sends_only_current_canonical_not_prior_history():
    initial = intent()
    changed = validate_intent(initial)
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    provider = Provider([
        {'content': '', 'tool_calls': [{'id': 'old-lookup', 'name': 'vocabulary', 'arguments': {}}]},
        reply(initial), reply(changed), reply(changed),
    ])
    workspace = Mock()
    workspace.list_specials.return_value = []
    workspace.vocabulary.side_effect = lambda: (
        {'intent_kinds': ['old tool marker']} if len(provider.exchanges)==1
        else {'timeframes': ['1m', '5m']})
    agent = Agent(provider, workspace)
    first = agent.send('long old request marker ' * 400)
    second = agent.send('FVG만 15분으로 변경')
    context = provider.exchanges[2]
    assert len(context) == 2 and [m['role'] for m in context] == ['system', 'user']
    assert json.dumps(first['result'], ensure_ascii=False) in context[-1]['content']
    assert 'FVG만 15분으로 변경' in context[-1]['content']
    assert 'long old request marker' not in json.dumps(context, ensure_ascii=False)
    assert 'old-lookup' not in json.dumps(context, ensure_ascii=False)
    assert 'old tool marker' not in json.dumps(context, ensure_ascii=False)
    assert second['can_apply'] and second['is_revision']
    assert agent.first_interpretation == initial['interpretation']
    assert agent.original_text.startswith('long old request marker ')
    agent.send('최종은 그대로')
    assert len(provider.exchanges[3]) == 2
    assert json.dumps(second['result'], ensure_ascii=False) in provider.exchanges[3][-1]['content']
    assert 'FVG만 15분으로 변경' not in provider.exchanges[3][-1]['content']


def test_send_followup_keeps_question_and_canonical_after_accept():
    first = intent()
    first['needs_clarification'] = True
    first['clarification_question'] = 'FVG는 5분 시간봉인가요?'
    provider = Provider([reply(intent())])
    agent = Agent(provider)
    accepted = agent.accept(first, 'original clarification request marker')
    answered = agent.send('네 5분입니다')
    context = provider.exchanges[0]
    request=json.loads(context[-1]['content'])
    assert request['context']['question'] == first['clarification_question']
    assert request['context']['current_strategy'] == accepted['result']
    assert request['message'] == '네 5분입니다'
    assert 'original clarification request marker' not in json.dumps(context, ensure_ascii=False)
    assert answered['can_apply'] and answered['revision'] == 2 and answered['is_revision']
    assert agent.pending_clarification is None


def test_send_to_accept_retains_same_state_and_validation_boundary():
    first = intent()
    provider = Provider([reply(first)])
    agent = Agent(provider)
    out = agent.send('첫 요청')
    revised = copy.deepcopy(out['result'])
    revised['interpretation']['within_sec'] = 1200
    accepted = agent.accept(revised, '교차 후 20분 안으로 변경')
    assert accepted['can_apply'] and accepted['is_revision'] and accepted['revision'] == 2
    assert len(provider.exchanges) == 1
    assert agent.apply(2)['strategy_intent']['within_sec'] == 1200
    assert agent.first_interpretation == first['interpretation']


def test_empty_request_does_not_invalidate_existing_candidate():
    agent = Agent(Provider())
    original = agent.accept(intent(), '전략')
    with pytest.raises(ValueError, match='입력'):
        agent.accept(intent(), '  ')
    assert agent.revision == original['revision'] and agent.candidate == original['result']
