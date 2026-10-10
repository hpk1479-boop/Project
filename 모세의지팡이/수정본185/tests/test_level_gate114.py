"""The OZ external-liquidity gate honours the ATR rule the strategy declared.

Recipe -> its own SWEEP_WATCH / MANUAL_WATCH -> the OZ engine's gate: first check,
survival, and the final OZ low/high distance, inside and outside the allowed distance.
"""
from types import SimpleNamespace as NS
import copy

import numpy as np
import pandas as pd
import pytest

from recipe_harness114 import Harness, SYMBOL, feed
from event_engine.model import FeedSnapshot
from indicator_facts import parameterized_atr_fact, rma_array, standalone_frame, true_range_array
from oz_engine.common import EXTERNAL_ATR_MULT, EXTERNAL_ATR_PERIOD, ExternalLiquiditySpec, sweep_external_spec
from oz_engine.controllers import ExternalLiquidityController
from oz_engine.market import COLUMNS, OZMarketView
from oz_engine.runtime import OZRuntime
from staff_schema import legacy_frame
from strategy_recipe.contract import level_gate_rule

START = 1790380000


def snapshot(high, low, close=None, *, seq=1):
    n = len(high)
    values = np.full((n, len(COLUMNS)), 100.)
    values[:, COLUMNS['high']] = high; values[:, COLUMNS['low']] = low
    values[:, COLUMNS['close']] = close if close is not None else 100.
    values[:, COLUMNS['open']] = 100.
    return FeedSnapshot(START + np.arange(n, dtype=np.int64) * 60, np.ones(n, dtype=np.int64), values, seq, 'e', {})


def view_of(snap):
    tr = true_range_array(snap.values[:, COLUMNS['high']], snap.values[:, COLUMNS['low']], snap.values[:, COLUMNS['close']])
    return OZMarketView(snap, atr_provider=lambda: rma_array(tr, EXTERNAL_ATR_PERIOD))


def random_snapshot(n=160, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .5, n))
    high = close + rng.uniform(.1, 2, n); low = close - rng.uniform(.1, 2, n)
    return snapshot(high, low, close)


@pytest.mark.parametrize('period', [1, 5, 14, 20, 50, 100])
def test_any_atr_period_is_the_one_shared_wilder_definition(period):
    snap = random_snapshot()
    shared = standalone_frame(legacy_frame(SYMBOL, '1m', snap), '1m').get(parameterized_atr_fact(period)).to_numpy()
    np.testing.assert_array_equal(view_of(snap).atr_series(period), shared, strict=True)


def test_period_14_is_the_existing_fact_itself():
    v = view_of(random_snapshot())
    assert v.atr_series(14) is v.atr()


@pytest.mark.parametrize('payload,expected', [
    ({}, (14, 1.5)), ({'external_atr_period': 20, 'external_atr_mult': 2}, (20, 2.)),
    ({'external_atr_period': 0, 'external_atr_mult': 0}, (14, 1.5)),
    ({'external_atr_period': 501, 'external_atr_mult': float('nan')}, (14, 1.5)),
    ({'external_atr_period': True, 'external_atr_mult': '3'}, (14, 1.5)),
    ({'external_atr_period': 3.5, 'external_atr_mult': -1}, (14, 1.5))])
def test_unset_or_invalid_rules_are_the_fixed_rule(payload, expected):
    spec = sweep_external_spec({'watch_id': 'w', 'symbol': SYMBOL, 'source_tf': '1m', **payload})
    assert (spec.atr_period, spec.atr_mult) == expected and spec.source_kind == 'SWEEP'


def test_contract_default_is_the_engines_own_rule_defined_once():
    assert level_gate_rule({}) == {'atr_period': EXTERNAL_ATR_PERIOD, 'atr_mult': EXTERNAL_ATR_MULT}
    assert level_gate_rule({'atr_period': 20, 'atr_mult': 2}) == {'atr_period': 20, 'atr_mult': 2.}


# --- the gate itself, with the strategy's rule ---------------------------------------------

