"""Existing command data contracts, independent of Composer execution."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional
import math
import re
import pandas as pd
import oz_profiles
from sweep_selectors import normalize_level_selectors
from watch_ma import parse_ma_expression, parse_ma_name
from command_interpreter import MT5_TIMEFRAMES, OZ_BASE_TFS, WATCH_TF_MAP, normalize_tf
from watch_orchestrator import (ChainTriggerSpec, TRIGGER_MODES, VALIDATION_MODES,
                                format_duration_ko)

@dataclass(frozen=True)
class SecretaryCommand:
    """An interpretation result; applying it belongs to the execution engine."""
    kind: str
    value: object = None
    message: str = ""

def _oz_trigger_mode(text: str) -> str:
    """자연어의 OZ 프로필 키워드(브레이커, 순서 무관)를 공식 trigger_mode로 변환합니다."""
    return oz_profiles.text_trigger_mode(text)


def _canonical_trigger_mode(value: object) -> str:
    """순서가 다른 조합도 공식 trigger_mode로 맞춥니다. 해석 불가 값은 그대로 두어 검증에서 거부합니다."""
    raw = str(value or "OZ").upper()
    flags = oz_profiles.trigger_flags(raw)
    return oz_profiles.canonical_trigger_mode(flags) if flags is not None else raw


def _oz_profile_label(validation_mode: str, trigger_mode: str) -> str:
    """공식 OZ 프로필의 사용자 표시명을 반환합니다."""
    return oz_profiles.profile_label(validation_mode, trigger_mode)

COMBINATIONS = {"ALL", "ANY"}
DESTINATIONS = {"OFFICIAL", "PRIVATE"}
CONDITION_KINDS = {"TREND", "WONBI", "FVG", "SWEEP", "PERCENTILE", "MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE", "TREND_METRIC"}

# Watch의 조건과 판정시점을 분리합니다.
# watch_type은 조건 자체만 나타내고, evaluation_mode가 LIVE/CLOSE 판정시점을 나타냅니다.
WATCH_EVALUATION_MODES = {"LIVE", "CLOSE"}
WATCH_LEGACY_CONTRACTS: dict[str, tuple[str, str]] = {
    "WONBI_TOUCH_CLOSE": ("WONBI_TOUCH", "CLOSE"),
    "BAR_CLOSE": ("BAR", "CLOSE"),
}
WATCH_DEFAULT_EVALUATION: dict[str, str] = {
    "EMA_CROSS": "CLOSE",
    "HMA_CROSS": "CLOSE",
    "BAR": "CLOSE",
    "WONBI_TOUCH": "LIVE",
    "PREV_DAY_TOUCH": "LIVE",
    "PERCENTILE_OUT": "LIVE",
    "PERCENTILE_OUT_IN": "LIVE",
    "FVG_NEW": "LIVE",
    "OZ_ALERT": "LIVE",
    "COMPOUND_CONDITION": "LIVE",
}

def normalize_watch_contract(watch_type: object, evaluation_mode: object = None) -> tuple[str, str]:
    raw_type = str(watch_type or "").strip().upper()
    raw_mode = str(evaluation_mode or "").strip().upper()
    legacy = WATCH_LEGACY_CONTRACTS.get(raw_type)
    if legacy is not None:
        condition_type, legacy_mode = legacy
        mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else legacy_mode
        return condition_type, mode
    mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else WATCH_DEFAULT_EVALUATION.get(raw_type, "LIVE")
    return raw_type, mode

def legacy_chain_watch_type(watch_type: object, evaluation_mode: object = None) -> str:
    """현재 watch_orchestrator 저장형식과 호환되는 내부 type을 반환합니다.

    외부/공용 계약은 canonical watch_type + evaluation_mode를 사용합니다.
    watch_orchestrator.py가 별도 모듈이므로 기존 저장상태를 깨지 않도록 내부 ChainTriggerSpec만
    기존 별칭을 유지하고, 김매니저 경계에서 canonical 계약으로 변환합니다.
    """
    condition_type, mode = normalize_watch_contract(watch_type, evaluation_mode)
    if condition_type == "WONBI_TOUCH" and mode == "CLOSE":
        return "WONBI_TOUCH_CLOSE"
    if condition_type == "BAR" and mode == "CLOSE":
        return "BAR_CLOSE"
    return condition_type

def trigger_watch_contract(trigger: object) -> tuple[str, str]:
    return normalize_watch_contract(
        getattr(trigger, "watch_type", ""),
        getattr(trigger, "evaluation_mode", None),
    )

def is_filter_watch_trigger(trigger: object) -> bool:
    condition_type, evaluation_mode = trigger_watch_contract(trigger)
    return (
        condition_type in {"BAR", "COMPOUND_CONDITION"}
        or (condition_type == "WONBI_TOUCH" and evaluation_mode == "CLOSE")
    )


def make_chain_trigger(watch_type: str, tf: str, *, evaluation_mode: str | None = None, **kwargs) -> ChainTriggerSpec:
    if str(watch_type or "").strip().upper() == "MA_EXPRESSION" and not evaluation_mode:
        evaluation_mode = parse_ma_expression(kwargs.get("ma_expression")).default_evaluation_mode
    condition_type, mode = normalize_watch_contract(watch_type, evaluation_mode)
    trigger = ChainTriggerSpec(
        legacy_chain_watch_type(condition_type, mode), tf,
        evaluation_mode=mode, **kwargs
    )
    try:
        trigger.condition_type = condition_type
    except Exception:
        pass
    return trigger

def canonical_watch_payload(payload: dict) -> dict:
    item = dict(payload)
    if item.get("watch_type") is not None:
        condition_type, mode = normalize_watch_contract(
            item.get("watch_type"), item.get("evaluation_mode")
        )
        item["watch_type"] = condition_type
        item["evaluation_mode"] = mode
    return item

# strategy_INDICATOR와 공유하는 metric subscription 계약. 계산식은 INDICATOR만 소유합니다.
# (wire 이름 TREND_*는 기존 통신/상태 호환을 위해 유지합니다.)
TREND_METRIC_FIELDS = {
    "price", "long_score", "short_score", "trend_score",
    "ema10_open", "ema50_open", "sma20_open", "wma17_open",
    "supertrend", "psar", "plus_di", "minus_di", "adx",
    "linreg20", "rsi14", "cci20", "macd", "macd_signal",
    "aroon_up", "aroon_down", "vortex_plus", "vortex_minus", "vwap",
    "bop", "cmf20", "mfi14", "chop14", "hv20", "hvma20",
    "mss", "vol_state", "vol_surge",
}
TREND_METRIC_OPERATORS = {"GT", "GTE", "LT", "LTE", "EQ", "NE"}

DEFAULT_COMPOSER_POLL_SEC = 0.50
DEFAULT_MA_STATE_STALE_SEC = 5.0
DEFAULT_TREND_METRIC_STALE_SEC = 5.0
DEFAULT_ENGINE_REFRESH_SEC = 30.0
DEFAULT_SWEEP_LEVELS = ("PDH", "PDL", "4H", "8H", "SESSION")
DEFAULT_SWEEP_ATR_PERIOD = 14
DEFAULT_SWEEP_ATR_MULT = 1.5

# TREND/FVG/SWEEP/원비 조건은 MT5 표준 시간봉만 사용합니다.
TREND_SUPPORTED_TFS = MT5_TIMEFRAMES
CONDITION_DATA_TFS = MT5_TIMEFRAMES

# 원비의 의미는 전략과 분리합니다. 실제 밴드 계산은 THE_STAFF_OF_MOSES가 담당합니다.
# 2.0 공통 계약: OPEN source / length 4 / sigma는 config.txt의 WONBI_SIGMA 입력값.
WONBI_SOURCE = "OPEN"


def split_csv(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").split(",")
    return tuple(x.strip() for x in items if str(x).strip())


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "enable", "enabled"}


def _event_time_token(value: object) -> str:
    if value is None:
        return "-"
    try:
        return str(pd.Timestamp(value).value)
    except Exception:
        return str(value)


@dataclass(frozen=True)
class ConditionSpec:
    kind: str
    tf: str
    direction: str = "AUTO"      # AUTO / LONG / SHORT
    side: str = ""               # WONBI/FVG/PERCENTILE/SWEEP 및 MA_* 방향/selector
    ma_family: str = ""          # MA_STATE / MA_PRICE_STATE / MA_SLOPE_STATE: SMA / WMA / EMA / HMA
    fast_period: int = 0          # MA_STATE fast period
    slow_period: int = 0          # MA_STATE slow / MA_PRICE_STATE·MA_SLOPE_STATE target period
    metric: str = ""             # TREND_METRIC only
    metric_operator: str = ""    # TREND_METRIC only: GT/GTE/LT/LTE/EQ/NE
    metric_value: Optional[float] = None   # numeric rhs
    metric_rhs: str = ""         # or another TREND metric field

    def __post_init__(self):
        kind = str(self.kind or "").upper()
        tf = normalize_tf(self.tf)
        direction = str(self.direction or "AUTO").upper()
        side = str(self.side or "").upper()
        ma_family = str(self.ma_family or "").upper()
        metric = str(self.metric or "").strip().lower()
        metric_operator = str(self.metric_operator or "").strip().upper()
        metric_rhs = str(self.metric_rhs or "").strip().lower()
        metric_value = self.metric_value
        try:
            fast_period = int(self.fast_period or 0)
            slow_period = int(self.slow_period or 0)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("MA_STATE period 오류")
        if kind not in CONDITION_KINDS:
            raise ValueError(f"지원하지 않는 조건: {kind}")
        if not tf:
            raise ValueError(f"MT5가 지원하지 않는 시간봉: {self.tf}")
        if kind == "TREND" and tf not in TREND_SUPPORTED_TFS:
            raise ValueError(f"TREND가 지원하지 않는 시간봉: {tf}")
        if kind != "TREND" and tf not in CONDITION_DATA_TFS:
            raise ValueError(
                f"{kind}가 지원하지 않는 시간봉: {tf} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        if direction not in {"AUTO", "LONG", "SHORT"}:
            raise ValueError(f"잘못된 방향: {direction}")
        if kind == 'SWEEP' and side:
            normalize_level_selectors((side,))
        if kind in {"MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE"}:
            if ma_family not in {"SMA", "WMA", "EMA", "HMA"}:
                raise ValueError(f"{kind} MA family 오류: {ma_family}")
            # Use the existing canonical MA contract, not the legacy CROSS
            # whitelist. Validate the raw period so fractions are not truncated.
            _, slow_period = parse_ma_name(f"{ma_family}{self.slow_period}")
            if kind == "MA_STATE":
                _, fast_period = parse_ma_name(f"{ma_family}{self.fast_period}")
        if kind == "MA_STATE":
            if side not in {"ABOVE", "BELOW"}:
                raise ValueError(f"MA_STATE 배열 방향 오류: {side}")
            if fast_period == slow_period:
                raise ValueError("MA_STATE fast/slow period가 같습니다")
        if kind == "MA_PRICE_STATE":
            if side not in {"ABOVE", "BELOW"}:
                raise ValueError(f"MA_PRICE_STATE 방향 오류: {side}")
            if fast_period != 0:
                raise ValueError("MA_PRICE_STATE는 fast_period를 사용하지 않습니다")
        if kind == "MA_SLOPE_STATE":
            if side not in {"UP", "DOWN"}:
                raise ValueError(f"MA_SLOPE_STATE 방향 오류: {side}")
            if fast_period != 0:
                raise ValueError("MA_SLOPE_STATE는 fast_period를 사용하지 않습니다")
        if kind == "TREND_METRIC":
            if metric not in TREND_METRIC_FIELDS:
                raise ValueError(f"지원하지 않는 TREND metric: {metric}")
            if metric_operator not in TREND_METRIC_OPERATORS:
                raise ValueError(f"TREND metric 비교연산 오류: {metric_operator}")
            if metric_rhs:
                if metric_rhs not in TREND_METRIC_FIELDS:
                    raise ValueError(f"지원하지 않는 TREND rhs metric: {metric_rhs}")
                if metric_value is not None:
                    raise ValueError("TREND metric rhs/value를 동시에 지정할 수 없습니다")
            else:
                if metric_value is None:
                    raise ValueError("TREND metric 비교값 누락")
                try:
                    metric_value = float(metric_value)
                except (TypeError, ValueError):
                    raise ValueError("TREND metric 비교값 오류")
                if not math.isfinite(metric_value):
                    raise ValueError("TREND metric 비교값 오류")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "tf", tf)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "ma_family", ma_family)
        object.__setattr__(self, "fast_period", fast_period)
        object.__setattr__(self, "slow_period", slow_period)
        object.__setattr__(self, "metric", metric)
        object.__setattr__(self, "metric_operator", metric_operator)
        object.__setattr__(self, "metric_value", metric_value)
        object.__setattr__(self, "metric_rhs", metric_rhs)

    def label(self) -> str:
        suffix = ""
        if self.kind == "TREND":
            suffix = "상승" if self.direction == "LONG" else "하락" if self.direction == "SHORT" else "방향일치"
        elif self.kind == "WONBI":
            suffix = self.side or "방향일치"
        elif self.kind == "FVG":
            suffix = self.side or "방향일치"
        elif self.kind == "SWEEP":
            suffix = self.side or (self.direction if self.direction != "AUTO" else "방향일치")
        elif self.kind == "PERCENTILE":
            suffix = self.side or "방향일치 OUT"
        elif self.kind == "MA_STATE":
            relation = "정배열" if self.side == "ABOVE" else "역배열"
            suffix = f"{self.ma_family}{self.fast_period}/{self.slow_period} {relation}"
        elif self.kind == "MA_PRICE_STATE":
            relation = "위" if self.side == "ABOVE" else "아래"
            suffix = f"봉가격 {self.ma_family}{self.slow_period} {relation}"
        elif self.kind == "MA_SLOPE_STATE":
            relation = "우상향" if self.side == "UP" else "우하향"
            suffix = f"{self.ma_family}{self.slow_period} {relation}(현재/2봉전)"
        elif self.kind == "TREND_METRIC":
            op_label = {"GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=", "EQ": "=", "NE": "!="}.get(self.metric_operator, self.metric_operator)
            rhs = self.metric_rhs or (f"{float(self.metric_value):g}" if self.metric_value is not None else "?")
            suffix = f"{self.metric} {op_label} {rhs}"
        return f"{self.kind}:{suffix}@{self.tf}"


@dataclass
class FVGCreatedWatchSpec:
    """strategy_FVG의 FVG_CREATED를 직접 소비하는 Composer 전용 개인 Watch."""

    watch_id: str
    symbol: str
    timeframes: tuple[str, ...]
    direction: Optional[str] = None
    persistent: bool = False
    request_chat_id: Optional[str] = None
    silent: bool = False
    final_action: str = "NOTIFY"
    oz_tfs: tuple[str, ...] = ()
    oz_direction: Optional[str] = None
    validation_mode: str = "NORMAL"
    trigger_mode: str = "OZ"

    def validate(self) -> None:
        self.watch_id = str(self.watch_id or "").strip()
        self.symbol = str(self.symbol or "").strip()
        self.timeframes = tuple(dict.fromkeys(normalize_tf(x) for x in self.timeframes if normalize_tf(x)))
        self.direction = str(self.direction or "").upper() or None
        self.request_chat_id = str(self.request_chat_id or "").strip() or None
        self.final_action = str(self.final_action or "NOTIFY").upper()
        self.oz_tfs = tuple(dict.fromkeys(normalize_tf(x) for x in self.oz_tfs if normalize_tf(x)))
        self.oz_direction = str(self.oz_direction or "").upper() or None
        self.validation_mode = str(self.validation_mode or "NORMAL").upper()
        self.trigger_mode = _canonical_trigger_mode(self.trigger_mode)
        if not self.watch_id or not self.symbol or not self.timeframes:
            raise ValueError("FVG 생성 Watch watch_id/symbol/timeframe 누락")
        if self.direction not in {None, "LONG", "SHORT"}:
            raise ValueError(f"FVG 생성 Watch 방향 오류: {self.direction}")
        if self.final_action not in {"NOTIFY", "OZ"}:
            raise ValueError(f"FVG 생성 Watch final_action 오류: {self.final_action}")
        if self.final_action == "OZ":
            if not self.oz_tfs:
                raise ValueError("FVG 생성→OZ 시간봉 누락")
            invalid = [tf for tf in self.oz_tfs if tf not in OZ_BASE_TFS]
            if invalid:
                raise ValueError(f"FVG 생성→OZ 지원하지 않는 시간봉: {','.join(invalid)}")
            if self.oz_direction not in {None, "LONG", "SHORT"}:
                raise ValueError(f"FVG 생성→OZ 방향 오류: {self.oz_direction}")
            if self.validation_mode not in VALIDATION_MODES:
                raise ValueError(f"FVG 생성→OZ validation 오류: {self.validation_mode}")
            if self.trigger_mode not in TRIGGER_MODES:
                raise ValueError(f"FVG 생성→OZ trigger 오류: {self.trigger_mode}")

    def label(self) -> str:
        side = "상승 " if self.direction == "LONG" else "하락 " if self.direction == "SHORT" else ""
        tf_label = "·".join(WATCH_TF_MAP.get(tf, tf) for tf in self.timeframes)
        base = f"{tf_label} {side}FVG 생성"
        if self.final_action == "OZ":
            oz_side = "하단 " if self.oz_direction == "LONG" else "상단 " if self.oz_direction == "SHORT" else ""
            oz_label = "·".join(WATCH_TF_MAP.get(tf, tf) for tf in self.oz_tfs)
            return f"{base} → {oz_label} {oz_side}{oz_profiles.profile_label(self.validation_mode,self.trigger_mode)}"
        return base

    def to_json(self) -> dict:
        oz_profiles.normalize_profile(self.validation_mode, self.trigger_mode)
        return {
            "watch_id": self.watch_id, "symbol": self.symbol, "timeframes": list(self.timeframes),
            "direction": self.direction, "persistent": self.persistent,
            "request_chat_id": self.request_chat_id, "silent": self.silent,
            "final_action": self.final_action, "oz_tfs": list(self.oz_tfs),
            "oz_direction": self.oz_direction, "validation_mode": self.validation_mode,
            "trigger_mode": self.trigger_mode,
        }

    @staticmethod
    def from_json(item: dict) -> "FVGCreatedWatchSpec":
        item = oz_profiles.normalize_watch_payload(item)
        spec = FVGCreatedWatchSpec(
            watch_id=item.get("watch_id"), symbol=item.get("symbol"),
            timeframes=tuple(item.get("timeframes", [])), direction=item.get("direction"),
            persistent=bool(item.get("persistent", False)),
            request_chat_id=item.get("request_chat_id"), silent=bool(item.get("silent", False)),
            final_action=item.get("final_action", "NOTIFY"),
            oz_tfs=tuple(item.get("oz_tfs", [])), oz_direction=item.get("oz_direction"),
            validation_mode=item.get("validation_mode", "NORMAL"),
            trigger_mode=item.get("trigger_mode", "OZ"),
        )
        spec.validate()
        return spec


@dataclass
class ConfigTimedChainSpec:
    """SPECIAL이 등록하는 공식 상시 시간연쇄. 개인 TimedChainSpec과 런타임/상태를 분리합니다."""

    spec_id: str
    name: str
    symbol: str
    cross_tf: str = "1m"
    ma_family: str = "EMA"
    fast_period: int = 50
    slow_period: int = 200
    fvg_tfs: tuple[str, ...] = ("5m", "6m")
    order_mode: str = "SEQUENTIAL"
    max_gap_sec: float = 1800.0
    max_gap_bars: int = 0
    final_window_sec: float = 3600.0
    oz_tfs: tuple[str, ...] = ("1m",)
    # 비어 있으면 기존 TIMED_CHAIN처럼 pair 성립 즉시 OZ를 arm합니다.
    # 값이 있으면 pair 성립 후 해당 TF의 동방향 FVG TOUCH를 기다린 뒤 OZ를 arm합니다.
    final_fvg_touch_tfs: tuple[str, ...] = ()
    validation_mode: str = "NORMAL"
    trigger_mode: str = "OZ"
    time_filters: tuple[str, ...] = ()
    # 최종 FVG TOUCH -> OZ 단계 전용 시간필터. 비어 있으면 제한 없음.
    final_time_filters: tuple[str, ...] = ()
    # 현재 방향 후보/최종 OZ를 반대 방향 이벤트로 즉시 무효화하는 공식 TIMED_CHAIN 전용 옵션.
    cancel_on_opposite_cross: bool = False
    cancel_on_opposite_fvg_tfs: tuple[str, ...] = ()
    alert_template: str = ""
    enabled: bool = True

    def validate(self) -> None:
        self.spec_id = str(self.spec_id or "").strip()
        self.name = str(self.name or self.spec_id).strip()
        self.symbol = str(self.symbol or "").strip()
        self.cross_tf = normalize_tf(self.cross_tf)
        self.ma_family = str(self.ma_family or "EMA").strip().upper()
        self.fast_period = int(self.fast_period)
        self.slow_period = int(self.slow_period)
        self.fvg_tfs = tuple(dict.fromkeys(normalize_tf(x) for x in self.fvg_tfs if normalize_tf(x)))
        raw_order_mode = str(self.order_mode or "SEQUENTIAL").strip().upper().replace("-", "_")
        self.order_mode = {
            "ORDERED": "SEQUENTIAL", "A_TO_B": "SEQUENTIAL", "SEQUENTIAL": "SEQUENTIAL",
            "ANY": "UNORDERED", "ANY_ORDER": "UNORDERED", "UNORDERED": "UNORDERED",
            "LATCH": "UNORDERED",
        }.get(raw_order_mode, raw_order_mode)
        self.oz_tfs = tuple(dict.fromkeys(normalize_tf(x) for x in self.oz_tfs if normalize_tf(x)))
        self.final_fvg_touch_tfs = tuple(dict.fromkeys(
            normalize_tf(x) for x in self.final_fvg_touch_tfs if normalize_tf(x)
        ))
        self.max_gap_sec = float(self.max_gap_sec)
        self.max_gap_bars = int(self.max_gap_bars)
        self.final_window_sec = float(self.final_window_sec)
        self.validation_mode = str(self.validation_mode or "NORMAL").upper()
        self.trigger_mode = _canonical_trigger_mode(self.trigger_mode)
        self.time_filters = tuple(str(x).strip().upper() for x in self.time_filters if str(x).strip())
        self.final_time_filters = tuple(
            str(x).strip().upper() for x in self.final_time_filters if str(x).strip()
        )
        self.cancel_on_opposite_cross = parse_bool(self.cancel_on_opposite_cross, False)
        self.cancel_on_opposite_fvg_tfs = tuple(dict.fromkeys(
            normalize_tf(x) for x in self.cancel_on_opposite_fvg_tfs if normalize_tf(x)
        ))
        if not self.spec_id or not self.symbol:
            raise ValueError("TIMED_CHAIN spec_id/symbol 누락")
        if not self.cross_tf:
            raise ValueError(f"{self.spec_id}: CROSS_TF 오류")
        if self.ma_family not in {"EMA", "HMA"}:
            raise ValueError(f"{self.spec_id}: CROSS_MA={self.ma_family}")
        if self.fast_period <= 0 or self.slow_period <= 0 or self.fast_period == self.slow_period:
            raise ValueError(f"{self.spec_id}: CROSS_FAST/CROSS_SLOW 오류")
        if not self.fvg_tfs:
            raise ValueError(f"{self.spec_id}: FVG_TFS 비어 있음")
        if self.order_mode not in {"SEQUENTIAL", "UNORDERED"}:
            raise ValueError(f"{self.spec_id}: ORDER_MODE={self.order_mode}")
        invalid_fvg_tfs = [tf for tf in self.fvg_tfs if tf not in CONDITION_DATA_TFS]
        if invalid_fvg_tfs:
            raise ValueError(
                f"{self.spec_id}: FVG가 지원하지 않는 시간봉={','.join(invalid_fvg_tfs)} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        invalid_final_fvg_tfs = [tf for tf in self.final_fvg_touch_tfs if tf not in CONDITION_DATA_TFS]
        if invalid_final_fvg_tfs:
            raise ValueError(
                f"{self.spec_id}: FINAL_FVG_TOUCH_TFS가 지원하지 않는 시간봉={','.join(invalid_final_fvg_tfs)} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        invalid_cancel_fvg_tfs = [tf for tf in self.cancel_on_opposite_fvg_tfs if tf not in CONDITION_DATA_TFS]
        if invalid_cancel_fvg_tfs:
            raise ValueError(
                f"{self.spec_id}: CANCEL_ON_OPPOSITE_FVG_TFS가 지원하지 않는 시간봉="
                f"{','.join(invalid_cancel_fvg_tfs)} (지원: {','.join(CONDITION_DATA_TFS)})"
            )
        non_setup_cancel_tfs = [tf for tf in self.cancel_on_opposite_fvg_tfs if tf not in self.fvg_tfs]
        if non_setup_cancel_tfs:
            raise ValueError(
                f"{self.spec_id}: CANCEL_ON_OPPOSITE_FVG_TFS는 FVG_TFS 안에서 선택해야 합니다: "
                f"{','.join(non_setup_cancel_tfs)}"
            )
        if not self.oz_tfs:
            raise ValueError(f"{self.spec_id}: OZ_TFS 비어 있음")
        invalid_oz_tfs = [tf for tf in self.oz_tfs if tf not in OZ_BASE_TFS]
        if invalid_oz_tfs:
            raise ValueError(
                f"{self.spec_id}: OZ가 지원하지 않는 시간봉={','.join(invalid_oz_tfs)} "
                f"(지원: {','.join(OZ_BASE_TFS)})"
            )
        if self.cross_tf not in CONDITION_DATA_TFS:
            raise ValueError(
                f"{self.spec_id}: CROSS_TF가 지원하지 않는 시간봉={self.cross_tf} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        if not math.isfinite(self.max_gap_sec) or self.max_gap_sec < 0:
            raise ValueError(f"{self.spec_id}: MAX_GAP_SEC 오류")
        if self.max_gap_bars < 0:
            raise ValueError(f"{self.spec_id}: MAX_GAP_BARS 오류")
        if self.max_gap_sec <= 0 and self.max_gap_bars <= 0:
            raise ValueError(f"{self.spec_id}: MAX_GAP_SEC/MAX_GAP_BARS 중 하나는 0보다 커야 합니다")
        if not math.isfinite(self.final_window_sec) or self.final_window_sec <= 0:
            raise ValueError(f"{self.spec_id}: OZ_WINDOW_SEC 오류")
        if self.validation_mode not in VALIDATION_MODES:
            raise ValueError(f"{self.spec_id}: VALIDATION_MODE={self.validation_mode}")
        if self.trigger_mode not in TRIGGER_MODES:
            raise ValueError(f"{self.spec_id}: TRIGGER_MODE={self.trigger_mode}")

    def expiry_label(self) -> str:
        parts: list[str] = []
        if self.max_gap_sec > 0:
            parts.append(format_duration_ko(self.max_gap_sec))
        if self.max_gap_bars > 0:
            parts.append(f"{self.max_gap_bars}봉({WATCH_TF_MAP.get(self.cross_tf, self.cross_tf)})")
        return " 또는 ".join(parts)

    def summary(self) -> str:
        link = "↔" if self.order_mode == "UNORDERED" else "→"
        mode = " · 순서무관" if self.order_mode == "UNORDERED" else ""
        if self.final_fvg_touch_tfs:
            final_text = (
                f"{format_duration_ko(self.final_window_sec)} 안에 "
                f"{'/'.join(self.final_fvg_touch_tfs)} 동방향 FVG 터치 → {','.join(self.oz_tfs)} 올존"
            )
        else:
            final_text = f"{format_duration_ko(self.final_window_sec)} 동안 {','.join(self.oz_tfs)} 올존"
        cancel_parts: list[str] = []
        if self.cancel_on_opposite_cross:
            cancel_parts.append("반대 크로스")
        if self.cancel_on_opposite_fvg_tfs:
            cancel_parts.append(f"반대 {'/'.join(self.cancel_on_opposite_fvg_tfs)} FVG")
        cancel_text = f" · 취소={' OR '.join(cancel_parts)}" if cancel_parts else ""
        return (
            f"{self.symbol} | {self.cross_tf} {self.ma_family}{self.fast_period}/{self.slow_period} 크로스 {link} "
            f"{'/'.join(self.fvg_tfs)} 신규 FVG (≤{self.expiry_label()}{mode}) → {final_text}{cancel_text}"
        )


@dataclass
class StrategySpec:
    spec_id: str
    name: str
    symbol: str
    conditions: tuple[ConditionSpec, ...]
    oz_tfs: tuple[str, ...]
    final_action: str = "OZ"          # OZ / NOTIFY
    combination: str = "ALL"
    validation_mode: str = "NORMAL"
    trigger_mode: str = "OZ"
    final_direction: Optional[str] = None   # None / LONG / SHORT (최종 OZ 방향 제한)
    # PRIVATE 자연어 전략에서 사용자가 최종 OZ 방향을 직접 썼는지 구분합니다.
    # False이면 조건 의미에서 추론한 기본 방향일 수 있습니다.
    final_direction_explicit: bool = False
    destination: str = "OFFICIAL"
    owner_chat_id: Optional[str] = None
    persistent: bool = True
    enabled: bool = True
    time_filters: tuple[str, ...] = ()
    sweep_levels: tuple[str, ...] = DEFAULT_SWEEP_LEVELS
    sweep_atr_period: int = DEFAULT_SWEEP_ATR_PERIOD
    sweep_atr_mult: float = DEFAULT_SWEEP_ATR_MULT
    source: str = "SPECIAL"           # SPECIAL / PRIVATE
    alert_template: str = ""          # SPECIAL 공식 메인전략 alert template only

    def validate(self) -> None:
        self.symbol = str(self.symbol or "").strip()
        self.final_action = str(self.final_action or "OZ").upper()
        self.combination = str(self.combination or "ALL").upper()
        self.validation_mode = str(self.validation_mode or "NORMAL").upper()
        self.trigger_mode = _canonical_trigger_mode(self.trigger_mode)
        self.final_direction = str(self.final_direction or "").upper() or None
        self.final_direction_explicit = bool(self.final_direction_explicit)
        self.destination = str(self.destination or "OFFICIAL").upper()
        self.oz_tfs = tuple(dict.fromkeys(normalize_tf(x) for x in self.oz_tfs if normalize_tf(x)))
        self.time_filters = tuple(str(x).strip().upper() for x in self.time_filters if str(x).strip())
        self.sweep_levels = normalize_level_selectors(self.sweep_levels)
        for cond in self.conditions:
            if cond.kind == "SWEEP" and cond.side: normalize_level_selectors((cond.side,))
        self.sweep_atr_period = max(1, int(self.sweep_atr_period))
        self.sweep_atr_mult = float(self.sweep_atr_mult)
        if not self.spec_id or not self.symbol:
            raise ValueError("spec_id/symbol 누락")
        if not self.conditions:
            raise ValueError(f"{self.spec_id}: CONDITIONS 비어 있음")
        if self.final_action not in {"OZ", "NOTIFY"}:
            raise ValueError(f"{self.spec_id}: FINAL_ACTION={self.final_action}")
        if self.final_action == "OZ":
            if not self.oz_tfs:
                raise ValueError(f"{self.spec_id}: OZ TF 비어 있음")
            invalid_oz_tfs = [tf for tf in self.oz_tfs if tf not in OZ_BASE_TFS]
            if invalid_oz_tfs:
                raise ValueError(
                    f"{self.spec_id}: OZ가 지원하지 않는 시간봉={','.join(invalid_oz_tfs)} "
                    f"(지원: {','.join(OZ_BASE_TFS)})"
                )
        else:
            self.oz_tfs = ()
        if self.combination not in COMBINATIONS:
            raise ValueError(f"{self.spec_id}: COMBINATION={self.combination}")
        if self.validation_mode not in VALIDATION_MODES:
            raise ValueError(f"{self.spec_id}: VALIDATION_MODE={self.validation_mode}")
        if self.trigger_mode not in TRIGGER_MODES:
            raise ValueError(f"{self.spec_id}: TRIGGER_MODE={self.trigger_mode}")
        if self.final_direction not in {None, "LONG", "SHORT"}:
            raise ValueError(f"{self.spec_id}: FINAL_DIRECTION={self.final_direction}")
        if self.destination not in DESTINATIONS:
            raise ValueError(f"{self.spec_id}: TARGET={self.destination}")
        if not math.isfinite(self.sweep_atr_mult) or self.sweep_atr_mult <= 0:
            raise ValueError(f"{self.spec_id}: SWEEP_ATR_MULT invalid")
        if self.destination == "PRIVATE" and not self.owner_chat_id:
            raise ValueError(f"{self.spec_id}: PRIVATE target without owner_chat_id")

    def summary(self) -> str:
        conditions = " + ".join(c.label() for c in self.conditions)
        if self.final_action == "NOTIFY":
            return f"{self.symbol} | {conditions} → 알림"
        profile_text = _oz_profile_label(self.validation_mode, self.trigger_mode)
        tfs = ",".join(self.oz_tfs)
        side = "매수 " if self.final_direction == "LONG" else "매도 " if self.final_direction == "SHORT" else ""
        return f"{self.symbol} | {conditions} → {tfs} {side}{profile_text}"

    def to_json(self) -> dict:
        oz_profiles.normalize_profile(self.validation_mode, self.trigger_mode)
        return {
            "spec_id": self.spec_id,
            "name": self.name,
            "symbol": self.symbol,
            "conditions": [asdict(x) for x in self.conditions],
            "oz_tfs": list(self.oz_tfs),
            "final_action": self.final_action,
            "combination": self.combination,
            "validation_mode": self.validation_mode,
            "trigger_mode": self.trigger_mode,
            "final_direction": self.final_direction,
            "final_direction_explicit": self.final_direction_explicit,
            "destination": self.destination,
            "owner_chat_id": self.owner_chat_id,
            "persistent": self.persistent,
            "enabled": self.enabled,
            "time_filters": list(self.time_filters),
            "sweep_levels": list(self.sweep_levels),
            "sweep_atr_period": self.sweep_atr_period,
            "sweep_atr_mult": self.sweep_atr_mult,
            "source": self.source,
            "alert_template": self.alert_template,
        }

    @staticmethod
    def from_json(item: dict) -> "StrategySpec":
        item = oz_profiles.normalize_watch_payload(item)
        spec = StrategySpec(
            spec_id=str(item.get("spec_id") or ""),
            name=str(item.get("name") or item.get("spec_id") or "PRIVATE"),
            symbol=str(item.get("symbol") or ""),
            conditions=tuple(ConditionSpec(**x) for x in item.get("conditions", [])),
            oz_tfs=tuple(item.get("oz_tfs", [])),
            final_action=str(item.get("final_action") or "OZ"),
            combination=str(item.get("combination") or "ALL"),
            validation_mode=str(item.get("validation_mode") or "NORMAL"),
            trigger_mode=str(item.get("trigger_mode") or "OZ"),
            final_direction=str(item.get("final_direction") or "").strip().upper() or None,
            final_direction_explicit=bool(
                item.get("final_direction_explicit", bool(item.get("final_direction")))
            ),
            destination=str(item.get("destination") or "PRIVATE"),
            owner_chat_id=str(item.get("owner_chat_id") or "").strip() or None,
            persistent=bool(item.get("persistent", False)),
            enabled=bool(item.get("enabled", True)),
            time_filters=tuple(item.get("time_filters", [])),
            sweep_levels=tuple(item.get("sweep_levels", DEFAULT_SWEEP_LEVELS)),
            sweep_atr_period=int(item.get("sweep_atr_period", DEFAULT_SWEEP_ATR_PERIOD)),
            sweep_atr_mult=float(item.get("sweep_atr_mult", DEFAULT_SWEEP_ATR_MULT)),
            source=str(item.get("source") or "PRIVATE"),
            alert_template=str(item.get("alert_template") or ""),
        )
        spec.validate()
        return spec


def condition_from_descriptor(raw: dict, default_tf: str) -> ConditionSpec:
    """사전의 primitive descriptor를 기존 공용 조건 계약으로 변환합니다."""
    item = dict(raw or {})
    tf = normalize_tf(item.pop("tf", None) or default_tf)
    allowed = {
        "kind", "direction", "side", "ma_family", "fast_period", "slow_period",
        "metric", "metric_operator", "metric_value", "metric_rhs",
    }
    kwargs = {k: v for k, v in item.items() if k in allowed and k != "kind"}
    return ConditionSpec(kind=item.get("kind"), tf=tf, **kwargs)


def parse_condition_token(token: str, default_tf: str = "") -> ConditionSpec:
    raw = str(token or "").strip().upper().replace(" ", "")
    if not raw:
        raise ValueError("빈 condition")
    if "@" in raw:
        name, tf = raw.rsplit("@", 1)
    else:
        name, tf = raw, default_tf
    tf = normalize_tf(tf)
    if not tf:
        raise ValueError(f"조건 TF 누락: {token}")

    # SPECIAL/개인전략이 공용으로 사용할 수 있는 canonical metric token.
    # 예: TM:ADX:GTE:25@15m / TM:MACD:GT:MACD_SIGNAL@15m
    metric_match = re.fullmatch(
        r"(?:TREND_METRIC|TM):([A-Z0-9_+\-]+):(GT|GTE|LT|LTE|EQ|NE):([A-Z0-9_.+\-]+)",
        name,
    )
    if metric_match:
        metric = metric_match.group(1).lower()
        operator = metric_match.group(2).upper()
        rhs_raw = metric_match.group(3)
        try:
            value = float(rhs_raw)
        except ValueError:
            return ConditionSpec(
                "TREND_METRIC", tf, metric=metric, metric_operator=operator,
                metric_rhs=rhs_raw.lower(),
            )
        return ConditionSpec(
            "TREND_METRIC", tf, metric=metric, metric_operator=operator, metric_value=value,
        )

    aliases = {
        "TREND": ("TREND", "AUTO", ""),
        "TREND_UP": ("TREND", "LONG", ""),
        "UPTREND": ("TREND", "LONG", ""),
        "TREND_DOWN": ("TREND", "SHORT", ""),
        "DOWNTREND": ("TREND", "SHORT", ""),
        "WONBI": ("WONBI", "AUTO", ""),
        "WONBI_TOUCH": ("WONBI", "AUTO", ""),
        "WONBI_LOWER": ("WONBI", "LONG", "LOWER"),
        "WONBI_LOWER_TOUCH": ("WONBI", "LONG", "LOWER"),
        "WONBI_UPPER": ("WONBI", "SHORT", "UPPER"),
        "WONBI_UPPER_TOUCH": ("WONBI", "SHORT", "UPPER"),
        "FVG": ("FVG", "AUTO", ""),
        "FVG_TOUCH": ("FVG", "AUTO", ""),
        "FVG_BULL": ("FVG", "LONG", "BULL"),
        "FVG_BULL_TOUCH": ("FVG", "LONG", "BULL"),
        "BULL_FVG_TOUCH": ("FVG", "LONG", "BULL"),
        "FVG_BEAR": ("FVG", "SHORT", "BEAR"),
        "FVG_BEAR_TOUCH": ("FVG", "SHORT", "BEAR"),
        "BEAR_FVG_TOUCH": ("FVG", "SHORT", "BEAR"),
        "SWEEP": ("SWEEP", "AUTO", ""),
        "SWEEP_TOUCH": ("SWEEP", "AUTO", ""),
        "EXT_LIQUIDITY": ("SWEEP", "AUTO", ""),
        "EXT_LIQUIDITY_TOUCH": ("SWEEP", "AUTO", ""),
        "SWEEP_LONG": ("SWEEP", "LONG", ""),
        "SWEEP_LONG_TOUCH": ("SWEEP", "LONG", ""),
        "SWEEP_SHORT": ("SWEEP", "SHORT", ""),
        "SWEEP_SHORT_TOUCH": ("SWEEP", "SHORT", ""),
        # 외부유동성 레벨 자체가 방향을 결정합니다.
        "PDH": ("SWEEP", "SHORT", "PDH"),
        "PDH_TOUCH": ("SWEEP", "SHORT", "PDH"),
        "PDL": ("SWEEP", "LONG", "PDL"),
        "PDL_TOUCH": ("SWEEP", "LONG", "PDL"),
        "SESSION_HIGH": ("SWEEP", "SHORT", "SESSION_HIGH"),
        "SESSION_LOW": ("SWEEP", "LONG", "SESSION_LOW"),
        "PREV_4H_HIGH": ("SWEEP", "SHORT", "PREV_4H_HIGH"),
        "PREV_4H_LOW": ("SWEEP", "LONG", "PREV_4H_LOW"),
        "PREV_8H_HIGH": ("SWEEP", "SHORT", "PREV_8H_HIGH"),
        "PREV_8H_LOW": ("SWEEP", "LONG", "PREV_8H_LOW"),
        # Percentile OUT: 하단 OUT=LONG, 상단 OUT=SHORT.
        "OUT": ("PERCENTILE", "AUTO", ""),
        "PERCENTILE_OUT": ("PERCENTILE", "AUTO", ""),
        "LOWER_OUT": ("PERCENTILE", "LONG", "LOWER"),
        "PERCENTILE_LOWER_OUT": ("PERCENTILE", "LONG", "LOWER"),
        "UPPER_OUT": ("PERCENTILE", "SHORT", "UPPER"),
        "PERCENTILE_UPPER_OUT": ("PERCENTILE", "SHORT", "UPPER"),
    }
    if name not in aliases:
        raise ValueError(f"지원하지 않는 condition token: {token}")
    kind, direction, side = aliases[name]
    return ConditionSpec(kind=kind, tf=tf, direction=direction, side=side)
