"""Trusted strategy lifecycle; inputs are observations from existing Python facts.

LIVE, replay and generated strategies use this same module. No I/O or market
calculation belongs here; the port supplies declared observations and snapshots.
"""
from __future__ import annotations
import copy
from . import windows

# Conditions that are events by their kind; a recent condition is a state whatever its kind.
_EVENT_KINDS_AT_NOTIFY = ('MA_CROSS', 'MA_PRICE_CROSS', 'MA_PRICE_TOUCH', 'FVG_NEW',
    'SESSION_START', 'OZ_ALERT', 'BAR_CLOSE', 'PERCENTILE_OUT_IN')
_EVENT_KINDS_AT_DELIVERY = ('MA_CROSS', 'MA_PRICE_CROSS', 'FVG_NEW', 'OZ_ALERT', 'BAR_CLOSE')


class IntentMachine:
    def __init__(self, meaning, symbol, direction, branch=0):
        self.meaning, self.symbol, self.direction, self.branch = meaning, symbol, direction, branch
        self.stage = 0; self.hits = {}; self.started = None; self.active_until = None
        self.active = False; self.disabled = False; self.used = {}; self.last_time = None
        self.sequence = 0
        self.state_hold = False
        self.after_ready = False
        self.setup_time = None
        self.sequence_at = None
        self.captures = {}
        self.snapshots = {}
        self.source_tf = None
        self.step_contexts = {}
        self.after_contexts = {}
        self.notification_context = None
        self.final_tf = None
        self.group = None
        self.extreme = None
        self.excursion_bar = None
        self.closed_bars = 0
        self.last_closed_bar = None
        self.activation_at = None
        # Source time of the bar boundary the snapshots were taken at.
        self.snapshot_at = None
        # Earliest source time among the conditions consumed together (all-at-once setups).
        self.first_at = None
        # A bar window (수정본173): the end of the bar the chain's start judged (closed judgments),
        # when the window ends (open of bar N+1, once known), and the same for each unordered hit.
        self.anchor_end = None
        self.window_close = None
        self.hit_ends = {}
        self.hit_closes = {}

    def checkpoint(self):
        return copy.deepcopy({k: v for k, v in vars(self).items() if k != 'meaning'})

    def restore(self, state):
        for key in self.checkpoint():
            if key in state: setattr(self, key, copy.deepcopy(state[key]))

    @staticmethod
    def matches(observation):
        return observation.get('ready', True) and observation.get('matched', False)

    def _eligible(self, i, observation):
        if not self.matches(observation): return False
        if not observation.get('event', False): return not self.state_hold
        token = observation.get('token')
        at = observation.get('at')
        # Retained observations cannot become new events after a setup opens.
        if at is None or (self.activation_at is not None and at < self.activation_at): return False
        if self.meaning.get('order_mode') == 'SEQUENTIAL' and self.started is not None:
            # Two events at one instant do not establish "A, then B".
            # Compare to the preceding event, not just the first event.
            if self.sequence_at is not None and observation.get('at', self.last_time) <= self.sequence_at: return False
            if self.sequence_at is None and observation.get('at', self.last_time) < self.started: return False
        return token is not None and token not in self.used.values()

    def _consume(self, i, observation):
        at = observation.get('at', self.last_time) if observation.get('event') else self.last_time
        self.first_at = at if self.first_at is None else min(self.first_at, at)
        if observation.get('event'): self.used[i] = observation.get('token')
        if observation.get('event') and self.meaning.get('order_mode') == 'SEQUENTIAL':
            self.sequence_at = observation.get('at', self.last_time)
        self.step_contexts[i] = self._observation_context(self.meaning['steps'][i],observation)
        capture = self.meaning['steps'][i].get('capture')
        if capture:
            self.captures[capture] = copy.deepcopy(observation.get('resource'))
        if observation.get('source_tf') and observation.get('event') and (
                self.source_tf is None or self.meaning['steps'][i]['kind'] == 'OZ_ALERT'):
            self.source_tf = observation['source_tf']

    def _reset(self, since=None):
        """Drop the current setup. Events from `since` on are new to the next one; by default
        that is now. A restart passes its own event time: the bar that restarted the cycle
        carries a timestamp earlier than the poll that first saw it."""
        self.stage = 0; self.hits.clear(); self.started = None; self.active_until = None
        self.active = False
        self.after_ready = False
        self.setup_time = None
        self.sequence_at = None
        self.captures.clear(); self.snapshots.clear(); self.extreme = None
        self.excursion_bar = None
        self.source_tf = None; self.group = None
        self.step_contexts.clear(); self.after_contexts.clear(); self.notification_context = None
        self.closed_bars = 0; self.last_closed_bar = None
        self.snapshot_at = None; self.first_at = None
        self.anchor_end = None; self.window_close = None; self.hit_ends = {}; self.hit_closes = {}
        self.activation_at = self.last_time if since is None else min(since, self.last_time)

    def checks_while_active(self):
        """State conditions are re-checked while watching, unless the strategy chose AT_START."""
        return (self.meaning.get('lifecycle') or {}).get('precondition_check', 'WHILE_ACTIVE') == 'WHILE_ACTIVE'

    def _window_start(self, now):
        """Where the final wait begins: the first condition, or when all were gathered."""
        if self.meaning.get('final_window_from', 'ALL_CONDITIONS') != 'FIRST_CONDITION': return now
        if self.started is not None: return min(self.started, now)
        # Conditions that hold together: the earliest source event among them, else now.
        return now if self.first_at is None else min(self.first_at, now)

    def _window_over(self, now, expiry, until=None):
        """Whether the final wait ended: reaching a lifecycle seconds limit ends it, a
        final_window_sec still accepts its own last instant."""
        until = self.active_until if until is None else until
        if until is None: return False
        return now >= until if expiry.get('seconds') is not None else now > until

    def rewind(self, names):
        """Drop an invalid captured step and descendants, preserving ancestors."""
        indexes = [i for i, s in enumerate(self.meaning['steps']) if s.get('capture') in names]
        if not indexes:
            after = self.meaning.get('after_conditions', [])
            indexes = [i for i, s in enumerate(after) if s.get('capture') in names]
            if indexes:
                lost = {s.get('capture') for s in after[min(indexes):]}
                self.captures = {k:v for k,v in self.captures.items() if k not in lost}
                self.after_ready = False; self.group = None
                self.after_contexts = {i:ctx for i,ctx in self.after_contexts.items() if i < min(indexes)}
                self.notification_context = None
            return
        index = min(indexes)
        if index == 0:
            self._reset(); return
        lost = {s.get('capture') for s in self.meaning['steps'][index:]}
        self.captures = {k:v for k,v in self.captures.items() if k not in lost}
        self.stage = min(self.stage, index)
        self.hits = {i:t for i,t in self.hits.items() if i < index}
        self.hit_ends = {i:t for i,t in self.hit_ends.items() if i < index}
        self.hit_closes = {i:t for i,t in self.hit_closes.items() if i < index}
        self.step_contexts = {i:ctx for i,ctx in self.step_contexts.items() if i < index}
        self.after_contexts.clear(); self.notification_context = None
        # Keep consumed identities until a replacement event arrives. An old
        # event must not reactivate its invalid descendants on the next poll.
        self.active = False; self.active_until = None; self.after_ready = False
        self.setup_time = None; self.group = None
        self.closed_bars = 0; self.last_closed_bar = None
        preceding = [r.get('at') for r in self.captures.values() if r and r.get('at') is not None]
        self.sequence_at = max(preceding) if preceding else self.started

    def _chain_window(self):
        """The chain's window (수정본173): `within`, or within_sec as its seconds form; None without one."""
        window = self.meaning.get('within')
        if window is not None: return window
        gap = self.meaning.get('within_sec')
        return None if gap is None else {'seconds': gap}

    def _anchor_close(self, window, closer):
        """When a bar window that began at the chain's start ends (open of bar N+1); kept once known."""
        if 'seconds' in window: return None
        if self.window_close is None and closer is not None and self.started is not None:
            self.window_close = closer(self.started, self.anchor_end)
        return self.window_close

    def _hit_close(self, window, closer, i):
        if 'seconds' in window: return None
        if self.hit_closes.get(i) is None and closer is not None:
            self.hit_closes[i] = closer(self.hits[i], self.hit_ends.get(i))
        return self.hit_closes.get(i)

    def _expired(self, window, closer, now, previous=None):
        """Whether the chain's window (or, before this poll's collection, its previous start) is over."""
        if 'seconds' in window:
            gap = window['seconds']
            return any(at is not None and now - at > gap for at in ((previous or (None,))[0], self.started))
        if self.started is not None and windows.chain_over(window, self.started, self._anchor_close(window, closer), now):
            return True
        if previous is None or previous[0] is None or previous == (self.started, self.anchor_end) or closer is None:
            return False
        return windows.chain_over(window, previous[0], closer(*previous), now)

    def _reanchor(self, observations, order, window):
        """A newer occurrence of a sequence's first event restarts its wait from that event (수정본173).

        Follow-ups gathered so far came before it and are dropped; their tokens stay used.
        """
        if order != 'SEQUENTIAL' or window is None or self.stage == 0 or self.started is None: return False
        o = observations[0]
        if not (o.get('event') and self.matches(o)): return False
        token, at = o.get('token'), o.get('at')
        if token is None or at is None or token in self.used.values(): return False
        if self.activation_at is not None and at < self.activation_at: return False
        if at <= max(t for t in (self.started, self.sequence_at) if t is not None): return False
        self.stage = 0; self.hits.clear(); self.hit_ends.clear(); self.hit_closes.clear()
        self.started = None; self.anchor_end = None; self.window_close = None; self.sequence_at = None
        self.captures.clear(); self.step_contexts.clear(); self.first_at = None; self.source_tf = None
        return True

    def _collect_setup(self, now, observations, order, combine, window, closer=None):
        if order == 'SEQUENTIAL':
            while self.stage < len(observations):
                i = self.stage; o = observations[i]
                if not self._eligible(i, o): break
                # A state gate earlier in the sequence must still hold.
                if any(not self.matches(x) for x in observations[:i] if not x.get('event')): break
                # Event windows measure the source occurrence, not when the
                # current board first exposes it. States still gate at now.
                at = o['at'] if o.get('event') else now
                end = o.get('bar_end') if o.get('event') else None
                if self.started is None:
                    self.started = at; self.anchor_end = end; self.window_close = None
                if window is not None and not windows.within_chain(
                        window, self.started, self._anchor_close(window, closer), at, end): return False
                self._consume(i, o); self.stage += 1
            return self.stage == len(observations)
        if order == 'UNORDERED':
            for i, o in enumerate(observations):
                if self._eligible(i, o):
                    self.hits[i] = o['at'] if o.get('event') else now
                    self.hit_ends[i] = o.get('bar_end') if o.get('event') else None
                    self.hit_closes.pop(i, None)
                    self._consume(i, o)
                    if self.started is None: self.started = self.hits[i]
                elif not o.get('event') and not self.matches(o):
                    self.hits.pop(i, None); self.step_contexts.pop(i, None)
                    self.hit_ends.pop(i, None); self.hit_closes.pop(i, None)
            if window is not None:
                if 'seconds' in window:
                    latest = max(self.hits.values(), default=now)
                    self.hits = {i: t for i, t in self.hits.items() if latest - t <= window['seconds']}
                else:
                    # Every hit kept has the latest one inside its own window.
                    latest = max(self.hits, key=lambda i: self.hits[i], default=None)
                    if latest is not None:
                        at, end = self.hits[latest], self.hit_ends.get(latest)
                        self.hits = {i: t for i, t in self.hits.items()
                            if windows.within_chain(window, t, self._hit_close(window, closer, i), at, end)}
                self.hit_ends = {i: v for i, v in self.hit_ends.items() if i in self.hits}
                self.hit_closes = {i: v for i, v in self.hit_closes.items() if i in self.hits}
                self.step_contexts = {i: ctx for i, ctx in self.step_contexts.items() if i in self.hits}
                self.started = min(self.hits.values(), default=None)
                first = min(self.hits, key=lambda i: self.hits[i], default=None)
                self.anchor_end = None if first is None else self.hit_ends.get(first)
                self.window_close = None if first is None else self.hit_closes.get(first)
            return len(self.hits) == len(observations)
        eligible = [self._eligible(i, o) for i, o in enumerate(observations)]
        complete = any(eligible) if combine == 'ANY' else all(eligible)
        if complete:
            for i, o in enumerate(observations):
                if eligible[i]: self._consume(i, o)
        return complete

    def advance(self, now, observations, *, cancelled=False, after_observations=(),
                lifecycle_context=None, restarted=False, restart_at=None, window_close=None):
        """Source-time-only. Returns ARM/NOTIFY/CANCEL actions, never sends them.

        window_close(at, end): for a chain window in bars, when the window of an event at `at`
        (closed judgments: of a bar ending at `end`) ends, or None while it has not ended.
        """
        if self.disabled or (self.last_time is not None and now < self.last_time): return []
        self.last_time = now
        self.notification_context = None
        if self.activation_at is None: self.activation_at = now
        if len(observations) != len(self.meaning['steps']): raise ValueError('observation count mismatch')
        actions = []
        lifecycle = self.meaning.get('lifecycle') or {}
        context = lifecycle_context or {}
        if lifecycle.get('invalidate_refs') and context.get('invalid_refs'):
            if self.active: actions.append('CANCEL')
            self.rewind(context['invalid_refs'])
            return actions
        if restarted:
            if self.active: actions.append('CANCEL')
            self._reset(restart_at); self.state_hold = False
        if lifecycle.get('invalidate_refs') and context.get('refs_valid') is False:
            cancelled = True
        expiry = lifecycle.get('expires') or {}
        if self.active and expiry.get('bars') is not None:
            count = context.get('closed_bars')
            # The port resolves a configured bar count; the declared bars is the fallback.
            cancelled |= count is not None and count > context.get('bar_limit', expiry['bars'])
        if self.active and context.get('excursion_exceeded'):
            cancelled = True
        if self.state_hold and any(not self.matches(o) for o in observations):
            self.state_hold = False
        if cancelled:
            if self.active: actions.append('CANCEL')
            self.state_hold = not any(o.get('event') for o in observations)
            self._reset(); return actions
        if self.active and self._window_over(now, expiry):
            self.state_hold = not any(o.get('event') for o in observations)
            actions.append('CANCEL'); self._reset()
        order = self.meaning.get('order_mode', 'SIMULTANEOUS')
        combine = self.meaning.get('global_combine', 'ALL')
        chain = self._chain_window()
        if self.active:
            # Preconditions are checked all the while by default; AT_START checked them only
            # when the strategy began watching, so a later change never cancels it.
            watching = self.checks_while_active()
            # A newer board publication can precede its Fact delivery. Unknown
            # data suppresses final output without treating it as a false state.
            states_raw = [o for o in observations if not o.get('event')]
            if watching and any(not o.get('ready', True) for o in states_raw): return actions
            # Current-state conditions gate every final observation as well.
            states = [self.matches(o) for o in observations if not o.get('event')]
            alive = (any(states) if combine == 'ANY' else all(states)) if states and watching else True
            if not alive:
                actions.append('CANCEL'); self._reset()
            else:
                if not self.after_ready and self._after_matches(after_observations):
                    self.after_ready = True
                    kind = self.meaning['final']['kind']
                    action = 'ARM' if kind == 'OZ' else 'DEFINE' if kind == 'DEFINE' else 'NOTIFY'
                    actions.append(action)
                    if action == 'NOTIFY': self._notification_consumed(observations)
                return actions
        if self.state_hold: return actions
        pending_snapshot = lifecycle.get('snapshots') and (
            self.stage == len(observations) if order == 'SEQUENTIAL' else len(self.hits) == len(observations))
        if pending_snapshot and chain is not None and self.started is not None and self._expired(chain, window_close, now):
            self._reset()
        # A delivered chain can finish within its source window even when the
        # current board is later. Incomplete chains still expire at board time;
        # a later delivery cannot revive an activation already reset by a poll.
        for attempt in range(2):
            previous = (self.started, self.anchor_end)
            previous_used = dict(self.used) if chain is not None else None
            complete = self._collect_setup(now, observations, order, combine, chain, window_close)
            if not complete and self._reanchor(observations, order, chain):
                # The newer start replaced the incomplete chain; its own window is what counts.
                previous = None
                complete = self._collect_setup(now, observations, order, combine, chain, window_close)
            expired = chain is not None and self._expired(chain, window_close, now, previous)
            if complete or not expired: break
            self.used = previous_used
            self._reset()
            # Re-evaluate once so expiry does not discard a new first event
            # arriving in this same publication. Older events stay ineligible.
        if complete:
            requested = lifecycle.get('snapshots') or {}
            supplied = context.get('snapshots') or {}
            if any(k not in supplied for k in requested):
                if expired: self._reset()
                return actions
            self.snapshots = copy.deepcopy(supplied)
            window = expiry.get('seconds', self.meaning.get('final_window_sec'))
            until = self._window_start(now) + window if window is not None else None
            if self._window_over(now, expiry, until):
                # Counted from the first condition, the final wait was already over
                # when the last condition arrived. Nothing is armed for it.
                self._reset()
                return actions
            self.snapshot_at = context.get('snapshot_at')
            self.sequence += 1; self.active = True
            self.setup_time = now
            self.active_until = until
            kind = self.meaning['final']['kind']
            action = 'ARM' if kind == 'OZ' else 'DEFINE' if kind == 'DEFINE' else 'NOTIFY'
            self.after_ready = not self.meaning.get('after_conditions') or self._after_matches(after_observations)
            if self.after_ready: actions.append(action)
            if action == 'NOTIFY' and self.after_ready:
                # Hold a state activation until it turns false; event activations
                # are ready to accept the next distinct event immediately.
                self._notification_consumed(observations)
        return actions

    def _notification_consumed(self, observations):
        # This is metadata from actual consumed observations, frozen before an
        # event-only activation is reset. It never participates in matching.
        for i, (step, observation) in enumerate(zip(self.meaning['steps'], observations)):
            if observation.get('event'): continue
            if self.matches(observation):
                self.step_contexts[i] = self._observation_context(step, observation)
            else: self.step_contexts.pop(i, None)
        completed = self.after_contexts if self.meaning.get('after_conditions') else self.step_contexts
        steps = self.meaning.get('after_conditions') or self.meaning['steps']
        # A recent condition tells about the past: it never completes an alert (수정본173).
        present = {i: ctx for i, ctx in completed.items() if not (i < len(steps) and steps[i].get('recent'))}
        events = [ctx for ctx in present.values() if ctx['event']]
        # States gate a consumed market event; their polling time must never
        # replace its origin. An explicit after-stage supplies the completion.
        candidates = events or tuple(present.values()) or tuple(completed.values())
        final = max(reversed(tuple(candidates)), key=lambda ctx: ctx['at'], default={})
        context = {'source_tf': final.get('source_tf'), 'env_tf': self.environment_tf(), 'completion': copy.deepcopy(final),
                   'condition_tfs': tuple(dict.fromkeys(tf for ctx in (
                       *self.step_contexts.values(), *self.after_contexts.values()) for tf in ctx['condition_tfs']))}
        if all(not s.get('recent') and (s.get('_event_mode') or s['kind'] in _EVENT_KINDS_AT_NOTIFY)
                for s in self.meaning['steps']): self._reset()
        self.notification_context = context
        if not self.meaning.get('persistent', True): self.disabled = True

    def _after_matches(self, observations):
        expected = self.meaning.get('after_conditions', [])
        if len(observations) != len(expected): return False
        matches = all(self.matches(o) and (o.get('at', self.last_time) > self.setup_time if o.get('event') else
            o.get('at', self.last_time) >= self.setup_time)
            for o in observations)
        if matches:
            for i,(step, observation) in enumerate(zip(expected, observations)):
                self.after_contexts[i] = self._observation_context(step,observation)
                if step.get('capture'):
                    self.captures[step['capture']] = copy.deepcopy(observation.get('resource'))
        return matches

    def environment_tf(self):
        """The timeframe the first condition matched on: the alert's environment frame."""
        return (self.step_contexts.get(0) or {}).get('source_tf')

    def _observation_context(self,step,observation):
        # A recent condition tells about the past; it names no frame of this signal (수정본173).
        return {'kind':step['kind'],'event':bool(observation.get('event')),
                'source_tf':observation.get('source_tf'),
                'condition_tfs':() if observation.get('recent') is not None else
                    tuple(observation.get('condition_tfs') or (observation.get('source_tf'),)),
                'at':observation.get('at',self.last_time),'resource':copy.deepcopy(observation.get('resource'))}

    def final_allowed(self, now, observations, final_observations=()):
        expiry = (self.meaning.get('lifecycle') or {}).get('expires') or {}
        if not self.active or not self.after_ready or self.disabled or self._window_over(now, expiry): return False
        states = [self.matches(o) for o in observations if not o.get('event')]
        okay = any(states) if self.meaning.get('global_combine') == 'ANY' and states else all(states)
        # AT_START checked the state conditions when watching began; the final does not repeat it.
        okay = okay or not self.checks_while_active()
        return okay and all(self.matches(o) for o in final_observations)

    def delivered(self):
        if (self.meaning.get('lifecycle') or {}).get('first_success'):
            self.state_hold = not any(not s.get('recent') and (s.get('_event_mode') or s['kind'] in _EVENT_KINDS_AT_DELIVERY)
                for s in self.meaning['steps'])
            self._reset()
        if not self.meaning.get('persistent', True):
            self.disabled = True; self._reset()
