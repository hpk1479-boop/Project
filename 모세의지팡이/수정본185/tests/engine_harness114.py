"""A real event engine (SWEEP, OZ and Composer processors) driven by synthetic market bundles.

The market inputs follow the OZ fixtures already used by the common-recipe tests: a LONG
cycle on 1m that completes after four publications. Levels (daily/4h/8h lows) are the one
thing a test chooses, which decides whether the external-liquidity gate lets that OZ through.
"""
import copy
import socket
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from command_interpreter import MT5_TIMEFRAMES, tf_seconds
from event_application import create_event_engine
from event_engine.model import FeedSnapshot, Kind
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe import registry
from strategy_recipe.contract import execution_plan
from strategy_recipe.port import IntentPort

SYMBOL = 'XAUUSD+'
START = 1790000040
CONFIG = {'WONBI_SIGMA': '3', 'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': 'TEST', 'STAFF_ALLOWED_SYMBOLS': SYMBOL,
          'TARGET_SYMBOLS': SYMBOL, 'LONDON': '1600-0100'}
COL = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}


def quiet(stamp, *, seq=1, tf='1m'):
    rows = 250
    times = stamp + np.arange(-rows + 1, 1, dtype=np.int64) * tf_seconds(tf)
    values = np.full((rows, len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
    for name, value in {'open': 100., 'high': 101., 'low': 99., 'close': 100., 'ema_50': 101., 'ema_200': 100.,
                        'hma_6': 102., 'hma_17': 100., 'wonbi_upper': 103., 'wonbi_lower': 97.,
                        'open_band_4_mid': 100.}.items():
        values[:, COL[name]] = value
    for name in PIPE_VALUE_COLUMNS:
        if name.startswith(('sma_', 'ema_', 'wma_', 'hma_')) and name not in ('ema_50', 'ema_200', 'hma_6', 'hma_17'):
            values[:, COL[name]] = 100.
        if name.endswith(('_lower_out', '_upper_out')):
            values[:, COL[name]] = float('nan')
    return FeedSnapshot(times, np.ones(rows, dtype=np.int64), values, seq, 'probe', {})


def oz_feed(stamp, phase, tf, *, daily_low=None):
    """Finite native inputs for the OZ LONG cycle: OUT->IN, HMA cross, B0 (phase 0..3)."""
    base = quiet(stamp, tf=tf)
    a = base.values.copy()
    def col(name, value): a[:, COL[name]] = value
    for name, value in {'open': 119., 'close': 120., 'low': 118.6, 'high': 120.6,
                        'hma_6': 118. if tf == '1m' else 120., 'hma_17': 119., 'open_band_4_mid': 119.,
                        'wonbi_lower': 117., 'wonbi_upper': 121.}.items(): col(name, value)
    if tf == '3m':
        for name, value in {'open': 121., 'close': 121., 'low': 120.5, 'high': 122.}.items(): col(name, value)
    if daily_low is not None and tf in ('1d', '4h', '8h'):
        col('low', daily_low); col('high', daily_low + 2.); col('open', daily_low + 1.); col('close', daily_low + 1.)
    a[-1, COL['low']] = 118.2 if tf not in ('1d', '4h', '8h') or daily_low is None else daily_low
    if phase >= 1 and tf == '1m': a[-1, COL['hma_6']] = 120.
    if phase >= 2 and tf == '1m':
        a[-2, COL['hma_6']] = 120.
        a[-1, COL['low']] = 118.1
    for family in ('RSI', 'STO', 'DI', 'price'):
        for name, value in ((family + '_val' if family != 'price' else 'price_hma_6', 50.),
                            (family + '_db' if family != 'price' else 'price_band_lower', 40.),
                            (family + '_ub' if family != 'price' else 'price_band_upper', 60.),
                            (family + '_regime_lower', 40.), (family + '_regime_upper', 60.), (family + '_regime_slope', 1.)):
            col(name, value)
        if (phase == 0 and tf == '1m') or tf == '3m':
            a[-1, COL[family + '_val' if family != 'price' else 'price_hma_6']] = 30.
        col(family + '_lower_out', float('nan')); col(family + '_upper_out', float('nan'))
        if (phase == 0 and tf == '1m') or tf == '3m': a[-1, COL[family + '_lower_out']] = 30.
    return FeedSnapshot(base.time, base.volume, a, base.seq, base.source_epoch, {})


def plugin(special_id='SPECIAL2', mutate=None):
    """The shipped recipe as the registry loads it, for one symbol and any time of day."""
    entry = registry.builtin_entries()[special_id]
    meaning = copy.deepcopy(entry['recipe']['strategy_intent'])
    meaning.update(symbols=[SYMBOL], time_filters=[], final_time_filters=0)
    if mutate: mutate(meaning)
    plan = execution_plan(meaning)['meaning']
    recipe = {**copy.deepcopy(entry['recipe']), 'strategy_intent': plan}
    def register(manager, plan=plan, title=entry['name'], namespace=special_id):
        IntentPort(manager, plan, title, namespace).install()
    return NS(__name__=special_id, register=register, recipe=recipe,
              OZ_DECLARATIONS=registry.oz_declarations(plan))


class Engine:
    def __init__(self, special_id='SPECIAL2', *, mutate=None, live=False, config=None):
        self.id = special_id
        self.block = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.block.start()
        self.engine = create_event_engine({**CONFIG, **(config or {})}, plugins={special_id: plugin(special_id, mutate)},
            enabled_specials=(special_id,), symbols=(SYMBOL,), backtest=not live, oz_evaluation='all')

    def close(self): self.block.stop()

    def send(self, stamp, feeds):
        self.engine.ingress.post(Kind.MARKET_BUNDLE, source='logic', source_seq=stamp, source_time=stamp * 1000,
            payload={'symbol': SYMBOL, 'feeds': feeds})
        self.engine.run()
        assert not self.engine.error_log, self.engine.error_log
        return self.port()

    def port(self):
        manager = self.engine.strategy_state['COMPOSER']['kernels'][SYMBOL].manager
        return manager._special_watch_handlers[self.id]

    def notices(self):
        return [dict(s.payload['content']) for s in self.engine.signals
                if s.payload.get('content', {}).get('type') == 'NOTIFICATION']

    def alerts(self):
        return [n for n in self.notices() if str(n.get('source_spec_id', '')).startswith(self.id + ':BRANCH:')]


LEVEL_TFS = ('1d', '4h', '8h')
TOUCH_LOW = 118.5        # low of the closed bars of the publication in which every timeframe touches


def level_feed(tf, level, stamp=START):
    """Higher-timeframe bars whose previous bar's low is `level`. Times never move, so the level keeps its identity."""
    base = quiet(stamp, tf=tf)
    a = base.values.copy()
    for name, value in {'open': level + 1., 'close': level + 1., 'low': level, 'high': level + 2.}.items():
        a[:, COL[name]] = value
    return FeedSnapshot(base.time, base.volume, a, 1, 'levels', {})


def touching(stamp, tf):
    """A quiet publication whose closed bars sit just above TOUCH_LOW: LONG levels at or above it are touched."""
    base = quiet(stamp, tf=tf)
    a = base.values.copy()
    for name, value in {'open': 119., 'close': 119., 'low': TOUCH_LOW, 'high': 120.}.items():
        a[:, COL[name]] = value
    return FeedSnapshot(base.time, base.volume, a, base.seq, base.source_epoch, {})


def run_cycle(engine, *, level=118.6, start=START):
    """A touching publication (the environment), then the four OZ phases on every timeframe.

    The daily/4h/8h feeds stay the same throughout, so `level` is the one lower liquidity
    level of every timeframe's watch. Returns the engine's strategy alerts.
    """
    def bundle(stamp, make):
        feeds = {tf: make(tf) for tf in MT5_TIMEFRAMES if tf not in LEVEL_TFS}
        feeds.update({tf: level_feed(tf, level) for tf in LEVEL_TFS})
        return feeds
    engine.send(start, bundle(start, lambda tf: touching(start, tf)))
    for i, phase in enumerate((0, 1, 2, 3)):
        stamp = start + 180 + (i - 1) * 60 if i > 1 else start + 180 + i
        base = start + 180 if i < 2 else stamp
        engine.send(stamp, bundle(stamp, lambda tf: oz_feed(base, phase, tf)))
    return engine.alerts()
