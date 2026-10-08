"""Command interpretation is separate from strategy generation and process execution."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab.ai import backtest_commands as commands
from lab.ai import provider


JOB = 'a' * 32
OTHER_JOB = 'b' * 32


def snapshot():
    return {'today': '2026-10-01', 'options': {'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY',
        'specials': ['SPECIAL1', 'SPECIAL2'], 'symbols': ['XAUUSD+', 'BTCUSD'],
        'spread_points': {'XAUUSD+': 12}, 'default_triggers': {'SPECIAL1': 'BREAKER'},
        'special_settings': {'SPECIAL1': {'enabled': False, 'trigger': None,
            'time_filters': {'MAIN_ASIA': {'enabled': True, 'start': '09:00', 'end': '15:00'}}},
            'SPECIAL2': {'enabled': True, 'trigger': '무지성 올존', 'time_filters': {}}}},
        'generated': [{'filename': 'Test_SPECIAL001.py', 'name': '생성 전략'}],
        'jobs': [{'job_id': JOB, 'phase': 'run', 'source': ['SPECIAL1'], 'symbol': 'XAUUSD+'}],
        'execution_defaults': {'cores': 2, 'work_size': 'MONTH', 'capture_start': 'keyframe',
                              'oz_evaluation': 'selected', 'overlap_trading_days': 3}}


def request(**changes):
    return {'target_mode': 'SPECIAL', 'specials': ['SPECIAL1'], 'symbol': 'XAUUSD+',
            'start': '2026-09-01', 'end': '2026-10-01', **changes}


def value(action='START', *, data=None, job_id=None, **changes):
    return {'supported': True, 'action': action,
            'request': request() if action == 'START' and data is None else data,
            'job_id': job_id, 'needs_clarification': False, 'clarification_question': None,
            'message_ko': '요청을 해석했습니다.', **changes}


def session(*values, state=None, executor=None):
    model = provider.ScriptedProvider([{'content': json.dumps(item, ensure_ascii=False)} for item in values])
    state = snapshot() if state is None else state
    executor = executor or Mock(return_value={'job_id': JOB, 'phase': 'planning'})
    return commands.Session(model, context_loader=lambda: copy.deepcopy(state), executor=executor), executor


def test_start_is_preview_only_then_confirm_calls_common_operation_once():
    agent, execute = session(value())
    result = agent.send('SPECIAL1 골드 9월 백테스트')
    execute.assert_not_called()
    assert result['can_confirm'] and not result['needs_clarification']
    assert '기간 (UTC): 2026-09-01 ~ 2026-09-30' in result['preview']
    assert '종료 경계 2026-10-01, 미포함' in result['preview']
    assert '스프레드: 12 포인트' in result['preview']
    assert '가상 진입' in result['preview'] and 'CPU 2' in result['preview']
    assert '최종 OZ 브레이커 올존' in result['preview']
    assert '아시아 09:00~15:00' in result['preview']
    assert '새 데이터 녹화' in result['preview']
    confirmed = agent.confirm(result['revision'])
    assert confirmed['result']['job_id'] == JOB
    execute.assert_called_once()
    selected = execute.call_args.args[0]['request']
    assert selected['special_settings']['SPECIAL1']['enabled'] is True
    assert selected['mode'] == 'BAR' and selected['result_mode'] == 'VIRTUAL_ENTRY'
    with pytest.raises(ValueError, match='확인할'):
        agent.confirm(result['revision'])
    assert execute.call_count == 1


@pytest.mark.parametrize('start,end,included_last', [
    ('2024-02-01', '2024-03-01', '2024-02-29'),
    ('2026-09-01', '2026-10-01', '2026-09-30'),
    ('2026-12-01', '2027-01-01', '2026-12-31'),
])
def test_leap_month_and_year_boundaries_show_exclusive_utc_end(start, end, included_last):
    agent, execute = session(value(data=request(start=start, end=end)))
    interpreted = agent.send('기간 백테스트')
    assert f'기간 (UTC): {start} ~ {included_last}' in interpreted['preview']
    assert f'종료 경계 {end}, 미포함' in interpreted['preview']
    execute.assert_not_called()


def test_missing_fields_ask_then_followup_keeps_previous_structured_command():
    first = value(data=request(symbol=None, start=None, end=None))
    agent, execute = session(first, value())
    missing = agent.send('SPECIAL1로 백테스트')
    assert missing['needs_clarification'] and '종목' in missing['clarification_question']
    assert not missing['can_confirm']
    execute.assert_not_called()
    complete = agent.send('금, 2026년 9월')
    assert complete['can_confirm']
    prompt = agent.provider.seen[-1]['content']
    assert '직전 명령' in prompt and 'SPECIAL1' in prompt and '직전 확인 질문' in prompt
    execute.assert_not_called()


def test_modified_command_invalidates_old_confirmation():
    agent, execute = session(value(), value(data=request(start='2026-08-01')))
    original = agent.send('9월 백테스트')
    revised = agent.send('시작일만 8월 1일로 수정')
    assert original['revision'] != revised['revision']
    with pytest.raises(ValueError, match='변경'):
        agent.confirm(original['revision'])
    execute.assert_not_called()
    agent.confirm(revised['revision'])
    assert execute.call_args.args[0]['request']['start'] == '2026-08-01'


@pytest.mark.parametrize('operation', ['cancel', 'reset'])
def test_cancel_and_reset_remove_pending_mutation(operation):
    agent, execute = session(value())
    interpreted = agent.send('백테스트')
    getattr(agent, operation)()
    with pytest.raises(ValueError):
        agent.confirm(interpreted['revision'])
    execute.assert_not_called()


def test_revision_is_unique_between_sessions_and_missing_revision_fails():
    first, _ = session(value())
    second, execute = session(value())
    a, b = first.send('백테스트'), second.send('백테스트')
    assert a['revision'] != b['revision']
    for revision in (None, 1, a['revision']):
        with pytest.raises(ValueError, match='변경'):
            second.confirm(revision)
    execute.assert_not_called()


def test_simultaneous_confirmations_execute_once():
    agent, execute = session(value())
    revision = agent.send('백테스트')['revision']
    def confirm():
        try:
            agent.confirm(revision)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(), range(2)))
    assert sorted(results) == [False, True]
    execute.assert_called_once()


def test_failed_operation_still_consumes_confirmation():
    execute = Mock(side_effect=ValueError('창고 설정 오류'))
    agent, _ = session(value(), executor=execute)
    revision = agent.send('백테스트')['revision']
    with pytest.raises(ValueError, match='창고'):
        agent.confirm(revision)
    with pytest.raises(ValueError, match='확인할'):
        agent.confirm(revision)
    execute.assert_called_once()


@pytest.mark.parametrize('action', ['STATUS', 'RECENT', 'RECONNECT'])
def test_read_only_commands_reuse_common_api_immediately(action):
    agent, execute = session(value(action))
    result = agent.send('작업 조회')
    assert not result['can_confirm'] and result['result']['job_id'] == JOB
    expected = {'action': action} if action == 'RECENT' else {'action': action, 'job_id': JOB}
    execute.assert_called_once_with(expected)


def test_stop_is_pinned_to_preview_job_even_when_recent_jobs_change():
    state = snapshot()
    agent, execute = session(value('STOP'), state=state)
    pending = agent.send('진행 작업 중지')
    assert pending['command']['job_id'] == JOB
    execute.assert_not_called()
    state['jobs'].insert(0, {'job_id': OTHER_JOB, 'phase': 'run'})
    agent.confirm(pending['revision'])
    execute.assert_called_once_with({'action': 'STOP', 'job_id': JOB})


@pytest.mark.parametrize('phase', ['planning', 'plan', 'starting', 'run', 'confirm'])
def test_ambiguous_running_jobs_ask_for_specific_target(phase):
    state = snapshot()
    state['jobs'].append({'job_id': OTHER_JOB, 'phase': phase})
    agent, execute = session(value('STOP'), state=state)
    result = agent.send('작업 중지')
    assert result['needs_clarification'] and '여러' in result['clarification_question']
    execute.assert_not_called()


def test_unknown_job_id_and_empty_history_never_stop_any_process():
    agent, execute = session(value('STOP', job_id=OTHER_JOB))
    assert agent.send('작업 중지')['needs_clarification']
    execute.assert_not_called()
    state = snapshot(); state['jobs'] = []
    agent, execute = session(value('STOP'), state=state)
    assert agent.send('작업 중지')['needs_clarification']
    execute.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'symbol': 'bad symbol'}, {'symbol': 3}, {'start': '2026-02-30'}, {'end': '2026-08-01'},
    {'mode': 'TIMER'}, {'result_mode': 'ORDER'}, {'spread_points': -1}, {'spread_points': float('inf')},
    {'spread_points': False}, {'build_only': 'true'}, {'rebuild': 1}, {'specials': {}},
    {'specials': ['SPECIAL999']}, {'warehouse': 'elsewhere'}, {'python_executable': 'other'},
    {'recipe': {}}, {'code': 'print(1)'}, {'argv': ['python']},
    {'target_mode': 'GENERATED', 'specials': [], 'filename': '../strategy.py'},
    {'target_mode': 'GENERATED', 'specials': [], 'filename': 'Test_SPECIAL002.py'},
])
def test_untrusted_execution_fields_are_rejected(changes):
    agent, execute = session(value(data=request(**changes)))
    result = agent.send('백테스트')
    assert not result['can_confirm'] and result['action'] is None
    execute.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'action': 'DELETE'}, {'supported': 'true'}, {'message_ko': {}}, {'clarification_question': []},
    {'needs_clarification': 'false'}, {'extra': 'shell'},
])
def test_invalid_model_envelope_never_executes(changes):
    agent, execute = session(value(**changes))
    assert not agent.send('백테스트')['can_confirm']
    execute.assert_not_called()


def test_model_tool_calls_are_not_executed():
    model = provider.ScriptedProvider([{'content': json.dumps(value()), 'tool_calls': [{'name': 'run'}]}])
    execute = Mock()
    agent = commands.Session(model, context_loader=snapshot, executor=execute)
    assert not agent.send('백테스트')['can_confirm']
    execute.assert_not_called()


def test_generated_and_build_only_and_watch_use_existing_selection_contracts():
    # 수정본139: WATCH runs alert-only, so the inherited VIRTUAL_ENTRY is refused until ALERT_ONLY is stated.
    watch = request(target_mode='WATCH', specials=[], watch_text='골드 1분 올존 계속 알려줘')
    agent, execute = session(value(data=watch))
    refused = agent.send('기존 대상 백테스트')
    assert not refused['can_confirm'] and 'WATCH 명령은 얼럿 온리만 됩니다.' in str(refused)
    execute.assert_not_called()
    requests = [request(target_mode='GENERATED', specials=[], filename='Test_SPECIAL001.py'),
                request(target_mode=None, specials=[], build_only=True),
                {**watch, 'result_mode': 'ALERT_ONLY'}]
    for data in requests:
        agent, execute = session(value(data=data))
        response = agent.send('기존 대상 백테스트')
        assert response['can_confirm']
        if data.get('target_mode') == 'GENERATED':
            assert response['command']['request']['mode'] == 'TICK'
            assert 'OZ 계산 범위: 전체' in response['preview']
        agent.confirm(response['revision'])
        execute.assert_called_once()


def test_changed_inherited_settings_require_new_preview_but_new_job_does_not():
    state = snapshot()
    agent, execute = session(value(), state=state)
    response = agent.send('백테스트')
    state['execution_defaults']['cores'] = 4
    with pytest.raises(ValueError, match='실행 설정이 변경'):
        agent.confirm(response['revision'])
    execute.assert_not_called()
    state = snapshot()
    agent, execute = session(value(), state=state)
    response = agent.send('백테스트')
    state['jobs'].insert(0, {'job_id': OTHER_JOB, 'phase': 'complete'})
    agent.confirm(response['revision'])
    execute.assert_called_once()


def test_command_conversation_uses_distinct_output_schema_and_bounded_state():
    agent, _ = session(value(), value(data=request(start='2026-08-01')))
    agent.send('백테스트'); agent.send('시작일만 변경')
    assert all(item == commands.command_schema() for item in agent.provider.response_schemas)
    assert agent.last_command['action'] == 'START'
    assert 'canonical intent' not in agent.provider.seen[-1]['content']
    assert 'special_settings' not in agent.provider.seen[-1]['content']
    assert 'execution_version' not in agent.provider.seen[-1]['content']


def test_strategy_draft_apply_and_command_confirm_cancel_reset_do_not_share_state():
    from lab.ai.agent import Agent
    intent = {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'],
        'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}],
        'order_mode': 'SIMULTANEOUS', 'within_sec': None,
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '전략 해석'}
    strategy = Agent(provider.ScriptedProvider([{'content': json.dumps(intent)}]))
    initial = strategy.send('15분 상승 추세 전략')
    before = copy.deepcopy(strategy.last_intent)
    command, execute = session(value())
    interpreted = command.send('기존 SPECIAL1 백테스트')
    command.confirm(interpreted['revision'])
    command.cancel(); command.reset()
    execute.assert_called_once()
    assert strategy.last_intent == before
    assert strategy.apply(initial['revision'])['strategy_intent'] == before['interpretation']


def test_default_executor_routes_builtin_generated_and_readonly_to_existing_facades(monkeypatch):
    from lab import integration, unified_backtest
    normal = Mock(return_value={'job_id': JOB})
    generated = Mock(return_value={'job_id': OTHER_JOB})
    monkeypatch.setattr(unified_backtest, 'start', normal)
    monkeypatch.setattr(integration, 'backtest', generated)
    clean, question = commands._normalize(value(), snapshot())
    assert question is None
    assert commands.execute(clean)['job_id'] == JOB
    assert normal.call_args.args[0]['target_mode'] == 'SPECIAL'
    clean, question = commands._normalize(value(data=request(target_mode='GENERATED', specials=[],
                                                            filename='Test_SPECIAL001.py')), snapshot())
    assert commands.execute(clean)['job_id'] == OTHER_JOB
    assert generated.call_args.args[0]['action'] == 'run'
    assert 'special_settings' not in generated.call_args.args[0]
    for action in ('STOP', 'STATUS', 'RECENT', 'RECONNECT'):
        method = Mock(return_value={'message': action})
        monkeypatch.setattr(unified_backtest, action.lower(), method, raising=False)
        command = {'action': action, **({'job_id': JOB} if action != 'RECENT' else {})}
        assert commands.execute(command)['message'] == action
        method.assert_called_once_with(*(() if action == 'RECENT' else (JOB,)))


@pytest.mark.parametrize('recent', [{'items': [{'job_id': JOB}]}, {'jobs': [{'job_id': JOB}]}])
def test_context_accepts_current_and_compatible_recent_envelopes_without_real_warehouse(monkeypatch, recent):
    from lab import storage, unified_backtest
    monkeypatch.setattr(unified_backtest, 'options', lambda: snapshot()['options'])
    monkeypatch.setattr(unified_backtest, 'recent', lambda: recent, raising=False)
    current = Mock()
    current.settings.return_value = snapshot()['execution_defaults']
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (current, None, None))
    monkeypatch.setattr(storage, 'recent', lambda: [])
    monkeypatch.setattr(storage, 'connections', lambda: {'warehouse': 'configured_runtime_location'})
    loaded = commands.context()
    assert loaded['jobs'] == [{'job_id': JOB}]
    assert len(loaded['execution_version']) == 64


def test_context_history_failure_does_not_require_an_existing_job_or_create_one(monkeypatch):
    from lab import storage, unified_backtest
    monkeypatch.setattr(unified_backtest, 'options', lambda: snapshot()['options'])
    monkeypatch.setattr(unified_backtest, 'recent', Mock(side_effect=ValueError('창고 미설정')), raising=False)
    current = Mock(); current.settings.return_value = {}
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (current, None, None))
    monkeypatch.setattr(storage, 'recent', lambda: [])
    monkeypatch.setattr(storage, 'connections', lambda: {})
    assert commands.context()['jobs'] == []


def test_optional_provider_schema_preserves_default_strategy_format_and_saved_model(monkeypatch):
    captured = []
    def urlopen(request, timeout):
        is_inventory = request.full_url.endswith('/api/ps')
        is_unload = request.full_url.endswith('/api/generate')
        if is_unload:
            body = json.loads(request.data)
            assert body['keep_alive'] == 0 and body['stream'] is False
        if not is_inventory and not is_unload:
            captured.append(json.loads(request.data))
        response = Mock()
        response.read.return_value = (b'{"models":[]}' if is_inventory else
                                      b'{"done":true}' if is_unload else b'{"message":{"content":"{}"}}')
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response
    monkeypatch.setattr(provider.urllib.request, 'urlopen', urlopen)
    model = provider.from_settings({'model': 'qwen3.5:4b'})
    model.chat([], [])
    model.chat([], [], response_schema=commands.command_schema())
    from lab.ai.schema import output_schema
    assert captured[0]['format'] == output_schema()
    assert captured[1]['format'] == commands.command_schema()
    assert [body['model'] for body in captured] == ['qwen3.5:4b', 'qwen3.5:4b']
    assert all(body['think'] is False and body['stream'] is False for body in captured)


def test_unconfigured_model_has_no_fallback_or_network(monkeypatch):
    send = Mock(side_effect=AssertionError('network forbidden'))
    monkeypatch.setattr(provider.urllib.request, 'urlopen', send)
    with pytest.raises(RuntimeError, match='AI 모델이 설정되지 않았습니다'):
        provider.from_settings({})
    send.assert_not_called()
