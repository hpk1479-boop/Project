"""SPECIAL2 end to end in a real event engine, in LIVE mode and in backtest mode, on the same input.

A touched lower level, then a LONG OZ whose B0 is 118.2 (ATR 2.0, so 1.5x is 3.0). The OZ passes
when the level is inside the allowed distance and is dropped when it is outside. The
shipped recipe changes only to use the OZ trigger these fixtures complete.
"""
from unittest.mock import patch

import pytest

from engine_harness114 import Engine, run_cycle
from oz_engine.controllers import OZWatchController

B0 = 118.2          # the fixtures' final OZ low (asserted below, not assumed)


def trigger_oz(meaning):
    meaning['final']['trigger_mode'] = 'OZ'


def without_gate(meaning):
    trigger_oz(meaning); meaning['final'].pop('level_gate')


def declared(period=14, mult=1.5):
    def mutate(meaning):
        trigger_oz(meaning); meaning['final']['level_gate'].update(atr_period=period, atr_mult=mult)
    return mutate


def run(mutate, level, *, live):
    engine = Engine(mutate=mutate, live=live)
    try:
        alerts = run_cycle(engine, level=level)
        runtime = engine.engine.processor_state['OZ_STATE']['runtime']
        states = {key.split('|')[1]: (state.status, state.level_price, state.max_distance, state.reason)
                  for key, state in runtime.external._states.items() if ':1m:' in key}
        keep = ('direction', 'source_tf', 'event_time', 'b0_price', 'b0_time', 'grade', 'source_spec_id', 'trigger_name')
        return [{key: alert.get(key) for key in keep} for alert in alerts], states
    finally:
        engine.close()


def both_modes(mutate, level):
    live, backtest = run(mutate, level, live=True), run(mutate, level, live=False)
    assert live == backtest, 'LIVE and backtest disagree on the same input'
    return backtest


@pytest.mark.parametrize('level', [118.6, 120.3, 121.05])
def test_a_level_inside_the_allowed_distance_lets_the_oz_through(level):
    alerts, states = both_modes(trigger_oz, level)
    assert len(alerts) == 1 and alerts[0]['source_tf'] == '1m' and alerts[0]['b0_price'] == B0
    assert states['LONG'][0] == 'CONFIRMED' and states['LONG'][2] == 3.0


@pytest.mark.parametrize('level,reason', [(121.15, 'ATR_1P5_SURVIVAL'), (125., 'ATR_1P5_FIRST_CHECK')])
def test_a_level_outside_the_allowed_distance_drops_the_oz(level, reason):
    alerts, states = both_modes(trigger_oz, level)
    assert alerts == []
    assert states['LONG'][0] == 'INVALID' and states['LONG'][3] == reason


@pytest.mark.parametrize('level', [118.6, 125.])
def test_without_the_link_the_same_oz_fires_whatever_the_level(level):
    alerts, states = both_modes(without_gate, level)
    assert len(alerts) == 1                                  # the omission this change closes
    assert states['LONG'][0] == 'PENDING_ATR'                # the gate never looked at it


def test_the_multiplier_the_strategy_declares_moves_the_boundary():
    # 1.0 x ATR2.0 = 2.0: 119.9 is inside, 120.2 outside; at the shipped 1.5 both are inside.
    assert len(both_modes(declared(14, 1.0), 119.9)[0]) == 1
    assert both_modes(declared(14, 1.0), 120.2)[0] == []
    assert len(both_modes(declared(14, 1.5), 120.2)[0]) == 1
    assert len(both_modes(declared(14, 3.0), 121.05)[0]) == 1


def test_the_final_oz_low_is_what_the_distance_check_receives():
    calls = []
    original = OZWatchController.validate_external_true_b0
    def spy(self, symbol, tf, direction, validation_mode, trigger_mode, true_b0_price):
        result = original(self, symbol, tf, direction, validation_mode, trigger_mode, true_b0_price)
        calls.append((tf, direction, true_b0_price, result))
        return result
    with patch.object(OZWatchController, 'validate_external_true_b0', spy):
        engine = Engine(mutate=trigger_oz)
        try:
            alerts = run_cycle(engine, level=118.6)
        finally:
            engine.close()
    assert len(alerts) == 1
    assert ('1m', 'LONG', B0, True) in calls
