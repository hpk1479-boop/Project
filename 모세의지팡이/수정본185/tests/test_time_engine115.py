"""The trading-time setting in a real event engine: LIVE and backtest alert on the same input.

SPECIAL2 loads through the registry exactly as the host does (user setting = `times`), then a
LONG OZ cycle is replayed at a known KST time. The sessions' windows are placed around, or away from,
that time, so the only thing that decides the alert is the trading-time setting.
"""
import datetime as dt
import socket
from unittest.mock import patch

import pytest

import engine_harness114 as eh
from engine_harness114 import Engine, START, SYMBOL, run_cycle
from event_application import create_event_engine
from oz_profiles import profile_label
from strategy_recipe.registry import load_plugins

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.fromtimestamp(START + 240, KST)
ID = 'SPECIAL2'
NOTHING_CHECKED = {key: {'enabled': False} for key in ('MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK')}
ASIA_ONLY = {**NOTHING_CHECKED, 'MAIN_ASIA': {'enabled': True}}


def window(shift_hours):
    start = NOW + dt.timedelta(hours=shift_hours - 1)
    return f'{start:%H%M}-{start + dt.timedelta(hours=2):%H%M}'


class TimedEngine(Engine):
    """The harness engine, but the strategy comes from load_plugins with the user's trading time."""

    def __init__(self, times, *, asia_covers_now, live):
        self.id = ID
        self.block = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.block.start()
        plugin = load_plugins({'SYMBOLS': SYMBOL}, selected=[ID], times=times,
                              triggers={ID: profile_label('NORMAL', 'OZ')})[ID]
        sessions = {'MAIN_ASIA': window(0 if asia_covers_now else 6), 'MAIN_LONDON': window(9), 'MAIN_NEWYORK': window(12)}
        self.engine = create_event_engine({**eh.CONFIG, **sessions}, plugins={ID: plugin}, enabled_specials=(ID,),
                                          symbols=(SYMBOL,), backtest=not live, oz_evaluation='all')


def alerts(times, asia_covers_now, *, live):
    engine = TimedEngine(times, asia_covers_now=asia_covers_now, live=live)
    try:
        keep = ('direction', 'source_tf', 'event_time', 'b0_price', 'source_spec_id', 'trigger_name')
        return [{key: alert.get(key) for key in keep} for alert in run_cycle(engine)]
    finally:
        engine.close()


def both_modes(times, asia_covers_now):
    live, backtest = alerts(times, asia_covers_now, live=True), alerts(times, asia_covers_now, live=False)
    assert live == backtest, 'LIVE and backtest disagree on the same input'
    return backtest


@pytest.mark.parametrize('times,covers,expected', [
    (None, True, True),                  # the strategy's own times (21:00-24:00 covers 23:18)
    (None, False, True),                 # ... moving 주요 거래시간 away does not move them
    ({ID: NOTHING_CHECKED}, True, True),
    ({ID: NOTHING_CHECKED}, False, True),    # nothing checked: 24 hours
    ({ID: {}}, False, True),
    ({ID: ASIA_ONLY}, True, True),
    ({ID: ASIA_ONLY}, False, False),         # only Asia checked, and it is not Asia now
])
def test_alert_follows_the_trading_time_setting_in_both_modes(times, covers, expected):
    sent = both_modes(times, covers)
    assert bool(sent) is expected
    if expected:
        assert len(sent) == 1 and sent[0]['source_tf'] == '1m'
