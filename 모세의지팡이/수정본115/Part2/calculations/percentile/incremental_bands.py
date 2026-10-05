"""Exact retained order-statistic windows for the two ORIGINAL band algorithms.

This is not numpy.percentile and is not a histogram approximation. Grid mode
keeps the exact 101 threshold expressions, strict '<' and inclusive '>=' tests,
and call-local extrema dependencies. High precision retains the two different
source interpolation expression trees. NativeCell bits/origins/taints survive.

Only source writes and entering/leaving rows modify the retained sorted window.
The current forming row is a reversible preview over completed state, NEVER an
append-only tick stream. History reindex/reset/restore rebuilds derived state.
Exceptional nonfinite/mixed-signed-zero grid inputs use the unchanged reference
row; their native ordering and comparison semantics are not guessed.
"""
from __future__ import annotations
from bisect import bisect_left, insort_right
from collections import Counter, deque
import math
from .contracts import NativeCell
from .numeric import cell, add, sub, mul
from .bands import Grid100BandEngine


class RollingNativeWindow:
    def __init__(self, period: int):
        if type(period) is not int or period < 1:
            raise ValueError('positive integral window required')
        self.period = period
        self.ids = ()
        self.order = deque()  # chronological bar identities, not observations
        self.cells = {}
        self.entries = {}
        self.sorted = []  # (numeric value, -chronological position, bar ID)
        self.taint_counts = Counter()
        self._taints = ()
        self._taints_dirty = False
        self.unavailable = self.nan_count = self.pos_zero = self.neg_zero = self.infinite = 0
        self.start = 0
        self.end = -1
        self.forming = True
        self.preview = None
        self.completed_bar_id = None
        self.market_refs = {}
        self.stats = dict(full_window_builds=0, inserts=0, removals=0,
                          dirty_replacements=0, preview_rows=0,
                          completed_bar_advances=0, order_statistic_rows=0,
                          reference_exception_rows=0, full_sorts=0)

    def reset(self):
        self.order.clear(); self.cells.clear(); self.entries.clear(); self.sorted.clear()
        self.taint_counts.clear(); self._taints = (); self._taints_dirty = False
        self.unavailable = self.nan_count = self.pos_zero = self.neg_zero = self.infinite = 0
        self.start = 0; self.end = -1
        self.preview = None; self.market_refs.clear()

    def bind(self, ids, layout, forming):
        if layout == 'REINDEX':
            self.reset()
        self.ids = ids
        self.forming = bool(forming)
        # SAME/APPEND retain chronological indices. Reindex fallback also
        # protects diagnostics which deliberately insert historical identities.
        if self.order and (self.end >= len(ids) or ids[self.end] != self.order[-1]
                           or ids[self.start] != self.order[0]):
            self.reset()
        completed = ids[-2] if forming and len(ids) > 1 else (ids[-1] if ids and not forming else None)
        if completed != self.completed_bar_id:
            self.completed_bar_id = completed
            self.stats['completed_bar_advances'] += int(completed is not None)
        if self.preview is not None and (not ids or self.preview[0] != ids[-1] or not forming):
            self.preview = None

    def _account(self, value, sign):
        if value.taint:
            for t in value.taint:
                self.taint_counts[t] += sign
                if not self.taint_counts[t]:del self.taint_counts[t]
            self._taints_dirty = True
        if not value.available:
            self.unavailable += sign
            return
        x = value.value
        if math.isnan(x):self.nan_count += sign
        elif math.isinf(x):self.infinite += sign
        elif x == 0.0:
            if value.bits >> 63:self.neg_zero += sign
            else:self.pos_zero += sign

    def _insert(self, key, value, position, *, left=False):
        if key in self.cells:raise ValueError('duplicate rolling bar identity')
        value = cell(value)
        self.cells[key] = value
        self._account(value, 1)
        if value.available and not math.isnan(value.value):
            entry = (value.value, -position, key)
            insort_right(self.sorted, entry); self.entries[key] = entry
        if left:self.order.appendleft(key)
        else:self.order.append(key)
        self.stats['inserts'] += 1

    def _remove(self, *, left=False):
        key = self.order.popleft() if left else self.order.pop()
        value = self.cells.pop(key)
        self._account(value, -1)
        entry = self.entries.pop(key, None)
        if entry is not None:
            i = bisect_left(self.sorted, entry)
            if i == len(self.sorted) or self.sorted[i] != entry:
                raise AssertionError('rolling order-statistic removal mismatch')
            self.sorted.pop(i)
        self.market_refs.pop(key, None)
        self.stats['removals'] += 1

    def replace_source(self, shift, value):
        position = len(self.ids)-1-shift
        if not 0 <= position < len(self.ids):return
        key = self.ids[position]
        old = self.cells.get(key)
        if old is None:return
        value = cell(value)
        # Rewrites update provenance too, even when the numeric bits agree.
        if old == value:return
        if old.bits != value.bits:
            entry = self.entries.pop(key, None)
            if entry is not None:self.sorted.pop(bisect_left(self.sorted, entry))
            self._account(old, -1); self._account(value, 1)
            if value.available and not math.isnan(value.value):
                entry = (value.value, -position, key)
                insort_right(self.sorted, entry); self.entries[key] = entry
        elif old.taint != value.taint:
            # Avoid touching the order statistic when only dependencies change.
            self._account(old, -1); self._account(value, 1)
        self.cells[key] = value
        self.stats['dirty_replacements'] += 1

    def sync_market(self, bars, source):
        # Unsmoothed PRICE: immutable completed bars are reused by identity.
        # Mutable mapping diagnostics are conservatively read again. No window
        # is reconstructed or sorted; only genuinely changed cells are replaced.
        for position in range(self.start, self.end+1):
            key = self.ids[position]; bar = bars[position]
            if isinstance(bar, dict) or self.market_refs.get(key) is not bar:
                self.replace_source(len(self.ids)-1-position, source(len(self.ids)-1-position))
                self.market_refs[key] = bar

    @property
    def taints(self):
        if self._taints_dirty:
            self._taints = tuple(sorted(self.taint_counts))
            self._taints_dirty = False
        return self._taints

    def dependency_taints(self, extras=()):
        if not extras:return self.taints
        all_taints = set(self.taints)
        for v in extras:all_taints.update(v.taint)
        return tuple(sorted(all_taints))

    def control(self, value):
        value = cell(value)
        ts = self.taints if not value.taint else tuple(sorted(set(self.taints).union(value.taint)))
        return NativeCell(value.bits, value.origin, ts)

    def values(self):
        # Exceptional reference fallback only; the normal path never calls it.
        return tuple(self.cells[key] for key in reversed(self.order))

    def move(self, end, source):
        if end < 0:
            self.reset()
            return
        if not self.order:
            start = max(0, end-self.period+1)
            for pos in range(start, end+1):
                self._insert(self.ids[pos], source(len(self.ids)-1-pos), pos)
            self.start, self.end = start, end
            self.stats['full_window_builds'] += 1
            return
        # Sliding back is needed for the preceding row's native recalculation;
        # sliding forward is one remove + one add, including preview rollback.
        while self.end > end:
            self._remove(); self.end -= 1
            wanted = max(0, self.end-self.period+1)
            if self.start > wanted:
                self.start -= 1
                self._insert(self.ids[self.start], source(len(self.ids)-1-self.start), self.start, left=True)
        while self.end < end:
            self.end += 1
            self._insert(self.ids[self.end], source(len(self.ids)-1-self.end), self.end)
            if len(self.order) > self.period:
                self._remove(left=True); self.start += 1

    def evaluate(self, shift, source, calculate):
        target = len(self.ids)-1-shift
        if self.forming and shift == 0:
            # Persistent window ends at the last completed row. A preview owns
            # one bar ID and is undone even if calculation raises an exception.
            self.move(target-1, source)
            self.preview = (self.ids[target], cell(source(0)))
            self.move(target, source)
            self.stats['preview_rows'] += 1
            try:return calculate()
            finally:self.move(target-1, source)
        self.move(target, source)
        return calculate()

    def percentile(self, percent, *, price):
        if self.unavailable:return NativeCell(None, 'SOURCE_WRITTEN', self.taints)
        if self.nan_count:return NativeCell.unknown('UNVERIFIED_NATIVE_ARRAYSORT_NAN')
        if self.pos_zero and self.neg_zero:
            return NativeCell(None, 'UNVERIFIED_NATIVE_DEPENDENCY', ('UNVERIFIED_NATIVE_ARRAYSORT_MIXED_SIGNED_ZERO',))
        n = len(self.sorted)
        if not n:return cell(0.0)
        at = lambda i:self.cells[self.sorted[i][2]]
        if not price and n == 1:return at(0)
        rank = (percent/100.0)*(n-1)
        lo = math.floor(rank)
        if price:
            hi = math.ceil(rank)
            if lo < 0:lo = 0
            if hi >= n:hi = n-1
            if not (0 <= lo < n and 0 <= hi < n):return NativeCell.unknown('SOURCE_ARRAY_BOUNDS')
            if lo == hi:return self.control(at(lo))
            w = rank-lo
            return self.control(add(mul(at(lo), 1.0-w), mul(at(hi), w)))
        frac = rank-lo
        if lo >= n-1:return self.control(at(n-1))
        if lo < 0:return self.control(at(0))
        return self.control(add(at(lo), mul(frac, sub(at(lo+1), at(lo)))))


