"""수정본135: 기억을 바꾸지 않고 사실값도 읽지 않는 조건은 같은 묶음 안에서 판단값을 다시 쓴다.

사실값(FVG·SWEEP)이 올 때마다 전략이 다시 판단하는 횟수와 순서는 그대로다. 다시 쓰는 값은 새로
계산한 값과 같아야 하고, 기억을 쓰는 조건(교차 등)은 지금처럼 매번 새로 판단한다. 새 묶음(시세판
게시)이나 시각이 바뀌면 새로 계산한다.
"""
from pathlib import Path
import collections
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, SYMBOL   # noqa: E402


def counting(port):
    calls = collections.Counter(); original = port._observe
    def observe(board, symbol, step, direction, now, machine=None):
        calls[step['kind']] += 1
        return original(board, symbol, step, direction, now, machine)
    port._observe = observe
    return calls


def fact_event():
    return {'kind': 'FACT_SNAPSHOT', 'strategy': 'FVG', 'symbol': SYMBOL, 'source_tf': '5m', 'facts': [],
            'complete': True, 'source_health': {'sources': {}}}


def feeds(now):
    rows = np.linspace(2390., 2410., 80)
    return {tf: feed(tf, now - now % seconds, n=80, open=rows, close=rows + .5, high=rows + 1, low=rows - 1)
            for tf, seconds in (('1m', 60), ('3m', 180), ('6m', 360), ('15m', 900))}


def test_pure_condition_is_reused_inside_a_bundle_and_recomputed_on_the_next():
    h = Harness.special('SPECIAL7')
    calls = counting(h.port)
    now = 1_790_000_000 + 1800
    h.publish(now, feeds(now))
    first = calls['TREND']
    assert first >= 1
    for _ in range(3):                                   # FVG/SWEEP Facts inside the same bundle
        h.port.handle_fact(fact_event())
    assert calls['TREND'] == first                       # same data, same time: reused
    h.publish(now + 1, feeds(now + 1))                   # next bundle
    assert calls['TREND'] > first


def test_a_new_publication_at_the_same_time_is_recomputed():
    # Valid inside one publication only: the same timestamp alone never reuses an answer.
    h = Harness.special('SPECIAL7')
    calls = counting(h.port)
    now = 1_790_000_000 + 1800
    h.publish(now, feeds(now))
    first = calls['TREND']
    h.publish(now, feeds(now))                           # another publication, same time
    assert calls['TREND'] > first


def test_percentile_is_not_in_the_whitelist():
    from strategy_recipe.port import IntentPort
    assert not {'PERCENTILE_OUT', 'PERCENTILE_OUT_IN'} & IntentPort.PURE_KINDS


def test_reused_answer_equals_a_fresh_observation():
    h = Harness.special('SPECIAL7')
    now = 1_790_000_000 + 1800
    h.publish(now, feeds(now))
    h.port.handle_fact(fact_event())
    board, symbol, at = h.port._board()
    for machine in h.machines:
        for step in machine.meaning['steps']:
            if step['kind'] in h.port.PURE_KINDS:
                reused = h.port.observe(board, symbol, step, machine.direction, at, machine)
                assert reused == h.port._observe(board, symbol, step, machine.direction, at, machine)


def test_memory_conditions_are_still_judged_on_every_poll():
    h = Harness.special('SPECIAL8')
    calls = counting(h.port)
    now = 1_790_000_000 + 1800
    h.publish(now, feeds(now))
    kinds = [k for k in calls if k not in h.port.PURE_KINDS]
    assert kinds                                         # SPECIAL8 has memory (event) conditions
    before = {k: calls[k] for k in kinds}
    h.port.handle_fact(fact_event())
    assert all(calls[k] > before[k] for k in kinds)      # not reused


def test_bound_timeframe_and_scope_decide_reuse():
    h = Harness.special('SPECIAL7')
    port = h.port
    step = {'kind': 'MA_STATE', 'tfs': ['SOURCE'], 'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200}
    machine = SimpleNamespace(source_tf='1m', final_tf='1m', captures={}, meaning={'steps': []})
    one = port._pure_key(SYMBOL, step, 'LONG', machine)
    machine.source_tf = '5m'
    assert port._pure_key(SYMBOL, step, 'LONG', machine) != one          # a re-bound step is recomputed
    assert port._pure_key(SYMBOL, dict(step, scope_ref='parent'), 'LONG', machine) is None
    assert port._pure_key(SYMBOL, dict(step, _event_mode=True), 'LONG', machine) is None
    assert port._pure_key(SYMBOL, dict(step, kind='MA_CROSS'), 'LONG', machine) is None
