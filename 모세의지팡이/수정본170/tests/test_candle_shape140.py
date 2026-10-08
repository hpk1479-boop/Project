"""수정본140: 망치형·역망치형(캔들 모양)과 이평 터치는 레시피 엔진과 가상진입이 같은 판정을 쓴다.

- 망치형: 아래꼬리 ≥ 몸통×2 이고 위꼬리 < 몸통×0.5. 역망치형은 위아래 반대. 몸통이 없으면 둘 다 아니다.
  색(양봉·음봉)은 모양에 들어가지 않는다(양봉·음봉 조건을 같이 넣는다).
- 캔들 모양은 공용 계산(indicator_facts의 candle_shape) 하나를 모든 Fact 경로가 쓴다.
- 이평 터치는 그 봉의 저가 ≤ 이평 ≤ 고가이다. 엔진의 MA_PRICE_TOUCH와 가상진입의 두 터치 조건이 같은 함수를 쓴다.
- 가상진입의 "확인봉 이평 터치"는 확인봉 자체가 터치한 것, "이평 터치 후 확인"은 터치한 봉 다음의 다른 봉이 확인한다.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from indicator_facts import FactStore, candle_shape_values, standalone_frame  # noqa: E402
from indicator_facts_numpy import ArrayFactFrame  # noqa: E402
from event_engine.facts import EventFacts  # noqa: E402
from event_engine.market import COLUMNS, MarketView  # noqa: E402
from event_engine.model import FeedSnapshot  # noqa: E402
from recipe_harness114 import Harness, feed, SYMBOL  # noqa: E402
from strategy_recipe.contract import execution_plan  # noqa: E402
from strategy_recipe.port import IntentPort  # noqa: E402
from strategy_recipe.state_judge import shape_matches, touch_matches  # noqa: E402
from event_backtest.virtual_contract import normalize_virtual_entry  # noqa: E402
from event_backtest.virtual_facts import VirtualFacts  # noqa: E402
from test_virtual_common113 import alert, policy, replay  # noqa: E402


# ----------------------------------------------------------------- the shape rule

@pytest.mark.parametrize('o,h,l,c,expected', [
    (101., 102.49, 99., 102., 1.),        # hammer: lower 2 = body×2, upper 0.49 < body×0.5
    (102., 102.49, 99., 101., 1.),        # a bearish candle can be a hammer: colour is separate
    (101., 102.49, 99.01, 102., 0.),      # lower 1.99 < body×2
    (101., 102.5, 99., 102., 0.),         # upper 0.5 is not shorter than half the body
    (101., 104., 100.51, 102., -1.),      # inverted hammer: upper 2 = body×2, lower 0.49
    (102., 104., 100.51, 101., -1.),      # bearish inverted hammer
    (101., 103.99, 100.51, 102., 0.),     # upper 1.99
    (101., 104., 100.5, 102., 0.),        # lower 0.5
    (100., 100., 98., 100., 0.),          # no body (dragonfly doji): neither
    (100., 102., 100., 100., 0.),         # no body (gravestone doji): neither
    (100., 100., 100., 100., 0.),         # flat
    (100., 101., 99., 100.5, 0.),         # ordinary candle
    (np.nan, 102., 99., 101., np.nan), (101., np.nan, 99., 102., np.nan),
    (101., 102., np.inf, 102., np.nan), (101., 102., 99., -np.inf, np.nan),
])
def test_the_shape_rule_and_its_boundaries(o, h, l, c, expected):
    np.testing.assert_allclose(candle_shape_values(o, h, l, c), expected, equal_nan=True)
    for rows in (view([(o, h, l, c)]),):
        for result in all_paths(rows):
            np.testing.assert_allclose(result, [expected], equal_nan=True)


@pytest.mark.parametrize('shape,value,matched', [
    ('HAMMER', 1, True), ('HAMMER', -1, False), ('HAMMER', 0, False),
    ('INVERTED_HAMMER', -1, True), ('INVERTED_HAMMER', 1, False), ('INVERTED_HAMMER', 0, False)])
def test_a_shape_condition_reads_the_fact(shape, value, matched):
    assert shape_matches(shape, value) is matched


@pytest.mark.parametrize('low,high,average,touched', [
    (99., 101., 100., True), (100., 101., 100., True), (99., 100., 100., True),
    (100.01, 101., 100., False), (98., 99.99, 100., False)])
def test_the_touch_rule_includes_both_ends_of_the_range(low, high, average, touched):
    assert touch_matches(low, high, average) is touched


# ----------------------------------------------------------------- one Fact on every path

def view(candles, *, seq=1):
    """candles: (open, high, low, close)."""
    values = np.full((len(candles), len(COLUMNS)), 100., dtype=float)
    for i, (o, h, l, c) in enumerate(candles):
        for name, value in (('open', o), ('high', h), ('low', l), ('close', c)):
            values[i, COLUMNS[name]] = value
    times = np.arange(len(candles), dtype='int64') * 60 + 1791158400
    return MarketView(FeedSnapshot(times, np.ones(len(candles), dtype='int64'), values, seq, 'shape140'))


def dataframe(market):
    return pd.DataFrame({'time': pd.to_datetime(market.time, unit='s'), 'open': market.column('open'),
                         'high': market.column('high'), 'low': market.column('low'),
                         'close': market.column('close'), 'volume': market.volume})


def all_paths(market, tf='1m'):
    events = EventFacts()
    events.invalidate({('GOLD', tf): market.snapshot})
    return (standalone_frame(dataframe(market), tf)['candle_shape'].to_numpy(),
            ArrayFactFrame(market, tf)['candle_shape'],
            events.get('GOLD', tf, market.snapshot, 'candle_shape', 'test'))


def test_a_forming_candle_changes_shape_while_closed_candles_stay():
    hammer, ordinary, inverted = (101., 102.4, 99., 102.), (100., 101., 99., 100.5), (101., 104., 100.6, 102.)
    incremental = ArrayFactFrame(view([hammer, inverted, ordinary]), '1m')
    store = FactStore()
    for seq, (forming, expected) in enumerate(((ordinary, 0.), (hammer, 1.), (inverted, -1.)), 1):
        current = view([hammer, inverted, forming], seq=seq)
        incremental.update(current)
        events = EventFacts(); events.invalidate({('GOLD', '1m'): current.snapshot})
        for result in (incremental['candle_shape'],
                       store.frame('GOLD', '1m', dataframe(current))['candle_shape'].to_numpy(),
                       events.get('GOLD', '1m', current.snapshot, 'candle_shape', 'test')):
            np.testing.assert_array_equal(result, [1., -1., expected])


# ----------------------------------------------------------------- engine and virtual entry agree

N = 120


def market(seed):
    rng = np.random.default_rng(seed)
    close = 2400. + np.cumsum(rng.normal(0, 1.2, N))
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, .2, N)
    body = np.abs(close - open_)
    # Wicks around the shape boundaries, so hammers, inverted hammers and plain candles all occur.
    upper = body * rng.choice([0., .3, .5, 1., 2., 2.5], N) + rng.choice([0., .01], N)
    lower = body * rng.choice([0., .3, .5, 1., 2., 2.5], N) + rng.choice([0., .01], N)
    high, low = np.maximum(open_, close) + upper, np.minimum(open_, close) - lower
    hma = np.convolve(open_, np.ones(5) / 5, mode='same')
    return {'open': open_, 'close': close, 'high': high, 'low': low, 'hma_50': hma}


@pytest.mark.parametrize('seed', range(5))
def test_engine_steps_and_virtual_conditions_give_the_same_answer(seed):
    columns = market(seed)
    meaning = execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'steps': [{'kind': 'TREND', 'tfs': ['1m']}],
                              'final': {'kind': 'NOTIFY'}})['meaning']
    h = Harness(meaning)
    shapes = [{'kind': 'CANDLE_SHAPE', 'tfs': ['1m'], 'shape': shape, 'bar_state': 'CLOSED'} for shape in ('HAMMER', 'INVERTED_HAMMER')]
    touch = {'kind': 'MA_PRICE_TOUCH', 'tfs': ['1m'], 'ma_family': 'HMA', 'slow_period': 50, 'bar_state': 'CLOSED',
             '_event_mode': True}
    seen = {'HAMMER': 0, 'INVERTED_HAMMER': 0, 'touch': 0}
    for end in range(N - 60, N):                       # the forming bar is bar `end`
        last_open = 1_790_000_000 + end * 60
        snapshot = feed('1m', last_open, n=end + 1, **{k: v[:end + 1] for k, v in columns.items()})
        now = last_open + 30
        h.publish(now, {'1m': snapshot})
        board = h.port._board()[0]
        facts = VirtualFacts(); facts.update({'1m': snapshot}, SYMBOL, now * 1000)
        for step in shapes:
            engine = h.port._observe(board, SYMBOL, step, 'LONG', now)
            entry = shape_matches(step['shape'], facts.shape('1m', now * 1000))
            assert engine.get('ready', True) and engine['matched'] == entry, (seed, end, step['shape'])
            seen[step['shape']] += entry
        engine = h.port._observe(board, SYMBOL, touch, 'LONG', now)
        row = facts.row('1m', now * 1000, closed=True)
        entry = touch_matches(row['low'], row['high'], facts.ma('1m', now * 1000, 'HMA', 50, closed=True))
        if end > N - 60:                               # a closed touch is new only after the first poll
            assert engine['matched'] == entry, (seed, end, 'touch')
            seen['touch'] += entry
    assert all(seen.values()), seen                    # every rule was seen true at least once


def test_the_engine_touch_reads_values_like_the_ma_comparator():
    from watch_ma import MACondition
    rng = np.random.default_rng(140)
    choices = np.array([99., 100., 101., np.nan, np.inf, -np.inf])
    for _ in range(400):
        features = {name: rng.choice(choices, 4) for name in ('LOW', 'HIGH', 'HMA50')}
        if rng.random() < .1: del features['HMA50']
        for current in (-1, -2, 0, 3, 7, -9):
            below = MACondition('COMPARE', left=('MA', 'LOW'), right=('MA', 'HMA50'), operator='<=').evaluate(features, current)
            above = MACondition('COMPARE', left=('MA', 'HIGH'), right=('MA', 'HMA50'), operator='>=').evaluate(features, current)
            before = None if below is None or above is None else below and above
            low, high, average = (IntentPort._value_at(features.get(n), current) for n in ('LOW', 'HIGH', 'HMA50'))
            after = None if None in (low, high, average) else touch_matches(low, high, average)
            assert before == after, (features, current)


# ----------------------------------------------------------------- recipe alerts on a candle shape

def shape_alerts(shape, bar_state, candles):
    """A one-step recipe: notify when the 1m candle has `shape`. Returns the bars (forming at the poll) that alerted."""
    meaning = execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'persistent': True, 'time_filters': [],
                              'final_time_filters': 0,
                              'steps': [{'kind': 'CANDLE_SHAPE', 'tfs': ['1m'], 'shape': shape, 'bar_state': bar_state}],
                              'final': {'kind': 'NOTIFY', 'direction': 'LONG'}})['meaning']
    h = Harness(meaning)
    alerted = []
    for end in range(3, len(candles)):
        last_open = 1_790_000_000 + end * 60
        part = candles[:end + 1]
        h.publish(last_open + 30, {'1m': feed('1m', last_open, n=len(part),
                                               **{name: [c[i] for c in part] for i, name in enumerate(('open', 'high', 'low', 'close'))})})
        alerted += [end] * (len(h.kernel.messages) - len(alerted))
    return alerted


PLAIN, HAMMER, INVERTED = (100., 101., 99., 100.5), (101., 102.4, 99., 102.), (101., 104., 100.6, 102.)


@pytest.mark.parametrize('shape,bar_state,candles,bars', [
    ('HAMMER', 'CLOSED', [PLAIN] * 5 + [HAMMER] + [PLAIN] * 3, [6]),      # alerts when the hammer has closed
    ('HAMMER', 'CLOSED', [PLAIN] * 5 + [INVERTED] + [PLAIN] * 3, []),
    ('INVERTED_HAMMER', 'CLOSED', [PLAIN] * 5 + [INVERTED] + [PLAIN] * 3, [6]),
    ('HAMMER', 'FORMING', [PLAIN] * 5 + [HAMMER] + [PLAIN] * 3, [5]),     # while the hammer is forming
])
def test_a_recipe_alerts_on_a_candle_shape(shape, bar_state, candles, bars):
    assert shape_alerts(shape, bar_state, candles) == bars


@pytest.mark.parametrize('step,error', [
    ({'shape': 'PIN'}, '망치형'), ({'shape': 'HAMMER', 'bar_state': 'UNSPECIFIED'}, '진행봉'),
    ({'bar_state': 'CLOSED'}, '망치형'), ({'shape': 'HAMMER', 'bar_state': 'CLOSED', 'side': 'BULL'}, '허용되지 않거나')])
def test_a_shape_step_needs_its_shape_and_bar(step, error):
    with pytest.raises(ValueError, match=error):
        execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'final': {'kind': 'NOTIFY'},
                        'steps': [{'kind': 'CANDLE_SHAPE', 'tfs': ['1m'], **step}]})


# ----------------------------------------------------------------- virtual entry conditions

def shape_policy(shape, *, candle=True):
    return policy(conditions=[{'kind': 'CANDLE_SHAPE', 'shape': shape}], candle=candle)


@pytest.mark.parametrize('direction,candle,p,status', [
    ('LONG', (0, 101, 102.4, 99, 102), shape_policy('HAMMER'), 'ENTERED'),                    # bullish hammer
    ('LONG', (0, 102, 102.4, 99, 101), shape_policy('HAMMER'), 'WAITING'),                    # bearish: candle close fails
    ('LONG', (0, 102, 102.4, 99, 101), shape_policy('HAMMER', candle=False), 'ENTERED'),      # shape alone
    ('LONG', (0, 101, 102.6, 99, 102), shape_policy('HAMMER'), 'WAITING'),                    # upper wick 0.6
    ('SHORT', (0, 102, 104, 100.6, 101), shape_policy('INVERTED_HAMMER'), 'ENTERED'),         # bearish inverted
    ('SHORT', (0, 102, 104, 100.6, 101), shape_policy('HAMMER'), 'WAITING'),
])
def test_a_confirmation_candle_shape_condition(direction, candle, p, status):
    rows = [candle, (60, 101, 101.5, 100.5, 101)]
    c = replay(rows, alert(direction=direction, stop=90 if direction == 'LONG' else 110), p)
    assert c.trades[0]['status'] == status
    assert (c.trades[0]['entry_time'] == 60000) == (status == 'ENTERED')


def touch_policy(kind):
    return policy(conditions=[{'kind': kind, 'family': 'HMA', 'period': 6}])


@pytest.mark.parametrize('kind,entry', [('MA_TOUCH_CANDLE', 60000), ('MA_TOUCH', 120000)])
def test_a_touch_on_the_confirmation_candle_itself_or_on_an_earlier_candle(kind, entry):
    # HMA6 = 9 (test_virtual_common113.view). Bar 0 touches it and closes bullish; bar 60 is bullish without a touch.
    rows = [(0, 9.5, 11, 8.8, 10.5), (60, 10.5, 11.5, 10.2, 11), (120, 11, 11.5, 10.8, 11.2)]
    c = replay(rows, alert(stop=8), touch_policy(kind))
    assert c.trades[0]['status'] == 'ENTERED' and c.trades[0]['entry_time'] == entry


@pytest.mark.parametrize('low,high,entered', [(8.99, 11, True), (9, 11, True), (9.01, 11, False),
                                               (7, 9, True), (7, 8.99, False)])
def test_a_confirmation_candle_touch_at_the_edges(low, high, entered):
    candle = (0, max(low, 8.995) if high >= 9.5 else 8.5, high, low, high)   # bullish, closes at its high
    rows = [candle, (60, 10, 10.5, 9.5, 10)]
    c = replay(rows, alert(stop=low - 1), policy(conditions=[{'kind': 'MA_TOUCH_CANDLE', 'family': 'HMA', 'period': 6}]))
    assert (c.trades[0]['entry_time'] == 60000) == entered


@pytest.mark.parametrize('bad,error', [
    ([{'kind': 'CANDLE_SHAPE'}], '캔들 모양'),
    ([{'kind': 'CANDLE_SHAPE', 'shape': 'PIN'}], '캔들 모양'),
    ([{'kind': 'CANDLE_SHAPE', 'shape': 'HAMMER', 'period': 6}], '이평 설정'),
    ([{'kind': 'MA_TOUCH_CANDLE', 'shape': 'HAMMER'}], '캔들 모양을 넣을 수 없습니다'),
    ([{'kind': 'CANDLE_CLOSE', 'shape': 'HAMMER'}], '캔들 모양을 넣을 수 없습니다'),
])
def test_invalid_candle_conditions_are_rejected(bad, error):
    with pytest.raises(ValueError, match=error):
        normalize_virtual_entry({'mode': 'CONFIRM', 'conditions': bad})


def test_new_conditions_keep_their_values():
    value = normalize_virtual_entry({'mode': 'CONFIRM', 'conditions': [
        {'kind': 'CANDLE_SHAPE', 'shape': 'INVERTED_HAMMER'}, {'kind': 'MA_TOUCH_CANDLE', 'family': 'EMA', 'period': 20, 'tf': '5m'}]})
    assert value['conditions'] == [{'kind': 'CANDLE_SHAPE', 'shape': 'INVERTED_HAMMER'},
                                   {'kind': 'MA_TOUCH_CANDLE', 'family': 'EMA', 'period': 20, 'tf': '5m'}]


# ----------------------------------------------------------------- one rule, named on every screen

def test_one_rule_and_every_screen_names_the_new_conditions():
    port = (ROOT / 'Part1/program/strategy_recipe/port.py').read_text(encoding='utf-8')
    rules = (ROOT / 'Part2/event_backtest/virtual_rules.py').read_text(encoding='utf-8')
    assert 'touch_matches(' in port and rules.count('touch_matches(') == 2
    assert "row['low'] <= average <= row['high']" not in rules
    assert 'shape_matches(' in port and 'shape_matches(' in rules
    unified = (ROOT / 'Part3/web/unified.js').read_text(encoding='utf-8')
    assert "CANDLE_SHAPE: '망치형·역망치형'" in unified and "MA_TOUCH_CANDLE: '확인봉 이평 터치'" in unified
    display = (ROOT / 'Part3/web/ai_display.js').read_text(encoding='utf-8')
    commands = (ROOT / 'Part3/lab/ai/backtest_commands.py').read_text(encoding='utf-8')
    for source in (display, commands):
        assert 'CANDLE_SHAPE' in source and 'MA_TOUCH_CANDLE' in source and '역망치형' in source
    from event_backtest.virtual_contract import schema
    assert schema()['properties']['conditions']['items']['properties']['shape']['enum'] == ['HAMMER', 'INVERTED_HAMMER']
