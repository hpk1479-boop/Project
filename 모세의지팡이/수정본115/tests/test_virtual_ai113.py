"""The AI contract transports post-alert choices without language reparsing."""
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
from event_backtest.virtual_contract import default_virtual_entry, normalize_virtual_entry, schema
from lab.ai import backtest_commands as commands
from lab.ai import research_backtest, research_editor, research_interpreter
from common_ai.security import external_payload
from common_ai.external_prompt import prepare


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No live transport is allowed in AI contract tests')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for key in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, key, deny)


def policy():
    return normalize_virtual_entry({'mode': 'CONFIRM', 'tf': '5m',
        'conditions': [{'kind': 'MA_POSITION', 'family': 'EMA', 'period': 50, 'tf': '15m'},
            {'kind': 'MA_CROSS', 'family': 'SMA', 'period': 20, 'tf': '5m'},
            {'kind': 'MA_TOUCH', 'family': 'WMA', 'period': 30, 'tf': '1m'},
            {'kind': 'ENGULFING'}, {'kind': 'NECKLINE_BREAK'}],
        'atr': {'tf': '15m', 'period': 21},
        'filters': [{'kind': 'CANDLE_ATR', 'measure': 'BODY', 'min': .5, 'max': 2},
            {'kind': 'CANDLE_ATR', 'measure': 'RANGE', 'min': None, 'max': 3},
            {'kind': 'MA_DISTANCE_ATR', 'family': 'HMA', 'period': 17, 'tf': '5m', 'min': .1, 'max': 1.5}],
        'stop': {'kind': 'RECENT_EXTREME', 'tf': '15m', 'bars': 7, 'multiplier': 1}})


def snapshot():
    return {'today': '2026-10-05', 'generated': [{'filename': 'Test_SPECIAL001.py'}], 'jobs': [],
        'options': {'symbols': ['XAUUSD+'], 'specials': ['SPECIAL1'], 'mode': 'BAR',
            'result_mode': 'VIRTUAL_ENTRY', 'virtual_entry': default_virtual_entry()},
        'execution_defaults': {}, 'execution_version': 'stable'}


def value(entry=None, target='SPECIAL'):
    request = {'target_mode': target, 'specials': ['SPECIAL1'] if target == 'SPECIAL' else None,
        'filename': 'Test_SPECIAL001.py' if target == 'GENERATED' else None,
        'watch_text': '5분 매수 올존' if target == 'WATCH' else None,
        'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01',
        'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0,
        'build_only': False, 'rebuild': False, 'available_only': True, 'virtual_entry': entry}
    return {'supported': True, 'action': 'START', 'request': request, 'job_id': None,
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '확인'}


def plan(entry):
    return {'strategy_text': None, 'steps': [{'draft': False, 'command': value(entry)}],
        'needs_clarification': False, 'clarification_question': None}


def test_shared_schema_is_embedded_intact_for_command_and_research():
    shared = schema()
    embedded = commands.command_schema()['properties']['request']['properties']['virtual_entry']['anyOf'][0]
    assert embedded == shared
    response = {'kind': 'BACKTEST', 'strategy': None, 'plan': plan(policy()), 'message_ko': '확인'}
    for contract in (commands.command_schema(), research_interpreter.response_schema('strategy')):
        Draft202012Validator.check_schema(contract)
        Draft202012Validator(contract).validate(value(policy()) if 'request' in contract.get('properties', {}) else response)
    bad = copy.deepcopy(response)
    bad['plan']['steps'][0]['command']['request']['virtual_entry']['atr']['period'] = 0
    assert not Draft202012Validator(research_interpreter.response_schema('strategy')).is_valid(bad)


@pytest.mark.parametrize('target', ['SPECIAL', 'GENERATED', 'WATCH'])
def test_normalization_preserves_policy_and_does_not_mutate_strategy_selection(target):
    original = value(policy(), target)
    saved = copy.deepcopy(original)
    command, question = commands._normalize(original, snapshot())
    assert question is None
    assert command['request']['virtual_entry'] == policy()
    assert command['request']['target_mode'] == target
    assert original == saved


def test_omitted_policy_uses_saved_policy_and_change_invalidates_confirmation():
    current = snapshot()
    current['options']['virtual_entry'] = policy()
    command, question = commands._normalize(value(), current)
    assert not question and command['request']['virtual_entry'] == policy()
    before = commands._settings_version(current)
    current['options']['virtual_entry']['stop']['bars'] = 8
    assert commands._settings_version(current) != before


@pytest.mark.parametrize('patch', [
    {'conditions': [{'kind': 'ENGULFING', 'family': 'HMA'}]},
    {'filters': [{'kind': 'CANDLE_ATR', 'measure': 'BODY', 'min': 2, 'max': 1}]},
    {'filters': [{'kind': 'CANDLE_ATR', 'min': None, 'max': None}]},
    {'stop': {'kind': 'ATR', 'multiplier': 0}},
    {'legacy_hma6_hma17': True},
])
def test_invalid_policy_is_rejected_without_local_repair(patch):
    request = value({**default_virtual_entry(), **patch})
    original = copy.deepcopy(request)
    with pytest.raises(ValueError):
        commands._normalize(request, snapshot())
    assert request == original


