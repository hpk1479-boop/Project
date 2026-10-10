"""Offline fixtures for common-Recipe lifecycle checks: no network, files or engine threads.

A real IntentPort runs a real recipe over a multi-timeframe Board. Time only moves
when a test publishes, so every boundary is exact.
"""
import copy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from event_composer_domain import ComposerManager, SpecialPluginAPI
from event_composition import NotificationPort
from event_engine.model import FeedSnapshot
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe import registry
from strategy_recipe.contract import execution_plan
from strategy_recipe.port import IntentPort

COL = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}
SECONDS = {'1m': 60, '2m': 120, '3m': 180, '5m': 300, '6m': 360, '10m': 600, '12m': 720,
           '15m': 900, '20m': 1200, '30m': 1800, '1h': 3600}
SYMBOL = 'TEST'


def feed(tf, last_open, n=6, *, seq=1, **columns):
    """n bars ending with the forming bar opened at last_open; scalars or per-bar lists."""
    values = np.full((n, len(PIPE_VALUE_COLUMNS)), 2400., dtype=float)
    for name, value in columns.items():
        values[:, COL[name]] = value
    times = last_open + (np.arange(n, dtype=np.int64) - (n - 1)) * SECONDS[tf]
    return FeedSnapshot(times, np.ones(n, dtype=np.int64), values, seq, 'epoch', {})


class Board:
    def __init__(self):
        self.feeds = {}; self.health = {}; self.observed = {}; self.source_time = 0
        self._frame_cache = {}; self._token = 0; self.atr = {}; self.wonbi = {}
        self.oz_resources = {}      # (symbol, tf, validation, trigger, direction) -> B0 time of a live OZ cycle
    @property
    def publication_token(self): return self._token
    def snapshot(self, symbol, tf): return self.feeds[symbol, tf]
    def fact(self, name, symbol, tf):
        if name == 'ATR14_GENERAL': return self.atr[symbol, tf]
        if name == 'WONBI_BANDS': return self.wonbi[symbol, tf]
        raise KeyError(name)
    def processor(self, name):
        if name == 'OZ_STATE': return {'resources': self.oz_resources}
        raise KeyError(name)


def manager(config=None):
    m = ComposerManager.__new__(ComposerManager)
    m._lock = threading.RLock(); m._delivery_context = threading.local()
    m.chat_id = 'TEST_CHANNEL'; m.config = dict(config or {})
    m._special_shared_resources = {}; m._active_children = {}; m._config_chain_active = {}
    m._special_oz_event_handlers = {}; m._special_oz_registration_scopes = []
    m._special_subscription_providers = {}; m._special_fact_observers = {}
    m._strategy_state_providers = {}; m._special_watch_handlers = {}
    m.official_specs = {}; m.official_chain_specs = {}
    m._save_active_children_state_locked = lambda: None
    m.commands = []; m._push = m.commands.append
    m._watch_links_for_ids = lambda *args: []
    m._filter_config_chain_deadline_event = lambda e: (e, False)
    m.completions = []
    m.watch_orchestrator = NS(handle_oz_stage_event=lambda e: {'handled': False, 'all_internal': False},
        filter_deadline_event=lambda e: (e, False),
        complete_final_oz=lambda e, **kwargs: m.completions.append(e))
    kernel = NS(board=Board(), symbol=SYMBOL, timestamp=0, config={'TELEGRAM_CHAT_ID': m.chat_id},
        current_event={}, messages=[], manager=m, initial_files={})
    m.event_services = NS(kernel=kernel)
    m.notifier = NotificationPort(kernel)
    m.special_api = SpecialPluginAPI(m)
    m._time_policy = NS(allows=lambda filters: True)
    return m, kernel


