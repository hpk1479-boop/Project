"""160: SWEEP_STATE shares one publication's views and levels across its watches, parses a watch's
spec only when its payload changes and skips a level sync whose ids did not change. Each must give
exactly what the full computation gives.
"""
from datetime import datetime, timezone
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import durable_protocol
import monitor_OZ
import staff_compat
import strategy_SWEEP
from event_engine import EventEngine, IngressSequencer, Kind
from event_engine.market import COLUMNS, select
from event_engine.model import FeedSnapshot
from event_engine import sweep_runtime
from event_engine.sweep_runtime import LiquidityDetector, SweepRuntime, REQUIRED_INDS
from event_engine.sweep_state import SweepConsumer, SweepProcessor
from strategy_SWEEP import filter_external_levels


def epoch(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def snapshot(times, high, low, seq):
    values = np.full((len(times), len(COLUMNS)), 100., dtype=float)
    values[:, COLUMNS['high']] = high
    values[:, COLUMNS['low']] = low
    values[:, COLUMNS['open']] = (high + low) / 2
    values[:, COLUMNS['close']] = (high + low) / 2
    return FeedSnapshot(np.asarray(times, dtype='int64'), np.ones(len(times), dtype='int64'), values, seq, 'test', {})


SECONDS = {'1m': 60, '5m': 300, '15m': 900, '4h': 14400, '8h': 28800, '1d': 86400}
START = epoch('2026-09-21T06:00:00')


def bundle(i, rows=200):
    """Feeds at the i-th one-minute publication: each frame's last bar is forming."""
    now = START + i * 60
    feeds = {}
    for tf, step in SECONDS.items():
        last = now // step * step
        times = last - step * np.arange(rows)[::-1]
        phase = times / step
        high = 100 + 12 * np.sin(phase / 7.0) + 3 * np.sin(phase * 1.3) + 2
        low = high - 4
        # The forming bar keeps moving inside its interval.
        high = high.copy(); low = low.copy()
        high[-1] += 6 * np.sin(i * 0.9); low[-1] -= 6 * np.cos(i * 0.7)
        feeds[tf] = snapshot(times, high, low, i + 1)
    return feeds


WATCHES = [
    {'watch_id': 'a', 'symbol': 'XAUUSD+', 'source_tf': '1m', 'levels': ['PDH', 'PDL', 'PREV_4H_HIGH', 'PREV_4H_LOW']},
    {'watch_id': 'b', 'symbol': 'XAUUSD+', 'source_tf': '5m', 'levels': ['PREV_8H_HIGH', 'PREV_8H_LOW', 'PWH', 'PWL']},
    {'watch_id': 'c', 'symbol': 'XAUUSD+', 'source_tf': '15m', 'session_london': '1600-2100',
     'session_newyork': '2200-0300'},
    {'watch_id': 'd', 'symbol': 'XAUUSD+', 'source_tf': '1m', 'session_london': '1600-2100',
     'session_newyork': '2200-0300', 'levels': ['PREV_SESSION_HIGH', 'PREV_SESSION_LOW', 'PDH', 'PDL']},
    {'watch_id': 'e', 'symbol': 'XAUUSD+', 'source_tf': '4h'},
]


# ---- the computation before 160, kept here as the reference --------------------------------------

def reference_load(self, spec):
    views = {tf: select(self.board, spec.symbol, tf, REQUIRED_INDS) for tf in self._required_tfs(spec)}
    if any(v is None for v in views.values()):
        return None, []
    levels = self.levels.get(views, spec.session_london, spec.session_newyork, spec.symbol)
    self._source_health[spec.watch_id] = sweep_runtime.health(views, REQUIRED_INDS)
    return views[spec.source_tf], filter_external_levels(levels, spec.levels)


def reference_sync(self, levels):
    active_ids = {lv["id"] for lv in levels}
    removed = [state for key, state in self.states.items() if key not in active_ids]
    if removed:
        self._dirty = True
    self.states = {key: state for key, state in self.states.items() if key in active_ids}
    for level in levels:
        if level["id"] not in self.states:
            self.states[level["id"]] = self._new_state(level)
            self._dirty = True
    return removed


def reference_spec(self, wid, payload):
    return self.module.SweepSpec.from_payload(payload)


def run(steps, changes):
    engine = EventEngine(IngressSequencer(), [SweepConsumer()],
                         [SweepProcessor(strategy_SWEEP, durable_protocol, staff_compat, monitor_OZ,
                                         initial=tuple(dict(w) for w in WATCHES))])
    record = []
    for i in range(steps):
        if i in changes:
            changes[i](engine.processor_state['SWEEP_STATE'].setdefault('watches', {}))
        engine.ingress.post(Kind.MARKET_BUNDLE, source='test', source_seq=i, source_time=(START + i * 60 + 1) * 1000,
                            payload={'symbol': 'XAUUSD+', 'feeds': bundle(i)})
        engine.run()
        state = engine.processor_state['SWEEP_STATE']
        core = state['runtime']
        record.append(repr((state['__board__']['events'], state['__board__']['external_events'],
                            {w: d.export_state() for w, d in sorted(core.detectors.items())},
                            core._fingerprints, core._source_health, core.levels.computations)))
    assert not engine.error_log
    return record


def replace_levels(watches):
    watches['a'] = dict(watches['a'], levels=['PWH', 'PWL'])


def edit_in_place(watches):
    watches['b']['levels'] = ['PDH', 'PDL']        # the same dict object, new content


def drop_and_add(watches):
    watches.pop('e')
    watches['f'] = {'watch_id': 'f', 'symbol': 'XAUUSD+', 'source_tf': '5m'}


CHANGES = {20: replace_levels, 35: edit_in_place, 50: drop_and_add}


def test_shared_views_levels_and_specs_equal_the_full_computation(monkeypatch):
    steps = 90
    optimized = run(steps, dict(CHANGES))
    with monkeypatch.context() as patch:
        patch.setattr(SweepRuntime, '_load', reference_load)
        patch.setattr(LiquidityDetector, '_sync_levels', reference_sync)
        patch.setattr(SweepProcessor, '_spec', reference_spec)
        reference = run(steps, dict(CHANGES))
    assert optimized == reference
    # The data really moves: touches happen and the levels are recomputed as bars close.
    assert sum("'SWEEP_TOUCH'" in step for step in reference) >= 3
    assert reference[-1] != reference[-2]


def test_watches_of_one_publication_share_one_level_computation(monkeypatch):
    calls = []
    original = sweep_runtime.LevelStore.get

    def counted(self, *args, **kwargs):
        calls.append(args[1:3])
        return original(self, *args, **kwargs)
    monkeypatch.setattr(sweep_runtime.LevelStore, 'get', counted)
    run(3, {})
    # Two session settings ('' and the London/New York pair) per publication, not five watches.
    assert len(calls) == 6


def test_a_changed_payload_is_parsed_again():
    processor = SweepProcessor(strategy_SWEEP, durable_protocol, staff_compat, monitor_OZ)
    payload = {'watch_id': 'w', 'symbol': 'XAUUSD+', 'source_tf': '1m', 'levels': ['PDH']}
    first = processor._spec('w', payload)
    assert processor._spec('w', payload) is first
    payload['levels'].append('PDL')                 # nested in-place change
    second = processor._spec('w', payload)
    assert second is not first and set(second.levels) >= {'PDH', 'PDL'}
    assert processor._spec('w', dict(payload, source_tf='5m')).source_tf == '5m'


def test_level_sync_skips_only_unchanged_ids():
    spec = strategy_SWEEP.SweepSpec('w', 'XAUUSD+', '1m')
    level = lambda i, d='SHORT': dict(id=str(i), direction=d, level_code='PDH', level_name='x', price=100. + i)
    fast, full = LiquidityDetector(spec), LiquidityDetector(spec)
    levels = [level(1), level(2)]
    sequences = [levels, levels, [level(1), level(2)], [level(2), level(1)], None, [level(2)], [], [level(3, 'LONG')]]
    for item in sequences:
        if item is None:
            levels.append(level(9))                  # the same list object, a new id
            item = levels
        assert fast._sync_levels(item) == reference_sync(full, item)
        assert fast.states == full.states and fast._dirty == full._dirty
