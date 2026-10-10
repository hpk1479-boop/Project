"""Exact row materialization over a frozen invocation; no kernel/tracker writes."""
from dataclasses import dataclass, replace
from collections.abc import Sequence
from ...contracts import digest
from .provider import FeatureBundle, FeatureDatum, PROVIDER_VERSION
from .derived import StrategyStateProjector, SourceSignalProjector, staff_number

@dataclass(frozen=True)
class InvocationFrame:
    binding: object
    profile: object
    bars: tuple
    result: object
    token: object
    sequence: int
    input_problem: str | None
    events: object = None
    params_hash: str | None = None

    @classmethod
    def capture(cls, binding, profile, bars, result, token, sequence, tracker, params_hash=None):
        problem = "SOURCE_GAP" if any(b.gap_before or b.quality != "COMPLETE_PREFIX" for b in bars) else None
        frame = cls(binding, profile, tuple(bars), result, token, sequence, problem)
        latest = _cells_at(frame, 0) if problem is None else {}
        events = tracker.observe(result.event.calc_event_id,
            *(staff_number(latest.get(name), binding.seed_policy) for name in ("strategy_value", "lower", "upper")))
        return replace(frame, events=events, params_hash=digest(profile.inputs) if params_hash is None else params_hash)

def _cells_at(frame, shift):
    bars, result, profile = frame.bars, frame.result, frame.profile
    family = frame.binding.family
    if shift >= len(bars):
        return {}
    cells = {name: result.cell(name, shift) for name in (
        "lower", "upper", "basis", "regime_lower", "regime_upper")}
    cells["strategy_value"] = result.cell("hma" if family == "PRICE" else "smooth", shift)
    cells["source_arrow_lower"] = result.cell("arrow_lower", shift)
    cells["source_arrow_upper"] = result.cell("arrow_upper", shift)
    cells["source_basis_color"] = result.cell("color", shift)
    cells["raw_value"] = result.cell("raw", shift)
    cells["band_source_value"] = (result.cell("ehlers", shift) if profile.params["InpUseSmooth"]
                                        else cells["raw_value"]) if family == "PRICE" else result.cell("smooth", shift)
    return cells

class RowProjector:
    @staticmethod
    def project_row(frame, index):
        binding, profile, bars, result, token = frame.binding, frame.profile, frame.bars, frame.result, frame.token
        handle, family, policy = binding.handle, binding.family, binding.seed_policy
        input_problem, events = frame.input_problem, frame.events
        cells_at = lambda shift: _cells_at(frame, shift)
        shift = len(bars)-1-index
        bar, cells = bars[index], cells_at(shift)
        # Native snapshots retain bits even when consumer readiness is gated.
        consumed = cells if input_problem is None else {name: None for name in cells}
        strategy = StrategyStateProjector.project(family, consumed,
            cells_at(shift+1) if input_problem is None else {}, policy)
        fields = []
        for name, cell in cells.items():
            number = staff_number(cell, policy)
            reason = input_problem
            if reason is None and number is None:
                reason = ("UNDEFINED_SOURCE_STATE" if not cell.available or (policy == "SOURCE_DEFINED_ONLY" and not cell.source_defined)
                          else "SOURCE_EMPTY" if cell.numeric_class == "EMPTY_VALUE" else "NONFINITE_OR_DECODER_LIMIT")
                if result.event.R < (32 if family == "PRICE" else 39):
                    reason = "NATIVE_MINIMUM"
            valid = "UNAVAILABLE" if reason else ("SOURCE_DEFINED_UNCERTIFIED" if policy == "SOURCE_DEFINED_ONLY" else "DIAGNOSTIC_ONLY")
            mapping = ({"upper":0,"lower":1,"strategy_value":5,"source_arrow_lower":6,
                        "source_arrow_upper":7,"basis":8,"source_basis_color":9,
                        "regime_upper":10,"regime_lower":11,"band_source_value":12}
                       if family == "PRICE" else {"lower":0,"upper":1,"strategy_value":2,
                        "band_source_value":2,"source_arrow_lower":3,"source_arrow_upper":4,
                        "basis":5,"source_basis_color":6,"regime_upper":7,"regime_lower":8})
            accessor = ("close" if family == "PRICE" else "internal.rawArr") if name == "raw_value" else f"buffer[{mapping[name]}]"
            if family == "PRICE" and name == "band_source_value" and not profile.params["InpUseSmooth"]:
                accessor = "close"
            fields.append((name, FeatureDatum(name, cell, None if reason else number, valid, reason, accessor, shift)))
        identity = digest((handle, token, result.event, frame.sequence, bar.bar_id,
                           tuple((name, cell.bits, cell.taint) for name, cell in cells.items())))
        return FeatureBundle(handle, family, PROVIDER_VERSION, profile.algorithm_version,
            profile.source_hash, frame.params_hash, binding.symbol, binding.timeframe,
            bar.bar_id, bar.open_ns, bar.state, token, token.source_ordinal, frame.sequence,
            result.event.calc_event_id, result.event.R, result.event.P, result.event.history_epoch,
            binding.seed_id, policy, binding.calc_policy, tuple(fields), strategy,
            SourceSignalProjector.project(consumed, policy), events if shift == 0 else None, identity,
            tuple(result.metadata.items()))


class ProjectedRows(Sequence):
    """Cache belongs to this invocation only; old views retain their frozen frame."""
    def __init__(self, frame):
        self.frame = frame
        self.start = max(0, len(frame.bars)-650)
        self._cache = {}

    def __len__(self):
        return len(self.frame.bars)-self.start

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index not in self._cache:
            self._cache[index] = RowProjector.project_row(self.frame, self.start+index)
        return self._cache[index]

    def indices(self, completed=False, exclude_forming=False):
        return tuple(i for i in range(len(self))
                     if (not completed or self.frame.bars[self.start+i].state == "COMPLETED")
                     and (not exclude_forming or self.frame.bars[self.start+i].state != "FORMING"))
