"""수정본183: 가상진입 조건 진입의 "이평 시가 위·아래"(MA_OPEN_POSITION). 매수 기준, 매도는 전부 반대.

수정본182의 진입 방식 "시가 이평 돌파"(MA_OPEN)를 조건 진입의 진입 조건 하나로 옮겼다. 판정은 그대로다.
- 알림 뒤 새 봉이 열릴 때마다 그 봉 시가와 그 봉 이평(기본 HMA6, 종류·기간은 입력값)을 비교한다.
  시가가 이평 이상(같아도)이 된 첫 봉에서만 한 번 판정하고, 그 전에는 패스 없이 기다린다.
  알림 뒤 첫 봉이 이미 위여도 그 봉이다.
- 그 봉에서 다른 진입 조건(예: 전봉 양봉)이 안 맞으면 패스(PASS_CONDITION), 시가와 이평의 거리가 ATR14의
  입력 배수(기본 0.3)를 넘으면 패스(PASS_DISTANCE)한다. 정확히 그 배수면 진입한다. 그 뒤에 거리가
  줄어도, 다음 봉에 조건이 맞아도 진입하지 않는다. ATR 제한 칸도 그 봉에서 한 번만 본다(BLOCKED).
  이 조건이 없는 조건 진입은 지금처럼 맞을 때까지 다음 봉을 다시 본다.
- 기다리는 동안 환경조건이 깨지면 패스(PASS_ENV). 시가 기준 역크로스는 진행봉(시가) 기준 이평 정배열 칸이다.
- 손절·익절은 그대로. 시간봉은 기준 프레임(알림 시간봉)으로만 정한다. 이 조건은 하나만 넣는다.

Synthetic candles: 31 closed history candles (open 10, high 11, low 9, close 10.5) end at the alert (0),
so ATR14 is 2 and the 0.3 limit is 0.6; the recorded HMA6 is 9 and HMA17 8 unless a row says otherwise.
"""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.virtual_contract import normalize_virtual_entry, required_timeframes, schema  # noqa: E402
from event_backtest.virtual_defaults import target_policy  # noqa: E402
from event_backtest.virtual_facts import VirtualFacts  # noqa: E402
from test_virtual_common113 import alert, history, replay, view  # noqa: E402

DEFAULT = {'kind': 'MA_OPEN_POSITION', 'family': 'HMA', 'period': 6, 'tf': 'SIGNAL', 'max_atr': 0.3}
CANDLE = {'kind': 'CANDLE_CLOSE'}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def opening(*others, filters=(), stop=None, **values):
    """A conditional entry with 이평 시가 위·아래 (values change its average or distance) and other conditions."""
    return normalize_virtual_entry({'schema': 2, 'mode': 'CONFIRM', 'tf': 'SIGNAL',
                                    'conditions': [{'kind': 'MA_OPEN_POSITION', **values}, *others],
                                    'filters': list(filters), 'stop': stop or {'kind': 'OZ_B0'}})


def plain(*conditions, filters=()):
    """A conditional entry without it."""
    return normalize_virtual_entry({'schema': 2, 'mode': 'CONFIRM', 'conditions': list(conditions),
                                    'filters': list(filters), 'stop': {'kind': 'OZ_B0'}})


def after(*rows):
    """The history (ATR14 2 at the first candle after the alert) and then the candles after the alert."""
    return [*history(), *rows]


def trade(rows, p=None, a=None):
    return replay(rows, a or alert(oz=True), p or opening()).trades[0]


def env(bar_state):
    return {'kind': 'ENVIRONMENT', 'condition': 'MA_STATE', 'family': 'HMA', 'fast': 6, 'slow': 17,
            'tf': 'SIGNAL', 'bar_state': bar_state}


BELOW_60 = (60, 8.5, 10.5, 8.5, 9.0)       # opens under HMA6 9
BELOW_120 = (120, 8.8, 10.0, 8.0, 9.2)
ABOVE_180 = (180, 9.2, 10.2, 8.2, 9.5)     # opens 0.2 over it
ABOVE_60 = (60, 9.4, 10.4, 8.4, 9.8)       # the first candle after the alert already opens over it


def test_the_history_gives_an_atr_of_two():
    facts = VirtualFacts()
    facts.update({'1m': view(after(ABOVE_60))}, 'TEST', 60000)
    assert facts.atr('1m', 60000, 14) == 2.0


# ----------------------------------------------------------------- the wait and the one judged candle

