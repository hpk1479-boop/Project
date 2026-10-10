"""Base-only AI operations become the same native-frame execution policy as the panel."""
import copy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace as NS

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.virtual_defaults import strategy_profile
from lab.ai import backtest_commands as commands, research_backtest, research_interpreter
from common_ai.security import external_payload
from common_ai.external_prompt import prepare


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('AI regression tests must not use a live connection')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def snapshot():
    profiles = {name: strategy_profile(name) for name in ('SPECIAL8', 'SPECIAL9')}
    return {'today': '2026-10-07', 'jobs': [], 'generated': [], 'execution_version': 'stable',
            'execution_defaults': {}, 'options': {'specials': list(profiles), 'symbols': ['TEST'],
            'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY', 'virtual_profiles': profiles}}


def value(name='SPECIAL9', base='2m', policy=None):
    return {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
            'clarification_question': None, 'message_ko': '확인', 'request': {
                'target_mode': 'SPECIAL', 'specials': [name] if name else [], 'filename': None,
                'watch_text': None, 'symbol': 'TEST', 'start': '2026-09-01', 'end': '2026-10-01',
                'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0,
                'build_only': False, 'rebuild': False, 'available_only': True,
                'base_frame': base, 'virtual_entry': copy.deepcopy(policy)}}


def plan(command):
    return {'strategy_text': None, 'steps': [{'draft': False, 'command': command}],
            'needs_clarification': False, 'clarification_question': None}


@pytest.mark.parametrize('tf,trend', [('2m', '20m'), ('3m', '30m'), ('5m', '1h')])
def test_base_only_request_moves_special9_strategy_environment_and_entry_together(tf, trend):
    original = value(base=tf)
    saved = copy.deepcopy(original)
    command, question = commands._normalize(original, snapshot())
    assert question is None
    entry = command['request']['virtual_entry']
    assert entry['tf'] == tf
    assert entry['filters'][1]['tf'] == trend
    assert entry['conditions'][1]['tf'] == entry['stop']['tf'] == 'SIGNAL'
    assert 'base_frame' not in command['request']
    from event_backtest.virtual_defaults import target_base_frames
    assert target_base_frames(['SPECIAL9'], entry) == {'SPECIAL9': tf}
    assert original == saved


@pytest.mark.parametrize('tf', ['2m', '5m'])
def test_special8_base_request_retains_recipe_conditions_and_uses_selected_frame_everywhere(tf):
    command, question = commands._normalize(value('SPECIAL8', tf), snapshot())
    entry = command['request']['virtual_entry']
    expected = strategy_profile('SPECIAL8')['default']
    expected['tf'] = tf
    assert not question and entry == expected
    from event_backtest.virtual_contract import required_timeframes
    assert required_timeframes(entry, {'signal_tf': tf, 'env_tf': tf, 'tf': tf}) == {'1m', tf}


def test_explicit_custom_policy_is_taken_as_given_and_cannot_choose_frames():
    # 수정본146: a full policy is not moved again, and its frames are the base frame's.
    from event_backtest.virtual_defaults import recipe_on_base
    custom = recipe_on_base(strategy_profile('SPECIAL9'), '2m')
    custom['stop']['multiplier'] = 2.0
    command, question = commands._normalize(value(base=None, policy=custom), snapshot())
    assert not question and command['request']['virtual_entry'] == custom
    for row, frame in (('stop', '15m'), ('trend', '15m')):
        chosen = copy.deepcopy(custom)
        (chosen['stop'] if row == 'stop' else chosen['filters'][1])['tf'] = frame
        with pytest.raises(ValueError, match='기준 프레임으로만'):
            commands._normalize(value(base=None, policy=chosen), snapshot())


@pytest.mark.parametrize('patch', [{'base_frame': '9m'}, {'result_mode': 'ALERT_ONLY'},
                                   {'build_only': True}, {'base_frame': 2}])
def test_invalid_operation_never_becomes_an_executable_command(patch):
    original = value()
    original['request'].update(patch)
    with pytest.raises(ValueError):
        commands._normalize(original, snapshot())


def test_unavailable_base_is_rejected_before_confirmation():
    with pytest.raises(ValueError, match='기준 프레임'):
        commands._normalize(value(base='4h'), snapshot())


def test_operation_survives_a_missing_strategy_but_is_consumed_when_it_is_known():
    command, question = commands._normalize(value(None), snapshot())
    assert question and command['request']['base_frame'] == '2m'
    assert command['request']['virtual_entry'] is None
    resolved = value()
    resolved['request'] = {key: copy.deepcopy(item) for key, item in command['request'].items()
                           if key in commands.REQUEST_FIELDS}
    resolved['request']['specials'] = ['SPECIAL9']
    ready, question = commands._normalize(resolved, snapshot())
    assert not question and 'base_frame' not in ready['request']
    assert ready['request']['virtual_entry']['filters'][1]['tf'] == '20m'


def test_unknown_recipe_cannot_execute_an_unchecked_base_change(monkeypatch):
    monkeypatch.setattr(commands, '_virtual_profile', lambda request: None)
    with pytest.raises(ValueError, match='레시피'):
        commands._normalize(value(), snapshot())