class Harness:
    """A recipe's strategy on a quiet Board; publish() advances source time and polls."""

    def __init__(self, meaning, name='TEST', config=None, namespace=None):
        self.manager, self.kernel = manager(config)
        self.board = self.kernel.board
        self.port = IntentPort(self.manager, meaning, name, namespace or name)
        self.port.install()
        self.manager.commands.clear()

    @classmethod
    def special(cls, special_id, *, mutate=None, config=None):
        """The shipped recipe, single source Part1/program/SPECIAL, for one symbol, any time of day."""
        entry = registry.builtin_entries()[special_id]
        meaning = copy.deepcopy(entry['recipe']['strategy_intent'])
        meaning.update(symbols=[SYMBOL], time_filters=[], final_time_filters=0)
        if mutate: mutate(meaning)
        return cls(execution_plan(meaning)['meaning'], entry['name'], config, namespace=special_id)

    @property
    def machines(self): return self.port.machines

    def publish(self, now, feeds, *, atr=None, wonbi=None):
        """Make `feeds` the board at source time `now` (seconds) and run one poll."""
        board = self.board
        for tf, snapshot in feeds.items():
            board.feeds[SYMBOL, tf] = snapshot
            board.observed[SYMBOL, tf] = int(now * 1000)
            chosen = atr.get(tf) if isinstance(atr, dict) else atr
            if chosen is not None: board.atr[SYMBOL, tf] = np.full(len(snapshot.time), chosen) if np.isscalar(chosen) else chosen
            elif (SYMBOL, tf) not in board.atr: board.atr[SYMBOL, tf] = np.full(len(snapshot.time), 1.)
            if wonbi is not None and tf in wonbi:
                board.wonbi[SYMBOL, tf] = {key: np.full(len(snapshot.time), value) if np.isscalar(value) else value
                    for key, value in wonbi[tf].items()}
            elif (SYMBOL, tf) not in board.wonbi:
                board.wonbi[SYMBOL, tf] = {key: np.full(len(snapshot.time), 2400.) for key in
                    ('wonbi_mid', 'wonbi_upper', 'wonbi_lower')}
        board.source_time = int(now * 1000); board._frame_cache = {}; board._token += 1
        self.kernel.timestamp = int(now * 1000)
        self.port.poll()

    def parent_oz(self, step, tf, now, *, direction='LONG', b0_time=None, validation='NORMAL', trigger='BREAKER'):
        """A parent OZ event, delivered the way the OZ engine delivers it to the strategy."""
        b0_time = float(now - 60 if b0_time is None else b0_time)
        self.board.oz_resources[SYMBOL, tf, validation, trigger, direction] = b0_time
        self.port.handle_oz_event({'source_spec_id': self.port._dependency_id(SYMBOL, tf, 'OZ_SOURCE', step),
            'symbol': SYMBOL, 'source_tf': tf, 'validation_mode': validation, 'trigger_mode': trigger,
            'direction': direction, 'b0_time': b0_time, 'b0_price': 2399., 'event_time': float(now)})

    def final_oz(self, machine, now, **extras):
        """A finished OZ for `machine`'s watch, delivered through the real Composer output path."""
        from composer_oz_dispatch import dispatch
        source, watch = self.port._sid(machine), self.port._wid(machine)
        event = {'kind': 'FINAL_ALERT', 'strategy': 'OZ', 'source_spec_id': source, 'source_spec_ids': [source],
            'watch_ids': [watch], 'direction': machine.direction, 'symbol': SYMBOL,
            'source_tf': machine.final_tf or '1m', 'validation_mode': machine.meaning['final']['validation_mode'],
            'trigger_mode': machine.meaning['final']['trigger_mode'], 'current_price': 2400.25, 'grade': 'A',
            'indicators_text': 'PRICE·RSI·STO', 'trigger_name': 'BO_BREAK', 'event_time': float(now),
            'event_id': f'final-{now}', 'message': 'old watch message', **extras}
        return dispatch(self.manager, event)

    @property
    def messages(self): return [m['message'] for m in self.kernel.messages]

    def watch_commands(self):
        return [c for c in self.manager.commands if c.get('action') == 'MANUAL_WATCH']

    def cancel_commands(self):
        return [c for c in self.manager.commands if c.get('action') == 'CANCEL_MANUAL']
