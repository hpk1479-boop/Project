"""Common Recipe adapter to the existing Part1 Board/Fact/Output contracts.

No network, files, threads, or market-calculation formulas live here.
Part1 connections use its public SPECIAL plugin API; no methods are patched.
"""
from __future__ import annotations
import hashlib
import itertools
import logging
import math
import re
import operator
from .runtime import IntentMachine
from .alerts import render_strategy_alert, outermost_level
from .state_judge import (STATE_KINDS, candle_matches, ma_names, shape_matches, state_matches, state_ready,
                          touch_matches)


def _intent_id(*values):
    return hashlib.sha256(repr(values).encode('utf-8')).hexdigest()[:24]


class IntentPort:
    def __init__(self, manager, meaning, name, namespace):
        self.manager = manager; self.api = manager.special_api
        self.meaning, self.name, self.namespace = meaning, name, namespace
        from watch_array_facts import WatchMAStore
        from event_engine.sweep_levels import LevelStore
        # Share current generic Fact calculators across generated strategies.
        caches = self.api.shared_resource('canonical_intent_facts',
            lambda: {'ma': WatchMAStore(), 'levels': LevelStore(), 'metrics': {}})
        self.ma, self.levels = caches['ma'], caches['levels']
        self.metric_frames = caches.setdefault('metrics', {})
        self._metric_atr = {}
        self.percentile_events = caches.setdefault('percentile_events', {})
        self._step_ids = {}
        self._restart_tokens = {}
        self.baselines = {}; self.previous = {}; self.oz_events = {}; self.oz_sequence = 0
        self._publication = None; self._observation_cache = {}; self.fact_snapshots = {}; self.fact_sources = {}
        self._pure_frame = None; self._pure_now = None; self._pure_cache = {}
        self._fact_versions = {}    # (family, symbol, timeframe, watch) -> how many Facts of it have arrived
        self._watch_ids = {}        # (step id, symbol, timeframe, family) -> the step's watch on that timeframe
        # Machines advanced in this publication and time, by branch: what their observations depended on.
        self._advance_frame = None; self._advance_now = None; self._advanced = {}; self._reuse_plans = {}
        self.machines = []; self.observations = {}; self.final_observations = {}; self.matches = {}
        self.units = meaning.get('branches') or [meaning]
        own_symbol = self.api.market_context()[1]
        self.symbol = own_symbol
        for symbol in meaning['symbols']:
            if own_symbol and symbol != own_symbol: continue
            for unit in self.units:
                groups = [[s] for s in unit['steps']] if unit.get('global_combine') == 'INDEPENDENT' else [unit['steps']]
                for steps in groups:
                    direction = unit['final'].get('direction', unit['direction'])
                    if direction == 'SAME_AS_PREVIOUS_DIRECTION' or (direction == 'BOTH' and 'direction' not in unit['final']):
                        direction = next((s.get('_resolved_direction', s.get('direction')) for s in reversed(steps)
                            if s.get('_resolved_direction', s.get('direction')) in ('LONG', 'SHORT')), unit['direction'])
                    neutral = unit['final']['kind'] == 'NOTIFY' and steps and all(s['kind'] in ('SESSION_START', 'MA_PRICE_TOUCH') for s in steps)
                    directions = ('BOTH',) if neutral and direction == 'BOTH' else ('LONG', 'SHORT') if direction == 'BOTH' else (direction,)
                    for direction in directions:
                        choices = [[dict(s, tfs=[tf], tf_combine='ANY') for tf in s['tfs']]
                                   if s.get('tf_combine') == 'INDEPENDENT' else [s] for s in steps]
                        for selected in itertools.product(*choices):
                            item = dict(unit, steps=[dict(s, _split_price_direction=len(directions)>1)
                                if s['kind'] == 'MA_PRICE_CROSS' else s for s in selected])
                            if unit.get('global_combine') == 'INDEPENDENT': item['global_combine'] = 'ALL'
                            final_tfs = item['final'].get('tfs', [])
                            life = item.get('lifecycle') or {}
                            split = item['final'].get('tf_combine') == 'INDEPENDENT' or life.get('first_success') or (life.get('expires') or {}).get('tf') == 'FINAL'
                            for final_tf in final_tfs if split and final_tfs else (None,):
                                selected_item = dict(item, final=dict(item['final'], tfs=[final_tf])) if final_tf else item
                                machine = IntentMachine(selected_item, symbol, direction, len(self.machines))
                                machine.activation_at = self.api.market_context()[2]
                                machine.final_tf = final_tf or (final_tfs[0] if len(final_tfs) == 1 else None)
                                self.machines.append(machine)

    def install(self):
        # Keep the original engine's command dispatch. No direct processor calls.
        self.api.register_subscription_provider(self.namespace, self.desired_subscriptions)
        self.api.register_fact_observer(self.namespace, self.handle_fact)
        self.api.register_strategy_state_provider(self.namespace, self)
        own_symbol = self.api.market_context()[1]
        desired_oz = set()
        for symbol in self.meaning['symbols']:
            if own_symbol and symbol != own_symbol: continue
            for step in self._all_steps():
                for tf in self._subscription_tfs(step):
                    if step['kind'].startswith('FVG'):
                        self.api.request_watch({'action': 'FVG_WATCH', 'watch_id': self._dependency_id(symbol, tf, 'FVG', step),
                                            'symbol': symbol, 'source_tf': tf})
                    elif step['kind'] == 'EXTERNAL_LIQUIDITY_TOUCH':
                        self.api.request_watch(self._sweep_payload(
                            step, symbol, tf, self._dependency_id(symbol, tf, 'SWEEP', step), self.api))
                    elif step['kind'] == 'OZ_ALERT':
                        sid = self._dependency_id(symbol, tf, 'OZ_SOURCE', step)
                        desired_oz.add(sid)
                        self.api.register_oz_handler(sid, self)
                        self.api.ensure_oz_watch({'action': 'MANUAL_WATCH', 'watch_id': sid,
                            'symbol': symbol, 'timeframes': [tf], 'direction': None,
                            'persistent': True, 'request_chat_id': None,
                            'watch_owner': 'KIM',
                            'source_spec_id': sid, 'source_name': self.name,
                            'validation_mode': step['validation_mode'], 'trigger_mode': step['trigger_mode']},
                            compare_keys=('symbol', 'timeframes', 'validation_mode', 'trigger_mode', 'watch_owner', 'request_chat_id', 'source_name'))
        self.api.register_watch_handler(self.namespace, self)
        for machine in self.machines:
            self.api.register_oz_handler(self._sid(machine), self)
            if machine.meaning['final']['kind'] == 'OZ' and machine.active and machine.after_ready and not machine.disabled:
                desired_oz.add(self._wid(machine)); self._arm_watch(machine)
        old = self.api.snapshot_oz_watches(symbol=own_symbol, watch_id_prefix=self.namespace + ':')
        stale = [watch_id for watch_id, _ in old if watch_id not in desired_oz]
        if stale: self.api.cancel_oz_watches(stale)

    def _arm_watch(self, machine):
        direction = machine.meaning['final'].get('direction', machine.direction)
        if direction in ('BOTH','SAME_AS_PREVIOUS_DIRECTION'): direction = machine.direction
        final_tfs = [self._bound_tf(tf, machine) for tf in machine.meaning['final']['tfs']]
        if any(tf in (None,'SOURCE','FINAL') for tf in final_tfs):
            raise ValueError('최종 OZ의 시간봉 참조가 선행 사건에 연결되지 않았습니다.')
        if len(final_tfs) == 1: machine.final_tf = final_tfs[0]
        link = self._level_link(machine)
        if link is None: return
        payload = {'action':'MANUAL_WATCH','watch_id':self._wid(machine),
            'symbol':machine.symbol,'timeframes':final_tfs,'direction':direction,'persistent':True,
            'request_chat_id':None,'watch_owner':'KIM',
            'source_spec_id':self._sid(machine),'source_name':self.name,
            'validation_mode':machine.meaning['final']['validation_mode'],
            'trigger_mode':machine.meaning['final']['trigger_mode'], **link}
        self.api.ensure_oz_watch(payload, compare_keys=('symbol','timeframes','direction','validation_mode',
            'trigger_mode','watch_owner','request_chat_id','source_name','external_watch_id','external_source_tf'))

    def _level_link(self, machine):
        """Tie the final OZ to the external-liquidity touch the strategy named.

        The OZ engine's own gate then applies the strategy's ATR rule to the level,
        including the final OZ's low/high distance. Nothing is judged here.
        Returns None when the named touch was never recorded: the watch is then not opened at
        all (an OZ without its gate must never alert) and the inconsistency is logged, instead
        of raising inside the poll that every strategy of the symbol shares.
        """
        gate = machine.meaning['final'].get('level_gate')
        if not gate: return {}
        touch = machine.captures.get(gate['ref'])
        if not touch or not touch.get('watch_id'):
            logging.error('[Recipe %s] 최종 OZ에 연결할 외부유동성 터치(%s)가 기록되지 않아 감시를 열지 않습니다.',
                          self.namespace, gate['ref'])
            return None
        return {'external_watch_id': touch['watch_id'], 'external_source_tf': touch['tf'],
                'external_liquidity_required': True, 'external_source_kind': 'SWEEP',
                # Which touch was selected, for the record; the gate re-selects by the same rule.
                'external_level_id': touch.get('id'), 'external_level_price': touch.get('price')}

    def _all_steps(self):
        return [s for unit in self.units for key in ('steps','cancel_conditions','final_conditions','after_conditions')
                for s in unit.get(key, [])] + [s for unit in self.units
                for s in (unit.get('lifecycle') or {}).get('restart_on', [])]

    def _subscription_tfs(self, step):
        candidates = set()
        for tf in step['tfs']:
            if tf not in ('SOURCE','FINAL'): candidates.add(tf); continue
            if tf == 'FINAL':
                candidates.update(t for unit in self.units for t in unit['final'].get('tfs', [])
                    if t not in ('SOURCE','FINAL'))
                if candidates: continue
            reference = step.get('ref')
            sources = [s for unit in self.units for s in unit['steps']
                if (s.get('capture') == reference if reference else True)]
            candidates.update(t for s in sources for t in s['tfs'] if t not in ('SOURCE','FINAL'))
        return sorted(candidates)

    def checkpoint(self):
        """JSON-safe state; the engine owns saving and restoring this payload."""
        from oz_engine.checkpoint import encode
        return {'signature': _intent_id(self.meaning), 'state': encode({
            'machines': [m.checkpoint() for m in self.machines],
            'baselines': self.baselines, 'previous': self.previous,
            'oz_events': self.oz_events, 'oz_sequence': self.oz_sequence,
            'restart_tokens': self._restart_tokens})}

    def restore(self, payload):
        from oz_engine.checkpoint import decode
        if payload.get('signature') != _intent_id(self.meaning): return False
        state = decode(payload['state'])
        if len(state.get('machines', [])) != len(self.machines): return False
        for machine, saved in zip(self.machines, state['machines']): machine.restore(saved)
        self._advanced = {}
        self.baselines = state.get('baselines', {})
        self.previous = state.get('previous', {})
        self.oz_events = state.get('oz_events', {})
        self.oz_sequence = state.get('oz_sequence', 0)
        self._restart_tokens = state.get('restart_tokens', {})
        return True

    def deadlines(self):
        """Source-time deadlines in seconds. The engine supplies timer events."""
        return tuple(m.active_until for m in self.machines
            if m.active and m.active_until is not None)

    def _step_id(self, step):
        key = id(step)
        if key not in self._step_ids: self._step_ids[key] = _intent_id(step)
        return self._step_ids[key]

    @staticmethod
    def _bound_tf(tf, machine):
        if tf == 'SOURCE':
            return machine.source_tf or next((s['tfs'][0] for s in machine.meaning['steps']
                if len(s['tfs']) == 1 and s['tfs'][0] not in ('SOURCE','FINAL')), None)
        if tf == 'FINAL':
            return machine.final_tf if machine.final_tf not in ('SOURCE','FINAL') else (
                IntentPort._bound_tf(machine.final_tf, machine) if machine.final_tf == 'SOURCE' else None)
        return tf

    def _dependency_id(self, symbol, tf, kind, step):
        selector = (step.get('_level_codes'), step.get('validation_mode'), step.get('trigger_mode'))
        # Watches of a different ATR rule are different watches. Without a rule the id is unchanged.
        if step.get('_level_gate'): selector += (tuple(sorted(step['_level_gate'].items())),)
        return self.namespace + ':' + kind + ':' + symbol + ':' + tf + ':' + _intent_id(selector)

    @staticmethod
    def _sweep_payload(step, symbol, tf, wid, api):
        """The one SWEEP_WATCH a touch step asks for; its strategy's ATR rule rides along."""
        payload = {'action': 'SWEEP_WATCH', 'watch_id': wid, 'symbol': symbol, 'source_tf': tf,
            'levels': step['_level_codes'], 'atr_period': 14,
            'atr_mult': float(api.config_get('SWEEP_ATR_MULT', 1.5)),
            'session_london': api.config_get('LONDON', ''), 'session_newyork': api.config_get('NEWYORK', '')}
        if step.get('_level_gate'):
            payload.update(external_atr_period=step['_level_gate']['atr_period'],
                           external_atr_mult=step['_level_gate']['atr_mult'])
        return payload

    def desired_subscriptions(self):
        desired = {'FVG': {}, 'SWEEP': {}}
        own_symbol = self.api.market_context()[1]
        for symbol in self.meaning['symbols']:
            if own_symbol and symbol != own_symbol: continue
            for step in self._all_steps():
                family = 'FVG' if step['kind'].startswith('FVG') else 'SWEEP' if step['kind'] == 'EXTERNAL_LIQUIDITY_TOUCH' else None
                if not family: continue
                for tf in self._subscription_tfs(step):
                    wid = self._dependency_id(symbol, tf, family, step)
                    payload = ({'action': family + '_WATCH', 'watch_id': wid, 'symbol': symbol, 'source_tf': tf}
                        if family == 'FVG' else self._sweep_payload(step, symbol, tf, wid, self.api))
                    desired.setdefault(family, {})[wid] = payload
        return desired

    def handle_fact(self, event):
        # The public observer is invoked only after Part1 accepts a fresh Fact.
        if event.get('kind') == 'FACT_SNAPSHOT':
            key = (event.get('strategy'), event.get('symbol'), event.get('source_tf'), event.get('watch_id'))
            self.fact_snapshots[key] = event
            self._fact_versions[key] = self._fact_versions.get(key, 0) + 1
            board, symbol, _ = self._board()
            self.fact_sources[key] = {tf:(board.snapshot(symbol, tf).source_epoch, board.snapshot(symbol, tf).seq)
                for tf in (event.get('source_health') or {}).get('sources', {}) if (symbol,tf) in board.feeds}
            self._publication = None
            self.poll()

    def _sid(self, m): return self.namespace + ':BRANCH:' + str(m.branch)
    def _wid(self, m): return self._sid(m) + ':OZ'

    def _board(self):
        return self.api.market_context()

    def _ma_features(self, symbol, tf, view, names, *, closed=False):
        # Every period, EMA21 included, goes through the shared MA Fact store.
        return self.ma.get(symbol, tf, view, list(names), closed=closed) if names else {}

    @staticmethod
    def _finite(*values):
        return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)

    @staticmethod
    def _candle_fact(row, name):
        """A candle Fact (indicator_facts candle_direction, candle_shape) of the judged bar.

        Both are per-bar formulas: the bar's own prices give the value the whole series
        would, so a new closed bar never recomputes the history. A virtual entry reads its
        confirmation candle the same way.
        """
        from indicator_facts import candle_direction_values, candle_shape_values
        if name == 'candle_direction':
            return float(candle_direction_values(row.get('open'), row.get('close')))
        return float(candle_shape_values(row.get('open'), row.get('high'), row.get('low'), row.get('close')))

    @staticmethod
    def _value_at(values, current):
        """One finite value of a series at `current`, or None (the MA comparator's reading)."""
        if values is None: return None
        index = current if current >= 0 else len(values) + current
        if not 0 <= index < len(values): return None
        try:
            value = float(values.iloc[index] if hasattr(values, 'iloc') else values[index])
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) else None

    def _metric(self, board, symbol, tf, view, metric, closed):
        from indicator_facts import ArrayFactFrame, FACTS
        from event_engine.market import MarketView
        from event_engine.indicator_runtime import IndicatorRuntime
        from strategy_INDICATOR import METRIC_FACTS, SCORE_FIELDS
        selected = MarketView(view.snapshot, end=-1) if closed else view
        key = (symbol, tf, closed)
        publication = board.publication_token
        cached = self.metric_frames.get(key)
        def needs_atr(name):
            return name == 'ATR14_GENERAL' or any(needs_atr(dep) for dep in FACTS[name].deps if not dep.startswith('@'))
        if metric not in self._metric_atr:
            fact_name = METRIC_FACTS.get(metric)
            self._metric_atr[metric] = metric in SCORE_FIELDS or (fact_name in FACTS and needs_atr(fact_name))
        atr_required = self._metric_atr[metric]
        atr = board.fact('ATR14_GENERAL', symbol, tf)[:len(selected)] if atr_required else None
        if cached is None or cached[0] != publication:
            frame = cached[1] if cached else ArrayFactFrame(selected, tf, atr)
            if cached: frame.update(selected, atr)
            cached = (publication, frame); self.metric_frames[key] = cached
        elif atr is not None and cached[1].provided_atr is None:
            cached[1].provided_atr = atr
        result = IndicatorRuntime.metrics(None, selected, cached[1], (metric,))
        return (result or {}).get('metrics', {}).get(metric)

    def _percentile(self, board, symbol, tf, view, closed, now):
        from oz_engine.common import percentile_states
        key = (symbol, tf, closed, view.snapshot.source_epoch)
        publication = board.publication_token
        cached = self.percentile_events.get(key)
        if cached is not None and cached['publication'] == publication: return cached
        current = percentile_states(view.row(-2 if closed else -1))
        stamp = int(view.time[-2 if closed else -1])
        previous = cached['states'] if cached else {}
        fresh = cached is not None and (not closed or stamp != cached['stamp'])
        transitions = {family for family, state in current.items()
            if fresh and state == 'IN' and previous.get(family) in ('LOWER_OUT', 'UPPER_OUT')}
        entered = {family for family, state in current.items()
            if fresh and state in ('LOWER_OUT', 'UPPER_OUT') and previous.get(family) in ('IN', 'LOWER_OUT', 'UPPER_OUT')
            and previous[family] != state}
        cached = dict(publication=publication, states=current, previous=previous,
            returned=transitions, entered=entered, stamp=stamp,
            at=int(view.time[-1]) if closed else now)
        self.percentile_events[key] = cached
        return cached

    def _fact_snapshot(self, board, family, symbol, tf, watch_id=None):
        if watch_id is None:
            matches = [e for (f, s, t, _), e in self.fact_snapshots.items() if f == family and s == symbol and t == tf]
            if not matches: return None
            event = matches[-1]
        else:
            # The table is keyed by the event's own (family, symbol, timeframe, watch): one entry at most.
            event = self.fact_snapshots.get((family, symbol, tf, watch_id))
            if event is None: return None
        key = (event.get('strategy'), event.get('symbol'), event.get('source_tf'), event.get('watch_id'))
        captured = self.fact_sources.get(key, {})
        sources = (event.get('source_health') or {}).get('sources') or {}
        from event_engine.market import available
        if not sources: return None
        for source_tf, epoch in sources.items():
            if not available(board, symbol, source_tf): return None
            snap = board.snapshot(symbol, source_tf)
            if snap.source_epoch != epoch or captured.get(source_tf) != (snap.source_epoch, snap.seq): return None
        return event.get('facts', ())

    @staticmethod
    def _bound_step(step, machine):
        """A step whose meaning depends on the machine asking: it refers to SOURCE/FINAL or to a capture."""
        return machine is not None and bool(step.get('ref') or step.get('scope_ref')
            or any(t in ('SOURCE', 'FINAL') for t in step['tfs']))

    def observe(self, board, symbol, step, direction, now, machine=None):
        publication = (board.publication_token, now, self.oz_sequence)
        if self._publication != publication:
            self._publication = publication; self._observation_cache = {}
        bound = self._bound_step(step, machine)
        key = (symbol, self._step_id(step), direction, machine.branch if bound else None)
        if key not in self._observation_cache:
            frame = getattr(board, '_frame_cache', None)
            pure = None if frame is None else self._pure_key(symbol, step, direction, machine)
            if pure is None:
                self._observation_cache[key] = self._observe(board, symbol, step, direction, now, machine)
            else:
                # A Fact event clears the cache above (polls run again inside one bundle), but these
                # conditions keep no memory and read no Fact, or only their own timeframes' Facts (the
                # key counts those): the same inputs give the same answer, so it is reused until the
                # board publication, the time or one of those Facts changes.
                # The publication object itself is kept, so a freed one's id can never match.
                if getattr(self, '_pure_frame', None) is not frame or self._pure_now != now:
                    self._pure_frame, self._pure_now, self._pure_cache = frame, now, {}
                if pure not in self._pure_cache:
                    self._pure_cache[pure] = self._observe(board, symbol, step, direction, now, machine)
                self._observation_cache[key] = self._pure_cache[pure]
        return self._observation_cache[key]

    # Explicit whitelist: conditions whose observation keeps no memory and reads no Fact, OZ event
    # or capture, only the market data, the time, the step, the direction and its timeframes.
    # Not listed: crosses/touch edges, new FVG, bar close, session start, price breaks (memory),
    # FVG/SWEEP/OZ (Facts), and PERCENTILE (compares with the previous publication).
    PURE_KINDS = frozenset(('CANDLE_STATE', 'CANDLE_SHAPE', 'TREND', 'MA_STATE', 'MA_SLOPE_STATE', 'MA_PRICE_STATE', 'WONBI_TOUCH',
                            'REGIME_BAND', 'TREND_METRIC'))

    # Conditions that keep no memory and read only the market data, the time and the Facts of their own
    # watches (family per kind, one watch per timeframe). They are reused like PURE_KINDS while no Fact of
    # those watches arrives: another watch's Fact changes nothing they read. A touch on 12 timeframes in two
    # directions has 24 watches, one Fact each per bundle, and every Fact polls all 24 machines again.
    FACT_KINDS = {'EXTERNAL_LIQUIDITY_TOUCH': 'SWEEP'}

    def _fact_scopes(self, symbol, step):
        """The Fact table keys a FACT_KINDS step reads: (family, symbol, timeframe, its watch) per timeframe."""
        family = self.FACT_KINDS[step['kind']]
        scopes = []
        for tf in step['tfs']:
            key = (self._step_id(step), symbol, tf, family)
            if key not in self._watch_ids:
                self._watch_ids[key] = self._dependency_id(symbol, tf, family, step)
            scopes.append((family, symbol, tf, self._watch_ids[key]))
        return scopes

    def _pure_key(self, symbol, step, direction, machine):
        if step['kind'] in self.FACT_KINDS:
            # A step bound to its machine (a capture, SOURCE/FINAL) reads more than its own watches.
            if step.get('ref') or step.get('scope_ref') or any(t in ('SOURCE', 'FINAL') for t in step['tfs']):
                return None
            return ('FACT', symbol, self._step_id(step), direction, tuple(step['tfs']),
                    tuple(self._fact_versions.get(scope) for scope in self._fact_scopes(symbol, step)))
        if step['kind'] not in self.PURE_KINDS or step.get('scope_ref') or step.get('_event_mode'):
            return None
        reference = machine.captures.get(step.get('ref')) if machine is not None and step.get('ref') else None
        tfs = []
        for declared in step['tfs']:
            tf = self._bound_tf(declared, machine) if machine is not None else declared
            if declared == 'SOURCE' and reference: tf = reference.get('tf')
            tfs.append(tf)
        price_tf = step.get('price_tf')
        if price_tf and machine is not None: price_tf = self._bound_tf(price_tf, machine)
        return (symbol, self._step_id(step), direction, tuple(tfs), price_tf)

    def _observe(self, board, symbol, step, direction, now, machine=None):
        from event_engine.market import select, MarketView
        kind = step['kind']; chosen = step.get('_resolved_direction', step.get('direction', direction))
        if chosen in ('BOTH', 'SAME_AS_PREVIOUS_DIRECTION'): chosen = direction
        values = []; matching_families = None
        for declared_tf in step['tfs']:
            tf = self._bound_tf(declared_tf, machine) if machine is not None else declared_tf
            reference = machine.captures.get(step.get('ref')) if machine is not None and step.get('ref') else None
            if declared_tf == 'SOURCE' and reference: tf = reference.get('tf')
            if tf in (None, 'SOURCE', 'FINAL'):
                values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                continue
            indicators = ('WONBI',) if kind == 'WONBI_TOUCH' else tuple(step.get('regime_families', ())) if kind == 'REGIME_BAND' else ()
            view = select(board, symbol, tf, indicators)
            minimum = (2 if step['bar_state'] == 'CLOSED' else 1) if kind in ('CANDLE_STATE', 'CANDLE_SHAPE') else 3
            if view is None or len(view) < minimum:
                values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                continue
            closed = step.get('bar_state') == 'CLOSED' or (kind in ('MA_CROSS', 'MA_PRICE_CROSS', 'MA_PRICE_TOUCH')
                and step.get('bar_state', 'UNSPECIFIED') == 'UNSPECIFIED')
            current = -2 if closed else -1
            row = view.row(current); stamp = int(view.time[current]); matched = False; token = None; event = False; at = now
            binding_tfs = (tf,)
            # What a step last saw (previous bar, edge) is kept per machine when the step is bound to
            # one: machines sharing a parent timeframe must each see the same new closed bar.
            key = (symbol, tf, self._step_id(step), chosen, view.snapshot.source_epoch,
                   *((machine.branch,) if self._bound_step(step, machine) else ()))
            resource = None
            touched = ()
            if kind == 'CANDLE_STATE':
                polarity = self._candle_fact(row, 'candle_direction')
                if not math.isfinite(polarity):
                    values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                    continue
                matched = candle_matches(step['side'], polarity)
            elif kind == 'CANDLE_SHAPE':
                # The same candle Fact and rule judge a virtual entry's confirmation candle.
                shape = self._candle_fact(row, 'candle_shape')
                if not math.isfinite(shape):
                    values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                    continue
                matched = shape_matches(step['shape'], shape)
            elif kind.startswith('MA_') or kind == 'TREND':
                if kind == 'MA_CROSS': names = (step['ma_left'], step['ma_right'])
                elif kind in STATE_KINDS: names = ma_names(step)
                else: names = (f"{step['ma_family']}{step['slow_period']}",)
                features = self._ma_features(symbol, tf, view, names, closed=closed)
                offset = 1 if closed else 0
                if kind in STATE_KINDS:
                    # The same rules judge a virtual entry's environment (state_judge).
                    if not state_ready(step, features, offset, names): return {'ready': False, 'matched': False}
                    close = row.get('close')
                    if kind == 'MA_PRICE_STATE' and step.get('price_tf'):
                        price_tf = self._bound_tf(step['price_tf'], machine) if machine else step['price_tf']
                        price_view = select(board, symbol, price_tf) if price_tf else None
                        if price_view is None or len(price_view) < offset + 1:
                            values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                            continue
                        close = price_view.row(current).get('close')
                        binding_tfs = (tf, price_tf)
                    matched = state_matches(step, chosen, features, close, offset, names)
                elif kind == 'MA_CROSS':
                    from watch_ma import MACondition
                    condition = MACondition('GOLDEN' if chosen == 'LONG' else 'DEAD', tuple(names))
                    answer = condition.evaluate(features, current)
                    matched = answer is not None and bool(answer); event = True
                    # A closed event is new only after registration's initial closed bar.
                    previous = self.baselines.get(key); self.baselines[key] = stamp
                    if closed and (previous is None or previous == stamp): matched = False
                    if not closed:
                        previous_edge = self.previous.get(('cross', key))
                        self.previous['cross', key] = matched
                        matched = matched and previous_edge is False
                    token = (stamp, chosen) if closed else (stamp, now, chosen)
                    if closed: at = int(view.time[-1])
                elif kind in ('MA_PRICE_CROSS', 'MA_PRICE_TOUCH'):
                    from watch_ma import MACondition
                    # Reuse the current MA Fact store and the current comparator.
                    # No moving-average formula is implemented by a strategy.
                    features = dict(features, PRICE=view.column('close'), LOW=view.column('low'), HIGH=view.column('high'))
                    if kind == 'MA_PRICE_CROSS':
                        relation = step['relation']
                        up = MACondition('GOLDEN', ('PRICE', names[0])).evaluate(features, current)
                        down = MACondition('DEAD', ('PRICE', names[0])).evaluate(features, current)
                        if up is None or down is None: answer = None
                        elif step.get('_split_price_direction'):
                            answer = (up and relation in ('BREAK_UP','BOTH')) if direction == 'LONG' else (
                                down and relation in ('BREAK_DOWN','BOTH'))
                        else:
                            answer = (up and relation in ('BREAK_UP','BOTH')) or (down and relation in ('BREAK_DOWN','BOTH'))
                    else:
                        # One touch rule for the alert and a virtual entry's confirmation candle.
                        low, high, average = (self._value_at(features.get(name), current) for name in ('LOW', 'HIGH', names[0]))
                        answer = None if None in (low, high, average) else touch_matches(low, high, average)
                    event = True
                    previous = self.baselines.get(key); self.baselines[key] = stamp
                    if answer is None:
                        self.previous.pop(('ma_price', key), None)
                        values.append({'ready': False, 'matched': False, 'event': True, 'token': None})
                        continue
                    matched = bool(answer)
                    if closed:
                        matched = matched and previous is not None and previous != stamp
                        at = int(view.time[-1])
                    else:
                        edge_key = ('ma_price', key)
                        previous_edge = self.previous.get(edge_key)
                        self.previous[edge_key] = bool(answer)
                        # A new candle can touch again without a false range in
                        # the previous candle; an initial historical touch cannot.
                        matched = matched and (previous_edge is False or
                            (kind == 'MA_PRICE_TOUCH' and previous is not None and previous != stamp))
                    token = (view.snapshot.source_epoch, tf, stamp, kind, chosen) if closed else (
                        view.snapshot.source_epoch, tf, stamp, kind, now, chosen)
            elif kind.startswith('FVG'):
                facts = self._fact_snapshot(board, 'FVG', symbol, tf)
                if facts is None:
                    values.append({'ready': False, 'matched': False, 'event': kind == 'FVG_NEW', 'token': None})
                    continue
                side = step.get('side') or ('BULL' if chosen == 'LONG' else 'BEAR')
                zones = [f for f in facts if f.get('fvg_side') == side]
                referenced = machine.captures.get(step.get('ref')) if machine is not None and step.get('ref') else None
                if step.get('ref'):
                    zones = [f for f in zones if referenced and referenced.get('tf') == tf and f.get('zone_id') == referenced.get('id')]
                if kind == 'FVG_NEW':
                    ids = tuple(sorted(f['zone_id'] for f in zones)); previous = self.baselines.get(key)
                    self.baselines[key] = ids; created = tuple(x for x in ids if previous is not None and x not in previous)
                    matched = bool(created); token = created; event = True
                    zones = [f for f in zones if f['zone_id'] in created]
                elif kind == 'FVG_STATE' and step.get('state', 'EXISTS') == 'EXISTS': matched = bool(zones)
                elif closed:
                    from strategy_FVG import candle_overlaps_zone
                    matched = any(candle_overlaps_zone(row.get('low'), row.get('high'), f['zone_bot'], f['zone_top']) for f in zones)
                else: matched = any(f.get('touched_now', False) for f in zones)
                if matched and zones:
                    selected_zone = zones[-1]
                    resource = dict(kind='FVG', id=selected_zone['zone_id'], tf=tf,
                        lower=selected_zone['zone_bot'], upper=selected_zone['zone_top'], at=at)
            elif kind == 'WONBI_TOUCH':
                side = step.get('side') or ('LOWER' if chosen == 'LONG' else 'UPPER')
                band = row.get('wonbi_lower' if side == 'LOWER' else 'wonbi_upper')
                value = row.get('low' if side == 'LOWER' else 'high')
                matched = self._finite(value, band) and (value <= band if side == 'LOWER' else value >= band)
            elif kind == 'EXTERNAL_LIQUIDITY_TOUCH':
                watch = self._dependency_id(symbol, tf, 'SWEEP', step)
                facts = self._fact_snapshot(board, 'SWEEP', symbol, tf, watch)
                if facts is None:
                    values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                    continue
                hits = [f for f in facts if f.get('direction') == chosen and f.get('level_code') in step['_level_codes']]
                matched = bool(hits); token = tuple(sorted((str(f.get('level_id')), str(f.get('touch_time'))) for f in hits))
                at = max((float(f.get('touch_time', f.get('event_time', now))) for f in hits), default=now)
                # The already accepted touched levels, copied as-is from the SWEEP Fact. The
                # alert names them; a captured touch links the final OZ to the one selected.
                touched = tuple(dict(id=f.get('level_id'), code=f.get('level_code'), name=f.get('level_name'),
                    price=f.get('level_price'), at=f.get('touch_time', f.get('event_time'))) for f in hits)
                selected = outermost_level(touched, chosen == 'LONG')
                if selected: resource = dict(selected, kind='LIQUIDITY', tf=tf, watch_id=watch, direction=chosen)
            elif kind == 'SESSION_START':
                raw = str(self.api.config_get(step['session'], '')); start = raw.split('-')[0].replace(':', '')
                if not re.fullmatch(r'\d{4}', start): raise ValueError('거래 세션 시작시각이 없습니다.')
                local = now + 9 * 3600; day = int(local // 86400); start_sec = int(start[:2])*3600 + int(start[2:])*60
                previous = self.previous.get(key); self.previous[key] = now
                boundary = day*86400 + start_sec - 9*3600
                matched = previous is not None and previous < boundary <= now; token = boundary; event = True
            elif kind == 'OZ_ALERT':
                found = self.oz_events.get((symbol, tf, step['validation_mode'], step['trigger_mode'], chosen))
                matched = found is not None; token = found[0] if found else None; at = found[1] if found else now; event = True
                resource = dict(found[2]) if found and len(found) > 2 else None
                if resource and self._reference_alive(board, symbol, resource) is False: matched = False
                if step.get('ref'):
                    reference = machine.captures.get(step['ref']) if machine is not None else None
                    matched &= bool(reference and resource and resource.get('id') == reference.get('id'))
            elif kind == 'BAR_CLOSE':
                stamp = int(view.time[-2]); previous = self.baselines.get(key)
                self.baselines[key] = stamp
                matched = previous is not None and previous != stamp
                token = (view.snapshot.source_epoch, tf, stamp); event = True
                at = int(view.time[-1])
                resource = dict(kind='BAR', id=token, tf=tf, at=at)
            elif kind == 'TREND_METRIC':
                value = self._metric(board, symbol, tf, view, step['metric'], closed)
                if not self._finite(value):
                    values.append({'ready': False, 'matched': False, 'event': False, 'token': None})
                    continue
                compare = {'GT':operator.gt, 'GTE':operator.ge, 'LT':operator.lt,
                    'LTE':operator.le, 'EQ':operator.eq, 'NE':operator.ne}[step['metric_operator']]
                matched = compare(value, step['metric_value'])
            elif kind in ('PERCENTILE_OUT', 'PERCENTILE_OUT_IN'):
                state = self._percentile(board, symbol, tf, view, closed, now)
                event = kind == 'PERCENTILE_OUT_IN' or step.get('_event_mode', False)
                side = step.get('side') or ('LOWER' if chosen == 'LONG' else 'UPPER')
                sides = ('LOWER_OUT','UPPER_OUT') if side == 'BOTH' else (side + '_OUT',)
                families = {family for family in step['families'] if (
                    state['states'].get(family) in sides and (not event or family in state['entered']) if kind == 'PERCENTILE_OUT' else
                    family in state['returned'] and state['previous'].get(family) in sides)}
                if any(state['states'].get(family) == 'NA' for family in step['families']):
                    values.append({'ready': False, 'matched': False, 'event': kind == 'PERCENTILE_OUT_IN', 'token': None})
                    continue
                matching_families = families if matching_families is None else matching_families & families
                matched = len(families) == len(step['families']) if step.get('family_combine') == 'ALL' else bool(families)
                token = (view.snapshot.source_epoch, tf, state['stamp'], state['at'], state['publication'], tuple(sorted(families)))
                if event: at = state['at']
            elif kind == 'REGIME_BAND':
                families = set()
                for family in step['regime_families']:
                    prefix = 'price' if family == 'PRICE' else family
                    relation = step['relation']; slope = row.get(prefix + '_regime_slope')
                    if relation.startswith('SLOPE_'): okay = self._finite(slope) and (slope > 0 if relation == 'SLOPE_UP' else slope < 0)
                    else:
                        value = row.get('close' if family == 'PRICE' else family + '_val')
                        lower, upper = row.get(prefix+'_regime_lower'), row.get(prefix+'_regime_upper')
                        if not self._finite(value, lower, upper): okay = False
                        elif relation == 'IN': okay = lower <= value <= upper
                        elif relation in ('ABOVE', 'OUT_UPPER'): okay = value > upper
                        else: okay = value < lower
                    if okay: families.add(family)
                if step.get('family_combine') == 'MATCHING_FAMILY': matching_families = families if matching_families is None else matching_families & families
                matched = (len(families) == len(step['regime_families']) if step.get('family_combine') == 'ALL' else bool(families))
            elif kind in ('PRICE_LEVEL', 'LIQUIDITY_LEVEL'):
                level = step['level']
                if level == 'DAY_OPEN':
                    daily = select(board, symbol, '1d')
                    targets = [daily.row(-1).get('open')] if daily is not None else []
                elif isinstance(level, str):
                    views = {t: select(board, symbol, t) for t in ('1d', '4h', '8h', '5m')}
                    levels = self.levels.get(views, self.api.config_get('LONDON', ''), self.api.config_get('NEWYORK', ''), symbol)
                    targets = [l['price'] for l in levels if l['level_code'] in step['_level_codes']]
                else: targets = [level]
                price = row.get('close'); previous = self.previous.get(key); self.previous[key] = price
                relation = step['relation']; event = relation.startswith('BREAK_')
                for target in targets:
                    if not self._finite(price, target): continue
                    if relation == 'ABOVE': okay = price > target
                    elif relation == 'BELOW': okay = price < target
                    elif relation == 'TOUCH': okay = row.get('low') <= target <= row.get('high')
                    elif not self._finite(previous): okay = False
                    elif relation == 'BREAK_UP': okay = previous <= target < price
                    else: okay = previous >= target > price
                    matched |= okay
                token = (stamp, now, chosen)
            if step.get('scope_ref'):
                reference = machine.captures.get(step['scope_ref']) if machine is not None else None
                matched &= self._scope_matches(board, symbol, reference,
                    resource if kind == 'OZ_ALERT' else {'b0_price': row.get('close'), 'at': at}, at)
            if step.get('negated'): matched = not matched
            # Touches become discrete edges in a sequence, but remain states in a filter.
            if kind == 'EXTERNAL_LIQUIDITY_TOUCH' and step.get('_event_mode'):
                event = True
            elif kind in ('WONBI_TOUCH', 'FVG_TOUCH') and step.get('_event_mode'):
                previous = self.previous.get(('edge', key)); self.previous['edge', key] = matched
                token = (stamp, now)
                matched = matched and previous is False; event = True
            values.append({'ready': True, 'matched': bool(matched), 'event': event, 'token': token,
                'at': at, 'source_tf': tf, 'condition_tfs':binding_tfs, 'resource': resource, 'touched_levels': touched})
        combine = step.get('tf_combine', 'ANY'); matched = all(v['matched'] for v in values) if combine == 'ALL' else any(v['matched'] for v in values)
        if matching_families is not None and step.get('family_combine') == 'MATCHING_FAMILY': matched = bool(matching_families)
        ready = all(v['ready'] for v in values) if combine == 'ALL' else any(v['ready'] for v in values)
        return {'ready': ready, 'matched': matched, 'event': any(v['event'] for v in values),
                'at': max((v.get('at', now) for v in values if v['matched']), default=now),
                'token': (self._step_id(step), tuple(v['token'] for v in values if v['matched'])),
                'families': matching_families,
                'source_tf': next((v.get('source_tf') for v in values if v['matched']), None),
                'condition_tfs': tuple(dict.fromkeys(tf for v in (
                    [v for v in values if v['matched']] if combine == 'ALL' else
                    [v for v in values if v['matched']][:1])
                    for tf in v.get('condition_tfs',(v.get('source_tf'),)))),
                'resource': next((v.get('resource') for v in values if v['matched'] and v.get('resource') is not None), None),
                'touched_levels': tuple(level for v in values if v['matched'] for level in v.get('touched_levels', ()))}

    def _time_allowed(self, filters):
        # The Composer's one trading-time policy, the same object KIM strategies use.
        return bool(self.api.time_allowed(filters))

    def _lifecycle_context(self, board, machine, now, observations=()):
        life = machine.meaning.get('lifecycle') or {}
        if not life: return None
        from event_engine.market import select
        import numpy as np
        source = machine.source_tf or next((o.get('source_tf') for o in observations
            if o.get('matched') and o.get('event')), None)
        source = source or self._bound_tf('SOURCE', machine)
        def tf_name(tf):
            return source if tf == 'SOURCE' else machine.final_tf if tf == 'FINAL' else tf
        views = {}
        def view_for(tf):
            tf = tf_name(tf)
            if tf not in views: views[tf] = select(board, machine.symbol, tf) if tf else None
            return views[tf]
        context = {}
        if life.get('snapshots') and not machine.active:
            values = {}
            boundary = max((o.get('at', now) for o in observations if o.get('matched') and o.get('event')), default=now)
            for name, spec in life['snapshots'].items():
                view = view_for(spec['tf'])
                if view is None or len(view) < 2: continue
                index = min(int(np.searchsorted(view.time, boundary, side='left')) - 1, len(view) - 2)
                if index < 0: continue
                if spec['field'] == 'ATR14':
                    values_array = board.fact('ATR14_GENERAL', machine.symbol, tf_name(spec['tf']))
                    value = float(values_array[index])
                else: value = view.row(index).get(spec['field'])
                if self._finite(value): values[name] = value
            context['snapshots'] = values
            # The bar boundary the values belong to; later windows start there, not at the poll.
            context['snapshot_at'] = boundary
        expires = life.get('expires') or {}
        if machine.active and expires.get('bars') is not None:
            view = view_for(expires['tf'])
            if view is not None and len(view) >= 2:
                last = machine.last_closed_bar if machine.last_closed_bar is not None else machine.setup_time
                count = len(view) - 1 - int(np.searchsorted(view.time[:-1], last, side='right'))
                machine.closed_bars += max(0, count)
                machine.last_closed_bar = max(float(last), float(view.time[-2]))
                context['closed_bars'] = machine.closed_bars
            context['bar_limit'] = self._bar_limit(expires)
        excursion = life.get('excursion')
        if machine.active and excursion:
            view = view_for(excursion['tf'])
            anchor = machine.snapshots.get(excursion['anchor']); size = machine.snapshots.get(excursion['snapshot'])
            if view is not None and self._finite(anchor, size) and size > 0:
                # Only the new tail is read; running extrema are kept for this activation. The
                # window opens at the bar boundary the anchor belongs to, not at the poll that saw it.
                marker = machine.excursion_bar
                if marker is None: marker = machine.snapshot_at if machine.snapshot_at is not None else machine.setup_time
                start = int(np.searchsorted(view.time, marker, side='left'))
                upward = machine.direction == 'LONG'
                if excursion.get('direction') == 'ADVERSE': upward = not upward
                tail = view.column('high' if upward else 'low')[start:]
                if len(tail):
                    value = float(np.max(tail) if upward else np.min(tail))
                    machine.extreme = value if machine.extreme is None else (
                        max(value, machine.extreme) if upward else min(value, machine.extreme))
                    machine.excursion_bar = int(view.time[-1])
                if machine.extreme is not None:
                    distance = machine.extreme - anchor if upward else anchor - machine.extreme
                    context['excursion_exceeded'] = distance >= size * excursion['multiplier']
        if life.get('invalidate_refs'):
            context['invalid_refs'] = [name for name, reference in machine.captures.items()
                if reference and self._reference_alive(board, machine.symbol, reference) is False]
        return context

    def _bar_limit(self, expires):
        """Bars a watch may live: the configured count when positive, else the declared one."""
        key = expires.get('bars_setting')
        if key:
            try: configured = int(str(self.api.config_get(key, '')).strip())
            except (TypeError, ValueError): configured = 0
            if configured >= 1: return configured
        return expires['bars']

    def _reference_alive(self, board, symbol, reference):
        if reference.get('kind') == 'OZ':
            if not self._finite(reference.get('b0_time')): return False
            from event_engine.market import available
            if not available(board, symbol, reference['tf']): return None
            state = board.processor('OZ_STATE')
            resources = state.get('resources')
            if resources is None: return None
            key = (symbol, reference['tf'], reference['validation_mode'], reference['trigger_mode'], reference['direction'])
            return None if key not in resources else resources[key] == reference['b0_time']
        if reference.get('kind') != 'FVG': return True
        facts = self._fact_snapshot(board, 'FVG', symbol, reference['tf'])
        # A temporarily unavailable Fact is unknown, not proof of invalidation.
        return None if facts is None else any(f.get('zone_id') == reference['id'] for f in facts)

    def _scope_matches(self, board, symbol, reference, origin, at):
        """OZ scope is a valid parent cycle; FVG scope is its captured price area."""
        if not reference or self._reference_alive(board, symbol, reference) is not True: return False
        activated = reference.get('at')
        if not self._finite(at, activated) or at <= activated: return False
        if reference.get('kind') == 'OZ': return True
        if reference.get('kind') != 'FVG' or not origin: return False
        price = origin.get('b0_price')
        lower, upper = reference.get('lower'), reference.get('upper')
        return self._finite(price, lower, upper) and lower <= price <= upper

    def _replace_group(self, machine):
        life = machine.meaning.get('lifecycle') or {}
        # Final-TF siblings created by the same parent event form one activation.
        group = (machine.symbol, machine.direction, tuple(sorted(machine.used.items(), key=lambda v: v[0])))
        machine.group = group
        if (life.get('replace') or {}).get('scope') != 'SYMBOL_DIRECTION': return
        victims = [m for m in self.machines if m is not machine and m.active
            and (m.symbol, m.direction) == (machine.symbol, machine.direction) and m.group != group]
        for sibling in victims:
            self.api.cancel_oz_watches([self._wid(sibling)]); sibling._reset()

    def _reuse_plan(self, m):
        """The Fact watches a machine's conditions read, when every one of them is reusable (PURE_KINDS,
        FACT_KINDS, not bound to the machine) and the machine has no lifecycle; else None.

        Such a machine's observations change only with the publication, the time or those Facts.
        """
        if m.branch not in self._reuse_plans:
            plan = None
            if not (m.meaning.get('lifecycle') or {}):
                scopes = []
                conditions = (*m.meaning['steps'], *m.meaning.get('final_conditions', ()),
                              *m.meaning.get('cancel_conditions', ()), *m.meaning.get('after_conditions', ()))
                for s in conditions:
                    if (s.get('ref') or s.get('scope_ref') or s.get('price_tf') in ('SOURCE', 'FINAL')
                            or any(t in ('SOURCE', 'FINAL') for t in s['tfs'])):
                        break
                    if s['kind'] in self.FACT_KINDS:
                        scopes.extend(self._fact_scopes(m.symbol, s))
                    elif s['kind'] not in self.PURE_KINDS or s.get('_event_mode'):
                        break
                else:
                    plan = tuple(dict.fromkeys(scopes))
            self._reuse_plans[m.branch] = plan
        return self._reuse_plans[m.branch]

    def poll(self, targets=()):
        board, symbol, now = self._board()
        if board is None: return
        frame = getattr(board, '_frame_cache', None)
        if self._advance_frame is not frame or self._advance_now != now:
            self._advance_frame, self._advance_now, self._advanced = frame, now, {}
        for m in self.machines:
            if m.symbol != symbol: continue
            plan = None if frame is None else self._reuse_plan(m)
            reuse = None if plan is None else (m.active, tuple(self._fact_versions.get(scope) for scope in plan))
            if reuse is not None and self._advanced.get(m.branch) == reuse:
                # Advanced in this publication and time on the same observations (none of the Facts it reads
                # arrived since, and no action anywhere): advancing again on the same input changes nothing.
                continue
            acted = False
            obs = [self.observe(board, symbol, s, m.direction, now, m) for s in m.meaning['steps']]
            # Trading time applies only at the final alert; setups progress at any time.
            final =[self.observe(board, symbol, s, m.direction, now, m) for s in m.meaning.get('final_conditions', [])] if m.active else []
            cancels = [self.observe(board, symbol, s, m.direction, now, m) for s in m.meaning.get('cancel_conditions', [])]
            self.observations[m.branch] = obs; self.final_observations[m.branch] = final
            after = [self.observe(board, symbol, s, m.direction, now, m) for s in m.meaning.get('after_conditions', [])]
            life = m.meaning.get('lifecycle') or {}
            restarts = [self.observe(board, symbol, s, m.direction, now, m) for s in life.get('restart_on', [])]
            replacing = [o for o in obs if (life.get('replace') or {}).get('scope') and m.active
                and o.get('event') and o.get('matched') and o.get('token') not in m.used.values()]
            replaced = bool(replacing)
            fresh_restarts = []
            for i, observation in enumerate(restarts):
                key = (m.branch, i)
                token = observation.get('token')
                fresh_restarts.append(observation.get('matched') and self._restart_tokens.get(key) != token)
                if observation.get('matched'): self._restart_tokens[key] = token
            restarted = replaced or any(fresh_restarts)
            # The event that restarts a cycle belongs to the new cycle, whenever the poll saw it.
            restart_at = min([o['at'] for o in (*replacing, *(o for o, fresh in zip(restarts, fresh_restarts) if fresh))
                if o.get('event') and o.get('at') is not None], default=None)
            for action in m.advance(now, obs, cancelled=any(o.get('matched') for o in cancels),
                    after_observations=after, lifecycle_context=self._lifecycle_context(board, m, now, obs),
                    restarted=restarted, restart_at=restart_at):
                # An action changes this machine and may change others (a replaced group): no machine
                # advanced before it is skipped again. Later ones in this poll are advanced after it.
                acted = True; self._advanced = {}
                if action == 'CANCEL': self.api.cancel_oz_watches([self._wid(m)])
                elif action == 'ARM':
                    self._replace_group(m)
                    self._arm_watch(m)
                elif action == 'NOTIFY':
                    # The completion is this alert's trigger: outside trading time it is dropped.
                    from special_time_slot import strategy_trading_time
                    if not self._time_allowed(strategy_trading_time(m.meaning)): continue
                    # The alert moment's own conditions hold now, as for an OZ final (final_allowed).
                    final = [self.observe(board, symbol, s, m.direction, now, m) for s in m.meaning.get('final_conditions', [])]
                    if not all(m.matches(o) for o in final): continue
                    event = {'signal_strategy': self.namespace, 'signal_source': 'SIGNAL', 'source_name': self.name, 'direction': m.direction,
                        'symbol': symbol, 'source_tf': (m.notification_context or {}).get('source_tf'),
                        'env_tf': (m.notification_context or {}).get('env_tf'),
                        'event_time': now, 'source_spec_id': self._sid(m)}
                    # Read the existing immutable market snapshot only when a
                    # notification is already decided; no extra indicator work.
                    from event_signal_context import market_signal_context
                    quote = market_signal_context(board,symbol,m.direction,now,
                        (m.notification_context or {}).get('condition_tfs', ()))
                    event.update({key:quote[key] for key in ('signal_tf','current_price')})
                    completion = (m.notification_context or {}).get('completion') or {}
                    if completion.get('kind') == 'OZ_ALERT':
                        resource = completion.get('resource') or {}
                        # Only the actual completing OZ observation carries its
                        # original pattern lifetime/price. A preceding OZ never
                        # turns a later ordinary trigger into another OZ signal.
                        event.update(signal_source='OZ',source_tf=resource.get('tf'),
                            signal_tf=resource.get('signal_tf'))
                        event.update({key:resource.get(key) for key in (
                            'b0_price','b0_time','neckline_price','neckline_time_ms')})
                        # A later gate completion uses its current quote/time;
                        # original OZ price belongs only to that same instant.
                        if resource.get('at') == now:
                            event['current_price'] = resource.get('current_price')
                    message = render_strategy_alert(self.name, m, event, observations=obs,
                        after_observations=after, final_observations=final)
                    self.api.notify(message, event=event,
                        event_id=_intent_id(self.namespace, symbol, m.branch, m.direction, now, m.sequence))
            if reuse is not None and not acted:
                self._advanced[m.branch] = reuse

    def handle_oz_event(self, event):
        # An OZ event can change OZ observations and, delivered, the machine itself.
        self._advanced = {}
        try:
            return self._handle_oz_event(event)
        finally:
            self._advanced = {}

    def _handle_oz_event(self, event):
        source = str(event.get('source_spec_id') or '')
        if ':OZ_SOURCE:' in source and source.startswith(self.namespace + ':'):
            self.oz_sequence += 1
            from oz_engine.common import epoch
            b0_time = epoch(event.get('b0_time'))
            key = (event.get('symbol'), event.get('source_tf'), event.get('validation_mode'),
                event.get('trigger_mode'), event.get('direction'))
            resource = dict(kind='OZ', id=(*key,b0_time), tf=event.get('source_tf'),
                validation_mode=event.get('validation_mode'), trigger_mode=event.get('trigger_mode'),
                direction=event.get('direction'), b0_time=b0_time,
                b0_price=event.get('b0_price'),
                signal_tf=event.get('signal_tf'),
                neckline_price=event.get('neckline_price'),neckline_time_ms=event.get('neckline_time_ms'),
                current_price=event.get('current_price'),
                at=float(event.get('event_time', self._board()[2])))
            self.oz_events[(event.get('symbol'), event.get('source_tf'), event.get('validation_mode'),
                            event.get('trigger_mode'), event.get('direction'))] = (self.oz_sequence, float(event.get('event_time', self._board()[2])), resource)
            self.poll()
            return {'ok': True, 'delivered': True, 'suppressed': True}
        machine = next((m for m in self.machines if self._sid(m) == source), None)
        if machine is None: return None
        board, symbol, now = self._board()
        self.poll()
        obs = [self.observe(board, symbol, s, machine.direction, now, machine) for s in machine.meaning['steps']]
        final = [self.observe(board, symbol, s, machine.direction, now, machine) for s in machine.meaning.get('final_conditions', [])]
        from special_time_slot import strategy_trading_time
        allowed = machine.final_allowed(now, obs, final) and self._time_allowed(strategy_trading_time(machine.meaning))
        event_time = event.get('event_time', now)
        allowed &= machine.setup_time is not None and float(event_time) >= machine.setup_time
        scope = machine.meaning['final'].get('scope_ref')
        if scope:
            reference = machine.captures.get(scope)
            allowed &= self._scope_matches(board, symbol, reference, event, event_time)
        family = machine.meaning['final'].get('regime_family')
        if family:
            candidates = {family}
            if family == 'MATCHING_FAMILY':
                groups = [o['families'] for o in (*obs, *final) if o.get('families') is not None]
                candidates = set.intersection(*groups) if groups else set()
            text = str(event.get('indicators_text') or '')
            allowed &= any(re.search(r'(?<![A-Z])' + f + r'(?![A-Z])', text, re.I) for f in candidates)
        if not allowed: return {'ok': True, 'delivered': True, 'suppressed': True}
        forwarded = dict(event, signal_strategy=self.namespace, source_name=self.name, request_chat_id=None,
                         env_tf=machine.environment_tf())
        forwarded['message'] = render_strategy_alert(self.name, machine, forwarded,
            observations=obs, final_observations=final)
        result = self.api.deliver_oz_event_core(forwarded)
        if result.get('delivered'):
            group = machine.group
            machine.delivered()
            if (machine.meaning.get('lifecycle') or {}).get('first_success'):
                siblings = [m for m in self.machines if m is machine or (group is not None and m.group == group)]
                self.api.cancel_oz_watches([self._wid(m) for m in siblings])
                for sibling in siblings:
                    if sibling is not machine: sibling.delivered()
            elif machine.disabled: self.api.cancel_oz_watches([self._wid(machine)])
        return result