def test_the_alert_waits_without_passing_until_an_open_reaches_the_average():
    waiting = trade(after(BELOW_60, BELOW_120))
    assert waiting['status'] == 'WAITING' and waiting['entry_time'] is None
    entered = trade(after(BELOW_60, BELOW_120, ABOVE_180))
    assert entered['status'] == 'ENTERED'
    assert (entered['entry_time'], entered['entry_price'], entered['stop_price']) == (180000, 9.2, 7)


def test_the_first_candle_after_the_alert_is_judged_even_when_it_already_opens_above():
    entered = trade(after(ABOVE_60))
    assert (entered['status'], entered['entry_time'], entered['entry_price']) == ('ENTERED', 60000, 9.4)


def test_an_open_equal_to_the_average_enters():
    entered = trade(after((60, 9.0, 10.5, 8.5, 9.5)))
    assert (entered['status'], entered['entry_price']) == ('ENTERED', 9.0)


@pytest.mark.parametrize('opened,status', [(9.6, 'ENTERED'), (9.61, 'PASS_DISTANCE')])
def test_the_distance_limit_includes_its_exact_atr_multiple(opened, status):
    # The open is 0.6 (= 0.3 x ATR 2) or 0.61 over HMA6 9.
    assert trade(after((60, opened, opened + 1, opened - 1, 10.0)))['status'] == status


def test_a_too_far_open_passes_and_no_later_candle_enters():
    passed = trade(after((60, 9.61, 10.61, 8.61, 10.0), (120, 9.1, 10.1, 8.1, 9.5), (180, 9.0, 10.0, 8.0, 9.4)))
    assert passed['status'] == 'PASS_DISTANCE' and passed['entry_time'] is None


@pytest.mark.parametrize('max_atr,status', [(0.1, 'PASS_DISTANCE'), (0.2, 'ENTERED'), (0.5, 'ENTERED')])
def test_the_distance_multiple_is_an_input(max_atr, status):
    # The open is 0.4 over the average: within 0.2 x 2 and 0.5 x 2, beyond 0.1 x 2.
    assert trade(after(ABOVE_60), opening(max_atr=max_atr))['status'] == status


@pytest.mark.parametrize('family', ['SMA', 'WMA'])
def test_any_average_and_period_can_be_the_one_to_reach(family):
    # SMA2 and WMA2 of the opens: 9.5 and 9.33 for the open 9 after the opens of 10, 9 for two opens of 9.
    rows = after((60, 9.0, 10.5, 8.5, 9.6), (120, 9.0, 10.6, 8.6, 9.8))
    p = opening(family=family, period=2)
    assert trade(rows[:-1], p)['status'] == 'WAITING'
    entered = trade(rows, p)
    assert (entered['status'], entered['entry_time'], entered['entry_price']) == ('ENTERED', 120000, 9.0)


def test_a_sell_waits_for_an_open_on_or_below_the_average():
    sell = alert(oz=True, direction='SHORT', stop=13)
    rows = after((60, 11.5, 12.5, 10.5, 11.0, 11, 12), (120, 10.8, 11.8, 9.8, 10.5, 11, 12))
    assert trade(rows[:-1], a=sell)['status'] == 'WAITING'
    entered = trade(rows, opening(CANDLE), sell)
    assert (entered['status'], entered['entry_time'], entered['entry_price']) == ('ENTERED', 120000, 10.8)


# ----------------------------------------------------------------- the other conditions, judged once

BEARISH_60 = (60, 8.9, 10.5, 8.5, 8.6)     # under HMA6 and bearish
BULLISH_60 = (60, 8.6, 10.5, 8.5, 8.9)     # under HMA6 and bullish
ABOVE_120 = (120, 9.1, 10.1, 8.1, 9.5)


def test_a_bearish_previous_candle_passes_on_the_judged_candle_and_never_waits_again():
    rows = after(BEARISH_60, ABOVE_120, ABOVE_180)
    passed = trade(rows, opening(CANDLE))
    assert passed['status'] == 'PASS_CONDITION' and passed['entry_time'] is None
    # Alone, the same candle enters: the previous candle is another condition.
    assert trade(rows)['entry_time'] == 120000
    # Without 이평 시가 위·아래 a conditional entry judges every candle from the first one after the alert,
    # whose previous candle (the alert's) closed bullish.
    assert trade(rows, plain(CANDLE))['entry_time'] == 60000


def test_its_place_in_the_list_does_not_matter():
    rows = after(BEARISH_60, ABOVE_120, ABOVE_180)
    later = plain(CANDLE, {'kind': 'MA_OPEN_POSITION'})
    assert later['conditions'] == [CANDLE, DEFAULT]
    assert trade(rows, later)['status'] == 'PASS_CONDITION'


