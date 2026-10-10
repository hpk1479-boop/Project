"""What a replay engine carries forward, made comparable across two replays (수정본168).

A replay split into blocks starts every block but the first a few days early (the warm-up), so
its state at the block's start only approximates a continuous replay. The block before it keeps
replaying past its own end; where both hold the same state after the same input, every later input
gives both the same decisions and alerts, and from there the later block is the continuous replay.

This module turns that state into plain data and a digest. Everything reachable from the engine's
consumer and processor states, its board and its scheduler is compared, except what the rules below
name, each with the reason it cannot change a later decision or alert:

  OMIT      statistics, caches recomputed from their inputs, per-event bindings, file paths
  RELATION  run-wide counters: only their relation to the stored copies matters
  IDENTITY  ids made from a counter (FactStream.prepare): the counter differs by where a run began
  HORIZON   history that can no longer match: older than the longest any later decision looks back

A value left in only delays the join (the earlier block replays longer); a value wrongly left out
could join two states that act differently, so every rule states why it cannot.

Outside the engine, the input side is not compared. STAFF rebuilds snapshots from the recordings
alone, and a reader that starts at a keyframe continues as one that read from the beginning (the
keyframe premise of capture_start='keyframe'); what reaches the engine is its board, compared here.
The runner reads up to 8 inputs ahead of the engine, so at a probe STAFF and the input selection
(_select_batch's carried snapshots) are as far ahead as the batch happens to reach: a position that
depends on where a replay began, not on the stream.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
import hashlib
import json
from pathlib import PurePath
import threading
import types

import numpy as np

_SKIP_TYPES = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType,
               types.BuiltinMethodType, type, types.GeneratorType, types.CodeType, types.FrameType,
               threading.local, threading.Thread, threading.Event, threading.Condition)
_SKIP_NAMES = frozenset(('lock', 'RLock', '_RLock', 'Condition', 'Event', 'Thread', 'TextIOWrapper',
                         'BufferedWriter', 'BufferedReader', 'ReferenceType', 'Semaphore',
                         'BoundedSemaphore', 'Logger', 'socket', 'partial', 'method-wrapper'))

# OMIT: whole objects, by class name (any class of the object's MRO).
OMIT_TYPES = {
    'BoardView': 'a view of the board for one event; every event binds a new one (board.py)',
    'EventFacts': 'Fact DAG cache: a missing value is recomputed from the snapshot (facts.py)',
    'LevelStore': 'external_levels() of the current closed bars, recomputed when they change (sweep_levels.py)',
    'BoardFrames': 'legacy frames rebuilt for each board signature (event_composition.legacy_frames)',
    'ArrayFactFrame': 'Facts of one view, rebuilt from the view at every new bar (indicator_facts_numpy.py)',
    'StructureStore': 'FVG structure() of the closed bars, recomputed when they change (fvg_structure.py)',
}

# OMIT: fields, by (class name, field).
OMIT_FIELDS = {
    ('OZRuntime', 'evaluations'): 'statistic',
    ('OZRuntime', 'views_created'): 'statistic',
    ('OZRuntime', 'external_revision'): 'decides only when external_memory is encoded again',
    ('OZRuntime', 'external_memory'): 'JSON encoding of ExternalLiquidityController, compared itself',
    ('ExternalLiquidityController', 'revision'): 'change counter for that encoding',
    ('ExternalLiquidityController', '_fingerprint'): 'the state at the last change, for that counter',
    ('OZWatchController', '_revision'): 'compared only with OZProfile.watch_generation (RELATION there)',
    ('SweepRuntime', 'board'): 'per-event binding (SweepRuntime.bind)',
    ('SweepRuntime', 'manager'): 'per-event binding; its sequence is the stream counter',
    ('SweepRuntime', 'events'): 'per-event binding',
    ('SweepRuntime', '_views'): 'per-event memo',
    ('SweepRuntime', '_event_levels'): 'per-event memo',
    ('SweepRuntime', '_filtered'): 'cache of a pure filter, checked by identity of its input (160)',
    ('LiquidityDetector', '_synced_ids'): 'the level ids last synced; the same ids change nothing (160)',
    ('IntentPort', '_step_ids'): 'memo keyed by object id',
    ('IntentPort', '_publication'): 'observation cache key of one publication',
    ('IntentPort', '_observation_cache'): 'observations of one publication',
    ('IntentPort', '_pure_frame'): 'pure observation cache (156)',
    ('IntentPort', '_pure_now'): 'pure observation cache (156)',
    ('IntentPort', '_pure_cache'): 'pure observation cache (156)',
    ('IntentPort', '_advance_frame'): 'advance reuse within one publication and time (157)',
    ('IntentPort', '_advance_now'): 'advance reuse within one publication and time (157)',
    ('IntentPort', '_advanced'): 'advance reuse within one publication and time (157)',
    ('IntentPort', '_reuse_plans'): 'derived from the recipe only',
    ('IntentPort', '_fact_versions'): 'Fact counts, read only as keys of the two caches above',
    ('IntentPort', '_watch_ids'): 'memo of _dependency_id()',
    ('IntentPort', '_metric_atr'): 'memo of a recipe property',
    ('IntentPort', 'metric_frames'): 'ArrayFactFrame cache keyed by publication',
    ('IntentPort', 'levels'): 'LevelStore cache',
    ('IntentPort', 'api'): 'reference to the manager',
    ('IntentPort', 'manager'): 'reference to the manager',
    ('IntentPort', 'oz_sequence'): 'RELATION: OZ tokens are compared relative to it',
    ('IntentMachine', 'sequence'): 'enters only the ids of alerts (port.py _intent_id(..., now, sequence))',
    ('NotificationPort', 'kernel'): 'reference to the kernel',
    ('NotificationPort', 'sequence'): 'message token counter: tokens are ids',
    ('NotificationPort', 'deliveries'): 'dedup of one parent event sent again; see ComposerManager._receipts',
    ('NotificationPort', 'delivery_times'): 'expiry of deliveries',
    ('NotificationPort', 'expiry'): 'expiry of deliveries',
    ('CompositionKernel', '_cached_frames'): 'BoardFrames cache',
    ('CompositionKernel', '_cached_signature'): 'its key, made of object ids',
    ('CompositionKernel', 'frames'): 'per-event binding',
    ('CompositionKernel', 'board'): 'per-event binding',
    ('CompositionKernel', 'current_event'): 'per-event binding',
    ('ComposerManager', '_receipts'): (
        'dedup of one event delivered twice. Fact events carry a stream position (unique), and an OZ '
        'alert id repeats only for the same timeframe, direction and B0, which OZProfile.alert_keys '
        '(HORIZON, compared) blocks first'),
    ('ComposerManager', '_fact_revisions'): 'stale-Fact check against later positions of the same stream',
    ('ComposerManager', '_oz_dispatch_transaction'): (
        'set during one OZ dispatch and reset to None (composer_oz_dispatch); read with getattr(..., None), '
        'so between events None and never set are the same'),
    ('WatchMAStore', 'entries'): 'MA values of the history below, recomputed when its marker changes',
    ('WatchMAStore', 'closed_entries'): 'closed MA values, recomputed when their inputs differ',
    ('WatchMAStore', 'recurrences'): 'EWM prefix cache (recurrence.py)',
    ('WatchMAStore', 'versions'): 'history rebuild counter, read only by the entries cache marker',
    ('WatchMAStore', 'computations'): 'statistic',
    ('Collector', 'events'): 'OZ events of the last publication (OZ_STATE __board__ events, read once)',
    ('FVGRuntime', 'board'): 'per-event binding (FVGRuntime.bind)',
    ('FVGRuntime', 'manager'): 'per-event binding; its sequence is the stream counter',
}

# OMIT: mapping keys anywhere.
OMIT_KEYS = {
    'issued_at': 'command metadata; builds a watch id only when a command has none (controllers.add_manual)',
    'identity_sequence': 'uuid4() counter of domain_clock; uuids are ids',
    'publication': ('engine_seq or board.publication_token (an object id) of one publication; read only '
                    'against the same publication (processor boards, IntentPort._percentile)'),
}

# Published by a processor for its consumers at that publication only (SweepConsumer, OZConsumer,
# FVGConsumer and OZRuntime._market read them when publication == event.engine_seq).
BOARD_ONCE = ('publication', 'events', 'external_events', 'external_memory')


def _skip(value):
    if isinstance(value, _SKIP_TYPES):
        return True
    return type(value).__name__ in _SKIP_NAMES


def _fields(value):
    found = {}
    if hasattr(value, '__dict__'):
        found.update(vars(value))
    for klass in type(value).__mro__:
        for slot in getattr(klass, '__slots__', ()):
            if isinstance(slot, str) and slot not in found and not slot.startswith('__') and hasattr(value, slot):
                found[slot] = getattr(value, slot)
    return found


def _names(value):
    return [klass.__name__ for klass in type(value).__mro__]


# A stored token that can never match again: the same as no token (Canon.step_token).
_DEAD = object()


class _Plain:
    """Data a rule already made plain."""
    def __init__(self, data):
        self.data = data


class Canon:
    """Plain data of one engine state; a value reached twice is written once and then referred to."""

    def __init__(self, engine, timeframes=None):
        self.engine = engine
        self.timeframes = None if timeframes is None else frozenset(timeframes)
        self.seen = {}
        self.alive = []        # keeps every visited object alive, so no id is reused during the walk
        self.oz_base = None    # IntentPort.oz_sequence of the port being walked
        self.oz_current = None  # its OZ tokens a later observation can still carry
        # Rules for plain containers that several owners share: they apply on any path.
        self.omit_ids = {}
        self.replace_ids = {}
        self.prepare()

    def prepare(self):
        for state in self.engine.strategy_state.values():
            for kernel in (state.get('kernels') or {}).values() if isinstance(state, Mapping) else ():
                providers = getattr(getattr(kernel, 'manager', None), '_strategy_state_providers', {}) or {}
                for provider in providers.values():
                    if 'IntentPort' in _names(provider):
                        # The shared metric cache holds (board.publication_token, ArrayFactFrame) pairs.
                        self.omit_ids[id(provider.metric_frames)] = 'ArrayFactFrame cache keyed by publication'
        fvg = self.engine.processor_state.get('FVG_STATE', {}).get('runtime')
        if fvg is not None and isinstance(getattr(fvg, '_seen_created', None), set):
            self.replace_ids[id(fvg._seen_created)] = lambda: self.seen_created(fvg)

    def seen_created(self, fvg):
        """HORIZON: FVGRuntime.process_watch_result looks a zone up only when its fvg_time equals a newly
        closed bar's time, later than the last closed time of its feed; entries up to that time can
        never be looked up again."""
        kept = set()
        for zone_id in fvg._seen_created:
            parts = str(zone_id).split('|')
            try:
                last = fvg._last_closed_time.get((parts[0], parts[1]))
                created = float(parts[3])
            except (IndexError, ValueError):
                last = created = None
            if last is None or created is None or created > float(last):
                kept.add(zone_id)
        return kept

    # ---- board helpers for HORIZON rules -------------------------------------------------------
    def bar_time(self, symbol, tf, back):
        """Open time of the bar `back` bars before the forming one, or None when unknown."""
        snapshot = self.engine.board._feeds.get((symbol, tf))
        if snapshot is None or len(snapshot.time) <= back:
            return None
        return float(snapshot.time[-1 - back])

    # ---- the walk ------------------------------------------------------------------------------
    def __call__(self, value):
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if value == value and value not in (float('inf'), float('-inf')) else repr(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {'bytes': hashlib.sha256(bytes(value)).hexdigest()}
        if isinstance(value, np.generic):
            return self(value.item())
        if isinstance(value, np.ndarray):
            array = np.ascontiguousarray(value)
            if array.dtype.hasobject:
                return {'ndarray_objects': [self(item) for item in array.ravel().tolist()]}
            return {'ndarray': [str(array.dtype), list(array.shape), hashlib.sha256(array.view(np.uint8)).hexdigest()]}
        if isinstance(value, PurePath):
            return '<path>'
        if isinstance(value, _Plain):
            return value.data
        if _skip(value):
            return None
        key = id(value)
        if key in self.seen:
            return {'ref': self.seen[key]}
        self.seen[key] = len(self.seen)
        self.alive.append(value)
        if key in self.omit_ids:
            return {'omitted': self.omit_ids[key]}
        if key in self.replace_ids:
            replacement = self.replace_ids[key]()
            self.alive.append(replacement)
            return self(replacement)
        if isinstance(value, Mapping):
            return self.mapping(value)
        if isinstance(value, (list, tuple, deque)):
            return [self(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return {'set': sorted(json.dumps(self(item), sort_keys=True, default=str) for item in value)}
        names = _names(value)
        omitted = next((n for n in names if n in OMIT_TYPES), None)
        if omitted:
            return {'omitted': omitted}
        special = next((getattr(self, 'object_' + n) for n in names if hasattr(self, 'object_' + n)), None)
        if special is not None:
            return special(value)
        return self.object(value, names)

    @staticmethod
    def key(key):
        return key if isinstance(key, str) else json.dumps(key, sort_keys=True, default=repr)

    def mapping(self, value, omit=()):
        rows = []
        for key, item in value.items():
            if key in OMIT_KEYS or key in omit:
                continue
            rows.append((self.key(key), item))
        if 'fact_revision' in value and 'event_id' in value:
            rows = self.fact_event(value, rows)
        rows.sort(key=lambda row: row[0])
        return {'map': [[name, self(item)] for name, item in rows]}

    def fact_event(self, event, rows):
        """IDENTITY: a FactStream position and the id made from it (durable_protocol.FactStream.prepare).
        The position is compared only with later positions of the same stream (_handle_fact_event),
        so stored copies act alike wherever the stream's count began."""
        revision = event.get('fact_revision')
        try:
            generation, sequence = revision
            from durable_protocol import identity
            derived = event.get('event_id') == identity(event.get('strategy'), generation, sequence)
        except (TypeError, ValueError, ImportError):
            return rows
        if not derived:
            return rows
        return [(name, item) for name, item in rows if name not in ('fact_revision', 'event_id')]

    def object(self, value, names, *, omit=(), replace=None):
        fields = {}
        for name, item in _fields(value).items():
            if name in omit or any((n, name) in OMIT_FIELDS for n in names):
                continue
            fields[name] = item
        replace = replace or {}
        rows = []
        for name in sorted(fields):
            rows.append([name, replace[name]() if name in replace else self(fields[name])])
        return {'object': type(value).__name__, 'fields': rows}

    # ---- RELATION / HORIZON rules, by class ---------------------------------------------------
    def object_OZProfile(self, profile):
        from oz_engine.common import _profile_key
        def generation():
            # RELATION: OZRuntime._market resets the profile's watch view when the controller's
            # revision differs from it; only whether they differ acts.
            current = profile.watch._revision.get(_profile_key(profile.validation_mode, profile.trigger_mode))
            return {'watch_current': profile.watch_generation == current}
        def alert_keys():
            # HORIZON: a key (tf, direction, B0) matches only a completing candidate with that B0,
            # and a candidate whose B0 is more than max_bars bars old is cancelled first
            # (OZProfile._check_base_invalidation). Keys older than that can never match again.
            kept = []
            for key in profile.alert_keys:
                tf, _, b0 = key
                cutoff = self.bar_time(profile.symbol, tf, profile.max_bars + 3)
                if cutoff is None or b0 is None or float(b0) >= cutoff:
                    kept.append(key)
            return {'set': sorted(json.dumps(self(item), sort_keys=True, default=str) for item in kept)}
        return self.object(profile, _names(profile), replace={'watch_generation': generation,
                                                              'alert_keys': alert_keys})

    def object_ExternalLiquidityController(self, controller):
        def invalidated():
            # HORIZON: an entry blocks a touch of its level with event_time <= its time. A touch's
            # event_time is the open time of the closed bar it was seen on (LiquidityDetector.process),
            # never earlier than the current last closed bar of its watch's timeframe.
            kept = {}
            for key, stamp in controller._invalidated.items():
                watch = str(key).split('|', 1)[0]
                spec = controller._specs.get(watch)
                cutoff = None if spec is None or '|' not in str(key) else self.bar_time(spec.symbol, spec.source_tf, 1)
                if cutoff is None or float(stamp) >= cutoff:
                    kept[key] = stamp
            return self.mapping(kept)
        return self.object(controller, _names(controller), replace={'_invalidated': invalidated})

    def object_OZRuntime(self, runtime):
        def memory():
            files = {}
            for name, text in runtime.memory.items():
                if name == 'oz_outgoing_events.json':
                    kept = self.outgoing(runtime, text)
                    # No record left reads as no file: Records._load gives empty records for both.
                    if kept is not None:
                        files[name] = kept
                else:
                    files[name] = text
            return self.mapping(files)
        return self.object(runtime, _names(runtime), replace={'memory': memory})

    def outgoing(self, runtime, text):
        """HORIZON: DomainEventSender re-sends a saved alert for the same event id, which is made of the
        timeframe, direction and B0 (OZProfile._evaluate_candidate); see OZProfile.alert_keys.
        None when no record can match again."""
        try:
            data = json.loads(text)
            records = data['records']
        except (ValueError, TypeError, KeyError):
            return text
        bars = int(runtime.config.get('MAX_BARS_AFTER_B0', '10')) + 3
        kept = {}
        for key, event in records.items():
            b0 = event.get('b0_time') if isinstance(event, dict) else None
            cutoff = None if b0 is None else self.bar_time(event.get('symbol'), event.get('source_tf'), bars)
            if cutoff is None or float(b0) >= cutoff:
                kept[key] = event
        return _Plain({'outgoing': self.mapping(kept)}) if kept else None

    def object_CompositionKernel(self, kernel):
        def memory():
            # The copy of OZ_STATE's external_memory (event_composition.step); see OZRuntime.
            return self.mapping(kernel.memory, omit=('oz_external_liquidity_state.json',))
        return self.object(kernel, _names(kernel), replace={'memory': memory})

    def object_Records(self, records):
        # Durable records: their content, without the file path and lock (durable_protocol.Records).
        return {'records': self(records._records), 'times': self(records._times),
                'unstamped': self(records._unstamped), 'clock': records._clock}

    def object_WatchMAStore(self, store):
        # Resident MA history (WatchMAStore._history keeps rows older than one snapshot).
        return self.object(store, _names(store))

    def object_IntentPort(self, port):
        outer = self.oz_base, self.oz_current
        self.oz_base = port.oz_sequence
        # The OZ tokens a later observation can still carry: the latest OZ event of each key.
        self.oz_current = {value[0] for value in port.oz_events.values() if isinstance(value, tuple) and value}
        try:
            def oz_events():
                # RELATION: an OZ_ALERT observation's token is this sequence number (port._observe);
                # tokens are compared only with tokens the same port stored (IntentMachine.used,
                # _restart_tokens), which are made relative below.
                rows = {key: (('oz', value[0] - port.oz_sequence), *value[1:]) if isinstance(value, tuple) and value else value
                        for key, value in port.oz_events.items()}
                return self.mapping(rows)
            def restart_tokens():
                rows = {}
                for (branch, index), token in port._restart_tokens.items():
                    machine = next((m for m in port.machines if m.branch == branch), None)
                    restart = (machine.meaning.get('lifecycle') or {}).get('restart_on', []) if machine else []
                    value = self.step_token(restart[index] if index < len(restart) else None, token, stored=True)
                    if value is not _DEAD:
                        rows[(branch, index)] = value
                return self.mapping(rows)
            def observations(name):
                def walk():
                    rows = {}
                    for branch, items in getattr(port, name).items():
                        machine = next((m for m in port.machines if m.branch == branch), None)
                        steps = (machine.meaning.get('steps' if name == 'observations' else 'final_conditions', [])
                                 if machine else [])
                        rows[branch] = [self.step_observation(steps[i] if i < len(steps) else None, item)
                                        for i, item in enumerate(items)]
                    return self.mapping(rows)
                return walk
            return self.object(port, _names(port), replace={
                'oz_events': oz_events, '_restart_tokens': restart_tokens,
                'observations': observations('observations'),
                'final_observations': observations('final_observations')})
        finally:
            self.oz_base, self.oz_current = outer

    def step_token(self, step, token, *, stored=False):
        """A step's token (port._observe: (step id, the matched timeframes' tokens)), comparable:
        RELATION  OZ_ALERT tokens are port.oz_sequence numbers; relative to the current one.
        HORIZON   a stored OZ_ALERT token holding a number that is no longer the latest event of any
                  key can never equal a later observation's token (numbers only grow, port.oz_events
                  keeps the latest per key): it acts as no token at all (_DEAD). One that may recur
                  is compared: it keeps a machine from taking or replacing with that event again.
        IDENTITY  PERCENTILE tokens hold board.publication_token, an object id. Live (forming-bar)
                  tokens also hold the poll time, and a closed-bar event matches only at the first
                  publication of a bar (_percentile: fresh), so that id never tells two events apart."""
        kind = (step or {}).get('kind')
        if kind not in ('OZ_ALERT', 'PERCENTILE_OUT', 'PERCENTILE_OUT_IN') or not (
                isinstance(token, tuple) and len(token) == 2 and isinstance(token[1], tuple)):
            return token
        if kind == 'OZ_ALERT':
            numbers = [item for item in token[1] if isinstance(item, int)]
            if stored and self.oz_current is not None and any(item not in self.oz_current for item in numbers):
                return _DEAD
            inner = tuple(('oz', item - self.oz_base) if isinstance(item, int) and self.oz_base is not None else item
                          for item in token[1])
        else:
            inner = tuple(item[:4] + item[5:] if isinstance(item, tuple) and len(item) == 6 else item for item in token[1])
        return (token[0], inner)

    def step_observation(self, step, observation):
        if not isinstance(observation, dict) or 'token' not in observation:
            return observation
        token = self.step_token(step, observation['token'])
        return observation if token is observation['token'] else {**observation, 'token': token}

    def object_IntentMachine(self, machine):
        def used():
            steps = machine.meaning.get('steps', [])
            rows = {}
            for index, token in machine.used.items():
                value = self.step_token(steps[index] if isinstance(index, int) and index < len(steps) else None, token,
                                        stored=True)
                if value is not _DEAD:
                    rows[index] = value
            return self.mapping(rows)
        return self.object(machine, _names(machine), replace={'used': used})

    def object_Scheduler(self, scheduler):
        # RELATION: _order only orders timers due at the same time (Scheduler.request/due); the heap's
        # list layout follows the push history, while due() pops in (due, order) order.
        base = scheduler._order
        queues = {symbol: sorted(((due, order - base, strategy, request) for due, order, strategy, request in queue),
                                 key=lambda row: row[:2])
                  for symbol, queue in scheduler._queues.items()}
        return {'object': 'Scheduler', 'times': self(scheduler._times), 'queues': self(queues)}


