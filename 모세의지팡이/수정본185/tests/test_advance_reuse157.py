"""수정본157: 같은 게시·같은 시각에 입력이 그대로인 판단 기계는 다시 진행하지 않는다.

대상은 조건이 모두 다시 쓸 수 있는 종류(135의 PURE_KINDS, 156의 FACT_KINDS, 기계에 묶이지 않음)이고
수명 규칙이 없는 기계다. 그런 기계의 관측은 게시·시각·자기가 읽는 사실로만 바뀐다. 그 셋이 그대로면
같은 입력으로 다시 진행해도 아무것도 바뀌지 않는다. 어느 기계든 행동(감시 걸기·풀기·알림)을 내면 그
앞에 진행한 기계를, OZ 이벤트가 오거나 상태를 복원하면 모든 기계를 다음 판단에서 다시 진행한다.
기준은 이 재사용을 끈 같은 전략이다: 같은 입력이면 기계 상태·관측·감시 명령·알림이 모두 같아야 한다.
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
from recipe_harness114 import Harness, SYMBOL                                   # noqa: E402
from event_engine.model import FeedSnapshot                                      # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS                                      # noqa: E402
from test_fact_reuse156 import SECONDS, START, fact, touch_steps                 # noqa: E402

COL = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}


def market(now, rng):
    """Every timeframe at `now`: a random walk with candles, averages and WONBI bands around it."""
    feeds, wonbi = {}, {}
    for tf, seconds in SECONDS.items():
        n = 80
        close = 2400. + np.cumsum(rng.normal(0., 1.5, n))
        values = np.full((n, len(PIPE_VALUE_COLUMNS)), 2400., dtype=float)
        values[:, COL['open']] = np.r_[close[0], close[:-1]]
        values[:, COL['close']] = close
        values[:, COL['high']] = np.maximum(values[:, COL['open']], close) + rng.uniform(0., 1., n)
        values[:, COL['low']] = np.minimum(values[:, COL['open']], close) - rng.uniform(0., 1., n)
        for name, span in (('ema_20', 6), ('ema_50', 12), ('ema_200', 30), ('hma_6', 3), ('hma_17', 6), ('hma_50', 12)):
            values[:, COL[name]] = np.convolve(np.r_[np.full(span - 1, close[0]), close], np.ones(span) / span, 'valid')
        times = (now - now % seconds) + (np.arange(n, dtype=np.int64) - (n - 1)) * seconds
        feeds[tf] = FeedSnapshot(times, np.ones(n, dtype=np.int64), values, int(now), 'epoch', {})
        width = rng.uniform(.5, 4.)
        wonbi[tf] = {'wonbi_mid': close, 'wonbi_upper': close + width, 'wonbi_lower': close - width}
    return feeds, wonbi


def state(h):
    return ([(m.branch, m.checkpoint()) for m in h.machines], dict(h.port.observations),
            list(h.manager.commands), list(h.messages))


@pytest.fixture
def advances(monkeypatch):
    """Advances per machine (the judgement work, not its result); nothing is added to the machines."""
    from strategy_recipe.runtime import IntentMachine
    counts = collections.Counter(); original = IntentMachine.advance
    def advance(self, *args, **kwargs):
        counts[id(self)] += 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(IntentMachine, 'advance', advance)
    return lambda h: sum(counts[id(m)] for m in h.machines)


def touch(step, tf, direction, now, label):
    code = next(iter(step['_level_codes']))
    return {'kind': 'SWEEP_TOUCH', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf, 'direction': direction,
            'level_code': code, 'level_name': code, 'level_id': f'{code}:{label}', 'level_price': 2399.,
            'touch_time': float(now - 5), 'event_time': float(now)}


def pair(name):
    reused, every = Harness.special(name), Harness.special(name)
    every.port._reuse_plan = lambda machine: None          # the judgement before 157: every poll advances all
    return reused, every


def special2_facts(rng, steps, now, b):
    order = list(steps); rng.shuffle(order)
    for branch, direction, tf, step in order:
        touches = []
        if rng.random() < .3:
            code = rng.choice(list(step['_level_codes']))
            touches = [{'kind': 'SWEEP_TOUCH', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf,
                        'direction': rng.choice([direction, direction, 'LONG' if direction == 'SHORT' else 'SHORT']),
                        'level_code': code, 'level_name': code, 'level_id': f'{code}:{b}', 'level_price': 2399.,
                        'touch_time': float(now - rng.randrange(0, 60)), 'event_time': float(now)}]
        yield step, tf, touches


def test_special2_skips_unchanged_machines_and_judges_exactly_alike(advances):
    reused, every = pair('SPECIAL2')
    rng = random.Random(157)
    steps = [(m.branch, m.direction, s['tfs'][0], s) for m, s in touch_steps(reused.port)]
    compared = 0
    for b in range(12):
        now = START + 60 * b
        feeds, wonbi = market(now, np.random.default_rng(b))
        for h in (reused, every):
            h.publish(now, feeds, wonbi=wonbi)
        assert state(reused) == state(every)
        for step, tf, touches in special2_facts(rng, steps, now, b):
            for h in (reused, every):
                h.port.handle_fact(fact(h.port, step, tf, touches))
            assert state(reused) == state(every), (b, tf)
            compared += 1
        if b in (4, 9):                                   # armed setups complete through their final OZ
            for h in (reused, every):
                for machine in [m for m in h.machines if m.active][:3]:
                    h.final_oz(machine, now + 30)
            assert state(reused) == state(every)
    assert compared == 12 * 24 and reused.messages
    assert any(c.get('action') == 'MANUAL_WATCH' for c in reused.manager.commands)
    # Touches here arm and cancel often (every action re-advances); real bundles rarely act.
    assert advances(reused) < advances(every) / 2


@pytest.mark.parametrize('name', ['SPECIAL1', 'SPECIAL7'])
def test_pure_condition_machines_judge_exactly_alike(advances, name):
    reused, every = pair(name)
    rng = random.Random(7)
    for b in range(30):
        now = START + 300 * b
        feeds, wonbi = market(now, np.random.default_rng(1000 + b))
        for h in (reused, every):
            h.publish(now, feeds, wonbi=wonbi)
        assert state(reused) == state(every)
        for _ in range(rng.randrange(0, 4)):              # FVG/SWEEP Facts of other strategies in the bundle
            event = {'kind': 'FACT_SNAPSHOT', 'strategy': rng.choice(['FVG', 'SWEEP']), 'symbol': SYMBOL,
                     'source_tf': rng.choice(list(SECONDS)), 'facts': [], 'complete': True,
                     'source_health': {'sources': {}}}
            for h in (reused, every):
                h.port.handle_fact(dict(event))
            assert state(reused) == state(every)
        if rng.random() < .3:
            for h in (reused, every):
                for machine in [m for m in h.machines if m.active][:2]:
                    h.final_oz(machine, now + 10)
            assert state(reused) == state(every)
    assert advances(reused) <= advances(every)


def test_only_reusable_machines_without_a_lifecycle_are_eligible():
    two = Harness.special('SPECIAL2')
    machine = two.machines[0]
    step = machine.meaning['steps'][0]
    watch = two.port._dependency_id(SYMBOL, step['tfs'][0], 'SWEEP', step)
    assert two.port._reuse_plan(machine) == (('SWEEP', SYMBOL, step['tfs'][0], watch),)
    for name in ('SPECIAL3', 'SPECIAL4', 'SPECIAL5', 'SPECIAL6', 'SPECIAL8', 'SPECIAL9'):
        h = Harness.special(name)                          # a lifecycle, or a condition with memory
        assert all(h.port._reuse_plan(m) is None for m in h.machines), name
    # Bound to its machine (a capture, SOURCE), or with a lifecycle: never skipped.
    for branch, meaning in enumerate(({'steps': [dict(step, ref='other')]},
                                      {'steps': [dict(step, scope_ref='other')]},
                                      {'steps': [dict(step, tfs=['SOURCE'])]},
                                      {'steps': [step], 'lifecycle': {'expires': {'bars': 3}}},
                                      {'steps': [step], 'cancel_conditions': [{'kind': 'BAR_CLOSE', 'tfs': ['1m']}]}), 100):
        fake = SimpleNamespace(branch=branch, symbol=SYMBOL, meaning=meaning)
        assert two.port._reuse_plan(fake) is None, meaning


def test_an_action_or_an_oz_event_advances_every_machine_again(advances):
    h = Harness.special('SPECIAL2')
    feeds, wonbi = market(START, np.random.default_rng(3))
    h.publish(START, feeds, wonbi=wonbi)
    steps = {(m.direction, s['tfs'][0]): s for m, s in touch_steps(h.port)}
    for (direction, tf), step in steps.items():
        h.port.handle_fact(fact(h.port, step, tf, []))
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['LONG', '5m'], '5m', []))
    assert advances(h) - before == 1                      # only the machine reading that watch
    commands = len(h.manager.commands)
    long15 = steps['LONG', '15m']
    h.port.handle_fact(fact(h.port, long15, '15m', [touch(long15, '15m', 'LONG', START, 'x')]))
    assert len(h.manager.commands) > commands             # the touch armed its final OZ watch
    acting = next(m for m in h.machines if m.active)
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['SHORT', '1m'], '1m', []))
    # The armed machine and every machine advanced before it in that poll, and the one reading the new Fact.
    short1 = next(m for m in h.machines if m.direction == 'SHORT' and m.meaning['steps'][0]['tfs'] == ['1m'])
    again = {m.branch for m in h.machines if m.branch <= acting.branch} | {short1.branch}
    assert advances(h) - before == len(again)
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['SHORT', '1m'], '1m', []))
    assert advances(h) - before == 1
    armed = next(m for m in h.machines if m.active)
    h.final_oz(armed, START + 30)                         # an OZ event: its own poll advances everyone
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['SHORT', '1m'], '1m', []))
    assert advances(h) - before == len(h.machines)


def test_restore_advances_every_machine_again(advances):
    h = Harness.special('SPECIAL2')
    feeds, wonbi = market(START, np.random.default_rng(4))
    h.publish(START, feeds, wonbi=wonbi)
    steps = {(m.direction, s['tfs'][0]): s for m, s in touch_steps(h.port)}
    h.port.handle_fact(fact(h.port, steps['LONG', '5m'], '5m', []))
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['LONG', '5m'], '5m', []))
    assert advances(h) - before == 1
    assert h.port.restore(h.port.checkpoint())
    before = advances(h)
    h.port.handle_fact(fact(h.port, steps['LONG', '5m'], '5m', []))
    assert advances(h) - before == len(h.machines)
