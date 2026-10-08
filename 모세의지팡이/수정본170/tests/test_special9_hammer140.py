"""수정본140 SPECIAL9: 15분 상승추세 · 1분 HMA17·50 골크 뒤 음봉 터치 알림, 다음 봉 터치 양봉 진입.

알림(얼럿 온리): 골든크로스 뒤, HMA50을 터치한 완성봉이 음봉이고 HMA50 위에서 마감하는 순간 15분이 상승추세.
  - 음봉·50헐 위·15분 상승은 터치 순간의 조건(터치보다 앞 단계)이라, 안 맞는 터치는 넘기고 같은 골크 안의
    다음 터치를 본다. 알림 뒤에는 새 골든크로스부터 다시 본다.
가상진입: 알림 다음 봉 하나만 본다(알림 후 1봉). 그 봉 자체가 HMA50을 터치하고 HMA50 위에서 양봉으로
  마감하면 다음 봉 시가에 진입, 아니면 패스(EXPIRED). HMA17·50 정배열이나 15분 상승추세가 깨지면 패스(PASS_ENV).
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed  # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS as C  # noqa: E402
from strategy_recipe.registry import builtin_entries  # noqa: E402
from event_backtest.virtual_contract import required_timeframes  # noqa: E402
from event_backtest.virtual_defaults import strategy_profile  # noqa: E402
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402

T0 = 1_790_000_000 - 1_790_000_000 % 900     # a 15-minute boundary
CROSS = 30
# open, high, low, close with HMA50 = 100
BARS = {'plain': (101.8, 102.5, 101.5, 102.0),        # bullish above HMA50, no touch
        'touch_bull': (101.0, 101.8, 99.5, 101.5),    # touch, bullish
        'touch_bear': (101.5, 101.8, 99.5, 100.5),    # touch, bearish, closes above HMA50
        'touch_low': (101.0, 101.2, 99.0, 99.5),      # touch, bearish, closes below HMA50
        'bear_only': (101.8, 102.0, 100.5, 101.0)}    # bearish above HMA50, no touch


# ----------------------------------------------------------------- the recipe

def test_the_recipe_alert_and_entry_cells():
    intent = builtin_entries()['SPECIAL9']['recipe']['strategy_intent']
    assert [(s['kind'], s['tfs']) for s in intent['steps']] == [
        ('MA_CROSS', ['1m']), ('TREND', ['15m']), ('CANDLE_STATE', ['1m']), ('MA_PRICE_STATE', ['1m']),
        ('MA_PRICE_TOUCH', ['1m'])]
    assert intent['steps'][2]['side'] == 'BEAR' and intent['steps'][3]['side'] == 'ABOVE'
    assert all(s['bar_state'] == 'CLOSED' for s in intent['steps'] if s['tfs'] == ['1m'])
    assert 'final_conditions' not in intent and intent['final']['kind'] == 'NOTIFY'
    policy = strategy_profile('SPECIAL9')['default']
    # Revision148's editing origin identifies a recipe slot; these assertions check its execution values.
    policy = {**policy, **{group: [{key: value for key, value in row.items() if key != 'recipe_index'}
                                 for row in policy[group]] for group in ('conditions', 'filters')}}
    assert policy['mode'] == 'CONFIRM' and policy['stop']['kind'] == 'ATR'
    assert policy['conditions'] == [{'kind': 'CANDLE_CLOSE'},
                                    {'kind': 'MA_TOUCH_CANDLE', 'family': 'HMA', 'period': 50, 'tf': 'SIGNAL'},
                                    {'kind': 'MA_POSITION', 'family': 'HMA', 'period': 50, 'tf': 'SIGNAL'}]
    assert policy['filters'] == [
        {'kind': 'ENVIRONMENT', 'condition': 'MA_STATE', 'tf': 'SIGNAL', 'bar_state': 'CLOSED', 'family': 'HMA', 'fast': 17, 'slow': 50},
        {'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': '15m', 'bar_state': 'FORMING'},
        {'kind': 'MAX_BARS', 'bars': 1}]
    assert strategy_profile('SPECIAL9')['timeframes'] == ['1m']


# ----------------------------------------------------------------- alerts

def minute_bars(count, events):
    rows, hma17 = [], 99.
    for i in range(count):
        kind = events.get(i, 'plain')
        if kind == 'golden': hma17, kind = 101., 'plain'
        if kind == 'dead': hma17, kind = 99., 'plain'
        rows.append((*BARS[kind], hma17, 100.))
    return rows


def minute_feed(rows, upto):
    part = rows[:upto + 1]
    values = {n: np.array([r[i] for r in part]) for i, n in enumerate(('open', 'high', 'low', 'close', 'hma_17', 'hma_50'))}
    return feed('1m', T0 + upto * 60, n=len(part), **values)


def quarter_feed(now, rising):
    step = 1. if rising else -1.
    opens = 2400. + step * np.arange(40)
    return feed('15m', now - now % 900, n=40, open=opens, close=opens, high=opens + 1, low=opens - 1, hma_50=opens - 5 * step)


def alerts(events, upto, rising=lambda bar: True):
    """The bars at whose opening poll SPECIAL9 alerted (the touch bar is the one before)."""
    h = Harness.special('SPECIAL9')
    rows = minute_bars(upto + 1, {CROSS: 'golden', **events})
    for bar in range(CROSS - 1, upto + 1):
        now = T0 + bar * 60 + 1
        h.publish(now, {'1m': minute_feed(rows, bar), '15m': quarter_feed(now, rising(bar))})
    return [int((m['event_time'] - T0) // 60) for m in h.kernel.messages], h


@pytest.mark.parametrize('events,expected', [
    ({31: 'touch_bear'}, [32]),                                                    # the first touch qualifies
    ({31: 'touch_bull', 33: 'touch_bear'}, [34]),                                 # a bullish touch is skipped
    ({31: 'bear_only', 32: 'touch_bull', 33: 'touch_low', 35: 'touch_bear'}, [36]),
    ({31: 'touch_bull', 33: 'touch_low'}, []),                                    # no bearish touch above HMA50
    ({31: 'touch_bear', 33: 'touch_bear'}, [32]),                                 # one alert per golden cross
    ({31: 'touch_bear', 33: 'dead', 35: 'golden', 36: 'touch_bear'}, [32, 37]),   # a new cross alerts again
])
def test_the_alert_is_a_bearish_touch_closing_above_hma50(events, expected):
    assert alerts(events, 38)[0] == expected


def test_the_touch_bar_alerts_only_once_it_has_closed():
    assert alerts({31: 'touch_bear'}, 31)[0] == []          # the touch bar is still forming
    found, h = alerts({31: 'touch_bear'}, 32)
    assert found == [32]
    message = h.kernel.messages[0]
    assert message['signal_source'] == 'SIGNAL' and message['direction'] == 'LONG'
    assert message['signal_tf'] == '1m' and message['env_tf'] == '1m'


def test_without_a_golden_cross_there_is_no_alert():
    h = Harness.special('SPECIAL9')
    rows = [(*BARS['touch_bear' if i == 31 else 'plain'], 99., 100.) for i in range(36)]
    for bar in range(CROSS - 1, 36):
        now = T0 + bar * 60 + 1
        h.publish(now, {'1m': minute_feed(rows, bar), '15m': quarter_feed(now, True)})
    assert h.kernel.messages == []


@pytest.mark.parametrize('rising,expected', [
    (lambda bar: bar > 32, [34]),       # down at the first touch: skipped; the next touch in the same cross alerts
    (lambda bar: False, []),
    (lambda bar: bar <= 32, [32]),      # the trend turning down later does not undo the alert
])
def test_the_15m_uptrend_is_read_at_the_touch(rising, expected):
    assert alerts({31: 'touch_bear', 33: 'touch_bear'}, 38, rising)[0] == expected


# ----------------------------------------------------------------- virtual entry

def view(times, rows, names):
    data = np.ones((len(rows), len(C)))
    for i, row in enumerate(rows):
        for name, value in zip(names, row):
            data[i, C.index(name)] = value
    return SimpleNamespace(time=np.asarray(times, dtype=np.int64), values=data, columns=C)


MINUTE = ('open', 'high', 'low', 'close', 'hma_17', 'hma_50')
HISTORY = [(-60 * k, 101.8, 102.5, 101.5, 102.0, 101., 100.) for k in range(30, 0, -1)]


def quarter(rising):
    step = 1. if rising else -1.
    opens = 2400. + step * np.arange(40)
    rows = [(o, o + 1, o - 1, o, o - 5 * step) for o in opens]
    return view(900 * (np.arange(40) - 39), rows, ('open', 'high', 'low', 'close', 'hma_50'))


def enter(candles, *, rising=True, hma17=101.):
    """SPECIAL9's alert at 0; `candles` are the 1m candles from 0 on (the first is the confirmation candle)."""
    p = strategy_profile('SPECIAL9')['default']
    a = dict(signal_id='s', strategy='SPECIAL9', symbol='TEST', tf='1m', direction='LONG', time_ms=0,
             signal_source='SIGNAL', signal_tf='1m', env_tf='1m', signal_price=100.5)
    assert required_timeframes(p, a) == {'1m', '15m'}
    rows = HISTORY + [(60 * i, *candle, hma17 if i == 0 else 101., 100.) for i, candle in enumerate(candles)]
    calc = VirtualEntry([a], 0, p)
    for i, row in enumerate(rows):
        if row[0] >= 0:
            calc.observe(row[0] * 1000, {'1m': view([r[0] for r in rows[:i + 1]], [r[1:] for r in rows[:i + 1]], MINUTE),
                                         '15m': quarter(rising)}, end_ms=10 ** 9)
    return calc.trades[0]