def test_a_bullish_previous_candle_enters_at_the_judged_open():
    entered = trade(after(BULLISH_60, ABOVE_120), opening(CANDLE))
    assert (entered['status'], entered['entry_time'], entered['entry_price']) == ('ENTERED', 120000, 9.1)


def test_an_atr_limit_is_judged_once_on_that_candle():
    # The previous candle's body is 0.5 = 0.25 ATR, then 0.4 = 0.2 ATR: never under 0.1 ATR.
    limit = [{'kind': 'CANDLE_ATR', 'measure': 'BODY', 'max': 0.1}]
    rows = after(ABOVE_60, (120, 9.3, 10.3, 8.3, 9.4))
    blocked = trade(rows, opening(filters=limit))
    assert (blocked['status'], blocked['blocked_checks'], blocked['entry_time']) == ('BLOCKED', 1, None)
    # Without it the conditional entry skips each blocked candle and keeps waiting.
    waiting = trade(rows, plain({'kind': 'MA_POSITION', 'family': 'HMA', 'period': 6}, filters=limit))
    assert (waiting['status'], waiting['blocked_checks']) == ('WAITING', 2)


# ----------------------------------------------------------------- limits while it waits

def test_an_inverse_cross_at_the_open_passes_while_waiting():
    passed = trade(after((60, 8.5, 10.5, 8.5, 9.0, 9, 9.5)), opening(filters=[env('FORMING')]))
    assert passed['status'] == 'PASS_ENV'


@pytest.mark.parametrize('bar_state,status', [('FORMING', 'PASS_ENV'), ('CLOSED', 'ENTERED')])
def test_the_open_based_limit_reads_this_candle_and_the_closed_one_the_previous(bar_state, status):
    # This candle opens over HMA6 9 while its HMA17 is 9.5; the alert candle's HMA6 9 was over its HMA17 8.
    rows = after((60, 9.2, 10.2, 8.2, 9.5, 9, 9.5))
    assert trade(rows, opening(filters=[env(bar_state)]))['status'] == status


@pytest.mark.parametrize('bars,status', [(2, 'EXPIRED'), (3, 'ENTERED')])
def test_the_n_bar_limit_works_while_waiting(bars, status):
    p = opening(filters=[{'kind': 'MAX_BARS', 'bars': bars}])
    assert trade(after(BELOW_60, BELOW_120, ABOVE_180), p)['status'] == status


def test_results_count_each_pass_reason():
    for rows, p, key in ((after((60, 9.61, 10.61, 8.61, 10.0)), opening(), 'pass_distance'),
                         (after(BEARISH_60, ABOVE_120), opening(CANDLE), 'pass_condition')):
        calc = replay(rows, alert(oz=True), p)
        summary, details = calc.results()
        assert summary[0][key] == 1 and summary[0]['passes'] == 1 and summary[0]['entries'] == 0
        assert {row['result'] for row in details} == {key.replace('pass_', 'PASS_').upper()}
        assert {row['entry_mode'] for row in details} == {'CONFIRM'}


# ----------------------------------------------------------------- the contract

def test_defaults_fill_hma6_and_point_three_and_a_normalized_policy_is_stable():
    p = opening()
    assert p['mode'] == 'CONFIRM' and p['conditions'] == [DEFAULT] and p['atr'] == {'tf': 'SIGNAL', 'period': 14}
    assert normalize_virtual_entry(copy.deepcopy(p)) == p
    changed = opening(family='WMA', period=17, max_atr=0.5)
    assert changed['conditions'] == [{**DEFAULT, 'family': 'WMA', 'period': 17, 'max_atr': 0.5}]


def test_the_entry_modes_are_immediate_and_conditional_only():
    with pytest.raises(ValueError, match='진입 방식: 즉시 진입 / 조건 진입'):
        normalize_virtual_entry({'schema': 2, 'mode': 'MA_OPEN', 'conditions': []})
    with pytest.raises(ValueError, match='지원하지 않는 필드 trigger'):
        normalize_virtual_entry({'schema': 2, 'mode': 'CONFIRM', 'conditions': [CANDLE],
                                 'trigger': {'family': 'HMA', 'period': 6, 'tf': 'SIGNAL', 'max_atr': 0.3}})


@pytest.mark.parametrize('data,error', [
    ({'mode': 'CONFIRM', 'conditions': [DEFAULT, DEFAULT]}, '하나만'),
    ({'mode': 'CONFIRM', 'conditions': [{'kind': 'MA_POSITION', 'max_atr': 0.3}]}, '이평 시가 위·아래에만'),
    ({'mode': 'IMMEDIATE', 'conditions': [DEFAULT]}, '조건 진입'),
])
def test_one_at_most_its_distance_only_on_it_and_only_in_a_conditional_entry(data, error):
    with pytest.raises(ValueError, match=error):
        normalize_virtual_entry({'schema': 2, **data})


