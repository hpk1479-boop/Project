"""수정본156: 자기 시간봉의 사실값만 읽는 조건(외부유동성 터치)은 다른 시간봉의 사실이 와도 다시 계산하지 않는다.

사실값이 올 때마다 전략이 다시 판단하는 횟수와 순서는 그대로다(135와 같다). 다시 쓰는 값은 새로 계산한
값과 같아야 한다. 그 시간봉의 사실이 새로 오거나, 새 묶음이 게시되거나, 시각이 바뀌면 새로 계산한다.
기준은 재사용을 끈 같은 전략이다: 같은 입력이면 기계 상태·관측·감시 명령·알림이 모두 같아야 한다.
"""
from pathlib import Path
import collections
import random
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, SYMBOL   # noqa: E402
from event_engine.model import FeedSnapshot      # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS      # noqa: E402

SECONDS = {'1m': 60, '2m': 120, '3m': 180, '4m': 240, '5m': 300, '6m': 360, '10m': 600, '12m': 720,
           '15m': 900, '20m': 1200, '30m': 1800, '1h': 3600}
START = 1_790_000_000 + 7200


def feeds(now, seed):
    """Every SPECIAL2 timeframe at source time `now`: 30 bars ending with the forming one."""
    rng = np.random.default_rng(seed)
    result = {}
    for tf, seconds in SECONDS.items():
        n = 30
        values = np.full((n, len(PIPE_VALUE_COLUMNS)), 2400., dtype=float) + rng.normal(0., 1., (n, 1))
        times = (now - now % seconds) + (np.arange(n, dtype=np.int64) - (n - 1)) * seconds
        result[tf] = FeedSnapshot(times, np.ones(n, dtype=np.int64), values, int(now), 'epoch', {})
    return result


def touch_steps(port):
    return [(m, s) for m in port.machines for s in m.meaning['steps'] if s['kind'] == 'EXTERNAL_LIQUIDITY_TOUCH']


def fact(port, step, tf, facts):
    return {'kind': 'FACT_SNAPSHOT', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf,
            'watch_id': port._dependency_id(SYMBOL, tf, 'SWEEP', step), 'facts': facts, 'complete': True,
            'source_health': {'sources': {tf: 'epoch'}}}


def counting(port):
    calls = collections.Counter(); original = port._observe
    def observe(board, symbol, step, direction, now, machine=None):
        calls[step['kind'], tuple(step['tfs'])] += 1
        return original(board, symbol, step, direction, now, machine)
    port._observe = observe
    return calls


def state(h):
    return ([(m.branch, m.checkpoint()) for m in h.machines], dict(h.port.observations),
            list(h.manager.commands), list(h.messages))


def run_both(bundles=10, seed=156):
    """The same bundles and Facts, in the same order, into SPECIAL2 with and without the reuse."""
    reused, fresh = Harness.special('SPECIAL2'), Harness.special('SPECIAL2')
    fresh.port.FACT_KINDS = {}                      # the judgement before 156: every Fact recomputes
    calls = {'reused': counting(reused.port), 'fresh': counting(fresh.port)}
    rng = random.Random(seed)
    steps = [(m.branch, m.direction, s['tfs'][0], s) for m, s in touch_steps(reused.port)]
    assert len(steps) == 24
    compared = 0
    for b in range(bundles):
        now = START + 60 * b
        data = feeds(now, seed + b)
        for h in (reused, fresh):
            h.publish(now, data)
        assert state(reused) == state(fresh)
        order = list(steps); rng.shuffle(order)
        for branch, direction, tf, step in order:
            touches = []
            if rng.random() < .3:                       # a touch of one of the step's own levels
                code = rng.choice(list(step['_level_codes']))
                touches = [{'kind': 'SWEEP_TOUCH', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf,
                            'direction': rng.choice([direction, direction, 'LONG' if direction == 'SHORT' else 'SHORT']),
                            'level_code': code, 'level_name': code, 'level_id': f'{code}:{b}',
                            'level_price': 2399., 'touch_time': float(now - rng.randrange(0, 60)),
                            'event_time': float(now)}]
            for h in (reused, fresh):
                h.port.handle_fact(fact(h.port, step, tf, touches))
            assert state(reused) == state(fresh), (b, tf, direction)
            compared += 1
        if b == bundles // 2:                           # an armed setup completes through its final OZ
            for h in (reused, fresh):
                for machine in [m for m in h.machines if m.active][:3]:
                    h.final_oz(machine, now + 30)
            assert state(reused) == state(fresh)
    return reused, fresh, calls, compared


def test_the_reuse_judges_exactly_like_recomputing_every_fact():
    reused, fresh, calls, compared = run_both()
    assert compared == 10 * 24
    assert any(command.get('action') == 'MANUAL_WATCH' for command in reused.manager.commands)  # setups armed
    assert reused.messages == fresh.messages
    touch = lambda counter: sum(v for (kind, _), v in counter.items() if kind == 'EXTERNAL_LIQUIDITY_TOUCH')
    assert touch(calls['reused']) < touch(calls['fresh']) / 5      # the judgement work, not its result