def test_preview_covers_every_condition_filter_and_stop_choice():
    command, _ = commands._normalize(value(policy()), snapshot())
    text = commands._preview(command, snapshot())
    for expected in ('직전 봉 매수 양봉/매도 음봉 마감 후 현재봉 시가', 'EMA50', 'SMA20', 'WMA30',
            '인걸핑', '해당 올존의 넥라인', 'ATR21', '몸통', '전체 길이', 'HMA17', '직전 7개 확정봉', '1:1.5'):
        assert expected in text
    for stop_kind in ('AUTO', 'OZ_B0', 'ATR'):
        entry = policy()
        entry['stop']['kind'] = stop_kind
        assert commands._virtual_preview(entry)


def test_policy_survives_both_external_allowlist_passes_and_original_plan():
    chosen = research_interpreter.selection(snapshot())
    messages = [{'role': 'user', 'content': json.dumps({'message': '기간만 바꿔 주세요',
        'context': {'selection': chosen, 'previous_plan': plan(policy())}}, ensure_ascii=False)}]
    contract = research_interpreter.response_schema('strategy')
    safe, _, _ = external_payload(messages, [], contract)
    data = json.loads(safe[1]['content'])
    assert data['context']['previous_plan']['steps'][0]['command']['request']['virtual_entry'] == policy()
    assert data['context']['selection']['options']['virtual_entry'] == default_virtual_entry()
    compact, _, _ = prepare(safe, [], contract, {})
    assert any('CONFIRM' in row['content'] and 'EMA' in row['content'] for row in compact)
    assert 'MA_POSITION' in compact[0]['content']
    assert '가상진입 정책은 command.request.virtual_entry' in compact[0]['content']


def test_scripted_ai_response_is_validated_as_policy_without_reparsing_user_text(monkeypatch):
    expected = {'kind': 'BACKTEST', 'strategy': None, 'plan': plan(policy()), 'message_ko': '확인'}
    received = []
    class Provider:
        def chat(self, messages, tools, response_schema):
            received.append(copy.deepcopy(messages))
            assert Draft202012Validator(response_schema).is_valid(expected)
            return {'content': json.dumps(expected, ensure_ascii=False)}
    monkeypatch.setattr(research_interpreter, 'external_system_prompt', lambda workspace: '{"moses_contract":{}}')
    original = '제가 지정한 진입 계획으로 실행해 주세요'
    result, actions = research_interpreter.interpret(Provider(), NS(workspace=None), 'strategy', [],
        {'selection': research_interpreter.selection(snapshot())}, original)
    assert result == expected and actions == []
    assert json.loads(received[0][1]['content'])['message'] == original


def test_confirmed_research_plan_keeps_checked_policy(monkeypatch):
    current = snapshot()
    session = research_backtest.Session(None, lambda: NS(last_intent=None), context_loader=lambda: copy.deepcopy(current))
    ready = session.accept_plan(plan(policy()), snapshot=current)
    assert ready['can_confirm'] and 'EMA50' in ready['preview']
    from lab import backtest_sequences
    received = []
    monkeypatch.setattr(backtest_sequences, 'start', lambda rows, **kwargs:
        (received.extend(copy.deepcopy(rows)) or {'sequence_id': 'offline', 'phase': 'plan'}))
    session.confirm(ready['revision'])
    assert received[0]['request']['virtual_entry'] == policy()


def test_default_research_result_and_policy_are_visible_in_editor_plan():
    current = snapshot()
    current['options']['result_mode'] = 'ALERT_ONLY'
    current['options']['virtual_entry'] = policy()
    draft = plan(None)
    draft['steps'][0]['command']['request']['result_mode'] = None
    session = research_backtest.Session(None, lambda: NS(last_intent=None), context_loader=lambda: current)
    ready = session.accept_plan(draft, snapshot=current)
    assert ready['can_confirm']
    effective = session.last_plan['steps'][0]['command']['request']
    assert effective['result_mode'] == 'VIRTUAL_ENTRY'
    assert effective['virtual_entry'] == policy()
    assert draft['steps'][0]['command']['request']['virtual_entry'] is None


def test_generated_adapter_passes_policy_into_same_part2_scenario(tmp_path, monkeypatch):
    from lab import backtest_adapters, storage
    target = tmp_path / 'Part3/TEST_SPECIAL/Test_SPECIAL001.py'
    target.parent.mkdir(parents=True)
    target.write_text('PART3_RECIPE = {}\n', encoding='utf-8')
    monkeypatch.setattr(storage, 'generated_path', lambda name: target)
    monkeypatch.setattr(storage, 'reopen', lambda name: {})
    request = value(policy(), 'GENERATED')['request']
    scenario, _ = backtest_adapters.prepare_generated(request, tmp_path)
    assert scenario['virtual_entry'] == policy()
    assert scenario['strategies'] == ['Test_SPECIAL001']


def test_editor_preserves_policy_and_uses_same_plan_validator():
    edited = plan(policy())
    assert research_editor.check_plan(edited, snapshot(), None) == []
    assert edited['steps'][0]['command']['request']['virtual_entry'] == policy()
    broken = copy.deepcopy(edited)
    broken['steps'][0]['command']['request']['virtual_entry']['mode'] = 'LEGACY'
    assert research_editor.check_plan(broken, snapshot(), None)
    broken = copy.deepcopy(edited)
    broken['steps'][0]['command']['request']['virtual_entry']['filters'][0]['min'] = 3
    errors = research_editor.check_plan(broken, snapshot(), None)
    assert errors and errors[0]['path'][-1] == 'virtual_entry'