HAMMER_TOUCH = (101.5, 102.0, 99.8, 101.9)            # touches HMA50, bullish, closes above
NEXT = (101.9, 102.4, 101.6, 102.2)


@pytest.mark.parametrize('candle,status', [
    (HAMMER_TOUCH, 'ENTERED'),
    ((101.5, 102.0, 100.5, 101.9), 'EXPIRED'),        # bullish above, no touch
    ((99.0, 100.2, 98.8, 99.8), 'EXPIRED'),           # touch and bullish, closes below HMA50
    ((101.9, 102.0, 99.8, 101.5), 'EXPIRED'),         # touch above HMA50, bearish
    ((101.5, 102.0, 100.0, 101.9), 'ENTERED'),        # its low exactly at HMA50 is a touch
])
def test_the_entry_is_the_next_candle_touching_hma50_and_closing_bullish_above_it(candle, status):
    trade = enter([candle, NEXT, NEXT])
    assert trade['status'] == status
    if status == 'ENTERED':
        assert trade['entry_time'] == 60000 and trade['entry_price'] == NEXT[0]


def test_only_the_next_candle_is_watched():
    # The next candle fails; the one after it would qualify, but the entry window was one candle.
    assert enter([(101.5, 102.0, 100.5, 101.9), HAMMER_TOUCH, NEXT, NEXT])['status'] == 'EXPIRED'


@pytest.mark.parametrize('rising,hma17', [(False, 101.), (True, 99.)])
def test_a_broken_15m_trend_or_hma17_50_order_passes_the_alert(rising, hma17):
    trade = enter([HAMMER_TOUCH, NEXT, NEXT], rising=rising, hma17=hma17)
    assert trade['status'] == 'PASS_ENV' and trade['entry_time'] is None
