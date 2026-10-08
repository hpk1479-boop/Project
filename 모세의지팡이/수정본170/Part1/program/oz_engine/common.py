"""OZ vocabulary/data records and unchanged scalar decision predicates."""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Optional,Iterable
import math,re
import datetime as dt
import oz_profiles

def finite_number(value):
    try:return math.isfinite(float(value))
    except (TypeError,ValueError):return False

def epoch(value):
    if value is None:return None
    if isinstance(value,(int,float)):
        return int(value) if math.isfinite(value) else None
    parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())

def timestamp_text(value):
    return dt.datetime.fromtimestamp(int(value),dt.timezone.utc).replace(tzinfo=None).isoformat(sep=' ',timespec='seconds')

PERCENTILES = ("RSI", "STO", "DI", "PRICE")


TF_MAP: dict[str, tuple[str, str]] = {
    "1m":  ("3m",  "6m"),
    "2m":  ("6m",  "12m"),
    "3m":  ("10m", "20m"),
    "4m":  ("12m", "30m"),
    "5m":  ("15m", "30m"),
    "6m":  ("15m", "30m"),
    "10m": ("30m", "1h"),
    "12m": ("30m", "1h"),
    "15m": ("1h",  "2h"),
    "20m": ("1h",  "2h"),
    "30m": ("2h",  "4h"),
    "1h":  ("3h",  "6h"),
    "2h":  ("6h",  "12h"),
    "3h":  ("8h",  "12h"),
    "4h":  ("12h", "1d"),
}


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


TF_LABELS = {
    "1m": "1분", "2m": "2분", "3m": "3분", "4m": "4분", "5m": "5분", "6m": "6분",
    "10m": "10분", "12m": "12분", "15m": "15분", "20m": "20분", "30m": "30분",
    "1h": "1시간", "2h": "2시간", "3h": "3시간", "4h": "4시간",
}


def tf_seconds(tf: str) -> int:
    m = re.fullmatch(r"(\d+)([mhd])", str(tf or "").strip().lower())
    if not m:
        return 0
    n = int(m.group(1)); unit = m.group(2)
    return n * (60 if unit == "m" else 3600 if unit == "h" else 86400)


@dataclass
class WatchSpec:
    watch_id: str
    source: str
    timeframes: tuple[str, ...]
    symbol: Optional[str] = None
    direction: Optional[str] = None
    persistent: bool = False
    watch_owner: str = "OZ"
    request_chat_id: Optional[str] = None
    external_watch_id: Optional[str] = None
    external_source_tf: Optional[str] = None
    source_spec_id: Optional[str] = None
    source_name: Optional[str] = None
    validation_mode: str = "NORMAL"      # NORMAL=상위 TF 검증, BLIND=상위 TF 무검증
    trigger_mode: str = "OZ"             # oz_profiles 공식 문자열: OZ / BREAKER


VALIDATION_MODES = oz_profiles.VALIDATION_MODES


TRIGGER_MODES = oz_profiles.TRIGGER_MODES


PROFILE_KEYS = oz_profiles.PROFILE_KEYS


def _profile_key(validation_mode: str, trigger_mode: str) -> str:
    return f"{validation_mode}:{trigger_mode}"


def _resolve_oz_modes(
    validation_mode: Optional[str] = None,
    trigger_mode: Optional[str] = None,
    oz_mode: Optional[str] = None,
) -> tuple[str, str]:
    """현재 validation_mode/trigger_mode 조합을 검증합니다."""
    return oz_profiles.normalize_profile(validation_mode, trigger_mode, oz_mode)


def _oz_profile_label(validation_mode: str, trigger_mode: str) -> str:
    return oz_profiles.profile_label(validation_mode, trigger_mode)


EXTERNAL_ATR_PERIOD = 14


EXTERNAL_ATR_MULT = 1.5


EXTERNAL_ATR_COLUMN = "atr_14"


@dataclass
class ExternalLiquiditySpec:
    watch_id: str
    symbol: str
    source_tf: str
    source_kind: str = "SWEEP"
    manual_level_price: Optional[float] = None
    manual_direction: Optional[str] = None
    registered_at: Optional[float] = None
    # Qualification size chosen by the strategy that owns this SWEEP watch.
    # The defaults are the existing fixed ATR14 x 1.5 rule.
    atr_period: int = EXTERNAL_ATR_PERIOD
    atr_mult: float = EXTERNAL_ATR_MULT


