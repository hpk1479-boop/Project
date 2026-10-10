"""Private source sessions over the existing immutable PIT market view."""
from dataclasses import dataclass, replace
from types import MappingProxyType

from ...contracts import PitError, digest
from ...models import PitView
from .contracts import CalcEvent, NativeCell
from .derived import (ObservedBoundaryTracker, StrategyStateProjector,
                      SourceSignalProjector, staff_number)
from .provider import FeatureBundle, FeatureDatum, PROVIDER_VERSION


@dataclass(frozen=True)
class PercentileSessionView:
    token: object
    rows: tuple

    def _rows(self, handle):
        for key, values in self.rows:
            if key == handle:
                return values
        raise PitError("E_UNDECLARED_DEPENDENCY", "foreign/unknown handle")

    def read(self, handle, field=None, shift=0, role=None):
        rows = self._rows(handle)
        if role == "COMPLETED":
            rows = tuple(row for row in rows if row.bar_role == "COMPLETED")
        elif role not in (None, "FORMING"):
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "bar role")
        if type(shift) is not int or shift < 0 or shift >= len(rows):
            raise PitError("E_FEATURE_NOT_AVAILABLE", "insufficient history")
        row = rows[-1-shift]
        if role == "FORMING" and row.bar_role != "FORMING":
            raise PitError("E_FEATURE_NOT_AVAILABLE", "no forming bar")
        return row if field is None else row.field(field)

    def window(self, handle, count=650, include_forming=True):
        if type(count) is not int or not 0 < count <= 650:
            raise PitError("E_RESOURCE_LIMIT", "feature window")
        rows = self._rows(handle)
        if not include_forming:
            rows = tuple(row for row in rows if row.bar_role != "FORMING")
        return rows[-count:]

    def readiness(self, handle, shift=0):
        row = self.read(handle, shift=shift)
        return MappingProxyType({name: value.unavailable_reason or value.validity
                                 for name, value in row.fields})


@dataclass(frozen=True)
class DemandSessionView:
    token: object
    _entries: tuple

    @property
    def rows(self):
        # Explicit full consumers receive the original full tuple, never sparse rows.
        return tuple((handle, tuple(rows)) for handle, rows in self._entries)

    def _rows(self, handle):
        for key, rows in self._entries:
            if key == handle:
                return rows
        raise PitError("E_UNDECLARED_DEPENDENCY", "foreign/unknown handle")

    @staticmethod
    def _indices(rows, completed=False, exclude_forming=False):
        from .projection import ProjectedRows
        if isinstance(rows, ProjectedRows):
            return rows.indices(completed, exclude_forming)
        return tuple(i for i, row in enumerate(rows)
                     if (not completed or row.bar_role == "COMPLETED")
                     and (not exclude_forming or row.bar_role != "FORMING"))

    def read(self, handle, field=None, shift=0, role=None):
        rows = self._rows(handle)
        if role not in (None, "FORMING", "COMPLETED"):
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "bar role")
        indices = self._indices(rows, role == "COMPLETED")
        if type(shift) is not int or shift < 0 or shift >= len(indices):
            raise PitError("E_FEATURE_NOT_AVAILABLE", "insufficient history")
        row = rows[indices[-1-shift]]
        if role == "FORMING" and row.bar_role != "FORMING":
            raise PitError("E_FEATURE_NOT_AVAILABLE", "no forming bar")
        return row if field is None else row.field(field)

    def window(self, handle, count=650, include_forming=True):
        if type(count) is not int or not 0 < count <= 650:
            raise PitError("E_RESOURCE_LIMIT", "feature window")
        rows = self._rows(handle)
        indices = self._indices(rows, exclude_forming=not include_forming)
        return tuple(rows[i] for i in indices[-count:])

    def readiness(self, handle, shift=0):
        row = self.read(handle, shift=shift)
        return MappingProxyType({name: value.unavailable_reason or value.validity
                                 for name, value in row.fields})


def _kernel(profile):
    # Imports are local to avoid loading optional raw dependencies at registry import.
    from .kernels.price import PriceSourceKernel
    from .kernels.rsi import RsiSourceKernel
    from .kernels.stochastic import StoSourceKernel
    from .kernels.disparity import DiSourceKernel
    return {"PRICE": PriceSourceKernel, "RSI": RsiSourceKernel,
            "STO": StoSourceKernel, "DI": DiSourceKernel}[profile.family](profile)


