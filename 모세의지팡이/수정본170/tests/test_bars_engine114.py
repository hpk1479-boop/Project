"""A configured bar count in a real event engine: LIVE and backtest agree, each child counts its own bars.

The strategy is the smallest one that declares what SPECIAL5 declares: a state condition,
independent final timeframes, and lifecycle.expires bars on FINAL with the MAX_BARS_AFTER_B0 setting.
"""
import pytest

from engine_harness114 import Engine, SYMBOL, quiet
from command_interpreter import tf_seconds

T = 1790001000
SETUP = T + 10


def declare(meaning):
    meaning.clear()
    meaning.update({'direction': 'LONG', 'symbols': [SYMBOL], 'time_filters': [], 'final_time_filters': 0,
        'steps': [{'kind': 'MA_STATE', 'tfs': ['5m'], 'ma_family': 'EMA', 'fast_period': 50,
                   'slow_period': 200, 'side': 'ABOVE'}],
        'order_mode': 'SIMULTANEOUS', 'persistent': True,
        'final': {'kind': 'OZ', 'tfs': ['1m', '3m'], 'tf_combine': 'INDEPENDENT',
                  'validation_mode': 'BLIND', 'trigger_mode': 'OZ'},
        'lifecycle': {'expires': {'bars': 10, 'bars_setting': 'MAX_BARS_AFTER_B0', 'tf': 'FINAL'}}})


def timeline(live, config):
    """First publication at which each child no longer lives."""
    engine = Engine('SPECIAL7', mutate=declare, live=live, config=config)
    try:
        ended = {}
        for now in [SETUP, *range(T + 65, T + 2300, 60)]:
            port = engine.send(now, {tf: quiet(now - now % tf_seconds(tf), tf=tf) for tf in ('1m', '3m', '5m')})
            for machine in port.machines:
                if not machine.active and machine.final_tf not in ended and now > SETUP:
                    ended[machine.final_tf] = now - T
            if len(ended) == 2: break
        return ended, [m.active for m in port.machines]
    finally:
        engine.close()


@pytest.mark.parametrize('config,limit', [({'MAX_BARS_AFTER_B0': '2'}, 2), ({'MAX_BARS_AFTER_B0': '4'}, 4), ({}, 10)])
def test_each_child_ends_after_the_configured_count_of_its_own_closed_bars(config, limit):
    live, backtest = timeline(True, config), timeline(False, config)
    assert live == backtest, 'LIVE and backtest disagree on the same input'
    ended, _ = backtest
    # Bars opened after the setup (T+10): 1m at T+60, T+120, ..; 3m at T+180, T+360, ..
    # The bar that opens with limit+1 closed bars behind it ends the child (`closed > limit`).
    assert ended == {'1m': 60 * (limit + 2) + 5, '3m': 180 * (limit + 2) + 5}
