"""수정본139: 추세·이평 정배열·이평 기울기·가격과 이평은 레시피 엔진과 가상진입이 같은 판정을 쓴다.

같은 녹화 값이면 엔진(라이브·재생 알림)과 Part2 가상진입(진입 제한·진입 조건)이 같은 답을 낸다.
확정봉(CLOSED)은 마지막 확정봉, 진행봉(FORMING)은 그 순간 가격을 넣은 진행봉으로 본다.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, SYMBOL   # noqa: E402
from strategy_recipe.contract import execution_plan   # noqa: E402
from event_backtest.virtual_facts import VirtualFacts  # noqa: E402
from event_backtest.virtual_rules import _state, environment_step  # noqa: E402

N = 260
STEPS = [
    {'kind': 'TREND'},
    {'kind': 'MA_STATE', 'ma_family': 'HMA', 'fast_period': 6, 'slow_period': 17},      # native columns
    {'kind': 'MA_STATE', 'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200},    # native EMAs
    {'kind': 'MA_STATE', 'ma_family': 'SMA', 'fast_period': 7, 'slow_period': 20},      # computed
    {'kind': 'MA_SLOPE_STATE', 'ma_family': 'HMA', 'slow_period': 168, 'lookback': 2},
    {'kind': 'MA_SLOPE_STATE', 'ma_family': 'WMA', 'slow_period': 9},
    {'kind': 'MA_PRICE_STATE', 'ma_family': 'HMA', 'slow_period': 50},
    {'kind': 'MA_PRICE_STATE', 'ma_family': 'SMA', 'slow_period': 12},
]


def market(seed):
    """A random walk whose native averages follow it, with native EMAs built by the EMA rule."""
    rng = np.random.default_rng(seed)
    close = 2400. + np.cumsum(rng.normal(0, 1.5, N))
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, .3, N)
    high = np.maximum(open_, close) + rng.uniform(0, 1, N)
    low = np.minimum(open_, close) - rng.uniform(0, 1, N)
    columns = {'open': open_, 'close': close, 'high': high, 'low': low}
    for name, window in (('hma_6', 3), ('hma_17', 8), ('hma_50', 20), ('hma_168', 60)):
        columns[name] = np.convolve(open_, np.ones(window) / window, mode='same') + rng.normal(0, .2, N)
    for name, period in (('ema_20', 20), ('ema_50', 50), ('ema_200', 200)):
        alpha, value, out = 2 / (period + 1), close[0], np.empty(N)
        for i, price in enumerate(close):
            value = value + alpha * (price - value); out[i] = value
        columns[name] = out
    return columns


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('bar_state', ['CLOSED', 'FORMING'])
def test_engine_and_virtual_entry_give_the_same_answer(seed, bar_state):
    columns = market(seed)
    meaning = execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'steps': [{'kind': 'TREND', 'tfs': ['1m']}],
                              'final': {'kind': 'NOTIFY'}})['meaning']
    h = Harness(meaning)
    compared = 0
    for end in range(N - 12, N):                       # the forming bar is bar `end`
        last_open = 1_790_000_000 + end * 60
        snapshot = feed('1m', last_open, n=end + 1, **{k: v[:end + 1] for k, v in columns.items()})
        now = last_open + 30
        h.publish(now, {'1m': snapshot})
        board = h.port._board()[0]
        facts = VirtualFacts(); facts.update({'1m': snapshot}, SYMBOL, now * 1000)
        quote = float(columns['close'][end])          # the forming bar's latest price
        for step in STEPS:
            step = dict(step, tfs=['1m'], bar_state=bar_state)
            for direction in ('LONG', 'SHORT'):
                engine = h.port._observe(board, SYMBOL, step, direction, now)
                closed = bar_state == 'CLOSED'
                entry = _state(step, direction, '1m', now * 1000, facts, closed=closed, price=None if closed else quote)
                assert engine.get('ready', True), step
                assert engine['matched'] == entry, (seed, end, bar_state, step, direction)
                compared += 1
    assert compared == 12 * len(STEPS) * 2


def test_an_environment_limit_is_the_recipe_step_it_repeats():
    assert environment_step({'condition': 'MA_STATE', 'family': 'EMA', 'fast': 50, 'slow': 200}) == \
        {'kind': 'MA_STATE', 'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200}
    assert environment_step({'condition': 'MA_SLOPE_STATE', 'family': 'HMA', 'period': 168, 'lookback': 2}) == \
        {'kind': 'MA_SLOPE_STATE', 'ma_family': 'HMA', 'slow_period': 168, 'lookback': 2}
    assert environment_step({'condition': 'TREND'}) == {'kind': 'TREND'}


def test_the_rules_live_in_one_place():
    port = (ROOT / 'Part1/program/strategy_recipe/port.py').read_text(encoding='utf-8')
    rules = (ROOT / 'Part2/event_backtest/virtual_rules.py').read_text(encoding='utf-8')
    for source in (port, rules):
        assert 'state_matches(' in source
    # No second copy of a state rule outside the shared module.
    assert "('SMA20', 'HMA50')" not in port and "('SMA20', 'HMA50')" not in rules
    assert "'UP' if chosen" not in port