class PercentileFeatureSession:
    def __init__(self, owner, provider=None):
        if not owner:
            raise PitError("E_ID_COLLISION", "owner")
        self.owner, self.provider = owner, provider
        from .digest_cache import MarketDigestCache
        self._digest_cache = MarketDigestCache()
        self._params_hashes = {}
        self._history_positions = {}
        self.materialization = getattr(provider, "materialization", "EAGER")
        self._bindings, self._profiles, self._kernels = {}, {}, {}
        self._history, self._rows, self._seen = {}, {}, {}
        self._returned, self._epochs, self._sequences, self._trackers = {}, {}, {}, {}
        self._token = None
        self._market_signature = None

    def _bind(self, binding, profile):
        handle = binding.handle
        if handle in self._bindings:
            return
        self._bindings[handle], self._profiles[handle] = binding, profile
        self._params_hashes[handle] = digest(profile.inputs)
        self._kernels[handle] = _kernel(profile)
        self._kernels[handle].state.freeze_versions = self.materialization == 'DEMAND'
        from .fingerprint_store import ExactFingerprintStore
        self._seen[handle], self._returned[handle], self._sequences[handle] = ExactFingerprintStore(), 0, 0
        self._trackers[handle] = ObservedBoundaryTracker()

    def bind_bundle(self, symbol, tf, params=None, seed="SOURCE_DEFINED_ONLY", calc_policy="PIT_TICK"):
        if self.provider is None:
            from .provider import PercentileFeatureProvider
            self.provider = PercentileFeatureProvider()
            self.provider._sessions[self.owner] = self
        return self.provider.bind_bundle(self.owner, symbol, tf, params, seed, calc_policy)

    def _ingest(self, view):
        if not isinstance(view, PitView):
            raise PitError("E_CURRENT_PIT_API_UNVERIFIED", "actual PitView is required")
        token = view.token
        signature = self._digest_cache(view)
        if self._token is not None:
            old = self._token
            if token == old:
                if signature != self._market_signature:
                    raise PitError("E_CHECKPOINT_PREFIX", "same token has different market data")
                return self._history, signature
            if (token.market_epoch != old.market_epoch or token.source_ordinal <= old.source_ordinal
                    or token.now_ns < old.now_ns or token.microstep < old.microstep):
                raise PitError("E_OBSERVER_ORDER", "cannot rewind or change market epoch")
            if token.boundary_version != old.boundary_version or token.profile != old.profile:
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "market policy changed")
        histories = dict(self._history)
        for binding in self._bindings.values():
            key = (binding.symbol, binding.timeframe)
            if key in histories and histories[key] is not self._history.get(key):
                continue
            if view.symbol != binding.symbol:
                raise PitError("E_UNDECLARED_DEPENDENCY", "market symbol differs")
            incoming = tuple(view.bars(binding.timeframe))
            if any(b.symbol != binding.symbol or b.timeframe != binding.timeframe for b in incoming):
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "bar feed identity")
            if len({b.bar_id for b in incoming}) != len(incoming) or any(
                    a.open_ns >= b.open_ns for a, b in zip(incoming, incoming[1:])):
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "unordered or duplicate bars")
            if any(b.open_ns > token.now_ns or b.last_ordinal > token.source_ordinal
                   or (b.state == "COMPLETED" and b.nominal_end_ns > token.now_ns)
                   or (b.complete_at_order is not None and b.complete_at_order > token.source_ordinal)
                   for b in incoming):
                raise PitError("E_FUTURE_READ", "market bar exceeds prefix")
            old = self._history.get(key, ())
            if not old:
                histories[key] = incoming
                continue
            if not incoming:
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "history disappeared")
            cached = self._history_positions.get(key)
            if cached is not None and cached[0] is old:
                positions = cached[1]
            elif cached is not None and len(cached[0]) == len(old) and all(
                    a.bar_id == b.bar_id for a, b in zip(cached[0], old)):
                positions = cached[1]
            else:
                positions = {b.bar_id: i for i, b in enumerate(old)}
            self._history_positions[key] = (old, positions)
            if incoming[0].bar_id not in positions:
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "missed history beyond retained PIT window")
            start = positions[incoming[0].bar_id]
            overlap = min(len(old)-start, len(incoming))
            if overlap != len(old)-start:
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "history shrank")
            for before, after in zip(old[start:], incoming[:overlap]):
                if before is after:
                    continue
                if before.bar_id != after.bar_id or before.open_ns != after.open_ns:
                    raise PitError("E_HISTORY_EPOCH_MISMATCH", "history insertion/reordering")
                if before.state == "COMPLETED" and before != after:
                    raise PitError("E_HISTORY_EPOCH_MISMATCH", "completed market candle changed")
                if before.state == "FORMING" and (after.open != before.open
                        or after.high < before.high or after.low > before.low
                        or after.tick_volume < before.tick_volume):
                    raise PitError("E_HISTORY_EPOCH_MISMATCH", "forming candle regressed")
            histories[key] = old[:start] + incoming
        return histories, signature

    @staticmethod
    def _input_for(value, handle, family):
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get(handle, value.get(family))
        return value

    def advance(self, view, events=None, *, captured_prestate=None, raw_input=None):
        histories, signature = self._ingest(view)
        token = view.token
        plans = []
        if events is not None and not isinstance(events, (dict, CalcEvent)):
            raise PitError("E_CALCULATION_POLICY_MISMATCH", "event or family/handle event mapping required")
        if isinstance(events, dict) and set(events) - set(self._bindings) - {
                b.family for b in self._bindings.values()}:
            raise PitError("E_UNDECLARED_DEPENDENCY", "unknown event target")
        if token != self._token and isinstance(events, dict) and any(
                handle not in events and binding.family not in events
                for handle, binding in self._bindings.items()):
            raise PitError("E_CALCULATION_POLICY_MISMATCH", "new market view requires an event for every binding")
        for handle, binding in self._bindings.items():
            bars = histories[(binding.symbol, binding.timeframe)]
            event = self._input_for(events, handle, binding.family)
            if event is None:
                if events is not None:
                    continue
                if binding.calc_policy != "PIT_TICK":
                    raise PitError("E_CALCULATION_POLICY_MISMATCH", "NATIVE_TRACE requires explicit events")
                if token == self._token:
                    continue
                event = CalcEvent(digest(("PIT_TICK", handle, token)), len(bars), self._returned[handle],
                                  self._epochs.get(handle, token.market_epoch), token.source_ordinal, token)
            capture = self._input_for(captured_prestate, handle, binding.family)
            raw = self._input_for(raw_input, handle, binding.family)
            if event.market_asof_token not in (None, token) or event.source_ordinal not in (0, token.source_ordinal):
                raise PitError("E_FUTURE_READ", "calc event does not match immutable market view")
            event = replace(event, market_asof_token=token, source_ordinal=token.source_ordinal)
            fingerprint = digest((event, capture, raw))
            if event.calc_event_id in self._seen[handle]:
                if self._seen[handle][event.calc_event_id] != fingerprint:
                    raise PitError("E_ID_COLLISION", "calc event id reused with different inputs")
                continue
            if event.R != len(bars) or event.P != self._returned[handle]:
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "R/P does not match retained history/actual prior return")
            if event.reset_reason is not None or (handle in self._epochs and event.history_epoch != self._epochs[handle]):
                raise PitError("E_HISTORY_EPOCH_MISMATCH", "history reset requires an explicit new session")
            if capture is not None and (binding.seed_policy != "NATIVE_STATE_CONDITIONED" or self._sequences[handle]):
                raise PitError("E_SEED_AFTER_MEASUREMENT", "capture is allowed only on first diagnostic call")
            plans.append((handle, binding, bars, event, capture, raw, fingerprint))
        # Validate every event before invoking any family. A native return 0 is
        # still a committed invocation; kernel writes must never be rolled back.
        for handle, binding, bars, event, capture, raw, fingerprint in plans:
            result = self._kernels[handle].invoke(bars, event, captured_prestate=capture, raw_input=raw)
            self._sequences[handle] += 1
            self._returned[handle], self._epochs[handle] = result.returned, event.history_epoch
            self._rows[handle] = self._project(handle, binding, bars, result, token)
            self._seen[handle][event.calc_event_id] = fingerprint
        self._history, self._market_signature, self._token = histories, signature, token
        return self.view(token)

    def _project(self, handle, binding, bars, result, token):
        from .projection import InvocationFrame, ProjectedRows
        frame = InvocationFrame.capture(binding, self._profiles[handle], bars, result,
            token, self._sequences[handle], self._trackers[handle], self._params_hashes[handle])
        rows = ProjectedRows(frame)
        return rows if self.materialization == "DEMAND" else tuple(rows)

    def view(self, token=None):
        if token is not None and token != self._token:
            raise PitError("E_FUTURE_READ", "retain immutable feature views for earlier tokens")
        view_type = DemandSessionView if self.materialization == "DEMAND" else PercentileSessionView
        return view_type(self._token, tuple(self._rows.items()))

    def read(self, handle, token, field=None, shift=0, role=None):
        return self.view(token).read(handle, field, shift, role)

    def window(self, handle, token, count=650, include_forming=True):
        return self.view(token).window(handle, count, include_forming)

    def readiness(self, handle, token, shift=0):
        return self.view(token).readiness(handle, shift)

    def checkpoint(self):
        from .checkpoint import snapshot_session
        return snapshot_session(self)

    def state_hash(self):
        return self.checkpoint().sha256