class IncrementalBandEngine:
    """Call-local extrema over a kernel-owned retained source window."""
    def __init__(self, kernel, source, period, leveling, *, precision=False, price=False):
        if kernel._band_window is None or kernel._band_window.period != period:
            kernel._band_window = RollingNativeWindow(period)
        self.window = kernel._band_window
        bars = kernel.bars.values
        last_state = bars[-1].get('state', 'FORMING') if bars and isinstance(bars[-1], dict) else (
            getattr(bars[-1], 'state', 'FORMING') if bars else 'FORMING')
        self.window.bind(kernel.state.bar_ids, kernel.state.layout_kind, last_state != 'COMPLETED')
        self.source = source
        self.period = period; self.leveling = leveling
        self.target = math.ceil(period*leveling/100.0)
        self.precision = precision; self.price = price
        self.minimum = cell(0.0); self.maximum = cell(0.0)
        if price:
            if kernel.profile.params['InpUseSmooth']:
                # Ehlers is executed first in the PRICE source. Propagate only
                # those exact source writes into retained membership.
                for write in kernel.state.writes:
                    if write.buffer == 'ehlers':self.window.replace_source(write.shift, write.cell)
            else:self.window.sync_market(bars, source)

    def row(self, shift, *, first=False, boundary=False, outgoing=None):
        if not self.price:
            self.window.replace_source(shift, self.source(shift))
        def calculate():
            self.window.stats['order_statistic_rows'] += 1
            if self.precision:
                return (self.window.percentile(self.leveling, price=self.price),
                        self.window.percentile(100.0-self.leveling, price=self.price))
            return self._grid(first, boundary, outgoing)
        return self.window.evaluate(shift, self.source, calculate)

    def _grid(self, first, boundary, outgoing):
        w = self.window
        if not w.order:return None, None
        extras = ()
        rebuild = first or boundary
        if not rebuild:
            outgoing = cell(outgoing) if outgoing is not None else NativeCell.unknown('OUTGOING_UNAVAILABLE')
            extras = (outgoing, self.minimum, self.maximum)
        deps_taints = w.dependency_taints(extras)
        if w.unavailable or any(not v.available for v in extras):
            unknown = NativeCell(None, 'SOURCE_WRITTEN', deps_taints)
            self.minimum = self.maximum = unknown
            return unknown, unknown
        if w.nan_count or w.infinite or (w.pos_zero and w.neg_zero):
            w.stats['reference_exception_rows'] += 1
            return Grid100BandEngine.row(self, w.values(), first=first, boundary=boundary, outgoing=outgoing)
        if not rebuild:
            rebuild = outgoing.value == self.minimum.value or outgoing.value == self.maximum.value
        if rebuild:
            low = w.sorted[0][0]; high = w.sorted[-1][0]
        else:
            low = self.minimum.value; high = self.maximum.value
            newest = w.cells[w.order[-1]].value
            if newest < low:low = newest
            if newest > high:high = newest
        def result(x):return NativeCell(cell(x).bits, 'SOURCE_LITERAL', deps_taints)
        self.minimum = result(low); self.maximum = result(high)
        if low == high:return self.minimum, self.maximum
        step = (high-low)*0.01
        lower = upper = None
        for s in range(101):
            threshold = low+s*step
            count = 0 if math.isnan(threshold) else bisect_left(w.sorted, (threshold, float('-inf')))
            if count >= self.target:
                lower = result(threshold); break
        for s in range(101):
            threshold = high-s*step
            count = 0 if math.isnan(threshold) else len(w.sorted)-bisect_left(w.sorted, (threshold, float('-inf')))
            if count >= self.target:
                upper = result(threshold); break
        return lower, upper
