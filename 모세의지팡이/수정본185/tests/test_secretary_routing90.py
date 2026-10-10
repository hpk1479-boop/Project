"""Kim Secretary interprets commands without registering or running engines."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from command_interpreter import CommandInterpreter
from command_models import SecretaryCommand
from domain_clock import event_scope
from kim_secretary import KimSecretary


@pytest.fixture
def secretary():
    interpreter = CommandInterpreter({'SYMBOLS': 'XAUUSD+,NAS100'})
    return KimSecretary(interpreter, {'LONDON': '08:00-16:00', 'NEWYORK': '14:00-21:00',
        'TELEGRAM_TOKEN': 'must-not-enter-secretary', 'ACCOUNT_PASSWORD': 'must-not-enter-secretary'})


def parse(secretary, text, *, retry=False, owner='user'):
    with event_scope(1790380680000, 'routing90', {}):
        return secretary.parse(text, owner, external_retry=retry)


def test_watch_interpretation_has_no_execution_effect(secretary, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Language parsing attempted execution')
    for name in ('_push', 'send_telegram', '_add_private_spec', '_add_timed_chain',
                 '_add_fvg_created_watch', '_reset_owner', '_list_owner', '_cancel_watch_reply',
                 '_gemini_canonicalize_command'):
        monkeypatch.setattr(secretary, name, forbidden, raising=False)
    before = deepcopy(secretary.command_interpreter.language())
    value = parse(secretary, '골드 1분 상단 원비 터치 알려줘')
    assert value.kind == 'WATCH'
    assert value.value['symbol'] == 'XAUUSD+'
    assert value.value['timeframes'] == ['1m']
    assert value.value['watch_type'] == 'WONBI_TOUCH'
    assert value.value['evaluation_mode'] == 'LIVE'
    assert value.value['direction'] == 'SHORT'
    assert value.value['level_side'] == 'HIGH'
    assert secretary.command_interpreter.language() == before
    assert secretary.config == {'LONDON': '08:00-16:00', 'NEWYORK': '14:00-21:00'}


def test_trend_gate_keeps_setup_and_final_timeframes(secretary):
    value = parse(secretary, '골드 15분 상승추세일 때 1분 올존 알려줘')
    assert value.kind == 'REGISTER_STRATEGY'
    strategy = value.value
    assert strategy.symbol == 'XAUUSD+'
    assert [(c.kind, c.tf, c.direction) for c in strategy.conditions] == [('TREND', '15m', 'LONG')]
    assert strategy.final_action == 'OZ'
    assert strategy.oz_tfs == ('1m',)
    assert strategy.final_direction == 'LONG'
    assert (strategy.validation_mode, strategy.trigger_mode) == ('NORMAL', 'OZ')


def test_sequence_keeps_event_order_and_window(secretary):
    value = parse(secretary, '골드 1분 EMA50 200 골크나면 30분 동안 5분 하단 원비 알려줘')
    assert value.kind == 'REGISTER_CHAIN'
    chain = value.value
    assert chain.symbol == 'XAUUSD+'
    assert [(step.watch_type, step.tf) for step in chain.triggers] == [('EMA_CROSS', '1m'), ('WONBI_TOUCH', '5m')]
    assert chain.triggers[0].direction == 'LONG'
    assert chain.triggers[0].next_window_sec == 1800
    assert chain.triggers[1].level_side == 'LOW'
    assert chain.final_action == 'NOTIFY'
    assert chain.order_mode == 'SEQUENTIAL'


def test_new_fvg_is_distinct_from_a_status_query_and_keeps_final_oz(secretary):
    notify = parse(secretary, '골드 5분 FVG 생성 알려줘')
    assert notify.kind == 'FVG_WATCH'
    assert notify.value['watch_type'] == 'FVG_NEW'
    assert notify.value['timeframes'] == ['5m']
    final = parse(secretary, '골드 5분 상승 FVG 생성되면 1분 올존 알려줘')
    assert final.kind == 'FVG_WATCH'
    assert final.value['timeframes'] == ['5m']
    assert final.value['direction'] == 'LONG'
    assert final.value['final_action'] == 'OZ'
    assert final.value['oz_tfs'] == ['1m']
    assert final.value['oz_direction'] == 'LONG'


@pytest.mark.parametrize('text,action,tf', [
    ('골드 15분 추세 조회', 'TREND_QUERY', '15m'),
    ('골드 15분 추세점수 몇 점?', 'TREND_QUERY', '15m'),
    ('골드 5분 FVG 조회', 'FVG_QUERY', '5m'),
    ('골드 1시간 외부유동성 조회', 'SWEEP_QUERY', '1h'),
])
def test_queries_return_requests_without_calculation(secretary, text, action, tf):
    value = parse(secretary, text)
    assert value.kind == 'QUERY'
    assert value.value['action'] == action
    assert value.value['symbol'] == 'XAUUSD+'
    assert value.value['source_tf'] == tf
    assert value.value['request_chat_id'] == 'user'
    if '점수' in text:
        assert value.value['purpose'] == 'TREND_SCORE_QUERY'
        assert value.value['requested_fields'] == ['trend_score', 'long_score', 'short_score']
    if action == 'SWEEP_QUERY':
        assert value.value['session_london'] == '08:00-16:00'
        assert value.value['session_newyork'] == '14:00-21:00'
    assert 'facts' not in value.value and 'result' not in value.value


@pytest.mark.parametrize('text,kind,owner', [
    ('취소', 'CANCEL_REPLY', 'user'), ('리셋', 'RESET', 'user'), ('내전략', 'LIST', 'user'),
    ('취소', 'IGNORE', ''), ('', 'IGNORE', 'user'), ('골드 알려줘', 'IGNORE', ''),
])
def test_owner_controls_are_interpretations_only(secretary, text, kind, owner):
    value = parse(secretary, text, owner=owner)
    assert value == SecretaryCommand(kind)


@pytest.mark.parametrize('text,fragment', [
    ('골드 1분 골든크로스(SMA0,EMA5) 알려줘', 'WATCH MA 조건 오류'),
    ('골드 7분 추세 조회', '지원하지 않는 시간봉'),
    ('골드 1분 VAH 알려줘', '지원하지 않는 SWEEP selector'),
])
def test_invalid_commands_return_errors_without_registration(secretary, text, fragment):
    value = parse(secretary, text)
    assert value.kind == 'ERROR' and value.value is None
    assert fragment in value.message


def test_external_normalization_is_a_request_with_one_retry(secretary):
    text = '골드 모르겠는 말 알려줘'
    first = parse(secretary, text)
    assert first == SecretaryCommand('EXTERNAL_NORMALIZE', text)
    retry = parse(secretary, text, retry=True)
    assert retry.kind == 'ERROR'
    assert '전략을 이해하지 못했습니다' in retry.message
    assert retry.value is None


def test_results_are_explicit_frozen_commands(secretary):
    value = parse(secretary, '골드 1분 올존 알려줘')
    assert value.kind == 'WATCH'
    assert value.value['action'] == 'MANUAL_WATCH'
    assert value.value['timeframes'] == ['1m']
    with pytest.raises(FrozenInstanceError):
        value.kind = 'RESET'