@pytest.mark.parametrize('bad', [{'family': 'XMA'}, {'max_atr': -0.1}, {'tf': 'ENV'}, {'period': 0},
                                 {'periods': 6}, {'max_atr': True}])
def test_bad_values_are_refused(bad):
    with pytest.raises(ValueError):
        opening(**bad)


def test_the_n_bar_limit_is_allowed_only_while_an_entry_waits():
    assert opening(filters=[{'kind': 'MAX_BARS', 'bars': 2}])['filters'] == [{'kind': 'MAX_BARS', 'bars': 2}]
    with pytest.raises(ValueError, match='N봉'):
        normalize_virtual_entry({'schema': 2, 'mode': 'IMMEDIATE', 'filters': [{'kind': 'MAX_BARS', 'bars': 2}]})


def test_the_shared_schema_and_timeframes_include_the_condition():
    shared = schema()
    assert shared['properties']['mode']['enum'] == ['IMMEDIATE', 'CONFIRM']
    assert 'trigger' not in shared['properties']
    item = shared['properties']['conditions']['items']['properties']
    assert 'MA_OPEN_POSITION' in item['kind']['enum'] and item['max_atr'] == {'type': 'number', 'minimum': 0}
    assert required_timeframes(opening(), alert(tf='5m')) == {'5m', '1m'}
    assert '15m' in required_timeframes(opening(tf='15m'), alert(tf='5m'))


def test_the_average_frame_comes_only_from_the_base_frame():
    assert target_policy(['SPECIAL1'], policy=opening())['conditions'] == [DEFAULT]
    with pytest.raises(ValueError, match='기준 프레임으로만'):
        target_policy(['SPECIAL1'], policy=opening(tf='15m'))


# ----------------------------------------------------------------- AI

def ai_command(entry):
    request = {'target_mode': 'SPECIAL', 'specials': ['SPECIAL1'], 'filename': None, 'watch_text': None,
               'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR',
               'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0, 'build_only': False, 'rebuild': False,
               'available_only': True, 'virtual_entry': entry}
    return {'supported': True, 'action': 'START', 'request': request, 'job_id': None,
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '확인'}


def ai_snapshot():
    return {'today': '2026-10-05', 'generated': [], 'jobs': [],
            'options': {'symbols': ['XAUUSD+'], 'specials': ['SPECIAL1'], 'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY'},
            'execution_defaults': {}, 'execution_version': 'stable'}


def test_the_ai_command_keeps_validates_and_shows_the_condition():
    from jsonschema import Draft202012Validator
    from lab.ai import backtest_commands as commands
    entry = opening(CANDLE, filters=[env('FORMING')], family='WMA', period=17, max_atr=0.5)
    Draft202012Validator(commands.command_schema()).validate(ai_command(entry))
    command, question = commands._normalize(ai_command(copy.deepcopy(entry)), ai_snapshot())
    assert question is None and command['request']['virtual_entry'] == entry
    text = commands._preview(command, ai_snapshot())
    assert '진입: 조건 진입' in text and 'WMA17 시가 위·아래 (거리 ' in text and 'ATR14 0.5배 이하)' in text
    assert ' + 양봉·음봉' in text and 'HMA6·17 정배열 유지 (진행봉)' in text


def test_the_ai_instructions_describe_the_condition_and_the_policy_survives_the_external_allowlist():
    from common_ai.security import external_payload
    from common_ai.external_prompt import prepare
    from lab.ai import research_interpreter
    entry = opening(CANDLE, filters=[env('FORMING')])
    plan = {'strategy_text': None, 'steps': [{'draft': False, 'command': ai_command(entry)}],
            'needs_clarification': False, 'clarification_question': None}
    messages = [{'role': 'user', 'content': json.dumps({'message': '기간만 바꿔 주세요',
        'context': {'selection': research_interpreter.selection(ai_snapshot()), 'previous_plan': plan}}, ensure_ascii=False)}]
    contract = research_interpreter.response_schema('strategy')
    safe, _, _ = external_payload(messages, [], contract)
    kept = json.loads(safe[1]['content'])['context']['previous_plan']['steps'][0]['command']['request']['virtual_entry']
    assert kept == entry
    compact, _, _ = prepare(safe, [], contract, {})
    content = compact[0]['content']
    assert 'MA_OPEN_POSITION' in content and 'max_atr' in content and 'MA_OPEN(' not in content
