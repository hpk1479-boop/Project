"""수정본141: 레시피 엔진의 양봉·음봉, 망치형·역망치형 판정은 판정하는 봉 하나만 계산한다.

예전에는 새 봉이 확정될 때마다 1분봉 이력 전체의 캔들 값을 처음부터 다시 계산했다
(SPECIAL9 백테스트 CPU의 약 64%). 두 값 모두 봉 하나의 시가·고가·저가·종가만 쓰는 공식이라,
판정하는 봉의 값은 전체 계산과 같아야 한다(검증 원칙 4). 확정봉·진행봉, 진행봉 가격 변화,
지난 봉 정정에서 전체 계산과 같은지 본다. 값이 없는 가격이 있는 화면은 예전처럼 엔진이 쓰지
않으므로(event_engine.market.select) 여기서는 정상 가격만 쓴다.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, SYMBOL  # noqa: E402
from indicator_facts_numpy import ArrayFactFrame  # noqa: E402
from event_engine.market import MarketView  # noqa: E402
from strategy_recipe.contract import execution_plan  # noqa: E402
from strategy_recipe.state_judge import candle_matches, shape_matches  # noqa: E402

STEPS = [{'kind': 'CANDLE_STATE', 'side': side, 'bar_state': state} for side in ('BULL', 'BEAR') for state in ('CLOSED', 'FORMING')] + \
        [{'kind': 'CANDLE_SHAPE', 'shape': shape, 'bar_state': state}
         for shape in ('HAMMER', 'INVERTED_HAMMER') for state in ('CLOSED', 'FORMING')]


def harness():
    meaning = execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'steps': [{'kind': 'TREND', 'tfs': ['1m']}],
                              'final': {'kind': 'NOTIFY'}})['meaning']
    return Harness(meaning)


def market(seed, n=90):
    rng = np.random.default_rng(seed)
    close = 2400. + np.cumsum(rng.normal(0, 1., n))
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.choice([0., .3, -.3], n)
    open_[::17] = close[::17]                                   # flat candles
    body = np.abs(close - open_)
    upper = body * rng.choice([0., .3, .5, 2., 2.5], n)
    lower = body * rng.choice([0., .3, .5, 2., 2.5], n)
    high, low = np.maximum(open_, close) + upper, np.minimum(open_, close) - lower
    return {'open': open_, 'high': high, 'low': low, 'close': close}


def judge(h, columns, end, now):
    """The engine's answers and the full-series answers for every candle step on bar `end` (forming)."""
    snapshot = feed('1m', 1_790_000_000 + end * 60, n=end + 1, **{k: v[:end + 1] for k, v in columns.items()})
    h.publish(now, {'1m': snapshot})
    board = h.port._board()[0]
    full = ArrayFactFrame(MarketView(snapshot), '1m')            # the old whole-history calculation
    for step in STEPS:
        current = -2 if step['bar_state'] == 'CLOSED' else -1
        name = 'candle_direction' if step['kind'] == 'CANDLE_STATE' else 'candle_shape'
        value = float(full[name][current])
        for direction in ('LONG', 'SHORT'):
            engine = h.port._observe(board, SYMBOL, dict(step, tfs=['1m']), direction, now)
            yield step, direction, engine, value


@pytest.mark.parametrize('seed', range(6))
def test_each_judged_bar_equals_the_whole_series_calculation(seed):
    columns = market(seed)
    h = harness()
    compared = 0
    seen = set()
    for end in range(3, len(columns['open'])):
        for step, direction, engine, value in judge(h, columns, end, 1_790_000_000 + end * 60 + 30):
            expected = candle_matches(step['side'], value) if step['kind'] == 'CANDLE_STATE' else shape_matches(step['shape'], value)
            assert engine.get('ready', True) and engine['matched'] == expected, (seed, end, step, direction)
            seen.add((step['kind'], step.get('side') or step.get('shape'), expected))
            compared += 1
    assert compared == (len(columns['open']) - 3) * len(STEPS) * 2
    assert len(seen) == 8                                       # every condition was seen both true and false


def test_a_forming_price_change_and_a_corrected_history_follow_the_judged_bar():
    columns = market(7, 30)
    h = harness()
    end = 25
    for change, (row, column, price) in enumerate([(25, 'close', 2500.), (25, 'close', 2300.),   # forming bar moves
                                                   (24, 'open', 2600.), (24, 'close', 2200.)], 1):  # last closed bar corrected
        columns[column][row] = price
        for step, direction, engine, value in judge(h, columns, end, 1_790_000_000 + end * 60 + 30 + change):
            expected = candle_matches(step['side'], value) if step['kind'] == 'CANDLE_STATE' else shape_matches(step['shape'], value)
            assert engine.get('ready', True) and engine['matched'] == expected, (change, step, direction)


def test_older_bars_do_not_change_the_answer():
    columns = market(11, 40)
    h = harness()
    before = [(s['kind'], s['bar_state'], d, e['matched']) for s, d, e, _ in judge(h, columns, 39, 1_790_000_000 + 39 * 60 + 30)]
    other = market(12, 40)
    for name, column in columns.items():
        column[:38] = other[name][:38]                           # every bar before the judged ones replaced
    after = [(s['kind'], s['bar_state'], d, e['matched']) for s, d, e, _ in judge(h, columns, 39, 1_790_000_000 + 39 * 60 + 31)]
    assert after == before and any(row[-1] for row in before)


def test_a_candle_judgement_does_not_rebuild_a_history_frame(monkeypatch):
    # Diagnostic: the history frame was the cost; the judgement must not need one.
    columns = market(3, 20)
    h = harness()
    snapshot = feed('1m', 1_790_000_000 + 19 * 60, n=20, **columns)
    h.publish(1_790_000_000 + 19 * 60 + 30, {'1m': snapshot})
    board = h.port._board()[0]
    import indicator_facts, indicator_facts_numpy
    def refuse(*args, **kwargs):
        raise AssertionError('a candle judgement rebuilt a whole-history frame')
    monkeypatch.setattr(indicator_facts_numpy, 'ArrayFactFrame', refuse)
    if hasattr(indicator_facts, 'ArrayFactFrame'):
        monkeypatch.setattr(indicator_facts, 'ArrayFactFrame', refuse)
    for step in STEPS:
        h.port._observe(board, SYMBOL, dict(step, tfs=['1m']), 'LONG', 1_790_000_000 + 19 * 60 + 30)