def gate(period, mult, snap, *, direction='LONG', level=100., touch_row=-2, extreme=None):
    """A controller holding one touched level, evaluated against `snap`."""
    ext = ExternalLiquidityController(NS(seconds=0.), sweep_registry={})
    ext._specs['w'] = sweep_external_spec({'watch_id': 'w', 'symbol': SYMBOL, 'source_tf': '1m',
        'external_atr_period': period, 'external_atr_mult': mult})
    when = int(snap.time[touch_row])
    from oz_engine.common import ExternalLiquidityState
    state = ExternalLiquidityState('w', 'PENDING_ATR', direction, 'level', level_price=level, event_time=when,
        touch_low=extreme if direction == 'LONG' else None, touch_high=extreme if direction == 'SHORT' else None)
    ext._states['w|' + direction + '|level'] = state
    return ext, state


def calm_then_wild(n=60):
    """Ranges of 2 for 45 bars then 6 for the rest: ATR14 has risen further than ATR50."""
    spread = np.r_[np.full(45, 1.), np.full(n - 45, 3.)]
    return snapshot(100 + spread, 100 - spread)


def test_the_snapshot_uses_the_declared_period_and_multiplier():
    snap = calm_then_wild()
    v = view_of(snap)
    when = len(snap.time) - 2
    results = {}
    for period, mult in ((14, 1.5), (50, 1.5), (50, 3.)):
        ext, state = gate(period, mult, snap, touch_row=-2)
        ext.update_market(SYMBOL, {'1m': v}, ['w'])
        results[period, mult] = state
        expected = float(v.atr_series(period)[when])
        assert state.atr_snapshot == expected and state.max_distance == expected * mult
    assert results[14, 1.5].atr_snapshot > results[50, 1.5].atr_snapshot        # periods really differ
    assert results[50, 3.].max_distance == results[50, 1.5].max_distance * 2


@pytest.mark.parametrize('direction,sign', [('LONG', -1), ('SHORT', 1)])
@pytest.mark.parametrize('extra,status', [(0., 'ACTIVE'), (1e-6, 'INVALID')])
def test_first_check_survival_and_final_distance_with_a_declared_rule(direction, sign, extra, status):
    snap = calm_then_wild()
    v = view_of(snap)
    when = len(snap.time) - 2
    period, mult = 50, 2.
    limit = float(v.atr_series(period)[when]) * mult
    # First check: the touch candle's extreme may sit exactly on the limit, not beyond it.
    ext, state = gate(period, mult, snap, direction=direction, level=100.,
                      extreme=100. + sign * (limit + extra))
    ext.update_market(SYMBOL, {'1m': v}, ['w'])
    assert state.status == status
    if status == 'INVALID': assert state.reason == 'ATR_1P5_FIRST_CHECK'
    # Final OZ low/high: |B0 - level| against the same limit.
    state.status, state.reason, state.max_distance = 'ACTIVE', None, limit
    assert ext.validate_true_b0('w', direction, 100. + sign * (limit - 1e-9))
    state.status = 'ACTIVE'
    assert not ext.validate_true_b0('w', direction, 100. + sign * (limit + 1e-6))
    assert state.status == 'INVALID' and state.reason == 'ATR_1P5_TRUE_B0_DISTANCE'
    # Survival: a later bar beyond the limit ends it.
    low = snap.values[:, COLUMNS['low']].copy(); high = snap.values[:, COLUMNS['high']].copy()
    column = low if direction == 'LONG' else high
    column[-1] = 100. + sign * (limit + 1e-6)
    moved = view_of(FeedSnapshot(snap.time, snap.volume, np.where(
        np.arange(snap.values.shape[1]) == COLUMNS['low'], low[:, None], np.where(
        np.arange(snap.values.shape[1]) == COLUMNS['high'], high[:, None], snap.values)), 2, 'e', {}))
    ext, state = gate(period, mult, snap, direction=direction, level=100.)
    state.status, state.max_distance, state.atr_snapshot = 'ACTIVE', limit, limit / mult
    ext.update_market(SYMBOL, {'1m': moved}, ['w'])
    assert state.status == 'INVALID' and state.reason == 'ATR_1P5_SURVIVAL'