def atr_rule(period, mult) -> tuple[int, float]:
    """A requested ATR period/multiplier; missing or invalid means the fixed ATR14 x 1.5 rule."""
    if type(period) is not int or not 1 <= period <= 500:
        period = EXTERNAL_ATR_PERIOD
    if type(mult) not in (int, float) or not math.isfinite(mult) or mult <= 0:
        mult = EXTERNAL_ATR_MULT
    return period, float(mult)


def sweep_external_spec(payload) -> "ExternalLiquiditySpec":
    """Gate spec for one SWEEP watch payload, using the ATR rule its strategy asked for."""
    period, mult = atr_rule(payload.get("external_atr_period"), payload.get("external_atr_mult"))
    return ExternalLiquiditySpec(payload["watch_id"], payload["symbol"], payload["source_tf"],
                                 atr_period=period, atr_mult=mult)


@dataclass
class ExternalLiquidityState:
    watch_id: str
    status: str = "WAIT_SWEEP"  # WAIT_SWEEP / PENDING_ATR / ACTIVE / CONFIRMED / INVALID
    direction: Optional[str] = None
    level_id: Optional[str] = None
    level_code: Optional[str] = None
    level_name: Optional[str] = None
    level_price: Optional[float] = None
    event_time: Optional[float] = None
    touch_high: Optional[float] = None
    touch_low: Optional[float] = None
    atr_snapshot: Optional[float] = None
    max_distance: Optional[float] = None
    reason: Optional[str] = None


def percentile_states(row: object) -> dict[str, str]:
    """Return LOWER_OUT / UPPER_OUT / IN / NA for the 4 Percentiles."""
    specs = {
        "RSI": ("RSI_val", "RSI_db", "RSI_ub"),
        "STO": ("STO_val", "STO_db", "STO_ub"),
        "DI": ("DI_val", "DI_db", "DI_ub"),
        "PRICE": ("price_hma_6", "price_band_lower", "price_band_upper"),
    }
    out: dict[str, str] = {}
    for name, (v_col, lo_col, hi_col) in specs.items():
        v, lo, hi = row.get(v_col), row.get(lo_col), row.get(hi_col)
        if not all(finite_number(x) for x in (v, lo, hi)):
            out[name] = "NA"
            continue
        prefix='price' if name=='PRICE' else name
        if finite_number(row.get(prefix+'_lower_out')):
            out[name] = "LOWER_OUT"
        elif finite_number(row.get(prefix+'_upper_out')):
            out[name] = "UPPER_OUT"
        else:
            out[name] = "IN"
    return out











@dataclass
class IndicatorEpisode:
    active: bool = False
    extreme_price: Optional[float] = None
    extreme_time: Optional[int] = None


@dataclass
class PercentileCandidate:
    """Independent OZ registration state for one trigger family."""
    percentile: str
    direction: str
    outin_b0_price: float
    outin_b0_time: int
    trigger_time: int
    mid_validated: bool = False
    invalidated: bool = False


@dataclass
class Candidate:
    direction: str
    outin_b0_price: float
    outin_b0_time: int
    trigger_time: int
    trigger_indicators: set[str] = field(default_factory=set)
    hma_b0_price: Optional[float] = None
    hma_b0_time: Optional[int] = None
    hma_cross_time: Optional[int] = None
    true_b0_price: Optional[float] = None
    true_b0_time: Optional[int] = None
    alerted: bool = False
    completed_outside_window: bool = False
    completion_time: Optional[float] = None
    # OZ 트리거 누적 상태: 후보가 살아있는 동안 한 번이라도 TRUE가 된 조건.
    # key = B0 / HMA17 / WONBI / CANDLE / HMA6_TURN, value = 표시명.
    trigger_hits: dict[str, str] = field(default_factory=dict)


@dataclass
class FinalTriggerDecision:
    trigger_name: str
    b0_state: str = "N/A"
    h17_state: str = "N/A"
    wonbi_state: str = "N/A"


@dataclass
class CandidateCompletionDecision:
    grade: str
    consensus_count: int
    matched_trigger_contexts: tuple[str, ...]
    trigger: FinalTriggerDecision
    bars_since: int
    cross_bars: int
    true_b0_price: float
    true_b0_time: int