def engine_parts(engine, *, timeframes=None):
    """The state by part: each consumer and processor, the board, timers and health.

    The replay's input selection (runner: _select_batch's `previous`) is not compared: it keeps a
    bundle by comparing it with the previous input of the same feed in the stream, and both replays
    read the same stream, so once a block has seen every feed (its warm-up) it chooses as the continuous
    replay does; what `previous` holds at a probe only depends on where the 8-input batches fell."""
    def limited(mapping):
        if timeframes is None:
            return mapping
        return {key: value for key, value in mapping.items() if key[1] in timeframes}
    parts = {}
    for name, state in engine.strategy_state.items():
        parts['strategy:' + name] = {key: value for key, value in state.items()
                                     # Condition-key counter of emitted signals (composition_consumer).
                                     if not (name == 'COMPOSER' and key == 'output_sequence')}
    for name, state in engine.processor_state.items():
        value = dict(state)
        if isinstance(value.get('__board__'), Mapping):
            value['__board__'] = {key: item for key, item in value['__board__'].items() if key not in BOARD_ONCE}
        if isinstance(value.get('stream'), Mapping):
            # The FactStream counter: positions are ids (IDENTITY in Canon.fact_event).
            value['stream'] = {key: item for key, item in value['stream'].items() if key != 'sequence'}
        parts['processor:' + name] = value
    parts['board'] = {'feeds': limited(engine.board._feeds), 'observed': limited(engine.board._observed),
                      'health': engine.board._health}
    parts['scheduler'] = engine.scheduler
    parts['engine'] = {'failures': engine.failures, 'disabled': engine.disabled, 'degraded': engine.degraded,
                       'status': engine.status, 'configured': engine._configured}
    return parts


def plain_parts(engine, **kwargs):
    canon = Canon(engine, kwargs.get('timeframes'))
    parts = engine_parts(engine, **kwargs)
    return {name: canon(parts[name]) for name in sorted(parts)}


def digests(engine, **kwargs):
    """(digest of the whole state, {part: digest}); one walk shares references across parts."""
    plain = plain_parts(engine, **kwargs)
    result = {name: hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), default=str).encode())
              .hexdigest() for name, data in plain.items()}
    whole = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return whole, result
