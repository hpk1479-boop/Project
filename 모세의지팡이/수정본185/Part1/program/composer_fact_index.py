"""Registration-time conservative (symbol, TF, fact family) dependencies."""
from collections import defaultdict
from collections.abc import MutableMapping


class SpecRegistry(MutableMapping):
    """Dictionary-compatible registry that cannot retain a stale dependency index.

    StrategySpec/ConditionSpec are frozen. Replacements, removals and restore
    assignments rebuild only the affected declaration. Unknown declarations are
    deliberately evaluated for every snapshot of their symbol.
    """
    families = {'TREND': 'TREND', 'TREND_METRIC': 'TREND', 'SWEEP': 'SWEEP',
                'FVG': 'FVG', 'WONBI': 'WONBI', 'PERCENTILE': 'PERCENTILE',
                'MA_STATE': 'MA', 'MA_PRICE_STATE': 'MA', 'MA_SLOPE_STATE': 'MA'}

    def __init__(self, values=()):
        self._items = {}; self._index = defaultdict(set); self._dependencies = {}
        self._fallback = defaultdict(set); self._order = {}; self._next = 0
        self.update(values)

    def __len__(self): return len(self._items)
    def __iter__(self): return iter(self._items)
    def __getitem__(self, key): return self._items[key]

    def __setitem__(self, key, spec):
        order = self._order.get(key, self._next)
        if key in self._items:
            old = self._items[key]
            for scope in self._dependencies.pop(key):
                self._index[scope].discard(key)
                if not self._index[scope]: del self._index[scope]
            self._fallback[getattr(old, 'symbol', '')].discard(key)
        self._next += 1; self._order[key] = order; self._items[key] = spec
        symbol = getattr(spec, 'symbol', '')
        dependencies = set(); known = bool(getattr(spec, 'conditions', ()))
        for condition in getattr(spec, 'conditions', ()):
            family = self.families.get(getattr(condition, 'kind', ''))
            tf = getattr(condition, 'tf', '')
            if not family or not tf: known = False
            else: dependencies.add((symbol, tf, family))
        self._dependencies[key] = dependencies
        for scope in dependencies: self._index[scope].add(key)
        if not known: self._fallback[symbol].add(key)

    def __delitem__(self, key):
        spec = self._items.pop(key)
        for scope in self._dependencies.pop(key):
            self._index[scope].discard(key)
            if not self._index[scope]: del self._index[scope]
        self._fallback[getattr(spec, 'symbol', '')].discard(key)
        self._order.pop(key)

    def affected(self, symbol, tf, family):
        keys = self._index.get((symbol, tf, family), set()) | self._fallback.get(symbol, set())
        return tuple(self._items[key] for key in sorted(keys, key=self._order.__getitem__)
                     if self._items[key].enabled)

    def declarations(self):
        return {key: {'dependencies': sorted(self._dependencies[key]),
                      'fallback': key in self._fallback.get(spec.symbol, ())}
                for key, spec in self._items.items()}