def test_rule_survives_a_checkpoint():
    ext, _ = gate(20, 2., calm_then_wild())
    saved = ext.export_payload()
    assert saved['specs']['w']['atr_period'] == 20 and saved['specs']['w']['atr_mult'] == 2.
    restored = ExternalLiquidityController(NS(seconds=0.), sweep_registry={}, initial=copy.deepcopy(saved))
    restored._load_state()
    assert (restored._specs['w'].atr_period, restored._specs['w'].atr_mult) == (20, 2.)
    # An older checkpoint without the fields means the fixed rule.
    del saved['specs']['w']['atr_period'], saved['specs']['w']['atr_mult']
    older = ExternalLiquidityController(NS(seconds=0.), sweep_registry={}, initial=saved)
    older._load_state()
    assert (older._specs['w'].atr_period, older._specs['w'].atr_mult) == (14, 1.5)


# --- Recipe -> OZ engine -------------------------------------------------------------------

def oz_runtime():
    import monitor_OZ
    runtime = OZRuntime(monitor_OZ, {})
    board = NS(processor=lambda name: {'registry': {}})
    def command(payload):
        event = NS(source_time=START * 1000, engine_seq=1, payload={'symbol': SYMBOL})
        runtime.process(event, board, command=copy.deepcopy(payload))
    return runtime, command


def linked_recipe(mutate=None):
    """SPECIAL2's own commands for a 5m LONG touch: its SWEEP_WATCH and the final OZ's MANUAL_WATCH."""
    h = Harness.special('SPECIAL2', mutate=mutate)
    from test_level_link114 import run, touch
    machine, watch = run(h, [touch('PDL', '전일저가(PDL)', 100., 'LONG')])
    sweep = h.port.desired_subscriptions()['SWEEP'][watch]
    return sweep, h.watch_commands()[-1], watch


def test_the_recipes_own_commands_register_its_rule_and_link_in_the_oz_engine():
    sweep, order, watch = linked_recipe()
    runtime, command = oz_runtime()
    command(sweep); command(order)
    spec = runtime.registry[watch]
    assert (spec.atr_period, spec.atr_mult, spec.source_tf) == (14, 1.5, '5m')
    assert runtime.external._specs[watch] == spec          # the gate itself holds the same rule from the start
    command(sweep)                                          # and keeps it when the watch is sent again
    assert runtime.external._specs[watch] == spec
    linked = runtime.watch._watches[order['watch_id']]
    assert linked.external_watch_id == watch and linked.external_source_tf == '5m' and linked.direction == 'LONG'
    assert runtime.watch._external_id_for_watch_locked(linked) == watch


@pytest.mark.parametrize('mutation,limit_atr', [
    (None, 14), (lambda m: m['final']['level_gate'].update(atr_period=50, atr_mult=3.), 50)])
def test_final_oz_distance_from_the_recipes_level_passes_inside_and_fails_outside(mutation, limit_atr):
    sweep, order, watch = linked_recipe(mutation)
    runtime, command = oz_runtime()
    command(sweep); command(order)
    command(sweep)                                              # a repeated watch keeps the declared rule
    assert runtime.external._specs[watch].atr_period == limit_atr
    runtime.external.apply_event({'kind': 'SWEEP_TOUCH', 'watch_id': watch, 'symbol': SYMBOL, 'source_tf': '5m',
        'direction': 'LONG', 'level_id': 'PDL:x', 'level_code': 'PDL', 'level_name': 'PDL', 'level_price': 100.,
        'event_time': float(START + 58 * 60), 'touch_high': 101., 'touch_low': 99.})
    snap = calm_then_wild()
    v = view_of(snap)
    spec = runtime.registry[watch]
    assert runtime.external._specs[watch] == spec               # the touch event adopted the registered rule
    runtime.external.update_market(SYMBOL, {'5m': v}, [watch])
    state = runtime.external.state(watch, 'LONG')
    expected = float(v.atr_series(limit_atr)[len(snap.time) - 2]) * spec.atr_mult
    assert state.status == 'ACTIVE' and state.max_distance == expected
    check = lambda price: runtime.watch.validate_external_true_b0(SYMBOL, '5m', 'LONG', 'NORMAL', 'BREAKER', price)
    assert check(100. - (expected - 1e-6))                      # inside the allowed distance
    assert runtime.external.status(watch, 'LONG') == 'CONFIRMED'
    assert not check(100. - (expected + 1e-6))                  # outside it
    assert runtime.external.status(watch, 'LONG') == 'INVALID'
