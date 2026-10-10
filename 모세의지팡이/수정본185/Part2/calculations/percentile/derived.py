"""STAFF projections and explicitly separate observed event namespaces.

These are consumer projections. Native cells are never normalized in storage.
"""
from dataclasses import dataclass
import math

from ...contracts import PitError, digest


def staff_number(cell, seed_policy="SOURCE_DEFINED_ONLY"):
    """Apply STAFF's decoder boundary only when a cell is consumed."""
    if cell is None:
        return None
    if hasattr(cell, "bits"):
        if not cell.available:
            return None
        if seed_policy == "SOURCE_DEFINED_ONLY" and not cell.source_defined:
            return None
        value = cell.value
    else:
        value = cell
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) and abs(value) <= 1e300 else None


def zone(value, lower, upper):
    """Match np.select's ordered predicates, including inverted bands."""
    if value is None or lower is None or upper is None:
        return "NA"
    if value < lower:
        return "LOWER_OUT"
    if value > upper:
        return "UPPER_OUT"
    return "IN"


def _zone_number(value):
    return {"LOWER_OUT": -1.0, "IN": 0.0, "UPPER_OUT": 1.0}.get(value)


@dataclass(frozen=True)
class StrategyState:
    percentile_zone: str
    regime_zone: str
    regime_slope: float | None
    value_slope: float | None
    long_regime: bool
    short_regime: bool
    staff_columns: tuple

    @property
    def columns(self):
        from types import MappingProxyType
        return MappingProxyType(dict(self.staff_columns))


class StrategyStateProjector:
    @staticmethod
    def project(family, cells, previous_cells=None, seed_policy="SOURCE_DEFINED_ONLY"):
        family = family.upper()
        previous_cells = previous_cells or {}
        number = lambda key: staff_number(cells.get(key), seed_policy)
        previous = lambda key: staff_number(previous_cells.get(key), seed_policy)
        value, lower, upper = number("strategy_value"), number("lower"), number("upper")
        basis, old_basis = number("basis"), previous("basis")
        old_value = previous("strategy_value")
        slope = None if basis is None or old_basis is None else basis - old_basis
        value_slope = None if value is None or old_value is None else value - old_value
        pct = zone(value, lower, upper)
        regime = zone(value, number("regime_lower"), number("regime_upper"))
        prefix = "price" if family == "PRICE" else family
        if family == "PRICE":
            columns = {"price_hma_6": value, "price_band_lower": lower,
                       "price_band_upper": upper, "price_regime_basis": basis}
        else:
            columns = {f"{prefix}_val": value, f"{prefix}_db": lower,
                       f"{prefix}_ub": upper, f"{prefix}_basis": basis,
                       f"{prefix}_slope": value_slope}
        columns.update({f"{prefix}_regime_lower": number("regime_lower"),
                        f"{prefix}_regime_upper": number("regime_upper"),
                        f"{prefix}_percentile_zone": _zone_number(pct),
                        f"{prefix}_percentile_in": None if pct == "NA" else pct == "IN",
                        f"{prefix}_regime_zone": _zone_number(regime),
                        f"{prefix}_regime_in": None if regime == "NA" else regime == "IN",
                        f"{prefix}_regime_slope": slope})
        rz = _zone_number(regime)
        return StrategyState(pct, regime, slope, value_slope,
                             slope is not None and rz is not None and slope > 0 and rz >= 0,
                             slope is not None and rz is not None and slope < 0 and rz <= 0,
                             tuple(columns.items()))


@dataclass(frozen=True)
class SourceBarArrow:
    namespace: str = "SOURCE_BAR_ARROW"
    lower: float | None = None
    upper: float | None = None


class SourceSignalProjector:
    @staticmethod
    def project(cells, seed_policy="SOURCE_DEFINED_ONLY"):
        return SourceBarArrow(lower=staff_number(cells.get("source_arrow_lower"), seed_policy),
                              upper=staff_number(cells.get("source_arrow_upper"), seed_policy))


@dataclass(frozen=True)
class ObservedEvents:
    observation_id: str
    out_in: tuple = ()
    boundary_cross: tuple = ()
    out_in_namespace: str = "OBSERVED_OUT_IN"
    boundary_namespace: str = "OBSERVED_BOUNDARY_CROSS"


class ObservedBoundaryTracker:
    """A private observer. Calling read/project is never an observation."""
    def __init__(self):
        self.previous = None
        self.last_id = None
        self.last_signature = None
        self.last_events = None

    def observe(self, observation_id, value, lower, upper):
        signature = digest((value, lower, upper))
        if observation_id == self.last_id:
            if signature != self.last_signature:
                raise PitError("E_ID_COLLISION", "observation id has different values")
            return self.last_events
        current = (value, lower, upper)
        current_zone = zone(*current)
        out_in, crosses = [], []
        if self.previous is not None:
            prior_zone = zone(*self.previous)
            if current_zone == "IN" and prior_zone in ("LOWER_OUT", "UPPER_OUT"):
                out_in.append("LONG" if prior_zone == "LOWER_OUT" else "SHORT")
            pv, pl, pu = self.previous
            if None not in (pv, pl, value, lower):
                if pv < pl and value >= lower:
                    crosses.append("LOWER_UP")
                if pv >= pl and value < lower:
                    crosses.append("LOWER_DOWN")
            if None not in (pv, pu, value, upper):
                if pv > pu and value <= upper:
                    crosses.append("UPPER_DOWN")
                if pv <= pu and value > upper:
                    crosses.append("UPPER_UP")
        self.previous = current
        self.last_id, self.last_signature = observation_id, signature
        self.last_events = ObservedEvents(observation_id, tuple(out_in), tuple(crosses))
        return self.last_events

    def restart(self):
        self.__init__()

    def snapshot(self):
        return {"previous": self.previous, "last_id": self.last_id,
                "last_signature": self.last_signature, "last_events": self.last_events}

    def restore(self, state):
        self.previous = state["previous"]
        self.last_id = state["last_id"]
        self.last_signature = state["last_signature"]
        self.last_events = state["last_events"]