def test_command_session_followup_and_confirmation_use_effective_policy_without_repeating_move():
    seen, executed = [], []
    responses = [value(), value(base='3m'), value(base=None)]
    class Provider:
        def chat(self, messages, tools, response_schema):
            seen.append(copy.deepcopy(messages))
            index = len(seen) - 1
            response = responses[index]
            if index:
                previous = json.loads(messages[1]['content'].split('전체 명령 반환):\n')[1].split('\n현재 요청:')[0])
                assert 'base_frame' not in previous['request']
                response['request']['virtual_entry'] = previous['request']['virtual_entry']
            if index == 2:
                response['request']['start'] = '2026-09-02'
            Draft202012Validator(response_schema).validate(response)
            return {'content': json.dumps(response, ensure_ascii=False)}
    session = commands.Session(Provider(), context_loader=snapshot,
                               executor=lambda command: executed.append(command) or {'offline': True})
    two = session.send('SPECIAL9 기준만 2분으로 바꿔 주세요')
    assert two['can_confirm'] and '20분 추세' in two['preview']
    three = session.send('기준만 3분으로 바꿔 주세요')
    assert three['can_confirm'] and '30분 추세' in three['preview']
    dates = session.send('시작일만 9월 2일로 바꿔 주세요')
    assert dates['can_confirm']
    assert dates['command']['request']['virtual_entry'] == three['command']['request']['virtual_entry']
    session.confirm(dates['revision'])
    assert len(executed) == 1 and executed[0]['request']['virtual_entry']['filters'][1]['tf'] == '30m'
    assert 'base_frame' not in executed[0]['request']
    with pytest.raises(ValueError):
        session.confirm(dates['revision'])


def test_research_keeps_pending_base_until_strategy_answer_and_consumes_it_for_confirm(monkeypatch):
    current = snapshot()
    session = research_backtest.Session(None, lambda: NS(last_intent=None), context_loader=lambda: current)
    ask = session.accept_plan(plan(value(None)), snapshot=current)
    assert ask['needs_clarification'] and not ask['can_confirm']
    pending = copy.deepcopy(session.last_plan)
    assert pending['steps'][0]['command']['request']['base_frame'] == '2m'
    pending['steps'][0]['command']['request']['specials'] = ['SPECIAL9']
    ready = session.accept_plan(pending, snapshot=current)
    assert ready['can_confirm'] and '20분 추세' in ready['preview']
    effective = session.last_plan['steps'][0]['command']['request']
    assert 'base_frame' not in effective
    from lab import backtest_sequences
    received = []
    monkeypatch.setattr(backtest_sequences, 'start', lambda rows, **kwargs:
                        received.extend(copy.deepcopy(rows)) or {'sequence_id': 'offline', 'phase': 'plan'})
    session.confirm(ready['revision'])
    assert received[0]['request']['virtual_entry'] == effective['virtual_entry']
    assert received[0]['request']['virtual_entry']['filters'][1]['tf'] == '20m'


@pytest.mark.parametrize('context_kind', ['command', 'research'])
def test_recipe_default_and_base_operation_survive_external_provider_allowlists(context_kind):
    current = snapshot()
    current['options']['virtual_profiles']['SPECIAL9']['private_source_path'] = 'DO_NOT_INCLUDE'
    chosen = commands._safe_snapshot(current) if context_kind == 'command' else research_interpreter.selection(current)
    if context_kind == 'command':
        messages = [{'role': 'system', 'content': commands._prompt(chosen)},
                    {'role': 'user', 'content': 'SPECIAL9 기준만 2분으로'}]
        contract = commands.command_schema()
    else:
        messages = [{'role': 'user', 'content': json.dumps({'message': '기준만 2분으로',
                    'context': {'selection': chosen, 'previous_plan': plan(value())}}, ensure_ascii=False)}]
        contract = research_interpreter.response_schema('strategy')
    safe, _, _ = external_payload(messages, [], contract, role='backtest' if context_kind == 'command' else 'strategy')
    data = json.loads(safe[0]['content'])
    selection = (data['moses_contract']['selection'] if context_kind == 'command'
                 else json.loads(safe[1]['content'])['context']['selection'])
    profile = selection['options']['virtual_profiles']['SPECIAL9']
    assert profile['base'] == '1m' and profile['default']['filters'][1]['tf'] == '15m'
    assert 'private_source_path' not in profile
    compact, _, _ = prepare(safe, [], contract, {})
    assert 'base_frame' in compact[0]['content'] and '변경 전' in compact[0]['content']
    if context_kind == 'research':
        previous = json.loads(safe[1]['content'])['context']['previous_plan']
        assert previous['steps'][0]['command']['request']['base_frame'] == '2m'


@pytest.mark.parametrize('fifteen,twenty,status', [(False, True, 'ENTERED'), (True, False, 'PASS_ENV')])
def test_normalized_ai_policy_decides_on_native_20m_environment_not_old_15m(fifteen, twenty, status):
    from test_base_change143 import virtual_trade
    command, question = commands._normalize(value(), snapshot())
    assert not question
    trade, _ = virtual_trade(command['request']['virtual_entry'], fifteen_rising=fifteen, twenty_rising=twenty)
    assert trade['status'] == status
