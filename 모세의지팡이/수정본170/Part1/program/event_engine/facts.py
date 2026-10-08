"""Event-scoped lazy DAG over the one canonical indicator_facts registry."""
from types import SimpleNamespace
from types import MappingProxyType
from collections.abc import Mapping
from functools import lru_cache
import numpy as np
import pandas as pd
from indicator_facts import FACTS
from staff_schema import PIPE_VALUE_COLUMNS
from .model import freeze, same_immutable_array


# The Wire column tuple is fixed for the loaded schema. Keep first-occurrence
# semantics of tuple.index, including its ValueError for unknown column names.
_COLUMN_INDEX = {}
for _index, _name in enumerate(PIPE_VALUE_COLUMNS):
    _COLUMN_INDEX.setdefault(_name, _index)


def _resolve_dependencies(name, stack, specs):
    if name in stack:
        raise ValueError('Fact dependency cycle: ' + name)
    spec = FACTS[name]
    specs[name] = spec
    result = set()
    for dep in spec.deps:
        if dep.startswith('@'):
            result.add(dep[1:])
        else:
            result.update(_resolve_dependencies(dep, stack + (name,), specs))
    return frozenset(result | {'time'})


@lru_cache(maxsize=256)
def _dependency_plan(name):
    # FactSpec is frozen. Retain every visited definition, not only the root,
    # so replacing/removing a transitive dependency cannot leave stale inputs.
    specs = {}
    columns = _resolve_dependencies(name, (), specs)
    return columns, tuple(specs.items())


def dependencies(name, stack=()):
    if stack:
        # Explicit traversal ancestry still participates in cycle detection.
        return _resolve_dependencies(name, stack, {})
    columns, specs = _dependency_plan(name)
    if any(FACTS.get(key) is not spec for key, spec in specs):
        _dependency_plan.cache_clear()
        columns, _ = _dependency_plan(name)
    return columns


def validate_reference(name, requester):
    spec = FACTS[name]
    if not spec.shared and requester != spec.owner:
        raise ValueError('private Fact belongs to ' + spec.owner + ': ' + name)
    for dep in spec.deps:
        if not dep.startswith('@'):
            validate_reference(dep, spec.owner)


def array(snapshot, name):
    if name in ('time', 'volume'):
        return getattr(snapshot, name)
    index = _COLUMN_INDEX.get(name)
    if index is None:
        index = PIPE_VALUE_COLUMNS.index(name)  # Preserve unknown-column error.
    return snapshot.values[:, index]


class EventFacts:
    def __init__(self, parameters=None):
        self.parameters=MappingProxyType(dict(parameters or {}))
        self._entries = {}
        self.computed = {}
        self._recurrences={}

    def remove(self,keys):
        for key in keys:
            self._entries.pop(key,None)
            for identity in tuple(self._recurrences):
                if identity[:2]==key:self._recurrences.pop(identity)

    def invalidate(self, changed):
        # Publication replacement is atomic with Board commit. Only these keys
        # are visited; unchanged symbols/TFs are never invalidated.
        for key, snapshot in changed.items():
            old = self._entries.get(key)
            carried = {}
            if old is not None and old[0].source_epoch == snapshot.source_epoch:
                prior, values = old
                # Exact comparisons are shared only within this key/snapshot
                # pair. Closed-bar and forming-bar slices have separate keys.
                comparisons = {}
                for name, value in values.items():
                    spec = FACTS[name]
                    cut = slice(None, -1) if spec.invalidation == 'bar_close' else slice(None)
                    for col in dependencies(name):
                        identity = (spec.invalidation, col)
                        if identity not in comparisons:
                            before, after = array(prior, col)[cut], array(snapshot, col)[cut]
                            comparisons[identity] = same_immutable_array(before, after) or np.array_equal(
                                before, after, equal_nan=True)
                        if not comparisons[identity]:
                            break
                    else:
                        carried[name] = value
            self._entries[key] = (snapshot, carried)

    def get(self, symbol, timeframe, snapshot, name, requester):
        validate_reference(name, requester)
        entry = self._entries.get((symbol, timeframe))
        values = entry[1] if entry is not None and entry[0] is snapshot else {}
        if name in values:
            # Authorization above is never bypassed. Preserve freeze(), including
            # an independent ndarray shape header on every public read.
            value = values[name]
            if isinstance(value,(np.ndarray,Mapping)):return freeze(value)
            return freeze(value.to_numpy() if isinstance(value, (pd.Series, pd.DataFrame)) else value)
        resolving = set()
        frames = {}

        def compute(target):
            if target in values:
                return values[target]
            if target in resolving:
                raise ValueError('Fact cycle: ' + target)
            resolving.add(target)
            spec = FACTS[target]
            cut = slice(None, -1) if spec.invalidation == 'bar_close' else slice(None)
            # All dependency nodes share this frame/axis. Mixing invalidation
            # axes across a dependency edge is explicitly rejected.
            for dep in spec.deps:
                if not dep.startswith('@') and FACTS[dep].invalidation != spec.invalidation:
                    raise ValueError('Fact dependency axes differ: ' + target)
            frame_key = (spec.invalidation, spec.array_compute is not None, target)
            if frame_key not in frames:
                data = {col: array(snapshot, col)[cut] for col in dependencies(target)}
                if spec.array_compute is None:
                    data['time'] = pd.to_datetime(data['time'], unit='s')
                    data = pd.DataFrame(data)
                frames[frame_key] = SimpleNamespace(df=data, tf=timeframe,parameters=self.parameters)
            frame = frames[frame_key]
            args = [None if dep.startswith('@') else compute(dep) for dep in spec.deps]
            if spec.array_compute is None:
                args = [pd.Series(x) if isinstance(x,np.ndarray) and x.ndim==1 else x for x in args]
            if spec.recurrence is not None:
                from .recurrence import EWMPrefix
                family,period=spec.recurrence
                if family!='rma' or len(args)!=1:raise ValueError('unsupported Fact recurrence: '+target)
                alpha=1./(1.+((1.-1./period)/(1./period)))
                cached=self._recurrences.setdefault((symbol,timeframe,target),EWMPrefix())
                value=cached.evaluate(snapshot.time[cut],args[0],snapshot.source_epoch,alpha,period)
            else:value = (spec.array_compute or spec.compute)(frame, *args)
            if isinstance(value,(np.ndarray,dict)):value=freeze(value)
            values[target] = value
            self.computed[(symbol, timeframe, target)] = self.computed.get((symbol, timeframe, target), 0) + 1
            resolving.remove(target)
            return value

        value = compute(name)
        if isinstance(value,(np.ndarray,Mapping)):return freeze(value)
        return freeze(value.to_numpy() if isinstance(value, (pd.Series, pd.DataFrame)) else value)

    def clear(self):
        self._entries.clear()
        self._recurrences.clear()


def owner_table(consumers=()):
    return tuple({'name': name, 'owner': spec.owner, 'dependencies': spec.deps,
                  'invalidation': spec.invalidation, 'shared': spec.shared,
                  'consumers': tuple(c.name for c in consumers if name in c.subscriptions().facts)}
                 for name, spec in sorted(FACTS.items()))