def test_a_fact_recomputes_only_its_own_watch():
    h = Harness.special('SPECIAL2')
    calls = counting(h.port)
    h.publish(START, feeds(START, 1))
    before = collections.Counter(calls)
    (machine, step), = [(m, s) for m, s in touch_steps(h.port) if s['tfs'] == ['5m'] and m.direction == 'LONG']
    h.port.handle_fact(fact(h.port, step, '5m', []))
    changed = {key: calls[key] - before[key] for key in calls if calls[key] != before[key]}
    # Only the touch reading that watch is judged again (157: per watch; the 5m SHORT touch has its own).
    assert changed == {('EXTERNAL_LIQUIDITY_TOUCH', ('5m',)): 1}


def test_a_new_fact_of_the_timeframe_is_seen_at_once():
    h = Harness.special('SPECIAL2')
    h.publish(START, feeds(START, 2))
    (machine, step), = [(m, s) for m, s in touch_steps(h.port) if s['tfs'] == ['15m'] and m.direction == 'SHORT']
    board, symbol, now = h.port._board()
    assert not h.port.observe(board, symbol, step, 'SHORT', now, machine)['ready']     # no Fact yet
    h.port.handle_fact(fact(h.port, step, '15m', []))
    board, symbol, now = h.port._board()
    empty = h.port.observe(board, symbol, step, 'SHORT', now, machine)
    assert empty['ready'] and not empty['matched']
    code = next(iter(step['_level_codes']))
    h.port.handle_fact(fact(h.port, step, '15m', [{'direction': 'SHORT', 'level_code': code, 'level_id': 'x',
                                                   'touch_time': float(START - 5)}]))
    board, symbol, now = h.port._board()
    touched = h.port.observe(board, symbol, step, 'SHORT', now, machine)
    assert touched['matched'] and touched['at'] == START - 5
    assert touched == h.port._observe(board, symbol, step, 'SHORT', now, machine)       # equals a fresh one


def test_a_new_publication_or_time_recomputes():
    h = Harness.special('SPECIAL2')
    calls = counting(h.port)
    h.publish(START, feeds(START, 3))
    first = calls['EXTERNAL_LIQUIDITY_TOUCH', ('1m',)]
    h.publish(START, feeds(START, 3))                   # another publication, same time
    second = calls['EXTERNAL_LIQUIDITY_TOUCH', ('1m',)]
    h.publish(START + 60, feeds(START + 60, 4))         # next bundle
    assert first < second < calls['EXTERNAL_LIQUIDITY_TOUCH', ('1m',)]


def test_a_touch_bound_to_its_machine_is_never_reused():
    h = Harness.special('SPECIAL2')
    step = {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], '_level_codes': ('PDL',)}
    machine = SimpleNamespace(source_tf='5m', final_tf='5m', captures={}, meaning={'steps': []}, branch=0)
    assert h.port._pure_key(SYMBOL, step, 'LONG', machine) is not None
    for bound in (dict(step, tfs=['SOURCE']), dict(step, ref='parent'), dict(step, scope_ref='parent')):
        assert h.port._pure_key(SYMBOL, bound, 'LONG', machine) is None
    one = h.port._pure_key(SYMBOL, step, 'LONG', machine)
    scope, = h.port._fact_scopes(SYMBOL, step)            # ('SWEEP', symbol, '5m', its watch)
    h.port._fact_versions[scope] = 7
    assert h.port._pure_key(SYMBOL, step, 'LONG', machine) != one


def test_direct_fact_lookup_equals_the_table_scan():
    h = Harness.special('SPECIAL2')
    h.publish(START, feeds(START, 5))
    port = h.port
    for family, tf, watch in (('SWEEP', '5m', 'a'), ('SWEEP', '5m', 'b'), ('FVG', '5m', 'a'), ('SWEEP', '1m', 'a')):
        port.handle_fact({'kind': 'FACT_SNAPSHOT', 'strategy': family, 'symbol': SYMBOL, 'source_tf': tf,
                          'watch_id': watch, 'facts': [{'id': family + tf + watch}],
                          'source_health': {'sources': {tf: 'epoch'}}})
    board = port._board()[0]

    def scan(family, symbol, tf, watch_id=None):          # the table scan before 156
        matches = [e for (f, s, t, _), e in port.fact_snapshots.items() if f == family and s == symbol and t == tf
                   and (watch_id is None or e.get('watch_id') == watch_id)]
        return matches[-1].get('facts', ()) if matches else None
    for family in ('SWEEP', 'FVG'):
        for tf in ('1m', '5m', '15m'):
            for watch in ('a', 'b', 'c', None):
                assert port._fact_snapshot(board, family, SYMBOL, tf, watch) == scan(family, SYMBOL, tf, watch)
