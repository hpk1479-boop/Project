# -*- coding: utf-8 -*-
"""
KIM SECRETARY
=============
Composer 2.0: TREND/FVG/SWEEP/WONBI/PERCENTILE 조건 조합, OZ 라우팅, Telegram 명령/알림 게이트웨이를 담당합니다.
MT5 Named Pipe 수신과 전략용 ZMQ 데이터 서버는 독립 Python `THE STAFF OF MOSES`로 분리되었습니다.
Telegram 자연어 해석은 `command_interpreter`, 개인 시간연쇄 런타임은 `watch_orchestrator`가 담당합니다.
전략 알림 인터페이스(tcp://127.0.0.1:5556)는 기존과 동일합니다.
"""

from __future__ import annotations
import domain_memory

import json
import re
import importlib.util
from domain_clock import datetime as dt
import logging
import os
import sys
import shutil
from pathlib import Path
import threading
from domain_clock import time
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo
from sweep_selectors import normalize_level_selectors
from durable_protocol import Records, identity, receipt_key, fact_scope, source_health, atomic_json, read_json
import oz_profiles

import pandas as pd

from command_interpreter import (
    COMMAND_ALIASES_FILENAME, DEFAULT_COMMAND_GOLD_SYMBOL, DEFAULT_COMMAND_LANGUAGE,
    DEFAULT_COMMAND_TF, MT5_TIMEFRAMES, OZ_BASE_TFS, OZ_TF_MAP, WATCH_TF_MAP,
    CommandInterpreter, normalize_tf, tf_seconds,
)
from watch_orchestrator import (
    CHAIN_FINAL_ACTIONS, CHAIN_TRIGGER_TYPES, DEFAULT_CHAIN_REFRESH_SEC, GENERIC_MA_PERIODS,
    TRIGGER_MODES, VALIDATION_MODES, ChainTriggerSpec, TimedChainSpec,
    UnorderedConditionLatch, WatchOrchestrator, format_duration_ko, stable_id,
)


# ---------------------------------------------------------------------
# Logging / config
# ---------------------------------------------------------------------
LOG_DIR = Path(__file__).resolve().parent / "logs"
def _atomic_write_json(path: Path, payload) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


def load_config(file_path: str = "config.txt") -> dict[str, str]:
    """KEY=VALUE 형식 설정파일을 읽습니다. 시스템 기본 파일은 config.txt 입니다."""
    config: dict[str, str] = {}
    script_dir = Path(__file__).resolve().parent

    requested = Path(file_path)
    path = requested if requested.is_absolute() else script_dir / requested
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
        logging.info("김비서 설정파일 로드: %s", path.resolve())
    except FileNotFoundError:
        logging.warning("[%s] 김비서 설정파일이 없습니다.", path)
    return config



def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def csv_items(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def safe_name(text: str) -> str:
    for c in '\\/:*?"<>|.':
        text = text.replace(c, "_")
    return text


def _oz_trigger_mode(text: str) -> str:
    """자연어의 OZ 프로필 키워드(브레이커/레짐/슈퍼, 순서 무관)를 공식 trigger_mode로 변환합니다."""
    return oz_profiles.text_trigger_mode(text)


def _canonical_trigger_mode(value: object) -> str:
    """순서가 다른 조합도 공식 trigger_mode로 맞춥니다. 해석 불가 값은 그대로 두어 검증에서 거부합니다."""
    raw = str(value or "OZ").upper()
    flags = oz_profiles.trigger_flags(raw)
    return oz_profiles.canonical_trigger_mode(flags) if flags is not None else raw


def _oz_profile_label(validation_mode: str, trigger_mode: str) -> str:
    """공식 OZ 프로필의 사용자 표시명을 반환합니다."""
    return oz_profiles.profile_label(validation_mode, trigger_mode)





# ---------------------------------------------------------------------
# THE STAFF OF MOSES runtime control
# ---------------------------------------------------------------------
WONBI_LENGTH = 4
WONBI_DEFAULT_SIGMA = 3.0
STAFF_ENDPOINT_DEFAULT = "tcp://127.0.0.1:5555"




# ---------------------------------------------------------------------
# Economy worker (isolated from data server)
# ---------------------------------------------------------------------
KST = dt.timezone(dt.timedelta(hours=9))












# ---------------------------------------------------------------------
# Central Telegram command / final-alert gateway + manual OZ Watch
# ---------------------------------------------------------------------
ALERT_ENDPOINT_DEFAULT = "tcp://127.0.0.1:5556"
OZ_COMMAND_FILE_DEFAULT = "oz_watch_command.jsonl"




# ---------------------------------------------------------------------
# KIM COMPOSER 2.0
# ---------------------------------------------------------------------
# 전략 엔진(TREND/FVG/SWEEP)은 사실만 계산합니다.
# 김매니저는 공식 Pipeline과 개인 Watch를 동일한 StrategySpec으로 변환하여
# 조건을 조합하고, setup 성립 시 monitor_OZ에 최종 트리거 감시를 arm합니다.
# ---------------------------------------------------------------------

from dataclasses import dataclass, field, asdict
import hashlib
import math

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
DEFAULT_SPECIAL_SCAN_SEC = 1.0
SPECIAL_FILENAME_RE = re.compile(r"^SPECIAL(\d+)\.py$", re.IGNORECASE)
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
    ma_family: str = ""          # MA_STATE / MA_PRICE_STATE / MA_SLOPE_STATE: EMA / HMA
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
        except (TypeError, ValueError):
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
        if kind == "MA_STATE":
            if side not in {"ABOVE", "BELOW"}:
                raise ValueError(f"MA_STATE 배열 방향 오류: {side}")
            if ma_family not in GENERIC_MA_PERIODS:
                raise ValueError(f"MA_STATE MA family 오류: {ma_family}")
            allowed = GENERIC_MA_PERIODS[ma_family]
            invalid = [x for x in (fast_period, slow_period) if x not in allowed]
            if invalid:
                raise ValueError(
                    f"{ma_family} 지원 period가 아닙니다: {','.join(map(str, invalid))} "
                    f"(지원: {','.join(map(str, allowed))})"
                )
            if fast_period == slow_period:
                raise ValueError("MA_STATE fast/slow period가 같습니다")
        if kind == "MA_PRICE_STATE":
            if side not in {"ABOVE", "BELOW"}:
                raise ValueError(f"MA_PRICE_STATE 방향 오류: {side}")
            if ma_family not in GENERIC_MA_PERIODS:
                raise ValueError(f"MA_PRICE_STATE MA family 오류: {ma_family}")
            allowed = GENERIC_MA_PERIODS[ma_family]
            if slow_period not in allowed:
                raise ValueError(
                    f"{ma_family} 지원 period가 아닙니다: {slow_period} "
                    f"(지원: {','.join(map(str, allowed))})"
                )
            if fast_period != 0:
                raise ValueError("MA_PRICE_STATE는 fast_period를 사용하지 않습니다")
        if kind == "MA_SLOPE_STATE":
            if side not in {"UP", "DOWN"}:
                raise ValueError(f"MA_SLOPE_STATE 방향 오류: {side}")
            if ma_family not in GENERIC_MA_PERIODS:
                raise ValueError(f"MA_SLOPE_STATE MA family 오류: {ma_family}")
            allowed = GENERIC_MA_PERIODS[ma_family]
            if slow_period not in allowed:
                raise ValueError(
                    f"{ma_family} 지원 period가 아닙니다: {slow_period} "
                    f"(지원: {','.join(map(str, allowed))})"
                )
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
            return f"{base} → {oz_label} {oz_side}올존"
        return base

    def to_json(self) -> dict:
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


class TimePolicy:
    def __init__(self, config: dict[str, str]):
        self.config = dict(config)

    @staticmethod
    def _inside(now_hm: str, raw: str) -> bool:
        value = str(raw or "").strip().replace(":", "")
        if "-" not in value:
            return False
        start, end = [x.strip() for x in value.split("-", 1)]
        if len(start) != 4 or len(end) != 4 or not start.isdigit() or not end.isdigit():
            return False
        if start <= end:
            return start <= now_hm <= end
        return now_hm >= start or now_hm <= end

    def allows(self, filters: tuple[str, ...]) -> bool:
        if not filters:
            return True
        now_hm = dt.datetime.now(KST).strftime("%H%M")
        for name in filters:
            key = str(name).upper()
            if key in {"ALL", "ANYTIME", "NONE"}:
                return True
            raw = self.config.get(key, "")
            if raw and self._inside(now_hm, raw):
                return True
        return False




class SpecialPluginAPI:
    """SPECIAL 모듈이 Composer의 private state를 직접 만지지 않도록 하는 공식 확장 경계.

    전략 계산은 SPECIAL 모듈이 소유하고, 이 객체는 기존 OZ watch lifecycle / Staff /
    알림 core에 대한 원자적 접근만 제공합니다.
    """

    def __init__(self, manager: "ComposerManager") -> None:
        self._manager = manager

    @property
    def official_chat_id(self) -> Optional[str]:
        return str(self._manager.chat_id or "").strip() or None

    def config_get(self, key: str, default=None):
        return self._manager.config.get(str(key), default)

    def time_allowed(self, filters: Iterable[str]) -> bool:
        return bool(self._manager._time_policy.allows(tuple(filters)))

    def staff_request(
        self,
        symbol: str,
        tfs: Iterable[str],
        indicators: Iterable[str],
        *,
        lane: str = "maintenance",
    ):
        # ZMQ REQ 소켓은 스레드간 공유하지 않습니다. FINAL_ALERT hook은 event 전용 client를 사용합니다.
        client = (
            self._manager._special_event_staff
            if str(lane or "").strip().lower() == "event"
            else self._manager.staff
        )
        return client.request(symbol, tfs, indicators)

    def register_watch_handler(self, watch_type: str, handler: object) -> None:
        self._manager.register_special_watch_handler(watch_type, handler)

    def register_oz_event_handler(self, spec_id: str, handler: object) -> None:
        self._manager.register_special_oz_event_handler(spec_id, handler)

    def render_oz_alert(self, spec: object, event: dict, fallback: str) -> str:
        return self._manager._render_official_oz_alert(spec, event, fallback)

    def deliver_oz_event_core(self, event: dict) -> dict:
        # SPECIAL hook 재진입 없이 김매니저의 기존 OZ lifecycle만 실행합니다.
        return self._manager._handle_oz_event_core(dict(event))

    @staticmethod
    def _matches(
        watch_id: str,
        payload: dict,
        *,
        source_spec_id: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        watch_id_prefix: Optional[str] = None,
    ) -> bool:
        if source_spec_id is not None:
            ids: list[str] = []
            raw_ids = payload.get("source_spec_ids")
            if isinstance(raw_ids, (list, tuple, set)):
                ids.extend(str(x).strip() for x in raw_ids if str(x).strip())
            single = str(payload.get("source_spec_id") or "").strip()
            if single:
                ids.append(single)
            if str(source_spec_id) not in set(ids):
                return False
        if symbol is not None and str(payload.get("symbol") or "") != str(symbol):
            return False
        if direction is not None and str(payload.get("direction") or "").upper() != str(direction).upper():
            return False
        if watch_id_prefix is not None and not str(watch_id).startswith(str(watch_id_prefix)):
            return False
        return True

    def has_oz_watch(self, watch_id: str) -> bool:
        wid = str(watch_id or "").strip()
        if not wid:
            return False
        with self._manager._lock:
            return wid in self._manager._active_children

    def get_oz_watch(self, watch_id: str) -> Optional[dict]:
        wid = str(watch_id or "").strip()
        if not wid:
            return None
        with self._manager._lock:
            payload = self._manager._active_children.get(wid)
            return dict(payload) if isinstance(payload, dict) else None

    def snapshot_oz_watches(
        self,
        *,
        source_spec_id: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        watch_id_prefix: Optional[str] = None,
    ) -> list[tuple[str, dict]]:
        with self._manager._lock:
            return [
                (str(wid), dict(payload))
                for wid, payload in self._manager._active_children.items()
                if isinstance(payload, dict) and self._matches(
                    str(wid), payload,
                    source_spec_id=source_spec_id, symbol=symbol, direction=direction,
                    watch_id_prefix=watch_id_prefix,
                )
            ]

    def ensure_oz_watch(
        self,
        payload: dict,
        *,
        compare_keys: Iterable[str] = (),
        push_if_changed: bool = True,
    ) -> bool:
        item = dict(payload)
        wid = str(item.get("watch_id") or "").strip()
        if not wid or str(item.get("action") or "").upper() != "MANUAL_WATCH":
            raise ValueError("SPECIAL OZ watch payload 오류")
        keys = tuple(str(x) for x in compare_keys)
        changed = False
        with self._manager._lock:
            current = self._manager._active_children.get(wid)
            changed = (
                not isinstance(current, dict)
                or not keys
                or any(current.get(key) != item.get(key) for key in keys)
            )
            if changed:
                self._manager._active_children[wid] = dict(item)
                self._manager._save_active_children_state_locked()
        if changed and push_if_changed:
            self._manager._push(dict(item))
        return changed

    def register_oz_watch(self, payload: dict, *, push: bool = True) -> None:
        item = dict(payload)
        wid = str(item.get("watch_id") or "").strip()
        if not wid or str(item.get("action") or "").upper() != "MANUAL_WATCH":
            raise ValueError("SPECIAL OZ watch payload 오류")
        with self._manager._lock:
            self._manager._active_children[wid] = dict(item)
            self._manager._save_active_children_state_locked()
        if push:
            self._manager._push(dict(item))

    def cancel_oz_watches(self, watch_ids: Iterable[str]) -> int:
        ids = tuple(dict.fromkeys(str(x).strip() for x in watch_ids if str(x).strip()))
        if not ids:
            return 0
        cancels: list[dict] = []
        with self._manager._lock:
            for wid in ids:
                item = self._manager._active_children.pop(wid, None)
                if not isinstance(item, dict):
                    continue
                cancels.append({
                    "action": "CANCEL_MANUAL",
                    "watch_id": wid,
                    "request_chat_id": item.get("request_chat_id"),
                    "validation_mode": item.get("validation_mode"),
                    "trigger_mode": item.get("trigger_mode"),
                })
            if cancels:
                self._manager._save_active_children_state_locked()
        for cancel in cancels:
            self._manager._push(cancel)
        return len(cancels)

    def replace_oz_watches(
        self,
        payloads: Iterable[dict],
        *,
        source_spec_id: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        watch_id_prefix: Optional[str] = None,
    ) -> tuple[list[str], list[str]]:
        new_items = [dict(x) for x in payloads]
        for item in new_items:
            if not str(item.get("watch_id") or "").strip() or str(item.get("action") or "").upper() != "MANUAL_WATCH":
                raise ValueError("SPECIAL OZ replacement payload 오류")

        cancels: list[dict] = []
        removed_ids: list[str] = []
        with self._manager._lock:
            for wid, current in list(self._manager._active_children.items()):
                if not isinstance(current, dict) or not self._matches(
                    str(wid), current,
                    source_spec_id=source_spec_id, symbol=symbol, direction=direction,
                    watch_id_prefix=watch_id_prefix,
                ):
                    continue
                self._manager._active_children.pop(wid, None)
                removed_ids.append(str(wid))
                cancels.append({
                    "action": "CANCEL_MANUAL",
                    "watch_id": str(wid),
                    "request_chat_id": current.get("request_chat_id"),
                    "validation_mode": current.get("validation_mode"),
                    "trigger_mode": current.get("trigger_mode"),
                })
            for item in new_items:
                self._manager._active_children[str(item["watch_id"])] = dict(item)
            if cancels or new_items:
                self._manager._save_active_children_state_locked()

        for cancel in cancels:
            self._manager._push(cancel)
        for item in new_items:
            self._manager._push(dict(item))
        return removed_ids, [str(x["watch_id"]) for x in new_items]


class ComposerManager:
    """2.0 중앙 Composer + 전략 이벤트 관문."""

    def __init__(
        self,
        config: dict[str, str],
        stop_event: threading.Event,
        oz_queue: OZCommandQueue,
        wonbi_state: WonbiState,
        notifier: Optional[NotificationService] = None,
        *, event_services=None,
    ):
        if event_services is None or notifier is None:
            raise ValueError("event services and output port are required")
        self.event_services = event_services
        # config.txt는 시스템/공통 설정만 소유합니다.
        # 공식 메인전략 정의는 SPECIAL/SPECIAL<n>.py가 직접 등록합니다.
        self.system_config = dict(config)
        self.config = dict(self.system_config)
        # SPECIAL은 공식 메인전략과 전략별 Watch 판정기의 단일 확장 슬롯입니다.
        # 첫 스캔에서 로드된 전략이 없으면 "스페셜 전략이 없습니다" 로그를 1회 남깁니다.
        try:
            special_base_dir = Path(__file__).resolve().parent if event_services is None else event_services.root
        except NameError:
            special_base_dir = Path.cwd()
        self.special_dir = special_base_dir / "SPECIAL"
        if event_services is None:self.special_dir.mkdir(parents=True, exist_ok=True)
        self._special_selection_active = os.environ.get("OZ_SPECIAL_SELECTION_ACTIVE", "").strip() == "1"
        self._enabled_special_names = {
            item.strip().casefold()
            for item in os.environ.get("OZ_ENABLED_SPECIALS", "").split(",")
            if item.strip()
        }
        # [전략 설정] SPECIAL별 최종 OZ 트리거 슬롯.
        # OZ_SYSTEM CONTROL -> 환경변수 OZ_SPECIAL_TRIGGERS(JSON) -> 공용 oz_profiles 등록 -> 각 SPECIAL 조회.
        # SPECIAL 로드 전에 등록해야 SPECIAL이 import 시점에 자기 최종 트리거를 확정할 수 있습니다.
        profile_api = oz_profiles if event_services is None else event_services.profiles
        try:
            special_trigger_map = dict(event_services.trigger_map) if event_services is not None else oz_profiles.parse_special_trigger_json(
                os.environ.get(oz_profiles.SPECIAL_TRIGGERS_ENV, "")
            )
        except ValueError as exc:
            logging.error("❌ [SPECIAL] 트리거 설정 해석 실패 | %s | 코드 기본값 사용", exc)
            special_trigger_map = {}
        for special_name, error_text in profile_api.set_special_trigger_overrides(special_trigger_map).items():
            logging.error("❌ [SPECIAL] 트리거 설정 무시 | %s | %s | 코드 기본값 사용", special_name, error_text)
        for special_name, (slot_vm, slot_tm, _slot_text) in sorted(profile_api.special_trigger_overrides().items()):
            logging.info("🟢 [SPECIAL] 트리거 슬롯 | %s | %s", special_name, oz_profiles.profile_label(slot_vm, slot_tm))
        self.special_modules: dict[str, object] = {}
        self._special_attempted_signatures: dict[str, tuple[int, int]] = {}
        self._special_initial_scan_done = False
        self._last_special_scan = 0.0
        # SPECIAL은 전략별 Watch 판정기를 이 레지스트리에 등록합니다.
        # 김매니저 본체는 condition type을 알 필요 없이 공용 Watch lifecycle만 관리합니다.
        self._special_watch_handlers: dict[str, object] = {}
        self._special_watch_event_handlers: list[object] = []
        # OZ FINAL_ALERT 전용 SPECIAL handler. spec_id로 직접 라우팅하여 전략간 예외 전파를 차단합니다.
        self._special_oz_event_handlers: dict[str, object] = {}
        aliases_name = str(config.get("COMMAND_ALIASES_FILE") or COMMAND_ALIASES_FILENAME).strip() or COMMAND_ALIASES_FILENAME
        self.command_aliases_path = self._resolve_config_path(aliases_name) if event_services is None else event_services.alias_path
        self.stop_event = stop_event
        self.oz_queue = oz_queue
        self.wonbi_state = wonbi_state
        self.notifier = notifier
        self.token = str(config.get("TELEGRAM_TOKEN", "")).strip()
        self.chat_id = str(config.get("TELEGRAM_CHAT_ID", "")).strip()
        self.alert_endpoint = str(config.get("MANAGER_ALERT_ENDPOINT", ALERT_ENDPOINT_DEFAULT)).strip()
        self._receipts = Records(LOG_DIR / 'event_receipts.json', resident=event_services is not None, retention_seconds=3*86400)
        self._fact_revisions = Records(LOG_DIR / 'fact_revisions.json', resident=event_services is not None)
        self._signature_records = Records(LOG_DIR / 'composer_signatures.json', resident=event_services is not None)
        self._source_bindings = {}
        self.source_status = {}
        self._delivery_context = threading.local()
        self._command_context = threading.local()
        # Stored atomically alongside the existing private WATCH state, not a new engine.
        self._watch_message_links: dict[str, dict] = {}
        self._lock = threading.RLock()

        self.official_specs: dict[str, StrategySpec] = {}
        self.official_chain_specs: dict[str, ConfigTimedChainSpec] = {}
        self.manual_specs: dict[str, StrategySpec] = {}
        self._last_signatures: dict[tuple[str, str], str] = {}
        for item in self._signature_records.all().values():
            self._last_signatures[(item['spec_id'], item['direction'])] = item['signature']
        self._active_children: dict[str, dict] = {}
        self.command_interpreter = (CommandInterpreter(
            self.system_config,
            self.command_aliases_path,
            allowed_symbols_provider=self._allowed_symbols,
        ) if event_services is None else event_services.interpreter(self))
        # 기존 외부 참조 호환용 읽기 별칭. 실제 소유권은 CommandInterpreter에 있습니다.
        self.command_aliases = self.command_interpreter.language()
        # 공식 SPECIAL TIMED_CHAIN은 개인 timed_chains와 완전히 분리합니다.
        self._config_chain_state: dict[str, dict] = {}
        self._config_chain_active: dict[str, dict] = {}
        self._last_config_chain_refresh = 0.0
        self._last_config_chain_bar_poll = 0.0
        self._config_chain_bar_times_cache: dict[tuple[str, str], tuple[float, ...]] = {}

        # Fact store
        self.trend_facts: dict[tuple[str, str], dict] = {}
        # strategy_FVG가 FVG lifecycle의 단일 사실 공급원입니다.
        # active zone 전체 상태와 현재 touch 상태를 Composer가 직접 유지합니다.
        self.fvg_zones: dict[tuple[str, str, str], dict] = {}
        self.fvg_touches: dict[tuple[str, str, str], dict] = {}
        self.fvg_created_watches: dict[str, FVGCreatedWatchSpec] = {}
        self.sweep_touches: dict[tuple[str, str, str, str], dict] = {}
        self.wonbi_facts: dict[tuple[str, str, str], dict] = {}
        self.percentile_facts: dict[tuple[str, str, str], dict] = {}
        # 현재 MA 배열 상태 Gate. CROSS 이벤트가 아니라 STAFF 최신 진행봉의 현재 배열을 추적합니다.
        self.ma_state_facts: dict[tuple[str, str, str, int, int], dict] = {}
        # 현재 봉 가격과 STAFF MA의 상대 위치. MA_STATE와 분리된 정식 fact입니다.
        self.ma_price_state_facts: dict[tuple[str, str, str, int], dict] = {}
        # STAFF MA의 현재값과 2봉 전 값을 비교한 방향 상태. 이평을 manager/TREND에서 재계산하지 않습니다.
        self.ma_slope_state_facts: dict[tuple[str, str, str, int], dict] = {}
        # strategy_INDICATOR가 구독된 필드만 push하는 지표값 cache.
        # key=(symbol, tf, metric), value={value, bar_time, received_mono}.
        self.trend_metric_facts: dict[tuple[str, str, str], dict] = {}
        self._trend_metric_gate_state: dict[tuple[str, str, str], dict] = {}
        # 사전 기반 복합조건 Watch의 edge/봉마감 상태. 특정 전략명은 저장하지 않습니다.
        self._compound_watch_state: dict[str, dict] = {}
        # 전략별 FILTER 판정 상태는 SPECIAL handler가 소유합니다.
        self._wonbi_touch_seq = 0
        self._percentile_touch_seq = 0
        self._ma_state_seq = 0
        self._ma_price_state_seq = 0
        self._ma_slope_state_seq = 0
        self._trend_metric_gate_seq = 0

        self._state_path = LOG_DIR / "composer_private_watches.json"
        self._chain_state_path = LOG_DIR / "composer_timed_chains.json"
        self._fvg_created_watch_state_path = LOG_DIR / "composer_fvg_created_watches.json"
        # KIM이 만든 현재 OZ 감시 목록을 logs 폴더에 별도 보존합니다.
        # KIM만 재시작되어도 감시 소유권을 잃지 않도록 하기 위한 상태 파일입니다.
        self._active_children_state_path = LOG_DIR / "composer_active_oz_watches.json"
        # 공식 SPECIAL TIMED_CHAIN의 stage 상태를 재시작 뒤에도 이어가기 위한 상태 파일입니다.
        self._config_chain_state_path = LOG_DIR / "composer_config_timed_chain_state.json"
        self._engine_subscriptions: dict[str, dict[str, dict]] = {"TREND": {}, "FVG": {}, "SWEEP": {}}
        self._subscription_dirty = True
        self.watch_orchestrator = WatchOrchestrator(
            lock=self._lock,
            state_path=self._chain_state_path,
            config=self.config,
            push_callback=self._push,
            notify_callback=self.send_telegram,
            mark_dirty_callback=lambda: setattr(self, "_subscription_dirty", True),
        )
        # manager_KIM 내부 기존 참조는 같은 dict 객체를 바라보게 유지합니다.
        self.timed_chains = self.watch_orchestrator.chains
        self._last_engine_refresh = 0.0
        self._last_wonbi_poll = 0.0
        self._last_wonbi_sigma: Optional[float] = None
        self._time_policy = TimePolicy(self.config)
        staff_endpoint = str(config.get("STAFF_ENDPOINT", config.get("STAFF_BIND_ENDPOINT", STAFF_ENDPOINT_DEFAULT)))
        staff_timeout_ms = int(config.get("ZMQ_TIMEOUT_MS", "5000"))
        self.staff = event_services.staff
        # 공식 SPECIAL TIMED_CHAIN 이벤트 스레드 전용 STAFF client.
        # maintenance worker의 self.staff와 ZMQ REQ 소켓을 공유하지 않아 봉 수 expiry 조회를 직렬 안전하게 유지합니다.
        self._config_chain_event_staff = event_services.staff
        # SPECIAL FINAL_ALERT hook 전용 STAFF client. event thread와 maintenance thread의 REQ 소켓을 분리합니다.
        special_event_staff_timeout_ms = int(
            config.get("SPECIAL_EVENT_STAFF_TIMEOUT_MS", str(min(staff_timeout_ms, 3000)))
        )
        self._special_event_staff = event_services.staff
        self.special_api = SpecialPluginAPI(self)

        self.context = None
        self.alert_sock = None

        if event_services is None or event_services.restore_state:
            self._load_private_state()
            self._load_timed_chain_state()
            self._load_fvg_created_watch_state()
            self._load_active_children_state()
        self._scan_special_strategies()

    @staticmethod
    def _resolve_config_path(file_path: str) -> Path:
        script_dir = Path(__file__).resolve().parent
        requested = Path(file_path)
        return requested if requested.is_absolute() else script_dir / requested

    def send_telegram(self, text: str, chat_id: Optional[str] = None, event_id: Optional[str] = None,
                      *, watch_id: Optional[str] = None, watch_registration: bool = False,
                      reply_to_message_id: Optional[int] = None) -> bool:
        target = str(chat_id or self.chat_id or "").strip()
        links = self._watch_links_for_ids([watch_id], target) if watch_id else []
        if watch_registration and links:
            link = links[0]
            # Persist text before HTTP so an ACK retry can recover the same confirmation ID.
            with self._lock:
                link["confirmation_text"] = str(text)
                self._save_private_state_locked()
            ids: list[int] = []
            delivered = self.notifier.send(
                text, chat_id=target,
                event_id=identity("WATCH_REGISTER", target, link["watch_id"]),
                sent_message_ids=ids,
            )
            if not delivered or not ids:
                logging.error("[WATCH Reply] 등록 확인 message_id를 받지 못했습니다 | %s", watch_id)
                return False
            with self._lock:
                link["confirmation_message_id"] = ids[0]
                self._save_private_state_locked()
            return True
        if links and reply_to_message_id is None:
            return self._send_watch_notification(text, target, links, event_id=event_id)
        parent = event_id or getattr(self._delivery_context, 'event_id', None)
        token = identity(parent, target, text) if parent else None
        kwargs = {"reply_to_message_id": reply_to_message_id} if reply_to_message_id else {}
        return self.notifier.send(text, chat_id=target or None, event_id=token, **kwargs)

    @staticmethod
    def _telegram_message_id(value: object) -> Optional[int]:
        # bool is an int in Python, but is never a Telegram message identifier.
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number > 0 and str(value).strip() == str(number) else None

    def _watch_links_for_ids(self, watch_ids: Iterable[object], owner: str) -> list[dict]:
        ids = {str(x) for x in watch_ids if x}
        if not ids:
            return []
        with self._lock:
            return [link for wid, link in self._watch_message_links.items()
                    if link.get("owner_chat_id") == str(owner)
                    and (wid in ids or ids.intersection(link.get("members", {})))]

    def _remember_watch_command(self, watch_id: str, owner: str, watch_type: str,
                                cancel_action: str) -> Optional[dict]:
        context = getattr(self._command_context, "current", None)
        if not context or context["owner_chat_id"] != str(owner) or not context["message_id"]:
            return None  # Legacy/programmatic callers keep their previous behaviour.
        with self._lock:
            link = self._watch_message_links.get(watch_id)
            if link is None:
                link = {
                    "watch_id": watch_id, "owner_chat_id": str(owner),
                    "command_message_id": context["message_id"],
                    "confirmation_message_id": None,
                    "original_command": context["original_text"],
                    "watch_type": watch_type, "cancel_action": cancel_action,
                    "confirmation_text": "", "members": {}, "status": "active",
                }
                self._watch_message_links[watch_id] = link
                self._save_private_state_locked()
            return link

    def _track_watch_payload(self, item: dict) -> bool:
        actions = {"MANUAL_WATCH": "CANCEL_MANUAL", "GENERIC_WATCH": "CANCEL_GENERIC",
                   "LOCAL_EVENT_WATCH": "CANCEL_LOCAL_EVENT", "FVG_EVENT_WATCH": "CANCEL_FVG_EVENT"}
        action = str(item.get("action") or "").upper()
        wid = str(item.get("watch_id") or "")
        owner = str(item.get("request_chat_id") or "")
        if action not in actions or not wid or not owner:
            return True
        parents = [item.get("chain_id"), item.get("source_chain_id"), item.get("source_spec_id"),
                   item.get("reply_parent_watch_id")]
        parents.extend(item.get("source_spec_ids") or [])
        links = self._watch_links_for_ids([wid, *parents], owner)
        if not links:
            link = self._remember_watch_command(wid, owner, str(item.get("watch_type") or "OZ"), actions[action])
            links = [link] if link else []
        with self._lock:
            if links and all(link.get("status") in {"cancelled", "cancel_requested", "inactive"} for link in links):
                return False  # A queued/stale parent snapshot must not re-arm a cancelled WATCH.
            changed = False
            for link in links:
                cancel_action = actions[action]
                if action == "LOCAL_EVENT_WATCH" and item.get("watch_type") in {"BAR", "WONBI_TOUCH"}:
                    cancel_action = "CANCEL_GENERIC"
                member = {"action": cancel_action, "watch_type": item.get("watch_type")}
                if link["members"].get(wid) != member:
                    link["members"][wid] = member
                    changed = True
            if changed:
                self._save_private_state_locked()
        return True

    def _retry_watch_confirmations(self) -> None:
        # The sendMessage receipt also stores the sent message_id. A restart between
        # HTTP success and binding can therefore recover without a second confirmation.
        with self._lock:
            pending = [dict(link) for link in self._watch_message_links.values()
                       if not link.get("confirmation_message_id") and link.get("confirmation_text")
                       and link.get("status") == "active"]
        for link in pending:
            self.send_telegram(link["confirmation_text"], link["owner_chat_id"],
                               watch_id=link["watch_id"], watch_registration=True)
        with self._lock:
            cancelling = [dict(link) for link in self._watch_message_links.values()
                          if link.get("status") == "cancel_requested"]
            undelivered = [link for link in self._watch_message_links.values()
                           if link.get("cancel_confirmation_pending")]
        for link in cancelling:
            # Reuse the exact idempotent engine command after a queue/ACK interruption.
            self._push({"action": link["cancel_action"], "watch_id": link["watch_id"],
                        "request_chat_id": link["owner_chat_id"], "reply_cancel": True})
        for link in undelivered:
            self._send_watch_cancel_result(link, link["cancel_result_text"])

    def _send_watch_cancel_result(self, link: dict, text: str) -> bool:
        with self._lock:
            # Replayed engine cancellation ACKs must not turn an earlier success into
            # a second, contradictory 'not found' message.
            link.setdefault("cancel_result_text", text)
            link["cancel_confirmation_pending"] = True
            self._save_private_state_locked()
        delivered = self.send_telegram(link["cancel_result_text"], link["owner_chat_id"],
                                       event_id=identity("WATCH_CANCEL", link["owner_chat_id"], link["watch_id"]))
        if delivered:
            with self._lock:
                link["cancel_confirmation_pending"] = False
                self._save_private_state_locked()
        return bool(delivered)

    def _send_watch_notification(self, text: str, owner: str, links: list[dict],
                                 *, event_id: Optional[str] = None) -> bool:
        delivered = True
        seen: set[int] = set()
        for link in links:
            # Late engine events after an explicit cancellation must not leak through.
            if link.get("status") in {"cancelled", "cancel_requested", "inactive"}:
                continue
            mid = self._telegram_message_id(link.get("command_message_id"))
            if mid is None or mid in seen:
                continue
            seen.add(mid)
            ok = self.send_telegram(text, owner, event_id=event_id, reply_to_message_id=mid)
            delivered = bool(ok) and delivered
        return delivered

    def _cancel_watch_reply(self, owner: str, reply_to_message_id: Optional[int]) -> None:
        guidance = "❓ 취소할 감시 등록 메시지에 답장으로 '취소'라고 보내주세요."
        with self._lock:
            matches = [link for link in self._watch_message_links.values()
                       if reply_to_message_id is not None
                       and link.get("owner_chat_id") == owner
                       and link.get("confirmation_message_id") == reply_to_message_id]
            if len(matches) != 1:
                self.send_telegram(guidance, owner)
                return
            link = matches[0]
            if link.get("status") in {"cancelled", "inactive"}:
                self.send_telegram("ℹ️ 이미 취소되었거나 종료된 감시입니다.", owner)
                return
            if link.get("status") == "cancel_requested":
                self.send_telegram("ℹ️ 해당 감시의 취소 처리가 진행 중입니다.", owner)
                return
            wid, action = link["watch_id"], link["cancel_action"]
            if action in {"CANCEL_MANUAL", "CANCEL_GENERIC"}:
                link["status"] = "cancel_requested"
                self._save_private_state_locked()
                try:
                    # No TF/profile filters: the existing engine removes this whole WATCH.
                    self._push({"action": action, "watch_id": wid,
                                "request_chat_id": owner, "reply_cancel": True})
                except Exception:
                    link["status"] = "active"
                    self._save_private_state_locked()
                    logging.exception("[WATCH Reply] 취소 명령 전달 실패 | %s", wid)
                    self.send_telegram("❌ 감시 취소 명령을 전달하지 못했습니다. 다시 시도해주세요.", owner)
                return
            persisted_ids = self._persisted_watch_ids(LOG_DIR / "oz_manual_watch_state.json", "watches", False)
            persisted_ids.update(self._persisted_watch_ids(LOG_DIR / "oz_generic_watch_state.json", "watches", False))
            alive = (wid in self.manual_specs or wid in self.fvg_created_watches or wid in self.timed_chains
                     or any(child_id in self._active_children or child_id in persisted_ids
                            for child_id in link["members"]))
            if not alive:
                link["status"] = "inactive"
                self._save_private_state_locked()
                self.send_telegram("ℹ️ 이미 취소되었거나 종료된 감시입니다.", owner)
                return
            # Manager-owned roots live in KIM, so remove only that exact parent record.
            # Child WATCH cancellation itself must go through the existing CANCEL_* paths.
            if action == "PRIVATE":
                self.manual_specs.pop(wid, None)
            elif action == "FVG_NEW":
                self.fvg_created_watches.pop(wid, None)
            elif action == "TIMED_CHAIN":
                self.timed_chains.pop(wid, None)
            else:
                logging.error("[WATCH Reply] 알 수 없는 manager-owned 취소 유형 | %s | %s", wid, action)
                self.send_telegram(guidance, owner)
                return
            for direction in ("LONG", "SHORT"):
                self._last_signatures.pop((wid, direction), None)

            active_children_to_cancel: list[str] = []
            for child_id, member in link["members"].items():
                shared = [other for other in self._watch_links_for_ids([child_id], owner)
                          if other is not link and other.get("status") == "active"]
                active = self._active_children.get(child_id)
                if active and wid in (active.get("source_spec_ids") or []):
                    active["source_spec_ids"] = [x for x in active["source_spec_ids"] if x != wid]
                    if active.get("source_spec_id") == wid:
                        active["source_spec_id"] = next(iter(active["source_spec_ids"]), None)
                if shared or (active and active.get("source_spec_ids")):
                    continue

                # KIM-owned active OZ children already have an established watch_id cancel API.
                # Use it instead of deleting _active_children directly.
                if member["action"] == "CANCEL_MANUAL" and child_id in self._active_children:
                    active_children_to_cancel.append(child_id)
                    continue

                # Generic/local-event/OZ-owned children are cancelled through their existing engine action.
                cancel_payload = {"action": member["action"], "watch_id": child_id}
                if member["action"] not in {"CANCEL_MANUAL", "CANCEL_GENERIC"}:
                    cancel_payload["watch_type"] = member.get("watch_type")
                self._push(cancel_payload)

            if active_children_to_cancel:
                self.special_api.cancel_oz_watches(active_children_to_cancel)
            link["status"] = "cancelled"
            self._save_private_state_locked()
            self._save_timed_chain_state_locked()
            self._save_fvg_created_watch_state_locked()
            self._save_active_children_state_locked()
            self._subscription_dirty = True
        self._send_watch_cancel_result(link, self._watch_cancel_text(link))

    @staticmethod
    def _watch_cancel_text(link: dict) -> str:
        text = str(link.get("confirmation_text") or link.get("original_command") or "감시").strip()
        if text.startswith("✅"):
            text = text[1:].strip()
        return f"🗑 {text} 취소"

    # -------------------------------
    # SPECIAL official strategy registry / loader
    # -------------------------------
    def register_special_watch_handler(self, watch_type: str, handler: object) -> None:
        """SPECIAL 전략의 Watch 판정기를 canonical condition type으로 등록합니다.

        handler는 필요에 따라 poll(targets), handle_event(event),
        engine_requirements(chain, trigger, evaluation_mode),
        needs_live_wonbi(chain, trigger, evaluation_mode)를 구현할 수 있습니다.
        """
        condition_type, _ = normalize_watch_contract(watch_type)
        if not condition_type or handler is None:
            raise ValueError("SPECIAL Watch handler 등록값이 비어 있습니다")
        previous = self._special_watch_handlers.get(condition_type)
        if previous is not None and previous in self._special_watch_event_handlers:
            self._special_watch_event_handlers.remove(previous)
        self._special_watch_handlers[condition_type] = handler
        event_handler = getattr(handler, "handle_event", None)
        if callable(event_handler) and handler not in self._special_watch_event_handlers:
            self._special_watch_event_handlers.append(handler)
        self._subscription_dirty = True

    @staticmethod
    def _special_strategy_from_definition(definition: StrategySpec | dict) -> StrategySpec:
        if isinstance(definition, StrategySpec):
            spec = definition
        elif isinstance(definition, dict):
            item = dict(definition)
            raw_conditions = item.get("conditions") or ()
            conditions: list[ConditionSpec] = []
            default_tf = normalize_tf(item.pop("default_tf", ""))
            for raw in raw_conditions:
                if isinstance(raw, ConditionSpec):
                    conditions.append(raw)
                elif isinstance(raw, dict):
                    conditions.append(ConditionSpec(**raw))
                else:
                    conditions.append(parse_condition_token(str(raw), default_tf))
            item["conditions"] = tuple(conditions)
            item["oz_tfs"] = tuple(item.get("oz_tfs") or ())
            item["time_filters"] = tuple(item.get("time_filters") or ())
            item["sweep_levels"] = tuple(item.get("sweep_levels") or DEFAULT_SWEEP_LEVELS)
            item["destination"] = "OFFICIAL"
            item["owner_chat_id"] = None
            item["persistent"] = True
            item["source"] = "SPECIAL"
            spec = StrategySpec(**item)
        else:
            raise TypeError("SPECIAL 전략 정의는 StrategySpec 또는 dict여야 합니다")
        spec.destination = "OFFICIAL"
        spec.owner_chat_id = None
        spec.persistent = True
        spec.source = "SPECIAL"
        spec.validate()
        return spec

    @staticmethod
    def _special_timed_chain_from_definition(
        definition: ConfigTimedChainSpec | dict,
    ) -> ConfigTimedChainSpec:
        if isinstance(definition, ConfigTimedChainSpec):
            spec = definition
        elif isinstance(definition, dict):
            item = dict(definition)
            for key in (
                "fvg_tfs", "oz_tfs", "final_fvg_touch_tfs", "time_filters",
                "final_time_filters", "cancel_on_opposite_fvg_tfs",
            ):
                if key in item:
                    item[key] = tuple(item.get(key) or ())
            spec = ConfigTimedChainSpec(**item)
        else:
            raise TypeError("SPECIAL 시간연쇄 정의는 ConfigTimedChainSpec 또는 dict여야 합니다")
        spec.validate()
        return spec

    def register_special_bundle(
        self,
        *,
        strategies: Iterable[StrategySpec | dict] = (),
        timed_chains: Iterable[ConfigTimedChainSpec | dict] = (),
    ) -> None:
        """SPECIAL 모듈의 공식 전략 정의를 검증 후 한 번에 등록합니다."""
        new_specs = [self._special_strategy_from_definition(x) for x in strategies]
        new_chains = [self._special_timed_chain_from_definition(x) for x in timed_chains]

        new_ids = [x.spec_id for x in (*new_specs, *new_chains)]
        if len(new_ids) != len(set(new_ids)):
            raise ValueError("SPECIAL bundle 안에 중복 spec_id가 있습니다")

        arm_payloads: list[dict] = []
        with self._lock:
            existing = set(self.official_specs) | set(self.official_chain_specs)
            duplicated = [x for x in new_ids if x in existing]
            if duplicated:
                raise ValueError(f"SPECIAL spec_id 중복: {','.join(duplicated)}")

            for spec in new_specs:
                self.official_specs[spec.spec_id] = spec
            for spec in new_chains:
                self.official_chain_specs[spec.spec_id] = spec

            if new_chains:
                restored = self._load_config_chain_state_for_specs_locked(
                    {x.spec_id: x for x in new_chains}
                )
                self._config_chain_state.update(restored)
                self._save_config_chain_state_locked()
                for spec in new_chains:
                    arm_payloads.extend(self._config_chain_trigger_payloads_locked(spec))

            self._subscription_dirty = True

        for payload in arm_payloads:
            self._push(payload)

        if new_specs or new_chains:
            logging.info(
                "🟢 [SPECIAL] 공식 전략 등록 | StrategySpec=%d TIMED_CHAIN=%d | %s",
                len(new_specs), len(new_chains), ", ".join(new_ids),
            )

    def register_special_strategy(self, definition: StrategySpec | dict) -> None:
        self.register_special_bundle(strategies=(definition,))

    def register_special_timed_chain(self, definition: ConfigTimedChainSpec | dict) -> None:
        self.register_special_bundle(timed_chains=(definition,))

    def _dispatch_special_watch_event(self, event: dict) -> Optional[dict]:
        for handler in tuple(self._special_watch_event_handlers):
            callback = getattr(handler, "handle_event", None)
            if not callable(callback):
                continue
            result = callback(event)
            if result is not None:
                return result
        return None

    def register_special_oz_event_handler(self, spec_id: str, handler: object) -> None:
        """OZ FINAL_ALERT를 특정 SPECIAL spec_id로 안전하게 라우팅합니다.

        handler는 ``handle_oz_event(event) -> Optional[dict]`` 를 구현합니다.
        None은 기존 김매니저 core로 계속 진행, dict는 해당 이벤트의 최종 ACK입니다.
        """
        key = str(spec_id or "").strip()
        if not key or handler is None:
            raise ValueError("SPECIAL OZ event handler 등록값이 비어 있습니다")
        with self._lock:
            self._special_oz_event_handlers[key] = handler

    def _special_oz_source_ids(self, event: dict) -> list[str]:
        candidates: list[str] = []
        raw_ids = event.get("source_spec_ids")
        if isinstance(raw_ids, (list, tuple, set)):
            candidates.extend(str(x).strip() for x in raw_ids if str(x).strip())
        single = str(event.get("source_spec_id") or "").strip()
        if single:
            candidates.append(single)

        watch_ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
        one = str(event.get("watch_id") or "").strip()
        if one and one not in watch_ids:
            watch_ids.append(one)

        with self._lock:
            for wid in watch_ids:
                payload = self._active_children.get(wid)
                if not isinstance(payload, dict):
                    continue
                child_ids = payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_single = str(payload.get("source_spec_id") or "").strip()
                if child_single:
                    candidates.append(child_single)
        return list(dict.fromkeys(candidates))

    def _dispatch_special_oz_event(self, event: dict) -> Optional[dict]:
        if str(event.get("kind") or "FINAL_ALERT").upper() != "FINAL_ALERT":
            return None
        source_ids = self._special_oz_source_ids(event)
        if not source_ids:
            return None

        with self._lock:
            handlers = [
                (spec_id, self._special_oz_event_handlers.get(spec_id))
                for spec_id in source_ids
                if self._special_oz_event_handlers.get(spec_id) is not None
            ]

        for spec_id, handler in handlers:
            callback = getattr(handler, "handle_oz_event", None)
            if not callable(callback):
                continue
            try:
                result = callback(dict(event))
            except Exception as exc:
                # spec_id로 격리된 FINAL_ALERT는 실패 폐쇄(fail-closed)합니다.
                # 다른 전략/일반 알림 경로에는 예외가 전파되지 않습니다.
                logging.exception("[SPECIAL OZ Hook] 처리 실패 | spec=%s", spec_id)
                return {
                    "ok": False, "delivered": False,
                    "error": f"special_oz_hook_failed:{spec_id}:{type(exc).__name__}:{exc}",
                }
            if result is not None:
                return result
        return None

    @staticmethod
    def _special_engine_requirement_payload(family: str, symbol: str, tf: str) -> Optional[tuple[str, str, dict]]:
        family = str(family or "").strip().upper()
        tf = normalize_tf(tf)
        symbol = str(symbol or "").strip()
        if not symbol or not tf:
            return None
        if family == "TREND":
            watch_id = stable_id("CMP:TREND", symbol, tf)
            return family, watch_id, {
                "action": "TREND_WATCH", "watch_id": watch_id,
                "symbol": symbol, "source_tf": tf,
            }
        if family == "FVG":
            watch_id = stable_id("CMP:FVG", symbol, tf)
            return family, watch_id, {
                "action": "FVG_WATCH", "watch_id": watch_id,
                "symbol": symbol, "source_tf": tf,
            }
        return None

    @staticmethod
    def _special_sort_key(path: Path) -> tuple[int, str]:
        match = SPECIAL_FILENAME_RE.fullmatch(path.name)
        return (int(match.group(1)) if match else sys.maxsize, path.name.lower())

    def _scan_special_strategies(self) -> None:
        """SPECIAL/SPECIAL<n>.py를 발견해 아직 로드하지 않은 공식전략/확장만 등록합니다."""
        initial_scan = not self._special_initial_scan_done
        loaded_numbers: list[str] = []
        try:
            candidates = sorted(
                (
                    path for path in self.special_dir.iterdir()
                    if path.is_file() and SPECIAL_FILENAME_RE.fullmatch(path.name)
                    and (
                        not self._special_selection_active
                        or path.stem.casefold() in self._enabled_special_names
                    )
                ),
                key=self._special_sort_key,
            )
        except (FileNotFoundError, NotADirectoryError):
            if initial_scan:
                logging.info("⚪ [SPECIAL] 스페셜 전략이 없습니다")
                self._special_initial_scan_done = True
            return
        except OSError as exc:
            logging.error("❌ [SPECIAL] 폴더 확인 실패 | %s", exc)
            if initial_scan:
                logging.info("⚪ [SPECIAL] 스페셜 전략이 없습니다")
                self._special_initial_scan_done = True
            return

        for path in candidates:
            key = path.name.lower()
            if key in self.special_modules:
                # 이미 성공적으로 로드한 SPECIAL은 실행 중 자동 교체하지 않습니다.
                continue

            try:
                stat = path.stat()
                signature = (int(stat.st_mtime_ns), int(stat.st_size))
            except OSError as exc:
                logging.error("❌ [SPECIAL] 전략 모듈 확인 실패 | %s | %s", path.name, exc)
                continue

            # 실패한 파일을 매 scan마다 반복 로깅하지 않습니다. 파일이 바뀌면 다시 시도합니다.
            if self._special_attempted_signatures.get(key) == signature:
                continue
            self._special_attempted_signatures[key] = signature

            module_name = f"_kim_special_{path.stem}_{signature[0]}_{signature[1]}"
            try:
                module_spec = importlib.util.spec_from_file_location(module_name, path)
                if module_spec is None or module_spec.loader is None:
                    raise ImportError("module spec 생성 실패")
                module = importlib.util.module_from_spec(module_spec)
                sys.modules[module_name] = module
                module_spec.loader.exec_module(module)

                register = getattr(module, "register", None)
                if not callable(register):
                    raise AttributeError("register(manager) 함수가 없습니다")
                register(self)
            except Exception:
                sys.modules.pop(module_name, None)
                logging.exception("❌ [SPECIAL] 전략 모듈 로드 실패 | %s", path.name)
                continue

            self.special_modules[key] = module
            match = SPECIAL_FILENAME_RE.fullmatch(path.name)
            if match:
                loaded_numbers.append(match.group(1))

        if loaded_numbers:
            logging.info("🟢 [SPECIAL] 로딩된 스페셜 전략 %s", ",".join(loaded_numbers))
        elif initial_scan and not self.special_modules:
            logging.info("⚪ [SPECIAL] 스페셜 전략이 없습니다")

        self._special_initial_scan_done = True

        # SPECIAL이 공식전략의 단일 소유자이므로 현재 registry에 없는 과거 공식 child는 정리합니다.
        with self._lock:
            cancel_payloads = self._prune_stale_official_children_locked()
        for payload in cancel_payloads:
            self._push(payload)

    def _scan_special_strategies_if_needed(self, now_mono: float) -> None:
        interval = float(self.system_config.get("SPECIAL_SCAN_SEC", DEFAULT_SPECIAL_SCAN_SEC))
        if now_mono - self._last_special_scan < max(0.5, interval):
            return
        self._last_special_scan = now_mono
        self._scan_special_strategies()

    @staticmethod
    def _decode_alert_template(template: str) -> str:
        # SPECIAL 템플릿에 escape 문자열이 들어온 경우 실제 개행/탭으로 정규화합니다.
        return str(template or "").replace("\\n", "\n").replace("\\t", "\t")

    def _official_spec_for_oz_event_locked(self, event: dict) -> Optional[StrategySpec]:
        candidates: list[str] = []

        raw_ids = event.get("source_spec_ids")
        if isinstance(raw_ids, (list, tuple, set)):
            candidates.extend(str(x).strip() for x in raw_ids if str(x).strip())
        source_spec_id = str(event.get("source_spec_id") or "").strip()
        if source_spec_id:
            candidates.append(source_spec_id)

        # 현재 프로세스에서 Composer가 arm한 child는 SPECIAL 공식 spec_id로 다시 연결합니다.
        for wid in (event.get("watch_ids") or []):
            payload = self._active_children.get(str(wid))
            if payload:
                child_ids = payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_id = str(payload.get("source_spec_id") or "").strip()
                if child_id:
                    candidates.append(child_id)

            chain_payload = self._config_chain_active.get(str(wid))
            if chain_payload:
                child_ids = chain_payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_id = str(chain_payload.get("source_spec_id") or "").strip()
                if child_id:
                    candidates.append(child_id)

        for spec_id in dict.fromkeys(candidates):
            spec = self.official_specs.get(spec_id)
            if spec is not None:
                return spec
            chain_spec = self.official_chain_specs.get(spec_id)
            if chain_spec is not None:
                return chain_spec
        return None

    def _render_official_oz_alert(self, spec: StrategySpec, event: dict, fallback: str) -> str:
        template = self._decode_alert_template(spec.alert_template)
        if not template:
            return fallback

        direction = str(event.get("direction") or "").upper()
        side_icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
        side_text = "매수" if direction == "LONG" else "매도" if direction == "SHORT" else direction
        tf = normalize_tf(event.get("source_tf") or event.get("tf")) or str(event.get("source_tf") or event.get("tf") or "")
        tf_label = OZ_TF_MAP.get(tf, tf)
        vm = str(event.get("validation_mode") or spec.validation_mode or "NORMAL").upper()
        tm = str(event.get("trigger_mode") or spec.trigger_mode or "OZ").upper()
        oz_label = _oz_profile_label(vm, tm)

        price = event.get("current_price")
        try:
            current_price = float(price)
        except (TypeError, ValueError):
            current_price = price

        values = {
            "side_icon": side_icon,
            "side_text": side_text,
            "direction": direction,
            "symbol": str(event.get("symbol") or spec.symbol),
            "sym": str(event.get("symbol") or spec.symbol),
            "tf": tf,
            "b_tf": tf,
            "tf_label": tf_label,
            "grade": str(event.get("grade") or ""),
            "indicators_text": str(event.get("indicators_text") or ""),
            "current_price": current_price,
            "trigger_name": str(event.get("trigger_name") or ""),
            "strategy_name": spec.name,
            "strategy_id": spec.spec_id,
            "validation_mode": vm,
            "trigger_mode": tm,
            "oz_label": oz_label,
        }
        try:
            return template.format_map(values)
        except Exception:
            logging.exception(
                "[Composer Strategy Alert] 템플릿 렌더링 실패 - 기존 알림 사용 | %s",
                spec.spec_id,
            )
            return fallback

    def _chain_watch_context(self, watch_id: object) -> Optional[dict]:
        """orchestrator의 LOCAL_EVENT payload가 최소형이어도 공용 Watch 계약으로 보강합니다."""
        wid = str(watch_id or "").strip()
        if not wid:
            return None
        with self._lock:
            for chain in getattr(self, "timed_chains", {}).values():
                for raw_index, current_id in chain.current_watch_ids.items():
                    if str(current_id or "") != wid:
                        continue
                    try:
                        index = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    if index < 0 or index >= len(chain.triggers):
                        continue
                    trig = chain.triggers[index]
                    condition_type, evaluation_mode = trigger_watch_contract(trig)
                    return {
                        "watch_id": wid,
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_type": condition_type,
                        "evaluation_mode": evaluation_mode,
                        "timeframes": [trig.tf],
                        "symbol": chain.symbol,
                        "direction": trig.direction,
                        "level_side": trig.level_side,
                        "ma_family": trig.ma_family,
                        "fast_period": trig.fast_period,
                        "slow_period": trig.slow_period,
                        "condition_name": getattr(trig, "condition_name", None),
                        "condition_combination": getattr(trig, "condition_combination", "ALL"),
                        "condition_specs": [dict(x) for x in (getattr(trig, "condition_specs", ()) or ())],
                        "request_chat_id": chain.owner_chat_id,
                        "silent": True,
                    }
        return None

    def _push(self, payload: dict) -> None:
        # 모든 Generic Watch 경계에서 condition type + evaluation_mode 계약으로 정규화합니다.
        item = canonical_watch_payload(payload)
        action = str(item.get("action") or "").upper()
        if not self._track_watch_payload(item):
            return
        if action in {"MANUAL_WATCH", "GENERIC_WATCH", "LOCAL_EVENT_WATCH"}:
            wid = str(item.get("watch_id") or "")
            managed = bool(item.get("chain_id") or item.get("source_spec_id")
                           or wid in getattr(self, "_active_children", {})
                           or wid in getattr(self, "_special_watch_handlers", {})
                           or self._chain_watch_context(wid))
            item.setdefault("watch_owner", "KIM" if managed else "OZ")

        # FVG_NEW는 monitor_OZ로 보내지 않습니다. strategy_FVG의 FVG_CREATED를
        # Composer가 직접 소비하므로 이 payload는 시간연쇄 상태/구독 갱신용 marker입니다.
        if action in {"FVG_EVENT_WATCH", "CANCEL_FVG_EVENT"}:
            with self._lock:
                self._subscription_dirty = True
            return

        # LOCAL_EVENT는 김매니저 내부 복합조건/SPECIAL handler와
        # monitor_OZ의 BAR/WONBI 공용 Generic Watch를 같은 lifecycle로 관리합니다.
        if action in {"LOCAL_EVENT_WATCH", "CANCEL_LOCAL_EVENT"}:
            context = self._chain_watch_context(item.get("watch_id"))
            if context:
                merged = dict(context)
                merged.update({k: v for k, v in item.items() if v is not None})
                item = canonical_watch_payload(merged)
            condition_type = str(item.get("watch_type") or "").upper()
            if action == "CANCEL_LOCAL_EVENT":
                self._compound_watch_state.pop(str(item.get("watch_id") or ""), None)
                if condition_type in {"BAR", "WONBI_TOUCH"}:
                    item["action"] = "CANCEL_GENERIC"
                    self.oz_queue.push(item)
                return
            if condition_type in {"BAR", "WONBI_TOUCH"}:
                item["action"] = "GENERIC_WATCH"
                self.oz_queue.push(item)
            return

        self.oz_queue.push(item)

    # -------------------------------
    # persistence
    # -------------------------------
    def _load_private_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            restored = {}
            for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                try:
                    spec = StrategySpec.from_json(item)
                except ValueError as exc:
                    logging.error("[Composer] saved strategy rejected | %s", exc)
                    continue
                if spec.source == "PRIVATE" and spec.owner_chat_id:
                    restored[spec.spec_id] = spec
            with self._lock:
                self.manual_specs = restored
                # Older files have no Telegram metadata and are valid as-is.
                links = raw.get("watch_message_links", []) if isinstance(raw, dict) else []
                self._watch_message_links = {
                    str(x["watch_id"]): x for x in links
                    if isinstance(x, dict) and x.get("watch_id") and x.get("owner_chat_id")
                    and self._telegram_message_id(x.get("command_message_id")) is not None
                    and isinstance(x.get("members"), dict)
                    and x.get("cancel_action") in {
                        "CANCEL_MANUAL", "CANCEL_GENERIC", "PRIVATE", "FVG_NEW", "TIMED_CHAIN"}
                }
            if restored:
                logging.info("♻️ [Composer] 개인전략 복원 %d건", len(restored))
        except Exception:
            logging.exception("[Composer] 개인전략 상태 복원 실패")

    def _save_private_state_locked(self) -> None:
        payload = {
            "version": 3,
            "watches": [x.to_json() for x in self.manual_specs.values()],
            "watch_message_links": list(self._watch_message_links.values()),
        }
        _atomic_write_json(self._state_path, payload)

    def _load_timed_chain_state(self) -> None:
        self.watch_orchestrator.load_state()
        if self.timed_chains:
            logging.info("♻️ [Composer 시간연쇄] %d건 복원", len(self.timed_chains))

    def _save_timed_chain_state_locked(self) -> None:
        self.watch_orchestrator.save_state_locked()

    @staticmethod
    def _default_config_chain_state() -> dict:
        return {
            "stage": 0,
            "latest_cross": None,
            "latest_fvg": None,
            "stage_deadline": None,
            "unordered_latch": UnorderedConditionLatch.new_state(),
            "post_touch_armed": None,
            "last_pair_signature": None,
            "spec_signature": None,
        }

    @staticmethod
    def _config_chain_spec_signature(spec: ConfigTimedChainSpec) -> str:
        parts: list[object] = [
            "CFGCHAINSTATE", spec.spec_id, spec.symbol, spec.cross_tf, spec.ma_family,
            spec.fast_period, spec.slow_period, ",".join(spec.fvg_tfs), spec.max_gap_sec,
        ]
        if spec.order_mode != "SEQUENTIAL":
            parts.extend(("ORDER_MODE", spec.order_mode))
        # MAX_GAP_BARS=0(기존 동작)에서는 이전 순서형 기준본과 같은 signature를 유지합니다.
        if spec.max_gap_bars > 0:
            parts.extend(("MAX_GAP_BARS", spec.max_gap_bars))
        if spec.final_fvg_touch_tfs:
            parts.extend(("FINAL_FVG_TOUCH_TFS", ",".join(spec.final_fvg_touch_tfs)))
        if spec.final_time_filters:
            parts.extend(("FINAL_TIME_FILTER", ",".join(spec.final_time_filters)))
        if spec.cancel_on_opposite_cross:
            parts.extend(("CANCEL_ON_OPPOSITE_CROSS", True))
        if spec.cancel_on_opposite_fvg_tfs:
            parts.extend(("CANCEL_ON_OPPOSITE_FVG_TFS", ",".join(spec.cancel_on_opposite_fvg_tfs)))
        return stable_id(*parts, length=24)

    @staticmethod
    def _config_chain_value_epoch(value) -> Optional[float]:
        if value in {None, ""}:
            return None
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                ts = float(value)
            else:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp):
                    return None
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                else:
                    stamp = stamp.tz_convert("UTC")
                ts = float(stamp.timestamp())
            return ts if math.isfinite(ts) else None
        except Exception:
            return None

    def _config_chain_closed_bar_times(
        self, spec: ConfigTimedChainSpec, client: StaffClientV2
    ) -> tuple[float, ...]:
        """CROSS_TF의 실제 확정봉 시각을 오름차순 epoch로 반환합니다."""
        try:
            data = client.request(spec.symbol, [spec.cross_tf], [])
            df = data.get(spec.cross_tf) if isinstance(data, dict) else None
            if df is None or len(df) < 2 or "time" not in df.columns:
                return ()
            # STAFF 계약상 마지막 행은 live bar이고 -2가 최신 확정봉입니다.
            values = df.iloc[:-1]["time"].tolist()
            epochs = [self._config_chain_value_epoch(x) for x in values]
            return tuple(sorted({float(x) for x in epochs if x is not None}))
        except Exception:
            logging.exception(
                "[Composer SPECIAL TIMED_CHAIN] 봉 수 expiry용 STAFF 조회 실패 | %s %s",
                spec.symbol, spec.cross_tf,
            )
            return ()

    @staticmethod
    def _config_chain_bar_age_from_times(anchor: Optional[float], bar_times: tuple[float, ...]) -> Optional[int]:
        if anchor is None or not bar_times:
            return None
        try:
            anchor_f = float(anchor)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(anchor_f):
            return None
        return sum(1 for ts in bar_times if float(ts) > anchor_f + 1e-6)

    @staticmethod
    def _config_chain_expiry_status(
        spec: ConfigTimedChainSpec, state: dict, now: float, bar_age: Optional[int]
    ) -> tuple[bool, Optional[str]]:
        deadline = state.get("stage_deadline")
        if spec.max_gap_sec > 0:
            try:
                if deadline is None or not math.isfinite(float(deadline)) or float(now) > float(deadline):
                    return True, "time"
            except (TypeError, ValueError):
                return True, "time"
        if spec.max_gap_bars > 0 and bar_age is not None and int(bar_age) > int(spec.max_gap_bars):
            # MAX_GAP_BARS=N은 A 다음 N개 확정봉까지 B를 허용합니다. N+1번째 확정봉부터 만료입니다.
            return True, "bars"
        return False, None

    def _load_config_chain_state_for_specs_locked(
        self, specs: dict[str, ConfigTimedChainSpec]
    ) -> dict[str, dict]:
        """공식 TIMED_CHAIN의 A→B 진행상태를 현재 config에 맞춰 복원합니다."""
        # SPECIAL modules register separately. Keep the unread startup records
        # until their owner registers, even if an earlier module saves first.
        if not hasattr(self, '_config_chain_restore_records'):
            self._config_chain_restore_records = {'states': {}, 'active': {}}
        if not getattr(self, '_config_chain_restore_loaded', False) and domain_memory.exists(self._config_chain_state_path):
            try:
                raw = read_json(self._config_chain_state_path)
                if isinstance(raw, dict) and isinstance(raw.get("states"), dict):
                    self._config_chain_restore_records = {
                        'states': raw['states'], 'active': raw.get('active', {})}
            except Exception:
                logging.exception(
                    "[Composer SPECIAL TIMED_CHAIN] 상태 복원 실패 | %s",
                    self._config_chain_state_path,
                )

        self._config_chain_restore_loaded = True
        raw_states = self._config_chain_restore_records['states']
        restored: dict[str, dict] = {}
        for spec_id, spec in specs.items():
            state = self._default_config_chain_state()
            expected_signature = self._config_chain_spec_signature(spec)
            state["spec_signature"] = expected_signature
            item = raw_states.get(spec_id)
            if isinstance(item, dict) and item.get("spec_signature") == expected_signature:
                state["last_pair_signature"] = item.get("last_pair_signature")
                post_touch = item.get("post_touch_armed") if isinstance(item.get("post_touch_armed"), dict) else None
                if post_touch is not None and spec.final_fvg_touch_tfs:
                    try:
                        expires_at = float(post_touch.get("expires_at"))
                        direction = str(post_touch.get("direction") or "").upper()
                        if math.isfinite(expires_at) and direction in {"LONG", "SHORT"}:
                            state["post_touch_armed"] = dict(post_touch)
                    except (TypeError, ValueError):
                        pass
                if spec.order_mode == "UNORDERED":
                    state["unordered_latch"] = UnorderedConditionLatch.normalize_state(
                        item.get("unordered_latch")
                    )
                    restored[spec_id] = state
                    continue
                try:
                    stage = int(item.get("stage", 0))
                except (TypeError, ValueError):
                    stage = 0
                cross = item.get("latest_cross") if isinstance(item.get("latest_cross"), dict) else None
                deadline = item.get("stage_deadline")
                try:
                    deadline = float(deadline) if deadline is not None else None
                except (TypeError, ValueError):
                    deadline = None

                # 유한한 deadline을 보존하여 지연 도착한 완료 시각을 판정합니다.
                # 봉 수 제한은 기존 실제 확정봉 기준으로 callback에서 재검사합니다.
                time_alive = spec.max_gap_sec <= 0 or (deadline is not None and math.isfinite(deadline))
                if stage == 1 and cross is not None and time_alive:
                    try:
                        cross_ts = float(cross.get("ts"))
                        bar_anchor = cross.get("bar_anchor_ts")
                        if spec.max_gap_bars > 0:
                            bar_anchor = float(bar_anchor)
                        if math.isfinite(cross_ts) and (
                            spec.max_gap_bars <= 0 or math.isfinite(float(bar_anchor))
                        ):
                            state["stage"] = 1
                            state["latest_cross"] = dict(cross)
                            state["stage_deadline"] = deadline
                    except (TypeError, ValueError):
                        pass
            restored[spec_id] = state

        raw_active = self._config_chain_restore_records['active']
        for wid, item in list(raw_active.items()):
            spec = specs.get(item.get('spec_id')) if isinstance(item, dict) else None
            if spec is None:
                continue
            if (item.get('spec_signature') == self._config_chain_spec_signature(spec)
                    and self._config_chain_value_epoch(item.get('expires_at')) is not None):
                self._config_chain_active[wid] = dict(item)
            raw_active.pop(wid, None)
        for spec_id in specs:
            raw_states.pop(spec_id, None)
        waiting_b = sum(1 for x in restored.values() if int(x.get("stage", 0)) == 1)
        if waiting_b:
            logging.info("♻️ [Composer SPECIAL TIMED_CHAIN] B 대기 상태 %d건 복원", waiting_b)
        return restored

    def _save_config_chain_state_locked(self) -> None:
        unread = getattr(self, '_config_chain_restore_records', {})
        payload = {
            "version": 4,
            "states": dict(unread.get('states', {}), **self._config_chain_state),
            "active": dict(unread.get('active', {}), **{
                wid: dict(item, spec_signature=self._config_chain_spec_signature(
                    self.official_chain_specs[item['spec_id']]))
                for wid, item in self._config_chain_active.items()
                if item.get('spec_id') in self.official_chain_specs}),
        }
        try:
            _atomic_write_json(self._config_chain_state_path, payload)
        except Exception:
            logging.exception(
                "[Composer SPECIAL TIMED_CHAIN] 상태 저장 실패 | %s",
                self._config_chain_state_path,
            )

    def _load_fvg_created_watch_state(self) -> None:
        restored: dict[str, FVGCreatedWatchSpec] = {}
        if domain_memory.exists(self._fvg_created_watch_state_path):
            try:
                raw = read_json(self._fvg_created_watch_state_path)
                for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                    spec = FVGCreatedWatchSpec.from_json(item)
                    restored[spec.watch_id] = spec
            except Exception:
                logging.exception("[Composer/FVG 생성 Watch] 상태 복원 실패 | %s", self._fvg_created_watch_state_path)

        # 2.0 전환 시 monitor_OZ에 저장되어 있던 단독 FVG_NEW Watch만 Composer로 1회 이관합니다.
        # 시간연쇄 FVG_NEW는 composer_timed_chains/SPECIAL가 원본이므로 중복 이관하지 않습니다.
        migrated = 0
        legacy_path = LOG_DIR / "oz_generic_watch_state.json"
        if domain_memory.exists(legacy_path):
            try:
                raw = read_json(legacy_path)
                for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                    if str(item.get("watch_type") or "").upper() != "FVG_NEW":
                        continue
                    if str(item.get("chain_id") or "").strip():
                        continue
                    try:
                        spec = FVGCreatedWatchSpec(
                            watch_id=item.get("watch_id"), symbol=item.get("symbol"),
                            timeframes=tuple(item.get("timeframes", [])), direction=item.get("direction"),
                            persistent=bool(item.get("persistent", False)),
                            request_chat_id=item.get("request_chat_id"), silent=bool(item.get("silent", False)),
                        )
                        spec.validate()
                    except Exception:
                        continue
                    if spec.watch_id not in restored:
                        restored[spec.watch_id] = spec
                        migrated += 1
            except Exception:
                logging.exception("[Composer/FVG 생성 Watch] monitor_OZ legacy 상태 이관 실패 | %s", legacy_path)

        self.fvg_created_watches = restored
        if restored:
            logging.info("♻️ [Composer/FVG 생성 Watch] %d건 복원%s", len(restored), f" · legacy {migrated}건 이관" if migrated else "")
        if migrated:
            with self._lock:
                self._save_fvg_created_watch_state_locked()

    def _save_fvg_created_watch_state_locked(self) -> None:
        payload = {
            "version": 1,
            "watches": [w.to_json() for w in self.fvg_created_watches.values()],
        }
        try:
            _atomic_write_json(self._fvg_created_watch_state_path, payload)
        except Exception:
            logging.exception("[Composer/FVG 생성 Watch] 상태 저장 실패 | %s", self._fvg_created_watch_state_path)

    def _add_fvg_created_watch(self, payload: dict) -> None:
        watch = FVGCreatedWatchSpec(
            watch_id=payload.get("watch_id"), symbol=payload.get("symbol"),
            timeframes=tuple(payload.get("timeframes", [])), direction=payload.get("direction"),
            persistent=bool(payload.get("persistent", False)),
            request_chat_id=payload.get("request_chat_id"), silent=bool(payload.get("silent", False)),
            final_action=payload.get("final_action", "NOTIFY"),
            oz_tfs=tuple(payload.get("oz_tfs", [])), oz_direction=payload.get("oz_direction"),
            validation_mode=payload.get("validation_mode", "NORMAL"),
            trigger_mode=payload.get("trigger_mode", "OZ"),
        )
        watch.validate()
        with self._lock:
            if watch.watch_id in self.fvg_created_watches:
                return
            self._remember_watch_command(watch.watch_id, watch.request_chat_id, "FVG_NEW", "FVG_NEW")
            self.fvg_created_watches[watch.watch_id] = watch
            self._save_fvg_created_watch_state_locked()
            self._subscription_dirty = True
        if not watch.silent:
            mode = " 지속" if watch.persistent else ""
            self.send_telegram(f"✅ {watch.symbol} · {watch.label()}{mode} 감시", watch.request_chat_id,
                               watch_id=watch.watch_id, watch_registration=True)
        logging.info("🟦 [Composer/FVG 생성 Watch] 시작 | %s | %s | persistent=%s", watch.watch_id, watch.label(), watch.persistent)

    def _load_active_children_state(self) -> None:
        if not domain_memory.exists(self._active_children_state_path):
            with self._lock:
                self._save_active_children_state_locked()
            return
        try:
            raw = read_json(self._active_children_state_path)
            restored: dict[str, dict] = {}
            for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                action = str(item.get("action") or "").strip().upper()
                if not watch_id.startswith("OZARM:") or action != "MANUAL_WATCH":
                    continue
                payload = dict(item)
                payload["watch_id"] = watch_id
                payload["action"] = "MANUAL_WATCH"
                restored[watch_id] = payload
            with self._lock:
                self._active_children = restored
            if restored:
                logging.info("♻️ [Composer→OZ] 재시작 감시 소유권 복원 %d건", len(restored))
        except Exception:
            logging.exception("[Composer→OZ] 현재 감시 상태 복원 실패")

    def _save_active_children_state_locked(self) -> None:
        payload = {
            "version": 1,
            "watches": [dict(x) for x in self._active_children.values()],
        }
        _atomic_write_json(self._active_children_state_path, payload)

    @staticmethod
    def _persisted_watch_ids(path: Path, key: str, mapping: bool) -> set[str]:
        """저장된 감시 번호만 읽습니다. 읽기 실패 시 빈 집합으로 처리합니다."""
        if not domain_memory.exists(path):
            return set()
        try:
            raw = read_json(path)
            items = raw.get(key, {} if mapping else []) if isinstance(raw, dict) else ({} if mapping else [])
            if mapping:
                if not isinstance(items, dict):
                    return set()
                return {str(x).strip() for x in items if str(x).strip()}
            if not isinstance(items, list):
                return set()
            return {
                str(item.get("watch_id") or "").strip()
                for item in items
                if isinstance(item, dict) and str(item.get("watch_id") or "").strip()
            }
        except Exception:
            logging.exception("[Composer 시작 동기화] 상태 파일 읽기 실패 | %s", path)
            return set()

    def _official_child_matches_spec_locked(self, payload: dict, spec: StrategySpec) -> bool:
        if str(payload.get("symbol") or "") != spec.symbol:
            return False
        child_tfs = tuple(normalize_tf(x) for x in (payload.get("timeframes") or ()) if normalize_tf(x))
        if child_tfs != tuple(spec.oz_tfs):
            return False
        if str(payload.get("validation_mode") or "NORMAL").upper() != spec.validation_mode:
            return False
        if _canonical_trigger_mode(payload.get("trigger_mode")) != spec.trigger_mode:
            return False

        external_watch_id = str(payload.get("external_watch_id") or "").strip()
        current_sweep_ids = {
            self._sweep_subscription_payload(spec, cond)["watch_id"]
            for cond in spec.conditions
            if cond.kind == "SWEEP"
        }
        if external_watch_id:
            return external_watch_id in current_sweep_ids
        return not current_sweep_ids

    def _prune_stale_official_children_locked(self) -> list[dict]:
        """현재 SPECIAL 공식 전략과 맞지 않는 OZ 감시만 KIM 상태에서 제거합니다."""
        cancel_payloads: list[dict] = []
        changed = False
        for child_id, payload in list(self._active_children.items()):
            # 개인 감시는 해당 사용자의 저장 상태가 기준이므로 여기서 제거하지 않습니다.
            if str(payload.get("request_chat_id") or "").strip():
                continue

            source_ids = []
            raw_ids = payload.get("source_spec_ids")
            if isinstance(raw_ids, (list, tuple, set)):
                source_ids.extend(str(x).strip() for x in raw_ids if str(x).strip())
            source_id = str(payload.get("source_spec_id") or "").strip()
            if source_id:
                source_ids.append(source_id)

            live_ids = []
            for spec_id in dict.fromkeys(source_ids):
                spec = self.official_specs.get(spec_id)
                if spec is not None and spec.enabled and self._official_child_matches_spec_locked(payload, spec):
                    live_ids.append(spec_id)

            if live_ids:
                normalized = list(dict.fromkeys(live_ids))
                if payload.get("source_spec_ids") != normalized:
                    payload["source_spec_ids"] = normalized
                    changed = True
                primary = normalized[0]
                if str(payload.get("source_spec_id") or "") != primary:
                    payload["source_spec_id"] = primary
                    changed = True
                spec = self.official_specs.get(primary)
                if spec is not None and str(payload.get("source_name") or "") != spec.name:
                    payload["source_name"] = spec.name
                    changed = True
                continue

            self._active_children.pop(child_id, None)
            cancel_payloads.append({
                "action": "CANCEL_MANUAL",
                "watch_id": child_id,
                "request_chat_id": None,
                "validation_mode": payload.get("validation_mode"),
                "trigger_mode": payload.get("trigger_mode"),
            })
            changed = True

        if changed:
            self._save_active_children_state_locked()
        return cancel_payloads

    # -------------------------------
    # config pipelines / hot reload
    # -------------------------------
    def _config_chain_trigger_payloads_locked(self, spec: ConfigTimedChainSpec) -> list[dict]:
        chain_id = f"CFGCHAIN:{spec.spec_id}"
        payloads = [{
            "action": "GENERIC_WATCH",
            "watch_id": stable_id("CFGCHAINTRG", spec.spec_id, "CROSS", length=20),
            "watch_type": f"{spec.ma_family}_CROSS",
            "timeframes": [spec.cross_tf],
            "symbol": spec.symbol,
            "direction": None,
            "ma_family": spec.ma_family,
            "fast_period": spec.fast_period,
            "slow_period": spec.slow_period,
            "persistent": True,
            "request_chat_id": None,
            "chain_id": chain_id,
            "chain_stage": 0,
            "silent": True,
            "source_spec_id": spec.spec_id,
        }]
        for idx, tf in enumerate(spec.fvg_tfs, start=1):
            payloads.append({
                "action": "FVG_EVENT_WATCH",
                "watch_id": stable_id("CFGCHAINTRG", spec.spec_id, "FVG", tf, length=20),
                "watch_type": "FVG_NEW",
                "timeframes": [tf],
                "symbol": spec.symbol,
                "direction": None,
                "persistent": True,
                "request_chat_id": None,
                "chain_id": chain_id,
                "chain_stage": idx,
                "silent": True,
                "source_spec_id": spec.spec_id,
            })
        return payloads

    # -------------------------------
    # engine subscriptions
    # -------------------------------
    def _all_specs_locked(self) -> tuple[StrategySpec, ...]:
        return tuple(x for x in (*self.official_specs.values(), *self.manual_specs.values()) if x.enabled)

    def _compound_conditions_for_trigger(self, trig: ChainTriggerSpec) -> tuple[ConditionSpec, ...]:
        condition_type, _evaluation_mode = trigger_watch_contract(trig)
        if condition_type != "COMPOUND_CONDITION":
            return ()
        out: list[ConditionSpec] = []
        for raw in getattr(trig, "condition_specs", ()) or ():
            try:
                cond = self._condition_from_descriptor(dict(raw), trig.tf)
            except Exception:
                logging.exception("[Composer 복합조건] primitive 조건 변환 실패 | %s", raw)
                continue
            if cond not in out:
                out.append(cond)
        return tuple(out)

    def _compound_spec_for_trigger(self, chain: TimedChainSpec, trig: ChainTriggerSpec) -> Optional[StrategySpec]:
        conditions = self._compound_conditions_for_trigger(trig)
        if not conditions:
            return None
        return StrategySpec(
            spec_id=f"COMPOUND:{chain.chain_id}:{getattr(trig, 'condition_name', None) or 'condition'}:{trig.tf}",
            name=str(getattr(trig, "condition_name", None) or "복합조건"),
            symbol=chain.symbol,
            conditions=conditions,
            oz_tfs=("1m",),
            final_action="NOTIFY",
            combination=str(getattr(trig, "condition_combination", "ALL") or "ALL").upper(),
            destination="PRIVATE",
            owner_chat_id=chain.owner_chat_id,
            persistent=True,
            enabled=True,
            source="PRIVATE",
        )

    def _sweep_subscription_payload(self, spec: StrategySpec, cond: ConditionSpec) -> dict:
        london = str(self.config.get("LONDON") or self.config.get("MAIN_LONDON") or "").strip()
        newyork = str(self.config.get("NEWYORK") or self.config.get("MAIN_NEWYORK") or "").strip()
        levels = (cond.side,) if cond.side else spec.sweep_levels
        watch_id = stable_id(
            "CMP:SWEEP", spec.symbol, cond.tf, ",".join(levels),
            spec.sweep_atr_period, spec.sweep_atr_mult, london, newyork,
        )
        return {
            "action": "SWEEP_WATCH",
            "watch_id": watch_id,
            "symbol": spec.symbol,
            "source_tf": cond.tf,
            "levels": list(levels),
            "atr_period": spec.sweep_atr_period,
            "atr_mult": spec.sweep_atr_mult,
            "session_london": london,
            "session_newyork": newyork,
        }

    def _desired_subscriptions_locked(self) -> dict[str, dict[str, dict]]:
        desired: dict[str, dict[str, dict]] = {"TREND": {}, "FVG": {}, "SWEEP": {}}

        def add_trend(symbol: str, tf: str, requested_fields=()) -> None:
            symbol = str(symbol or "").strip()
            tf = normalize_tf(tf)
            if not symbol or not tf:
                return
            wid = stable_id("CMP:TREND", symbol, tf)
            payload = desired["TREND"].setdefault(wid, {
                "action": "TREND_WATCH", "watch_id": wid,
                "symbol": symbol, "source_tf": tf,
            })
            fields = {
                str(x or "").strip().lower()
                for x in requested_fields
                if str(x or "").strip().lower() in TREND_METRIC_FIELDS
            }
            if fields:
                merged = set(payload.get("requested_fields") or ()) | fields
                payload["requested_fields"] = sorted(merged)

        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "TREND":
                    add_trend(spec.symbol, cond.tf)
                elif cond.kind == "TREND_METRIC":
                    fields = [cond.metric]
                    if cond.metric_rhs:
                        fields.append(cond.metric_rhs)
                    add_trend(spec.symbol, cond.tf, fields)
                elif cond.kind == "FVG":
                    wid = stable_id("CMP:FVG", spec.symbol, cond.tf)
                    desired["FVG"][wid] = {
                        "action": "FVG_WATCH", "watch_id": wid,
                        "symbol": spec.symbol, "source_tf": cond.tf,
                    }
                elif cond.kind == "SWEEP":
                    payload = self._sweep_subscription_payload(spec, cond)
                    desired["SWEEP"][payload["watch_id"]] = payload

        # SPECIAL 전략은 필요한 사실 엔진 구독만 선언합니다. 김매니저는 전략명을 분기하지 않습니다.
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                condition_type, evaluation_mode = trigger_watch_contract(trig)
                if condition_type == "COMPOUND_CONDITION":
                    compound_spec = self._compound_spec_for_trigger(chain, trig)
                    if compound_spec is not None:
                        for cond in compound_spec.conditions:
                            if cond.kind == "TREND":
                                add_trend(compound_spec.symbol, cond.tf)
                            elif cond.kind == "TREND_METRIC":
                                fields = [cond.metric]
                                if cond.metric_rhs:
                                    fields.append(cond.metric_rhs)
                                add_trend(compound_spec.symbol, cond.tf, fields)
                            elif cond.kind == "FVG":
                                add_fvg_id = stable_id("CMP:FVG", compound_spec.symbol, cond.tf)
                                desired["FVG"][add_fvg_id] = {
                                    "action": "FVG_WATCH", "watch_id": add_fvg_id,
                                    "symbol": compound_spec.symbol, "source_tf": cond.tf,
                                }
                            elif cond.kind == "SWEEP":
                                payload = self._sweep_subscription_payload(compound_spec, cond)
                                desired["SWEEP"][payload["watch_id"]] = payload
                    continue
                handler = self._special_watch_handlers.get(condition_type)
                requirement_fn = getattr(handler, "engine_requirements", None) if handler is not None else None
                if not callable(requirement_fn):
                    continue
                try:
                    requirements = requirement_fn(chain, trig, evaluation_mode) or ()
                except Exception:
                    logging.exception("[SPECIAL Watch] engine requirement 오류 | %s", condition_type)
                    continue
                for requirement in requirements:
                    try:
                        family, symbol, tf = requirement
                    except (TypeError, ValueError):
                        continue
                    resolved = self._special_engine_requirement_payload(family, symbol, tf)
                    if resolved is None:
                        continue
                    family, wid, payload = resolved
                    if family == "TREND":
                        add_trend(symbol, tf, payload.get("requested_fields") or ())
                    elif family in desired:
                        desired[family][wid] = payload

        # 신규 FVG 생성 알림/시간연쇄 역시 strategy_FVG 한 곳만 구독합니다.
        def add_fvg(symbol: str, tf: str) -> None:
            tf = normalize_tf(tf)
            if not symbol or not tf:
                return
            wid = stable_id("CMP:FVG", symbol, tf)
            desired["FVG"][wid] = {
                "action": "FVG_WATCH", "watch_id": wid,
                "symbol": symbol, "source_tf": tf,
            }

        for watch in self.fvg_created_watches.values():
            for tf in watch.timeframes:
                add_fvg(watch.symbol, tf)

        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            if not chain.active_child_id or chain.order_mode == "FILTER":
                if chain.order_mode in {"UNORDERED", "FILTER"}:
                    for trig in chain.triggers:
                        if trig.watch_type == "FVG_NEW":
                            add_fvg(chain.symbol, trig.tf)
                elif chain.stage < len(chain.triggers):
                    trig = chain.triggers[chain.stage]
                    if trig.watch_type == "FVG_NEW":
                        add_fvg(chain.symbol, trig.tf)
            # CANCEL_ON FVG는 후보가 생겨 실제 invalidation watch_id가 arm된 동안에만 구독합니다.
            for raw_idx in tuple(chain.invalidation_watch_ids):
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(chain.invalidation_triggers):
                    cancel_trig = chain.invalidation_triggers[idx]
                    if cancel_trig.watch_type == "FVG_NEW":
                        add_fvg(chain.symbol, cancel_trig.tf)

        for chain_spec in self.official_chain_specs.values():
            if not chain_spec.enabled:
                continue
            for tf in dict.fromkeys((*chain_spec.fvg_tfs, *chain_spec.final_fvg_touch_tfs)):
                add_fvg(chain_spec.symbol, tf)
        return desired

    def _sync_engine_subscriptions(self, force_refresh: bool = False) -> None:
        with self._lock:
            desired = self._desired_subscriptions_locked()
            current = {k: dict(v) for k, v in self._engine_subscriptions.items()}

        cancel_actions = {"TREND": "CANCEL_TREND", "FVG": "CANCEL_FVG", "SWEEP": "CANCEL_SWEEP"}
        for family in ("TREND", "FVG", "SWEEP"):
            removed = set(current[family]) - set(desired[family])
            for wid in sorted(removed):
                self._push({"action": cancel_actions[family], "watch_id": wid})
            for wid, payload in desired[family].items():
                if force_refresh or wid not in current[family] or current[family][wid] != payload:
                    self._push(payload)

        with self._lock:
            self._engine_subscriptions = desired
            self._subscription_dirty = False

    def _refresh_active_oz_children(self) -> None:
        with self._lock:
            payloads = [dict(x) for x in self._active_children.values()]
        for payload in payloads:
            self._push(payload)

    def _refresh_engine_family_after_ping(self, family: str) -> int:
        """전략 프로그램 시작 시 저장 상태와 현재 KIM 설정을 맞춘 뒤 현재 감시를 재전달합니다."""
        family = str(family or "").upper()
        if family not in {"TREND", "FVG", "SWEEP"}:
            return 0

        state_files = {
            "TREND": (LOG_DIR / "trend_watch_state.json", "CANCEL_TREND"),
            "FVG": (LOG_DIR / "fvg_watch_state.json", "CANCEL_FVG"),
            "SWEEP": (LOG_DIR / "sweep_watch_state.json", "CANCEL_SWEEP"),
        }
        with self._lock:
            desired_all = self._desired_subscriptions_locked()
            desired = {k: dict(v) for k, v in desired_all.get(family, {}).items()}

        state_path, cancel_action = state_files[family]
        persisted = self._persisted_watch_ids(state_path, "watches", mapping=True)
        stale = sorted(persisted - set(desired))
        for wid in stale:
            self._push({"action": cancel_action, "watch_id": wid})
        for payload in desired.values():
            self._push(dict(payload))

        with self._lock:
            self._engine_subscriptions[family] = desired

        logging.info(
            "♻️ [Composer 시작 동기화] %s | 옛 감시 %d건 정리 · 현재 감시 %d건 재전달",
            family, len(stale), len(desired),
        )
        return len(stale) + len(desired)

    def _refresh_oz_after_ping(self) -> int:
        """OZ 시작 시 옛 감시만 취소하고 현재 KIM이 소유한 감시는 그대로 복구합니다."""
        now = time.time()
        payloads: list[dict] = []
        with self._lock:
            # 현재 공식 설정과 맞지 않는 재시작 잔여 감시를 먼저 KIM 상태에서 제거합니다.
            stale_child_cancels = self._prune_stale_official_children_locked()

            # 일반 Composer 전략에서 만들어진 현재 OZ 감시
            payloads.extend(dict(x) for x in self._active_children.values())

            # 개인 시간연쇄의 현재 단계 또는 기간제 OZ 감시
            for chain in self.timed_chains.values():
                if not chain.enabled:
                    continue
                if chain.active_child_id:
                    payload = self._chain_final_payload_locked(chain)
                    if payload:
                        payloads.append(dict(payload))
                else:
                    payloads.extend(dict(x) for x in self._chain_trigger_payloads_locked(chain))
                payloads.extend(dict(x) for x in self.watch_orchestrator.invalidation_payloads_locked(chain))

            # 설정 파일의 상시 시간연쇄 조건 감시
            for spec in self.official_chain_specs.values():
                if spec.enabled:
                    payloads.extend(dict(x) for x in self._config_chain_trigger_payloads_locked(spec))

            # 설정 파일 시간연쇄에서 이미 성립해 기간제로 살아 있는 OZ 감시
            for item in self._config_chain_active.values():
                payloads.append({k: v for k, v in item.items()
                                 if k not in {"expires_at", "spec_id", "completion_deadline", "spec_signature"}})

            # 외부유동성 Gate의 현재 정답 목록도 KIM의 현재 전략 조건에서 계산합니다.
            desired_external = self._desired_subscriptions_locked().get("SWEEP", {})

        # 같은 감시가 여러 경로에서 잡혀도 한 번만 사용합니다.
        unique: dict[tuple[str, str], dict] = {}
        for payload in payloads:
            item = canonical_watch_payload(payload)
            action = str(item.get("action") or "").upper()
            if action == "LOCAL_EVENT_WATCH":
                context = self._chain_watch_context(item.get("watch_id"))
                if context:
                    merged = dict(context)
                    merged.update({k: v for k, v in item.items() if v is not None})
                    item = canonical_watch_payload(merged)
                if str(item.get("watch_type") or "").upper() in {"BAR", "WONBI_TOUCH"}:
                    item["action"] = "GENERIC_WATCH"
                    action = "GENERIC_WATCH"
            key = (action, str(item.get("watch_id") or ""))
            if key[1]:
                unique[key] = item

        desired_manual_ids = {wid for (action, wid) in unique if action == "MANUAL_WATCH"}
        desired_generic_ids = {wid for (action, wid) in unique if action == "GENERIC_WATCH"}
        desired_external_ids = set(desired_external)
        # Direct Telegram WATCH roots live in OZ's existing persisted state. Their
        # reply mapping keeps startup cleanup from treating them as orphaned KIM children.
        with self._lock:
            for link in self._watch_message_links.values():
                if link.get("cancel_action") == "CANCEL_MANUAL" and link.get("status") == "active":
                    desired_manual_ids.add(link["watch_id"])
                    desired_external_ids.add(link["watch_id"])

        persisted_manual = self._persisted_owned_oz_ids(LOG_DIR / "oz_manual_watch_state.json")
        persisted_generic = self._persisted_owned_oz_ids(LOG_DIR / "oz_generic_watch_state.json")
        persisted_external = self._persisted_watch_ids(LOG_DIR / "oz_external_liquidity_state.json", "specs", mapping=True)

        cancel_payloads: list[dict] = list(stale_child_cancels)
        cancel_payloads.extend(
            {"action": "CANCEL_MANUAL", "watch_id": wid}
            for wid in sorted(persisted_manual - desired_manual_ids)
        )
        cancel_payloads.extend(
            {"action": "CANCEL_GENERIC", "watch_id": wid}
            for wid in sorted(persisted_generic - desired_generic_ids)
        )
        cancel_payloads.extend(
            {"action": "CANCEL_SWEEP", "watch_id": wid}
            for wid in sorted(persisted_external - desired_external_ids)
        )

        # 옛것만 먼저 지운 뒤 현재 감시를 재전달합니다. 전체 초기화는 하지 않으므로
        # 살아 있는 외부유동성 터치/ATR 상태는 보존됩니다.
        seen_cancel: set[tuple[str, str]] = set()
        cancel_count = 0
        for payload in cancel_payloads:
            key = (str(payload.get("action") or ""), str(payload.get("watch_id") or ""))
            if not key[1] or key in seen_cancel:
                continue
            seen_cancel.add(key)
            self._push(payload)
            cancel_count += 1

        for payload in unique.values():
            self._push(payload)
        # SWEEP_WATCH는 전략 프로그램뿐 아니라 OZ의 외부유동성 Gate 등록에도 사용됩니다.
        for payload in desired_external.values():
            self._push(dict(payload))

        logging.info(
            "♻️ [Composer 시작 동기화] OZ | 옛 감시 %d건 정리 · 현재 감시 %d건 재전달 · 외부유동성 %d건 확인",
            cancel_count, len(unique), len(desired_external),
        )
        return cancel_count + len(unique) + len(desired_external)

    @staticmethod
    def _persisted_owned_oz_ids(path: Path) -> set[str]:
        """Cancel only KIM-owned watches; preserve unclassified legacy standalone watches."""
        try:
            raw = read_json(path)
            return {
                str(item["watch_id"]) for item in raw.get("watches", [])
                if isinstance(item, dict) and item.get("watch_id")
                and (item.get("watch_owner") == "KIM" or
                     (not item.get("watch_owner") and (item.get("chain_id") or item.get("source_spec_id"))))
            }
        except FileNotFoundError:
            return set()
        except Exception:
            logging.exception("[Composer] owned watch state read failed | %s", path)
            return set()

    # -------------------------------
    # fact matching
    # -------------------------------
    @staticmethod
    def _condition_direction_allowed(cond: ConditionSpec, direction: str) -> bool:
        return cond.direction in {"AUTO", direction}

    @staticmethod
    def _condition_eval_direction(spec: StrategySpec, cond: ConditionSpec, final_direction: str) -> str:
        """PRIVATE에서 최종 OZ 방향을 명시한 경우 조건 고유 방향과 최종 방향을 분리합니다.

        예: 상승 FVG가 조건이지만 사용자가 `매도 올존`을 명시하면
        FVG 조건은 BULL/LONG 사실을 그대로 검사하고 최종 OZ만 SHORT로 arm합니다.
        """
        if (
            spec.source == "PRIVATE"
            and spec.final_direction_explicit
            and cond.direction in {"LONG", "SHORT"}
        ):
            return str(cond.direction)
        return str(final_direction)

    @staticmethod
    def _trend_metric_compare(left: float, operator: str, right: float) -> bool:
        op = str(operator or "").upper()
        if op == "GT":
            return left > right
        if op == "GTE":
            return left >= right
        if op == "LT":
            return left < right
        if op == "LTE":
            return left <= right
        if op == "EQ":
            return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        if op == "NE":
            return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        return False

    def _trend_metric_fact_fresh_locked(self, symbol: str, tf: str, metric: str) -> Optional[dict]:
        fact = self.trend_metric_facts.get((symbol, tf, metric))
        if not fact:
            return None
        try:
            stale_sec = float(self.config.get("TREND_METRIC_STALE_SEC", DEFAULT_TREND_METRIC_STALE_SEC))
        except (TypeError, ValueError):
            stale_sec = DEFAULT_TREND_METRIC_STALE_SEC
        received = float(fact.get("received_mono") or 0.0)
        if received <= 0 or time.monotonic() - received > max(1.0, stale_sec):
            return None
        try:
            value = float(fact.get("value"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return fact

    def _condition_status_locked(self, spec: StrategySpec, cond: ConditionSpec, direction: str, health_batch=None) -> tuple[bool, str]:
        if not self._condition_source_usable(spec, cond, health_batch):
            return None, 'SUSPENDED:SOURCE_HEALTH'
        condition_direction = self._condition_eval_direction(spec, cond, direction)
        if not self._condition_direction_allowed(cond, condition_direction):
            return False, ""

        if cond.kind == "TREND":
            event = self.trend_facts.get((spec.symbol, cond.tf))
            if not event:
                return False, ""
            fact_dir = str(event.get("direction") or "").upper()
            if fact_dir != condition_direction:
                return False, ""
            token = f"TREND:{fact_dir}:{_event_time_token(event.get('bar_time'))}"
            return True, token

        if cond.kind == "WONBI":
            expected_side = cond.side or ("LOWER" if condition_direction == "LONG" else "UPPER")
            if expected_side == "LOWER" and condition_direction != "LONG":
                return False, ""
            if expected_side == "UPPER" and condition_direction != "SHORT":
                return False, ""
            event = self.wonbi_facts.get((spec.symbol, cond.tf, expected_side))
            if not event or not event.get("active"):
                return False, ""
            return True, f"WONBI:{expected_side}:{event.get('touch_id')}"

        if cond.kind == "PERCENTILE":
            expected_side = cond.side or ("LOWER" if condition_direction == "LONG" else "UPPER")
            if expected_side == "LOWER" and condition_direction != "LONG":
                return False, ""
            if expected_side == "UPPER" and condition_direction != "SHORT":
                return False, ""
            event = self.percentile_facts.get((spec.symbol, cond.tf, expected_side))
            if not event or not event.get("active"):
                return False, ""
            families = ",".join(event.get("families") or ())
            return True, f"OUT:{expected_side}:{families}:{event.get('touch_id')}"

        if cond.kind == "MA_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.fast_period, cond.slow_period)
            fact = self.ma_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_STATE:{cond.ma_family}{cond.fast_period}/{cond.slow_period}:"
                f"{relation}:{state_id}"
            )

        if cond.kind == "MA_PRICE_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.slow_period)
            fact = self.ma_price_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_PRICE_STATE:PRICE/{cond.ma_family}{cond.slow_period}:"
                f"{relation}:{state_id}"
            )

        if cond.kind == "MA_SLOPE_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.slow_period)
            fact = self.ma_slope_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_SLOPE_STATE:{cond.ma_family}{cond.slow_period}:"
                f"{relation}:CURRENT_VS_2BARS:{state_id}"
            )

        if cond.kind == "TREND_METRIC":
            left_fact = self._trend_metric_fact_fresh_locked(spec.symbol, cond.tf, cond.metric)
            gate_key = (spec.spec_id, str(direction), cond.label())
            if left_fact is None:
                state = self._trend_metric_gate_state.get(gate_key)
                if state is not None:
                    state["active"] = False
                return False, ""
            try:
                left = float(left_fact.get("value"))
            except (TypeError, ValueError):
                return False, ""

            if cond.metric_rhs:
                right_fact = self._trend_metric_fact_fresh_locked(spec.symbol, cond.tf, cond.metric_rhs)
                if right_fact is None:
                    state = self._trend_metric_gate_state.get(gate_key)
                    if state is not None:
                        state["active"] = False
                    return False, ""
                try:
                    right = float(right_fact.get("value"))
                except (TypeError, ValueError):
                    return False, ""
            else:
                right = float(cond.metric_value)

            passed = self._trend_metric_compare(left, cond.metric_operator, right)
            state = self._trend_metric_gate_state.get(gate_key)
            if not passed:
                if state is not None:
                    state["active"] = False
                return False, ""

            if state is None or not state.get("active"):
                self._trend_metric_gate_seq += 1
                state = {"active": True, "episode": self._trend_metric_gate_seq}
                self._trend_metric_gate_state[gate_key] = state
            episode = int(state.get("episode") or 0)
            rhs_label = cond.metric_rhs or f"{right:g}"
            return True, (
                f"TREND_METRIC:{cond.metric}:{cond.metric_operator}:{rhs_label}:EP{episode}"
            )

        if cond.kind == "FVG":
            expected_side = cond.side or ("BULL" if condition_direction == "LONG" else "BEAR")
            if expected_side == "BULL" and condition_direction != "LONG":
                return False, ""
            if expected_side == "BEAR" and condition_direction != "SHORT":
                return False, ""
            matches = [
                ev for (symbol, tf, _), ev in self.fvg_touches.items()
                if symbol == spec.symbol and tf == cond.tf
                and str(ev.get("fvg_side") or "").upper() == expected_side
            ]
            if not matches:
                return False, ""
            tokens = sorted(
                f"{ev.get('zone_id')}:{_event_time_token(ev.get('event_time'))}" for ev in matches
            )
            return True, "FVG:" + ",".join(tokens)

        if cond.kind == "SWEEP":
            sub = self._sweep_subscription_payload(spec, cond)
            wid = sub["watch_id"]
            matches = [
                ev for (symbol, tf, watch_id, _), ev in self.sweep_touches.items()
                if symbol == spec.symbol and tf == cond.tf and watch_id == wid
                and str(ev.get("direction") or "").upper() == condition_direction
            ]
            if not matches:
                return False, ""
            tokens = sorted(
                f"{ev.get('level_id')}:{_event_time_token(ev.get('touch_time') or ev.get('event_time'))}" for ev in matches
            )
            return True, "SWEEP:" + ",".join(tokens)

        return False, ""

    def _evaluate_spec_direction_locked(self, spec: StrategySpec, direction: str, health_batch=None) -> Optional[str]:
        states = [self._condition_status_locked(spec, cond, direction, health_batch) for cond in spec.conditions]
        if spec.combination == "ALL":
            passed = all(ok for ok, _ in states)
        else:
            passed = any(ok for ok, _ in states)
        if not passed:
            return None
        active_tokens = [token for ok, token in states if ok and token]
        raw = f"{spec.spec_id}|{direction}|" + "|".join(active_tokens)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _selected_sweep_touch_locked(self, spec: StrategySpec, direction: str) -> Optional[dict]:
        """현재 실제 터치된 외부유동성 중 방향별 최외곽 레벨 하나를 고릅니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return None

        candidates: dict[tuple[str, str], dict] = {}
        for cond in spec.conditions:
            condition_direction = self._condition_eval_direction(spec, cond, direction)
            if cond.kind != "SWEEP" or not self._condition_direction_allowed(cond, condition_direction):
                continue
            sub = self._sweep_subscription_payload(spec, cond)
            wid = str(sub.get("watch_id") or "")
            for (symbol, tf, watch_id, level_id), ev in self.sweep_touches.items():
                if symbol != spec.symbol or tf != cond.tf or watch_id != wid:
                    continue
                if str(ev.get("direction") or "").upper() != condition_direction:
                    continue
                try:
                    level_price = float(ev.get("level_price"))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(level_price):
                    continue
                item = dict(ev)
                item["_external_watch_id"] = wid
                item["_external_source_tf"] = cond.tf
                item["_level_price"] = level_price
                candidates[(wid, str(level_id))] = item

        if not candidates:
            return None

        values = list(candidates.values())
        if direction == "LONG":
            # 실제 터치된 하단 유동성 중 가장 낮은 가격을 사용합니다.
            return min(values, key=lambda x: (x["_level_price"], -float(x.get("event_time") or x.get("touch_time") or 0.0)))
        # 실제 터치된 상단 유동성 중 가장 높은 가격을 사용합니다.
        return max(values, key=lambda x: (x["_level_price"], float(x.get("event_time") or x.get("touch_time") or 0.0)))

    def _arm_oz_locked(self, spec: StrategySpec, direction: str, signature: str) -> None:
        last_key = (spec.spec_id, direction)
        if self._last_signatures.get(last_key) == signature:
            return
        if not self._time_policy.allows(spec.time_filters):
            return

        child_id = stable_id("OZARM", spec.spec_id, direction, signature, length=20)
        request_chat_id = spec.owner_chat_id if spec.destination == "PRIVATE" else None
        selected_sweep = self._selected_sweep_touch_locked(spec, direction)
        external_watch_id = None
        external_source_tf = None
        external_level_id = None
        external_level_price = None
        if selected_sweep is not None:
            external_watch_id = str(selected_sweep.get("_external_watch_id") or "") or None
            external_source_tf = str(selected_sweep.get("_external_source_tf") or "").lower() or None
            external_level_id = str(selected_sweep.get("level_id") or "") or None
            external_level_price = selected_sweep.get("_level_price")

        payload = {
            "action": "MANUAL_WATCH",
            "watch_id": child_id,
            "timeframes": list(spec.oz_tfs),
            "symbol": spec.symbol,
            "direction": direction,
            # Composer가 final alert 후 watch_id로 직접 취소합니다.
            # monitor_OZ의 legacy one-shot replacement가 서로 다른 전략을 지우지 않도록 persistent로 등록합니다.
            "persistent": True,
            "request_chat_id": request_chat_id,
            "validation_mode": spec.validation_mode,
            "trigger_mode": spec.trigger_mode,
            "source_spec_id": spec.spec_id,
            "source_spec_ids": [spec.spec_id],
            "source_name": spec.name,
        }
        if external_watch_id is not None:
            payload.update({
                "external_watch_id": external_watch_id,
                "external_source_tf": external_source_tf,
                "external_liquidity_required": True,
                "external_source_kind": "SWEEP",
                # 아래 두 값은 판정용이 아니라 선택 결과 추적/로그용입니다.
                "external_level_id": external_level_id,
                "external_level_price": external_level_price,
            })

        profile = (
            request_chat_id or "OFFICIAL", spec.symbol, direction, tuple(spec.oz_tfs),
            spec.validation_mode, spec.trigger_mode, external_watch_id, external_source_tf,
        )
        for active in self._active_children.values():
            active_profile = (
                str(active.get("request_chat_id") or "OFFICIAL"), str(active.get("symbol") or ""),
                str(active.get("direction") or ""), tuple(active.get("timeframes") or ()),
                str(active.get("validation_mode") or ""), str(active.get("trigger_mode") or ""),
                str(active.get("external_watch_id") or "") or None,
                str(active.get("external_source_tf") or "").lower() or None,
            )
            if active_profile == profile:
                # 기존 setup 병합 동작은 유지하되, 어떤 SPECIAL 공식 전략들이 이 child를
                # 공유하는지는 보존하여 최종 알림 템플릿을 spec_id로 찾을 수 있게 합니다.
                source_ids = active.setdefault("source_spec_ids", [])
                if spec.spec_id not in source_ids:
                    source_ids.append(spec.spec_id)
                    self._track_watch_payload(active)
                    self._save_active_children_state_locked()
                self._last_signatures[last_key] = signature
                self._remember_signature(spec.spec_id, direction, signature)
                logging.info("ℹ️ [Composer→OZ] 동일 OZ profile 이미 arm - setup 병합 | %s %s %s", spec.name, spec.symbol, direction)
                return
        self._push(payload)
        self._active_children[child_id] = payload
        self._save_active_children_state_locked()
        self._last_signatures[last_key] = signature
        self._remember_signature(spec.spec_id, direction, signature)
        logging.info(
            "🎯 [Composer→OZ] %s | %s | %s | TF=%s | validation=%s trigger=%s | child=%s | 외부유동성=%s %s",
            spec.name, spec.symbol, direction, ",".join(spec.oz_tfs),
            spec.validation_mode, spec.trigger_mode, child_id,
            external_level_id or "없음",
            f"@ {float(external_level_price):.6f}" if external_level_price is not None else "",
        )

        # 개인 1회성 전략은 setup을 한 번 OZ에 넘긴 시점에서 Composer 등록을 종료합니다.
        if spec.source == "PRIVATE" and not spec.persistent:
            self.manual_specs.pop(spec.spec_id, None)
            self._save_private_state_locked()
            self._subscription_dirty = True

    def _notify_spec_locked(self, spec: StrategySpec, direction: str, signature: str) -> None:
        last_key = (spec.spec_id, direction)
        if self._last_signatures.get(last_key) == signature:
            return
        if not self._time_policy.allows(spec.time_filters):
            return

        side_text = "매수" if direction == "LONG" else "매도"
        condition_text = " + ".join(c.label() for c in spec.conditions)
        message = f"🔔 {spec.name} 조건 성립\n{spec.symbol} · {side_text}\n{condition_text}"
        delivered = self.send_telegram(message, spec.owner_chat_id if spec.destination == "PRIVATE" else None,
                                       watch_id=spec.spec_id)
        if not delivered:
            return

        self._last_signatures[last_key] = signature
        self._remember_signature(spec.spec_id, direction, signature)
        logging.info("🔔 [Composer 조건알림] %s | %s | %s", spec.name, spec.symbol, direction)

        # 일반 '알려줘'는 1회성입니다. 계속/지속/항상을 붙인 경우에만 다음 새 조건 성립도 기다립니다.
        if spec.source == "PRIVATE" and not spec.persistent:
            self.manual_specs.pop(spec.spec_id, None)
            self._save_private_state_locked()
            self._subscription_dirty = True

    def _evaluate_symbol_locked(self, symbol: str) -> None:
        specs = [s for s in self._all_specs_locked() if s.symbol == symbol]
        sources = set()
        for spec in specs:
            for cond in spec.conditions:
                _, binding = self._condition_source_binding(spec, cond)
                sources.update(binding.get('sources') or {})
        # One fresh health snapshot per evaluation, never a cross-event/time-based cache.
        checked_at = time.monotonic()
        health_batch = (self.staff.health(symbol, sorted(sources)) if sources else {}, checked_at)
        for spec in specs:
            directions = (spec.final_direction,) if spec.final_direction in {"LONG", "SHORT"} else ("LONG", "SHORT")
            for direction in directions:
                signature = self._evaluate_spec_direction_locked(spec, direction, health_batch)
                if not signature:
                    continue
                if spec.final_action == "NOTIFY":
                    self._notify_spec_locked(spec, direction, signature)
                else:
                    self._arm_oz_locked(spec, direction, signature)

    # -------------------------------
    # facts from TREND / FVG / SWEEP / OZ
    # -------------------------------
    def _handle_fact_event(self, event: dict) -> dict:
        revision = event.get('fact_revision')
        scope = fact_scope(event)
        if revision:
            previous = self._fact_revisions.get(scope)
            if previous and tuple(revision) <= tuple(previous['revision']):
                return {'ok': True, 'delivered': False, 'stale': True}
        if event.get('kind') == 'FACT_SNAPSHOT':
            return self._reconcile_facts(event)
        if event.get('source_health'):
            self._source_bindings[scope] = event['source_health']
        result = self._apply_fact_event(event)
        if revision and result.get('ok'):
            self._fact_revisions.put(scope, {'revision': revision})
        return result

    def _reconcile_facts(self, event: dict) -> dict:
        family, symbol, tf = event.get('strategy'), event.get('symbol'), normalize_tf(event.get('source_tf'))
        facts = event.get('facts')
        if event.get('complete') is not True or not isinstance(facts, list) or not symbol or not tf:
            return {'ok': False, 'error': 'incomplete_snapshot'}
        if any(not isinstance(f, dict) or f.get('symbol') != symbol or f.get('source_tf') != tf for f in facts):
            return {'ok': False, 'error': 'snapshot_scope_mismatch'}
        with self._lock:
            if family == 'TREND':
                if len(facts) != 1 or facts[0].get('trend') not in {'UP','DOWN','NEUTRAL'}:
                    return {'ok': False, 'error': 'invalid_trend_snapshot'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                self.trend_facts[(symbol,tf)] = dict(facts[0])
            elif family == 'FVG':
                if any(not f.get('zone_id') for f in facts): return {'ok':False,'error':'invalid_zone'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                for store in (self.fvg_zones, self.fvg_touches):
                    for key in list(store):
                        if key[:2] == (symbol,tf): store.pop(key)
                for fact in facts:
                    key = (symbol,tf,fact['zone_id'])
                    self.fvg_zones[key] = dict(fact)
                    if fact.get('touched_now'): self.fvg_touches[key] = dict(fact)
            elif family == 'SWEEP':
                wid = event.get('watch_id')
                if not wid or any(f.get('watch_id') != wid or not f.get('level_id') for f in facts):
                    return {'ok':False,'error':'invalid_sweep_scope'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                for key in list(self.sweep_touches):
                    if key[:3] == (symbol,tf,wid): self.sweep_touches.pop(key)
                for fact in facts: self.sweep_touches[(symbol,tf,wid,fact['level_id'])] = dict(fact)
            else:
                return {'ok':False,'error':'invalid_snapshot_family'}
            self._source_bindings[fact_scope(event)] = event.get('source_health') or {}
            self._evaluate_symbol_locked(symbol)
        return {'ok': True, 'delivered':False,'reconciled':True}

    def _remember_signature(self, spec_id, direction, signature):
        self._signature_records.put(identity(spec_id, direction),
                                    {'spec_id':spec_id, 'direction':direction,'signature':signature})

    def _condition_source_binding(self, spec, cond):
        family = 'TREND' if cond.kind == 'TREND_METRIC' else 'MA' if cond.kind.startswith('MA_') else cond.kind
        wid = self._sweep_subscription_payload(spec, cond)['watch_id'] if family == 'SWEEP' else None
        scope = fact_scope({'strategy':family,'symbol':spec.symbol,'source_tf':cond.tf,'watch_id':wid})
        binding = self._source_bindings.get(scope) or {}
        return scope, binding

    def _condition_source_usable(self, spec, cond, health_batch=None):
        scope, binding = self._condition_source_binding(spec, cond)
        sources = binding.get('sources') or {}
        if not sources or any(not epoch for epoch in sources.values()):
            self.source_status[scope] = 'UNKNOWN'
            return False
        elapsed = 0.0
        if health_batch is None:
            health = self.staff.health(spec.symbol, sources)
        else:
            health, checked_at = health_batch
            elapsed = max(0.0, time.monotonic() - checked_at)
            # Older STAFFs do not describe their freshness window: ask them directly.
            if any(health.get(tf, {}).get('status') == 'FRESH' and
                   ('age_seconds' not in health[tf] or 'stale_seconds' not in health[tf]) for tf in sources):
                health = self.staff.health(spec.symbol, sources)
                elapsed = 0.0
        usable = all(health.get(tf, {}).get('status') == 'FRESH'
                     and health[tf].get('source_epoch') == epoch
                     and (health[tf].get('age_seconds') is None or
                          float(health[tf]['age_seconds']) + elapsed <= float(health[tf]['stale_seconds']))
                     and all(health[tf].get('indicators', {}).get(ind, False) for ind in binding.get('indicators', []))
                     for tf, epoch in sources.items())
        self.source_status[scope] = 'READY' if usable else 'SUSPENDED'
        return usable

    def _observe_local_source(self, family, symbol, tf, df, indicators):
        self._source_bindings[fact_scope({'strategy':family,'symbol':symbol,'source_tf':tf})] = source_health({tf:df}, indicators)

    def _apply_fact_event(self, event: dict) -> dict:
        kind = str(event.get("kind") or "").upper()
        symbol = str(event.get("symbol") or "").strip()
        tf = normalize_tf(event.get("source_tf") or event.get("tf"))
        changed = False
        direct_fvg_watches: list[FVGCreatedWatchSpec] = []
        chain_events: list[dict] = []

        with self._lock:
            if kind == "TREND_STATE" and symbol and tf:
                self.trend_facts[(symbol, tf)] = dict(event)
                changed = True

            elif kind == "TREND_METRIC_STATE" and symbol and tf:
                metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
                received_mono = time.monotonic()
                bar_time = event.get("bar_time")
                for metric, raw_value in metrics.items():
                    metric_name = str(metric or "").strip().lower()
                    if metric_name not in TREND_METRIC_FIELDS:
                        continue
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(value):
                        continue
                    self.trend_metric_facts[(symbol, tf, metric_name)] = {
                        "value": value,
                        "bar_time": bar_time,
                        "received_mono": received_mono,
                    }
                    changed = True

            elif kind == "FVG_CREATED" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                direction = str(event.get("direction") or "").upper()
                if direction not in {"LONG", "SHORT"}:
                    side = str(event.get("fvg_side") or "").upper()
                    direction = "LONG" if side == "BULL" else "SHORT" if side == "BEAR" else ""
                if zone_id:
                    self.fvg_zones[(symbol, tf, zone_id)] = dict(event)
                    changed = True
                raw_fvg_event_ts = self._local_chain_epoch(event.get("event_time") or event.get("fvg_time"))
                filter_fvg_event_ts = (
                    float(raw_fvg_event_ts) + float(tf_seconds(tf))
                    if raw_fvg_event_ts is not None else time.time()
                )

                # 개인 "신규 FVG 생성" Watch: monitor_OZ 로컬 FVG 계산 없이 직접 처리합니다.
                for watch in self.fvg_created_watches.values():
                    if watch.symbol != symbol or tf not in watch.timeframes:
                        continue
                    if watch.direction is not None and watch.direction != direction:
                        continue
                    direct_fvg_watches.append(watch)

                # 개인 시간연쇄/순서무관의 FVG_NEW 조건.
                for chain in self.timed_chains.values():
                    if not chain.enabled or chain.symbol != symbol:
                        continue

                    # 후보가 살아 있는 동안 arm된 CANCEL_ON FVG는 정상 stage와 별도 음수 stage로 전달합니다.
                    for raw_idx, cancel_watch_id in tuple(chain.invalidation_watch_ids.items()):
                        try:
                            cancel_idx = int(raw_idx)
                        except (TypeError, ValueError):
                            continue
                        if cancel_idx < 0 or cancel_idx >= len(chain.invalidation_triggers):
                            continue
                        cancel_trig = chain.invalidation_triggers[cancel_idx]
                        if cancel_trig.watch_type != "FVG_NEW" or cancel_trig.tf != tf:
                            continue
                        if cancel_trig.direction is not None and cancel_trig.direction != direction:
                            continue
                        chain_events.append({
                            "kind": "GENERIC_TRIGGER", "watch_id": cancel_watch_id,
                            "chain_id": chain.chain_id,
                            "chain_stage": self.watch_orchestrator._invalidation_stage(cancel_idx),
                            "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                            "direction": direction, "zone_id": zone_id,
                            "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                            "message": f"↩️ [CANCEL_ON FVG] {symbol} · {tf}",
                        })

                    if chain.active_child_id and chain.order_mode != "FILTER":
                        continue
                    if chain.order_mode in {"UNORDERED", "FILTER"}:
                        for idx, trig in enumerate(chain.triggers):
                            if trig.watch_type != "FVG_NEW" or trig.tf != tf:
                                continue
                            if trig.direction is not None and trig.direction != direction:
                                continue
                            key = str(idx)
                            watch_id = chain.current_watch_ids.get(key) or stable_id(
                                "CHAINFILTER" if chain.order_mode == "FILTER" else "CHAINUNORD",
                                chain.chain_id, idx, length=20
                            )
                            chain.current_watch_ids[key] = watch_id
                            chain_events.append({
                                "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                                "chain_id": chain.chain_id, "chain_stage": idx,
                                "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                                "direction": direction, "zone_id": zone_id,
                                "event_id": zone_id,
                                "event_time": (
                                    filter_fvg_event_ts if chain.order_mode == "FILTER"
                                    else event.get("event_time") or event.get("fvg_time")
                                ),
                                "message": (
                                    f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                                    if direction == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                                ),
                            })
                        continue

                    if chain.stage >= len(chain.triggers):
                        continue
                    trig = chain.triggers[chain.stage]
                    if trig.watch_type != "FVG_NEW" or trig.tf != tf:
                        continue
                    if trig.direction is not None and trig.direction != direction:
                        continue
                    watch_id = chain.current_watch_id or stable_id("CHAINTRG", chain.chain_id, chain.stage, length=20)
                    chain.current_watch_id = watch_id
                    chain_events.append({
                        "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                        "chain_id": chain.chain_id, "chain_stage": chain.stage,
                        "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                        "direction": direction, "zone_id": zone_id,
                        "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                        "message": (
                            f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                            if direction == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                        ),
                    })

                # SPECIAL TIMED_CHAIN의 FVG 축. Cross는 기존 monitor_OZ, FVG는 Composer가 직접 받습니다.
                for chain_spec in self.official_chain_specs.values():
                    if not chain_spec.enabled or chain_spec.symbol != symbol or tf not in chain_spec.fvg_tfs:
                        continue
                    watch_id = stable_id("CFGCHAINTRG", chain_spec.spec_id, "FVG", tf, length=20)
                    chain_events.append({
                        "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                        "chain_id": f"CFGCHAIN:{chain_spec.spec_id}",
                        "chain_stage": list(chain_spec.fvg_tfs).index(tf) + 1,
                        "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                        "direction": direction, "zone_id": zone_id,
                        "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                    })

            elif kind == "FVG_TOUCH" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    zone = dict(self.fvg_zones.get(key) or {})
                    zone.update(event)
                    zone["touched_now"] = True
                    self.fvg_zones[key] = zone
                    self.fvg_touches[key] = dict(event)
                    changed = True

            elif kind == "FVG_TOUCH_END" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    self.fvg_touches.pop(key, None)
                    if key in self.fvg_zones:
                        self.fvg_zones[key]["touched_now"] = False
                    changed = True

            elif kind in {"FVG_FILLED", "FVG_EXPIRED"} and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    self.fvg_touches.pop(key, None)
                    self.fvg_zones.pop(key, None)
                    changed = True

            elif kind == "SWEEP_TOUCH" and symbol and tf:
                watch_id = str(event.get("watch_id") or "").strip()
                level_id = str(event.get("level_id") or "").strip()
                if watch_id and level_id:
                    self.sweep_touches[(symbol, tf, watch_id, level_id)] = dict(event)
                    changed = True

            elif kind == "SWEEP_INVALIDATED" and symbol and tf:
                watch_id = str(event.get("watch_id") or "").strip()
                level_id = str(event.get("level_id") or "").strip()
                if watch_id and level_id:
                    self.sweep_touches.pop((symbol, tf, watch_id, level_id), None)
                    changed = True

            if changed:
                self._evaluate_symbol_locked(symbol)

        # 공식 시간연쇄도 fact-store 갱신과 분리된 공용 event boundary에서 진행합니다.
        # FVG fact 수신 분기는 전략 단계/OZ arm을 직접 알지 않습니다.
        self._dispatch_official_timed_chain_event(event)

        removed_watch = False
        followup_oz_payloads: list[dict] = []
        for watch in direct_fvg_watches:
            side = str(event.get("direction") or "").upper()
            if side not in {"LONG", "SHORT"}:
                fvg_side = str(event.get("fvg_side") or "").upper()
                side = "LONG" if fvg_side == "BULL" else "SHORT"

            consumed = False
            if watch.final_action == "OZ":
                zone_id = str(event.get("zone_id") or event.get("fvg_time") or time.time_ns())
                child_id = stable_id("OZARM:FVGCREATED", watch.watch_id, zone_id, length=20)
                followup_oz_payloads.append({
                    "action": "MANUAL_WATCH", "watch_id": child_id,
                    "timeframes": list(watch.oz_tfs), "symbol": watch.symbol,
                    "direction": watch.oz_direction, "persistent": False,
                    "request_chat_id": watch.request_chat_id,
                    "validation_mode": watch.validation_mode, "trigger_mode": watch.trigger_mode,
                    "source_name": "FVG 생성→OZ", "source_fvg_zone_id": zone_id,
                    "reply_parent_watch_id": watch.watch_id,
                })
                oz_side = "하단 " if watch.oz_direction == "LONG" else "상단 " if watch.oz_direction == "SHORT" else ""
                oz_label = "·".join(WATCH_TF_MAP.get(x, x) for x in watch.oz_tfs)
                self.send_telegram(
                    f"✅ {WATCH_TF_MAP.get(tf, tf)} FVG 생성\n{oz_label} {oz_side}올존 감시 시작",
                    watch.request_chat_id, watch_id=watch.watch_id,
                )
                consumed = True
            else:
                message = (
                    f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                    if side == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                )
                consumed = self.send_telegram(message, watch.request_chat_id, watch_id=watch.watch_id)

            if consumed and not watch.persistent:
                with self._lock:
                    self.fvg_created_watches.pop(watch.watch_id, None)
                    self._save_fvg_created_watch_state_locked()
                    self._subscription_dirty = True
                removed_watch = True

        for payload in followup_oz_payloads:
            self._push(payload)

        for chain_event in chain_events:
            self._handle_generic_trigger(chain_event)

        return {
            "ok": True, "delivered": False, "composed": changed,
            "fvg_created_watches_fired": len(direct_fvg_watches),
            "fvg_chain_events": len(chain_events), "subscription_changed": removed_watch,
        }

    @staticmethod
    def _trend_query_result_text(event: dict, symbol: str, tf: str) -> str:
        """추세(경량 Fact) 조회와 추세점수(전체 지표, 요청 시 계산) 조회 결과 문구."""
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        score_query = str(event.get("purpose") or "").upper() == "TREND_SCORE_QUERY" or bool(
            set(metrics) & {"trend_score", "long_score", "short_score"})
        if not event.get("ok"):
            return f"❌ {symbol} {tf} {'추세점수' if score_query else '추세'} 조회 실패"

        def number(value, fmt):
            try:
                x = float(value)
            except (TypeError, ValueError):
                return "-"
            return format(x, fmt) if math.isfinite(x) else "-"

        if score_query:
            return (
                f"📊 {symbol} {tf} 추세점수 · {number(metrics.get('trend_score'), '.1f')}점\n"
                f"LONG {number(metrics.get('long_score'), '.1f')} / SHORT {number(metrics.get('short_score'), '.1f')}"
                f" · 전체 지표 기준"
            )
        return (
            f"📈 {symbol} {tf} 추세 · {event.get('trend')}\n"
            f"SMA20(시가) 기울기 {number(event.get('sma20_slope'), '+.6g')} / "
            f"HMA50 기울기 {number(event.get('hma50_slope'), '+.6g')}"
        )

    def _handle_query_result(self, event: dict) -> dict:
        kind = str(event.get("kind") or "").upper()
        target = str(event.get("request_chat_id") or "").strip() or self.chat_id
        symbol = str(event.get("symbol") or "").strip()
        tf = str(event.get("source_tf") or "").strip()

        special_result = self._dispatch_special_watch_event(event)
        if special_result is not None:
            return special_result

        if kind == "TREND_QUERY_RESULT":
            text = self._trend_query_result_text(event, symbol, tf)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        if kind == "FVG_QUERY_RESULT":
            if not event.get("ok"):
                text = f"❌ {symbol} {tf} FVG 조회 실패"
            else:
                zones = [x for x in (event.get("zones") or []) if isinstance(x, dict)]
                if not zones:
                    text = f"📦 {symbol} {tf} FVG · 현재 추적 중인 유효 FVG 없음"
                else:
                    rows = [f"📦 {symbol} {tf} FVG · 유효 {len(zones)}개"]
                    for z in zones:
                        side = str(z.get("fvg_side") or "-")
                        try:
                            bot = f"{float(z.get('zone_bot')):.6f}"
                            top = f"{float(z.get('zone_top')):.6f}"
                        except (TypeError, ValueError):
                            bot, top = "-", "-"
                        touched = " · 현재터치" if z.get("touched_now") else ""
                        age = z.get("age_bars")
                        age_text = f" · {age}봉 전" if age is not None else ""
                        rows.append(f"• {side} {bot} ~ {top}{age_text}{touched}")
                    text = "\n".join(rows)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        if kind == "SWEEP_QUERY_RESULT":
            if not event.get("ok"):
                text = f"❌ {symbol} {tf} 외부유동성 조회 실패"
            else:
                levels = [x for x in (event.get("levels") or []) if isinstance(x, dict)]
                if not levels:
                    text = f"🌐 {symbol} {tf} 외부유동성 · 현재 레벨 없음"
                else:
                    rows = [f"🌐 {symbol} {tf} 외부유동성 · {len(levels)}개"]
                    for lv in levels:
                        name = str(lv.get("level_name") or lv.get("level_code") or "-")
                        direction = str(lv.get("direction") or "-")
                        try:
                            price = f"{float(lv.get('level_price')):.6f}"
                        except (TypeError, ValueError):
                            price = "-"
                        state = " · 터치" if lv.get("touched") else ""
                        if lv.get("consumed"):
                            state += " · 소진"
                        rows.append(f"• {name} · {direction} · {price}{state}")
                    text = "\n".join(rows)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        return {"ok": True, "delivered": False}

    def _handle_oz_event(self, event: dict) -> dict:
        if str(event.get('kind', '')).upper() == 'FINAL_ALERT':
            event, expired = self._filter_config_chain_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
            event, expired = self.watch_orchestrator.filter_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
        special_result = self._dispatch_special_oz_event(event)
        if special_result is not None:
            return special_result
        return self._handle_oz_event_core(event)

    def _handle_oz_event_core(self, event: dict) -> dict:
        kind = str(event.get("kind") or "FINAL_ALERT").upper()
        watch_id = str(event.get("watch_id") or "").strip()

        # OZ 자체가 시간연쇄의 선행 단계인 경우 FINAL_ALERT를 내부 단계 이벤트로 소비합니다.
        # 같은 alert 묶음에 일반 Watch가 함께 있으면 그 일반 알림은 그대로 전달합니다.
        oz_stage = {"handled": False, "all_internal": False}
        if kind == "FINAL_ALERT":
            oz_stage = self.watch_orchestrator.handle_oz_stage_event(event)
            if oz_stage.get("handled") and oz_stage.get("all_internal"):
                return {
                    "ok": True, "delivered": True, "suppressed": True,
                    "chain_stage": True, "internal_watch_ids": oz_stage.get("internal_ids", []),
                }

        # Composer가 자동 arm한 OZ의 등록 ACK는 사용자/공식채널에 노출하지 않습니다.
        if kind == "CONTROL_ACK" and watch_id.startswith(("OZARM:", "CFGCHAINTRG:", "CHAINTRG:")):
            return {"ok": True, "delivered": True, "suppressed": True}

        message = str(event.get("message") or "").strip()
        if not message:
            return {"ok": False, "delivered": False, "error": "empty_message"}
        request_chat_id = str(event.get("request_chat_id") or "").strip() or None

        # 재시작 동기화/내부 정리에서 발생한 CONTROL_ACK는 사용자 명령 응답이 아닙니다.
        # 목적지가 없는 CONTROL_ACK를 공식 채널 기본값으로 보내지 않습니다.
        if kind == "CONTROL_ACK" and request_chat_id is None:
            logging.info("🔇 [OZ CONTROL_ACK 억제] 내부/재시작 정리 ACK | watch_id=%s | %s", watch_id or "-", message)
            return {"ok": True, "delivered": False, "suppressed": True}

        # 개인전략(request_chat_id 있음)은 monitor_OZ의 기존 심플 알림을 그대로 유지합니다.
        # 공식 메인전략만 SPECIAL에 등록된 템플릿으로 렌더링합니다.
        if kind == "FINAL_ALERT" and request_chat_id is None:
            with self._lock:
                spec = self._official_spec_for_oz_event_locked(event)
                if spec is not None:
                    message = self._render_official_oz_alert(spec, event, message)

        target = request_chat_id or self.chat_id
        role = str(event.get("watch_event") or "")
        links = self._watch_links_for_ids([watch_id], target)
        if kind == "CONTROL_ACK" and role == "REGISTERED" and links:
            delivered = self.send_telegram(message, target, watch_id=watch_id, watch_registration=True)
        elif kind == "CONTROL_ACK" and role == "CANCELLED" and event.get("reply_cancel") and links:
            link = links[0]
            removed = int(event.get("removed_count") or 0)
            with self._lock:
                if not link.get("cancel_result_text"):
                    link["status"] = "cancelled" if removed else "inactive"
                    self._save_private_state_locked()
            result_text = self._watch_cancel_text(link) if removed else "ℹ️ 이미 취소되었거나 종료된 감시입니다."
            delivered = self._send_watch_cancel_result(link, result_text)
        elif kind == "FINAL_ALERT" or role == "ALERT":
            ids = [str(x) for x in (event.get("watch_ids") or []) if x]
            if watch_id:
                ids.append(watch_id)
            # Internal OZ stage watches have already been consumed by the orchestrator.
            ids = [x for x in ids if x not in oz_stage.get("internal_ids", [])]
            links = self._watch_links_for_ids(ids, target)
            delivered = self._send_watch_notification(message, target, links) if links else self.send_telegram(message, target)
            # Preserve legacy/unmapped watches sharing the same engine event.
            known = {wid for link in links for wid in [link["watch_id"], *link["members"]]}
            if links and any(wid not in known for wid in ids):
                delivered = self.send_telegram(message, target) and delivered
        else:
            delivered = self.send_telegram(message, target)
        if delivered and kind == "FINAL_ALERT":
            watch_ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
            with self._lock:
                active_changed = False
                for wid in watch_ids:
                    payload = self._active_children.pop(wid, None)
                    if payload is not None:
                        active_changed = True
                        self._push({
                            "action": "CANCEL_MANUAL",
                            "watch_id": wid,
                            "request_chat_id": payload.get("request_chat_id"),
                            "validation_mode": payload.get("validation_mode"),
                            "trigger_mode": payload.get("trigger_mode"),
                        })
                if active_changed:
                    self._save_active_children_state_locked()
            self.watch_orchestrator.complete_final_oz(event, delivered=True)
        return {
            "ok": bool(delivered),
            "delivered": bool(delivered),
            "error": None if delivered else "telegram_send_failed",
        }

    def _handle_event(self, event: object) -> dict:
        key = receipt_key(event) if isinstance(event, dict) else None
        if not key:
            return self._dispatch_event(event)
        with self._receipts.lock:
            previous = self._receipts.get(key)
            if previous and previous.get('status') == 'done':
                return dict(previous['reply'], duplicate=True)
            self._receipts.put(key, {'status': 'processing', 'event': event})
            self._delivery_context.event_id = key
            try:
                reply = self._dispatch_event(event)
                if reply.get('ok'):
                    self._receipts.put(key, {'status': 'done', 'reply': reply})
                return dict(reply, event_id=event['event_id'], accepted=bool(reply.get('ok')))
            finally:
                self._delivery_context.event_id = None

    def _dispatch_event(self, event: object) -> dict:
        if not isinstance(event, dict):
            return {"ok": False, "delivered": False, "error": "invalid_event"}
        kind = str(event.get("kind") or "FINAL_ALERT").upper()
        strategy = str(event.get("strategy") or "").upper()
        if kind == "PING":
            # 각 프로그램은 명령 수신 준비를 끝낸 뒤 PING을 보냅니다.
            # 그 시점에 현재 살아 있어야 할 감시만 다시 전달하여 시작 직후 명령 유실을 막습니다.
            if strategy in {"TREND", "FVG", "SWEEP"}:
                self._refresh_engine_family_after_ping(strategy)
            elif strategy == "OZ":
                self._refresh_oz_after_ping()
            return {"ok": True, "delivered": False, "pong": True, "service": "manager_KIM Composer 2.0"}
        if kind.endswith("QUERY_RESULT"):
            return self._handle_query_result(event)
        if kind == "GENERIC_TRIGGER":
            return self._handle_generic_trigger(event)
        if strategy in {"TREND", "FVG", "SWEEP"}:
            return self._handle_fact_event(event)
        if strategy == "OZ" or kind == "CONTROL_ACK":
            return self._handle_oz_event(event)
        if kind == "FINAL_ALERT":
            message = str(event.get("message") or "").strip()
            delivered = self.send_telegram(message) if message else False
            return {"ok": bool(delivered), "delivered": bool(delivered)}
        return {"ok": False, "delivered": False, "error": f"unknown_kind:{kind}"}

    # -------------------------------
    # WONBI polling (Composer condition)
    # -------------------------------
    def _wonbi_requirements_locked(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "WONBI":
                    out.setdefault(spec.symbol, set()).add(cond.tf)
        # SPECIAL 전략이 LIVE 판정에 Composer 원비 fact를 쓰는 경우만 공용 요구사항으로 추가합니다.
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                condition_type, evaluation_mode = trigger_watch_contract(trig)
                if condition_type == "COMPOUND_CONDITION":
                    for cond in self._compound_conditions_for_trigger(trig):
                        if cond.kind == "WONBI":
                            out.setdefault(chain.symbol, set()).add(cond.tf)
                    continue
                handler = self._special_watch_handlers.get(condition_type)
                requirement_fn = getattr(handler, "needs_live_wonbi", None) if handler is not None else None
                if not callable(requirement_fn):
                    continue
                try:
                    required = bool(requirement_fn(chain, trig, evaluation_mode))
                except Exception:
                    logging.exception("[SPECIAL Watch] WONBI requirement 오류 | %s", condition_type)
                    required = False
                if required:
                    out.setdefault(chain.symbol, set()).add(trig.tf)
        return out

    def _condition_market(self, symbol, tfs, indicators, *, ma_names=None):
        if self.event_services is None:
            return self.staff.request(symbol, tfs, indicators)
        kernel=self.event_services.kernel
        return kernel.inputs.read(kernel,symbol,tfs,indicators,ma_names=ma_names)

    @staticmethod
    def _condition_row(frame,index):
        return frame.row(index) if hasattr(frame,'row') else frame.iloc[index]

    def _update_wonbi(self) -> None:
        with self._lock:
            required = self._wonbi_requirements_locked()
        for symbol, tf_set in sorted(required.items()):
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), ["WONBI"])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('WONBI', symbol, tf, df, ['WONBI'])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                try:
                    low = float(row.get("low"))
                    high = float(row.get("high"))
                    lower = float(row.get("wonbi_lower"))
                    upper = float(row.get("wonbi_upper"))
                except (TypeError, ValueError):
                    continue
                if not all(math.isfinite(x) for x in (low, high, lower, upper)):
                    continue
                event_time = row.get("time")
                changed = False
                with self._lock:
                    for side, active in (("LOWER", low <= lower), ("UPPER", high >= upper)):
                        key = (symbol, tf, side)
                        before = bool(self.wonbi_facts.get(key, {}).get("active"))
                        if active and not before:
                            self._wonbi_touch_seq += 1
                            self.wonbi_facts[key] = {
                                "active": True,
                                "touch_id": f"{_event_time_token(event_time)}:{self._wonbi_touch_seq}",
                                "event_time": event_time,
                                "price_low": low,
                                "price_high": high,
                                "level": lower if side == "LOWER" else upper,
                            }
                            changed = True
                            logging.info("🟠 [Composer WONBI] %s %s %s TOUCH", symbol, tf, side)
                        elif not active and before:
                            self.wonbi_facts[key] = {"active": False, "touch_id": None, "event_time": event_time}
                            changed = True
                            logging.info("↩️ [Composer WONBI] %s %s %s TOUCH END", symbol, tf, side)
                    if changed:
                        self._evaluate_symbol_locked(symbol)

    def _percentile_requirements_locked(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "PERCENTILE":
                    out.setdefault(spec.symbol, set()).add(cond.tf)
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind == "PERCENTILE":
                        out.setdefault(chain.symbol, set()).add(cond.tf)
        return out

    @staticmethod
    def _percentile_out_families(row: pd.Series) -> tuple[tuple[str, ...], tuple[str, ...]]:
        specs = {
            "RSI": ("RSI_val", "RSI_db", "RSI_ub"),
            "STO": ("STO_val", "STO_db", "STO_ub"),
            "DI": ("DI_val", "DI_db", "DI_ub"),
            "PRICE": ("price_hma_6", "price_band_lower", "price_band_upper"),
        }
        lower, upper = [], []
        for family, (v_col, lo_col, hi_col) in specs.items():
            try:
                value = float(row.get(v_col))
                lo = float(row.get(lo_col))
                hi = float(row.get(hi_col))
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(x) for x in (value, lo, hi)):
                continue
            if getattr(row,'native_out',False):
                prefix='price' if family=='PRICE' else family
                if math.isfinite(float(row.get(prefix+'_lower_out'))):lower.append(family)
                elif math.isfinite(float(row.get(prefix+'_upper_out'))):upper.append(family)
            elif value < lo:
                lower.append(family)
            elif value > hi:
                upper.append(family)
        return tuple(lower), tuple(upper)

    def _update_percentile(self) -> None:
        with self._lock:
            required = self._percentile_requirements_locked()
        for symbol, tf_set in sorted(required.items()):
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), ["RSI", "STO", "DI", "PRICE"])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('PERCENTILE', symbol, tf, df, ['RSI', 'STO', 'DI', 'PRICE'])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                lower, upper = self._percentile_out_families(row)
                event_time = row.get("time")
                changed = False
                with self._lock:
                    for side, families in (("LOWER", lower), ("UPPER", upper)):
                        key = (symbol, tf, side)
                        before = self.percentile_facts.get(key, {})
                        before_active = bool(before.get("active"))
                        before_families = tuple(before.get("families") or ())
                        active = bool(families)
                        if active and (not before_active or before_families != families):
                            self._percentile_touch_seq += 1
                            self.percentile_facts[key] = {
                                "active": True,
                                "touch_id": f"{_event_time_token(event_time)}:{self._percentile_touch_seq}",
                                "event_time": event_time,
                                "families": families,
                            }
                            changed = True
                            logging.info("🟣 [Composer OUT] %s %s %s | %s", symbol, tf, side, ",".join(families))
                        elif not active and before_active:
                            self.percentile_facts[key] = {
                                "active": False, "touch_id": None, "event_time": event_time, "families": (),
                            }
                            changed = True
                            logging.info("↩️ [Composer OUT] %s %s %s END", symbol, tf, side)
                    if changed:
                        self._evaluate_symbol_locked(symbol)

    def _ma_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int, int]]]]:
        out: dict[str, dict[str, set[tuple[str, int, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.fast_period), int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.fast_period), int(cond.slow_period))
                    )
        return out

    def _ma_price_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int]]]]:
        """현재 봉 가격 vs STAFF MA 조건이 필요한 종목/TF/MA만 수집합니다."""
        out: dict[str, dict[str, set[tuple[str, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_PRICE_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_PRICE_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.slow_period))
                    )
        return out

    def _ma_slope_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int]]]]:
        """현재 STAFF MA와 2봉 전 STAFF MA 비교가 필요한 종목/TF/MA만 수집합니다."""
        out: dict[str, dict[str, set[tuple[str, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_SLOPE_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_SLOPE_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.slow_period))
                    )
        return out

    def _update_ma_state(self) -> None:
        """STAFF 최신 진행봉에서 MA 배열/가격 상대위치/방향 상태를 한 요청으로 갱신합니다.

        MA_STATE는 MA 대 MA 현재 배열, MA_PRICE_STATE는 현재 봉 가격 대 MA 상태,
        MA_SLOPE_STATE는 STAFF가 전달한 같은 MA의 현재값과 2봉 전 값을 비교합니다.
        manager_KIM과 strategy_INDICATOR에서는 HMA를 재계산하지 않습니다.
        """
        with self._lock:
            pair_required = self._ma_state_requirements_locked()
            price_required = self._ma_price_state_requirements_locked()
            slope_required = self._ma_slope_state_requirements_locked()

        symbols = set(pair_required) | set(price_required) | set(slope_required)
        for symbol in sorted(symbols):
            pair_tf_map = pair_required.get(symbol, {})
            price_tf_map = price_required.get(symbol, {})
            slope_tf_map = slope_required.get(symbol, {})
            tf_names = set(pair_tf_map) | set(price_tf_map) | set(slope_tf_map)
            tfs = sorted(tf_names, key=tf_seconds)
            indicators = sorted({
                family
                for tf in tf_names
                for family in (
                    *(x[0] for x in pair_tf_map.get(tf, set())),
                    *(x[0] for x in price_tf_map.get(tf, set())),
                    *(x[0] for x in slope_tf_map.get(tf, set())),
                )
            })
            if not tfs:
                continue
            ma_names = {tf: {f'{family}{period}' for family,period in (
                *((family,p) for family,fast,slow in pair_tf_map.get(tf,()) for p in (fast,slow)),
                *price_tf_map.get(tf,()), *slope_tf_map.get(tf,()))} for tf in tfs}
            data = self._condition_market(symbol, tfs, indicators, ma_names=ma_names)
            if not data:
                continue
            observed_at = time.time()

            for tf in tfs:
                pair_reqs = pair_tf_map.get(tf, set())
                price_reqs = price_tf_map.get(tf, set())
                slope_reqs = slope_tf_map.get(tf, set())
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('MA', symbol, tf, df, [])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                two_bars_ago = self._condition_row(df,-3) if len(df) >= 3 else None
                event_time = row.get("time")

                with self._lock:
                    for family, fast, slow in sorted(pair_reqs):
                        prefix = family.lower()
                        fast_col = f"{prefix}_{int(fast)}"
                        slow_col = f"{prefix}_{int(slow)}"
                        try:
                            fast_value = float(row.get(fast_col))
                            slow_value = float(row.get(slow_col))
                        except (TypeError, ValueError):
                            fast_value = slow_value = float("nan")

                        if not (math.isfinite(fast_value) and math.isfinite(slow_value)):
                            relation = "UNKNOWN"
                        elif fast_value > slow_value:
                            relation = "ABOVE"
                        elif fast_value < slow_value:
                            relation = "BELOW"
                        else:
                            relation = "EQUAL"

                        key = (symbol, tf, family, int(fast), int(slow))
                        before = self.ma_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_state_seq}"
                            logging.info(
                                "📐 [Composer MA상태] %s %s %s%d/%d | %s → %s",
                                symbol, tf, family, fast, slow, before_relation or "NONE", relation,
                            )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_state_seq}"

                        self.ma_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "fast_value": fast_value if math.isfinite(fast_value) else None,
                            "slow_value": slow_value if math.isfinite(slow_value) else None,
                        }

                    for family, period in sorted(price_reqs):
                        ma_col = f"{family.lower()}_{int(period)}"
                        try:
                            price_value = float(row.get("close"))
                            ma_value = float(row.get(ma_col))
                        except (TypeError, ValueError):
                            price_value = ma_value = float("nan")

                        if not (math.isfinite(price_value) and math.isfinite(ma_value)):
                            relation = "UNKNOWN"
                        elif price_value > ma_value:
                            relation = "ABOVE"
                        elif price_value < ma_value:
                            relation = "BELOW"
                        else:
                            relation = "EQUAL"

                        key = (symbol, tf, family, int(period))
                        before = self.ma_price_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_price_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_price_state_seq}"
                            logging.info(
                                "📐 [Composer 봉/MA상태] %s %s PRICE/%s%d | %s → %s",
                                symbol, tf, family, period, before_relation or "NONE", relation,
                            )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_price_state_seq}"

                        self.ma_price_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "price_value": price_value if math.isfinite(price_value) else None,
                            "ma_value": ma_value if math.isfinite(ma_value) else None,
                        }

                    for family, period in sorted(slope_reqs):
                        ma_col = f"{family.lower()}_{int(period)}"
                        try:
                            current_value = float(row.get(ma_col))
                            two_bars_value = float(two_bars_ago.get(ma_col)) if two_bars_ago is not None else float("nan")
                        except (TypeError, ValueError):
                            current_value = two_bars_value = float("nan")

                        if not (math.isfinite(current_value) and math.isfinite(two_bars_value)):
                            relation = "UNKNOWN"
                        elif current_value > two_bars_value:
                            relation = "UP"
                        elif current_value < two_bars_value:
                            relation = "DOWN"
                        else:
                            relation = "FLAT"

                        key = (symbol, tf, family, int(period))
                        before = self.ma_slope_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_slope_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_slope_state_seq}"
                            logging.info(
                                "📐 [Composer MA방향] %s %s %s%d 현재/2봉전 | %s → %s",
                                symbol, tf, family, period, before_relation or "NONE", relation,
                            )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_slope_state_seq}"

                        self.ma_slope_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "current_value": current_value if math.isfinite(current_value) else None,
                            "two_bars_ago_value": two_bars_value if math.isfinite(two_bars_value) else None,
                        }


    # -------------------------------
    # timed trigger-chain engine
    # -------------------------------
    @staticmethod
    def _duration_matches(text: str) -> list[tuple[int, int, float]]:
        return CommandInterpreter.duration_matches(text)

    def _parse_start_at(self, text: str) -> Optional[dict]:
        return self.command_interpreter.parse_start_at(text)

    @staticmethod
    def _overlaps_span(start: int, end: int, spans: Iterable[tuple[int, int, object]]) -> bool:
        return CommandInterpreter.overlaps_span(start, end, spans)

    def _trigger_tf_before(
        self,
        text: str,
        pos: int,
        duration_spans: list[tuple[int, int, float]],
        fallback: str = "",
    ) -> str:
        occ = [
            x for x in self._tf_occurrences(text)
            if x[1] <= pos and not self._overlaps_span(x[0], x[1], duration_spans)
        ]
        tf = occ[-1][2] if occ else normalize_tf(fallback)
        if not tf:
            tf = self._command_default_tf()
        return tf

    def _parse_chain_triggers(
        self,
        text: str,
        end_pos: int,
        durations: list[tuple[int, int, float]],
    ) -> list[tuple[int, int, ChainTriggerSpec]]:
        low = str(text or "").lower()
        candidates: list[tuple[int, int, ChainTriggerSpec]] = []

        # EMA/HMA Cross. family 생략 시 EMA이며, 자연어 family 별칭은 command_aliases.json에서 먼저 canonical로 정규화됩니다.
        # 예: EMA50/200 골크, 50 200지수 골크나면, 지수 50 200 골크.
        family_alias = r"(?:ema|hma|지수이평|지수이동평균|지수평균|헐이평|헐이동평균)"
        ma_re = re.compile(
            rf"(?:(?P<prefix>{family_alias})\s*)?"
            r"(?P<fast>\d{1,4})\s*(?:/|,|·|와|과|및|\s)+\s*(?P<slow>\d{1,4})\s*"
            rf"(?:(?P<suffix>{family_alias})\s*)?"
            r"(?P<cross>골크|데크|크로스)",
            re.I,
        )

        def _ma_family(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            token = re.sub(r"\s+", "", str(value).lower())
            if token in {"hma", "헐이평", "헐이동평균"}:
                return "HMA"
            if token in {"ema", "지수이평", "지수이동평균", "지수평균"}:
                return "EMA"
            return None

        previous_tf = ""
        for m in ma_re.finditer(low, 0, end_pos):
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            word = re.sub(r"\s+", "", m.group("cross").lower())
            direction = "LONG" if word == "골크" else "SHORT" if word == "데크" else None
            prefix_family = _ma_family(m.group("prefix"))
            suffix_family = _ma_family(m.group("suffix"))
            if prefix_family and suffix_family and prefix_family != suffix_family:
                raise ValueError("MA family 표현이 서로 충돌합니다")
            family = prefix_family or suffix_family or "EMA"
            trig = ChainTriggerSpec(
                watch_type=f"{family}_CROSS", tf=tf, direction=direction, ma_family=family,
                fast_period=int(m.group("fast")), slow_period=int(m.group("slow")),
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 이평 family/period를 전혀 쓰지 않고 "골크/데크 나면 알려줘"라고 하면
        # 시스템 공통 기본값인 EMA50/200 Cross로 해석합니다.
        cross_only_re = re.compile(
            r"골크|데크|크로스",
            re.I,
        )
        existing_ma_spans = [(a, b) for a, b, t in candidates if t.watch_type in {"EMA_CROSS", "HMA_CROSS"}]
        for m in cross_only_re.finditer(low, 0, end_pos):
            if any(m.start() < b and m.end() > a for a, b in existing_ma_spans):
                continue
            prefix = low[max(0, m.start() - 32):m.start()]
            # 시간봉 숫자는 MA period가 아닙니다. TF 표현을 지운 뒤 남는 숫자/MA family가 있을 때만
            # 불완전한 명시로 보고 기본값 적용을 막습니다.
            ma_probe = re.sub(r"\d+\s*(?:분|시간|일|m|h|d)", " ", prefix, flags=re.I)
            if re.search(r"\d", ma_probe) or re.search(family_alias, ma_probe, re.I):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            word = re.sub(r"\s+", "", m.group(0).lower())
            direction = "LONG" if word == "골크" else "SHORT" if word == "데크" else None
            default_family = str(self._command_default("ma_family", "EMA")).upper()
            default_fast = int(self._command_default("ma_fast", 50))
            default_slow = int(self._command_default("ma_slow", 200))
            trig = ChainTriggerSpec(
                watch_type=f"{default_family}_CROSS", tf=tf, direction=direction, ma_family=default_family,
                fast_period=default_fast, slow_period=default_slow,
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # FVG는 '생성/신규' 의미가 명시된 경우에만 FVG_NEW로 취급합니다.
        # command_aliases.json이 정상 로드되면 '신규'는 '생성'으로 정규화되지만,
        # 외부 alias 파일 누락/지연 시에도 같은 명령이 동작하도록 파서에서도 직접 허용합니다.
        # 기존 FVG=터치 의미는 건드리지 않습니다.
        for m in re.compile(r"fvg", re.I).finditer(low, 0, end_pos):
            local = low[max(0, m.start() - 16):min(end_pos, m.end() + 18)]
            if not any(word in local for word in ("생성", "신규")):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            direction = "LONG" if "상승" in local else "SHORT" if "하락" in local else None
            trig = ChainTriggerSpec("FVG_NEW", tf, direction=direction)
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 사전에 정의된 복합조건 매크로. 김매니저는 이름의 의미를 알지 않고
        # primitive ConditionSpec 조합만 보존/판정합니다.
        close_words = ("봉마감", "봉 마감", "확정봉", "봉확정", "봉 확정")
        for macro_hit in self._condition_macro_matches(low[:end_pos]):
            start, end = int(macro_hit["start"]), int(macro_hit["end"])
            tf = self._trigger_tf_before(low, start, durations, previous_tf)
            direction = self._macro_direction_near(low, start, end)
            close_local = low[max(0, start - 8):min(end_pos, end + 34)]
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            expanded = self._expand_condition_macro(
                str(macro_hit["name"]), tf, direction or "AUTO"
            )
            trig = make_chain_trigger(
                "COMPOUND_CONDITION", tf,
                evaluation_mode=evaluation_mode,
                direction=direction,
                condition_name=str(expanded["name"]),
                condition_combination=str(expanded.get("combination") or "ALL"),
                condition_specs=tuple(dict(x) for x in expanded.get("conditions") or ()),
            )
            trig.validate()
            candidates.append((start, end, trig))
            previous_tf = tf

        # STAFF MA 자연어도 공용 COMPOUND_CONDITION 계약으로 변환합니다.
        # LIVE/CLOSE는 조건 종류와 분리하며, 봉마감 표현이 없으면 기본 LIVE입니다.
        for hit in self._ma_watch_condition_matches(low[:end_pos]):
            start, end = int(hit["start"]), int(hit["end"])
            tf = self._trigger_tf_before(low, start, durations, previous_tf)
            close_local = low[max(0, start - 16):min(end_pos, end + 36)]
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            descriptor = dict(hit["descriptor"])
            descriptor["tf"] = tf
            trig = make_chain_trigger(
                "COMPOUND_CONDITION", tf, evaluation_mode=evaluation_mode,
                direction=str(hit.get("direction") or "") or None,
                condition_name=str(hit["name"]),
                condition_combination="ALL",
                condition_specs=(descriptor,),
            )
            trig.validate()
            candidates.append((start, end, trig))
            previous_tf = tf

        # 원비는 알림/연쇄 문맥에서 그 자체가 밴드 터치를 뜻합니다.
        # "하단 원비 알려줘"와 "하단 원비 터치 알려줘"를 동일하게 처리합니다.
        for m in re.compile(r"원비", re.I).finditer(low, 0, end_pos):
            side_local = low[max(0, m.start() - 14):min(end_pos, m.end() + 8)]
            close_local = low[max(0, m.start() - 8):min(end_pos, m.end() + 34)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            if "하단" in side_local:
                level_side, direction = "LOW", "LONG"
            elif "상단" in side_local:
                level_side, direction = "HIGH", "SHORT"
            else:
                level_side, direction = "BOTH", None
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            trig = make_chain_trigger(
                "WONBI_TOUCH", tf, evaluation_mode=evaluation_mode,
                direction=direction, level_side=level_side,
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # Percentile OUT→IN을 OUT보다 먼저 잡습니다.
        occupied: list[tuple[int, int]] = []
        out_in_re = re.compile(r"(?:아웃|out)\s*(?:→|->|-)?\s*(?:인|in)", re.I)
        for m in out_in_re.finditer(low, 0, end_pos):
            local = low[max(0, m.start() - 12):min(end_pos, m.end() + 12)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            side = "LOW" if "하단" in local else "HIGH" if "상단" in local else "BOTH"
            direction = "LONG" if side == "LOW" else "SHORT" if side == "HIGH" else None
            trig = ChainTriggerSpec("PERCENTILE_OUT_IN", tf, direction=direction, level_side=side)
            trig.validate()
            candidates.append((m.start(), m.end(), trig)); occupied.append((m.start(), m.end())); previous_tf = tf
        for m in re.compile(r"아웃|out", re.I).finditer(low, 0, end_pos):
            if any(m.start() < b and m.end() > a for a, b in occupied):
                continue
            local = low[max(0, m.start() - 12):min(end_pos, m.end() + 12)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            side = "LOW" if "하단" in local else "HIGH" if "상단" in local else "BOTH"
            direction = "LONG" if side == "LOW" else "SHORT" if side == "HIGH" else None
            trig = ChainTriggerSpec("PERCENTILE_OUT", tf, direction=direction, level_side=side)
            trig.validate()
            candidates.append((m.start(), m.end(), trig)); previous_tf = tf

        # 전일 고가/저가 터치.
        for aliases, side, direction in (
            (("전일고가",), "HIGH", "SHORT"),
            (("전일저가",), "LOW", "LONG"),
        ):
            pos = self._keyword_pos(low[:end_pos], aliases)
            if pos >= 0:
                tail = low[pos:min(end_pos, pos + 30)]
                if "터치" in tail:
                    tf = self._trigger_tf_before(low, pos, durations, previous_tf)
                    trig = ChainTriggerSpec("PREV_DAY_TOUCH", tf, direction=direction, level_side=side)
                    trig.validate(); candidates.append((pos, pos + 4, trig)); previous_tf = tf

        # 단독 봉마감 이벤트. 이미 CLOSE 복합조건이 같은 봉마감 표현을 소비했다면 BAR를 중복 생성하지 않습니다.
        close_re = re.compile(r"봉\s*마감|확정봉|봉\s*확정", re.I)
        for m in close_re.finditer(low, 0, end_pos):
            context = low[max(0, m.start() - 36):m.start()]
            if "원비" in context or self._condition_macro_matches(context):
                continue
            if any(
                trigger_watch_contract(existing) == ("COMPOUND_CONDITION", "CLOSE")
                and start - 16 <= m.start()
                and m.end() <= end + 36
                for start, end, existing in candidates
            ):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            trig = make_chain_trigger("BAR", tf, evaluation_mode="CLOSE")
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 동일 구간이 중복 해석되는 것을 막고 문장 순으로 정렬합니다.
        candidates.sort(key=lambda x: (x[0], x[1]))
        dedup: list[tuple[int, int, ChainTriggerSpec]] = []
        seen = set()
        for start, end, trig in candidates:
            condition_type, evaluation_mode = trigger_watch_contract(trig)
            key = (start, condition_type, evaluation_mode, trig.tf, trig.direction, trig.level_side, trig.fast_period, trig.slow_period)
            if key in seen:
                continue
            seen.add(key)
            dedup.append((start, end, trig))
        return dedup

    def _explicit_oz_tfs_for_chain(
        self,
        text: str,
        zone_start: int,
        oz_pos: int,
        durations: list[tuple[int, int, float]],
    ) -> tuple[str, ...]:
        low = str(text or "").lower()
        zone = low[zone_start:oz_pos]
        if "모든프레임" in zone:
            return OZ_BASE_TFS
        found = []
        for start, end, tf in self._tf_occurrences(low):
            if start < zone_start or end > oz_pos or tf not in OZ_BASE_TFS:
                continue
            if self._overlaps_span(start, end, durations):
                continue
            found.append(tf)
        return tuple(dict.fromkeys(found))

    def _parse_scheduled_generic_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        """절대 시작시각이 붙은 단일 Generic Watch를 예약 Chain으로 변환합니다."""
        clean = str(text or "").strip()
        low = clean.lower()
        if "올존" in low or "알려" not in low:
            return None
        start_info = self._parse_start_at(clean)
        if start_info is None:
            return None
        durations = self._duration_matches(clean)
        triggers_raw = self._parse_chain_triggers(clean, len(clean), durations)
        if len(triggers_raw) != 1:
            return None
        trig = triggers_raw[0][2]
        trig.validate()
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        chain = TimedChainSpec(
            chain_id=f"SCHEDULE:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id,
            symbol=symbol,
            triggers=(trig,),
            final_action="NOTIFY",
            final_watch_persistent=False,
            start_at=float(start_info["timestamp"]),
            started=False,
        )
        chain.validate()
        return chain

    def _parse_oz_watch_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        """OZ→OZ 직렬 Watch 또는 절대시각 예약 OZ를 해석합니다.

        예:
          - 15분 무지성 브레이커 올존에서 1분 올존 알려줘
          - 오후 2시부터 15분 무지성 브레이커 올존 감시해

        각 `올존` 앞의 TF/무지성/브레이커/방향은 그 단계에만 귀속합니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        oz_matches = list(re.finditer(r"올존", low, re.I))
        start_info = self._parse_start_at(clean)
        if len(oz_matches) < 2 and start_info is None:
            return None
        if not oz_matches:
            return None
        if "알려" not in low and "계속" not in low:
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        durations = self._duration_matches(clean)
        occurrences = self._tf_occurrences(clean)

        def _tf_for_segment(seg_start: int, oz_start: int) -> str:
            found = [
                tf for a, b, tf in occurrences
                if a >= seg_start and b <= oz_start
                and not self._overlaps_span(a, b, durations)
            ]
            tf = found[-1] if found else self._command_default_tf()
            if tf not in OZ_BASE_TFS:
                raise ValueError(
                    f"지원하지 않는 올존 시간봉입니다: {tf} "
                    f"(지원: {','.join(OZ_BASE_TFS)})"
                )
            return tf

        def _profile(segment: str) -> tuple[Optional[str], str, str]:
            direction = self._parse_oz_direction(segment)
            vm = "BLIND" if "무지성" in segment.lower() else "NORMAL"
            tm = _oz_trigger_mode(segment)
            return direction, vm, tm

        triggers: list[ChainTriggerSpec] = []
        prev_end = 0
        for m in oz_matches[:-1]:
            segment = clean[prev_end:m.end()]
            tf = _tf_for_segment(prev_end, m.start())
            direction, vm, tm = _profile(segment)
            trig = ChainTriggerSpec(
                "OZ_ALERT", tf, direction=direction,
                validation_mode=vm, trigger_mode=tm,
            )
            trig.validate()
            triggers.append(trig)
            prev_end = m.end()

        final_m = oz_matches[-1]
        final_segment = clean[prev_end:final_m.end()]
        final_tf = _tf_for_segment(prev_end, final_m.start())
        direction, validation_mode, trigger_mode = _profile(final_segment)
        if direction is None and triggers and triggers[-1].direction in {"LONG", "SHORT"}:
            direction = triggers[-1].direction

        # 마지막 OZ 앞에 명시된 `N분/시간 동안`은 최종 Watch의 유효시간입니다.
        final_durations = [
            sec for a, b, sec in durations
            if a >= prev_end and b <= final_m.start()
        ]
        final_window_sec = float(final_durations[-1]) if final_durations else None
        final_watch_persistent = bool(final_window_sec is not None or "계속" in low)

        chain = TimedChainSpec(
            chain_id=f"OZCHAIN:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id,
            symbol=symbol,
            triggers=tuple(triggers),
            final_action="OZ",
            final_window_sec=final_window_sec,
            oz_tfs=(final_tf,),
            direction=direction,
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
            final_watch_persistent=final_watch_persistent,
            start_at=(float(start_info["timestamp"]) if start_info else None),
            started=(start_info is None),
        )
        chain.validate()
        return chain

    def _parse_timed_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        clean = str(text or "").strip()
        low = clean.lower()
        is_unordered = any(x in low for x in (
            "순서무관", "순서 무관", "순서상관없이", "순서 상관없이", "아무순서", "아무 순서",
        ))

        # 범용 CANCEL_ON shorthand. `역크로스나면 패스/취소`는 본 조건으로 파싱하지 않고
        # 첫 방향성 MA Cross의 정확한 반대 Cross를 무효화 조건으로 자동 생성합니다.
        cancel_re = re.compile(
            r"(?:(?:역\s*크로스)|(?:반대\s*(?:크로스|골크|데크)))"
            r"(?:\s*(?:가|이))?\s*(?:나오면|나면|발생하면|뜨면|오면|면)?\s*"
            r"(?:패스|취소|무효|리셋)",
            re.I,
        )
        cancel_matches = list(cancel_re.finditer(clean))
        has_opposite_cross_cancel = bool(cancel_matches)
        parse_clean_chars = list(clean)
        for mm in cancel_matches:
            for pos in range(mm.start(), mm.end()):
                parse_clean_chars[pos] = " "
        parse_clean = "".join(parse_clean_chars)

        durations = self._duration_matches(clean)
        oz_pos = low.rfind("올존")
        parse_end = oz_pos if oz_pos >= 0 else len(low)
        triggers_raw = self._parse_chain_triggers(parse_clean, parse_end, durations)
        now_mode = "지금부터" in low

        # duration 없이도 단독 COMPOUND_CONDITION 알림은 1단계 SEQUENTIAL Watch로 처리합니다.
        # OZ 문장에서는 CLOSE 조건만 이 경로를 사용해 봉마감 의미를 보존하고,
        # 일반 LIVE OZ 조건은 기존 StrategySpec 경로를 그대로 사용합니다.
        single_compound = False
        single_compound_mode = ""
        if len(triggers_raw) == 1:
            condition_type, single_compound_mode = trigger_watch_contract(triggers_raw[0][2])
            single_compound = condition_type == "COMPOUND_CONDITION"
        direct_compound_notify = bool(
            not durations and single_compound and oz_pos < 0 and "알려" in low
        )
        direct_compound_close_oz = bool(
            not durations and single_compound and oz_pos >= 0 and single_compound_mode == "CLOSE"
        )
        direct_compound = direct_compound_notify or direct_compound_close_oz

        if not durations and not is_unordered and not has_opposite_cross_cancel and not direct_compound:
            return None

        # FILTER는 각 이벤트 자체에 독립 유효시간을 부여합니다.
        # 기존 SEQUENTIAL/UNORDERED 문법을 바꾸지 않기 위해 봉마감/복합조건 이벤트가 있거나,
        # 논리 연결어(그리고/혹은/또는)와 모든 조건별 duration이 명시된 경우에만 진입합니다.
        post_duration_by_index: dict[int, float] = {}
        for idx, (_start, end, _trig) in enumerate(triggers_raw):
            boundary = triggers_raw[idx + 1][0] if idx < len(triggers_raw) - 1 else parse_end
            found = [sec for a, b, sec in durations if a >= end and b <= boundary]
            if found:
                post_duration_by_index[idx] = float(found[-1])
        # 기존 FILTER 의미는 유지하되 legacy suffix 대신 condition + evaluation_mode로 판정합니다.
        # 복합조건은 LIVE/CLOSE 모두 FILTER, BAR는 CLOSE 이벤트, WONBI는 CLOSE일 때만 FILTER입니다.
        has_filter_event = any(
            is_filter_watch_trigger(trig)
            for _a, _b, trig in triggers_raw
        )
        has_logic_connector = bool(re.search(r"그리고|혹은|또는|\bor\b", low[:parse_end], re.I))
        all_have_validity = bool(triggers_raw) and len(post_duration_by_index) == len(triggers_raw)
        is_filter = (not is_unordered) and (not direct_compound) and (
            has_filter_event or (has_logic_connector and all_have_validity)
        )

        # 시간연쇄/순서무관/유효시간 필터로 볼 만한 구조가 아니면 기존 개인전략 파서에게 넘깁니다.
        if is_unordered:
            if len(triggers_raw) < 2:
                return None
        elif is_filter:
            if not triggers_raw:
                return None
            missing_validity = [idx for idx in range(len(triggers_raw)) if idx not in post_duration_by_index]
            if missing_validity:
                raise ValueError("조건 유효시간 필터는 각 조건 뒤에 'N분/시간 동안'을 지정해 주세요")
        elif not triggers_raw and not (now_mode and oz_pos >= 0):
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        # SEQUENTIAL: 다음 조건까지 deadline / UNORDERED: 공통 latch 창 / FILTER: 조건별 독립 유효시간.
        triggers: list[ChainTriggerSpec] = []
        unordered_windows: list[float] = []
        for idx, (start, end, trig) in enumerate(triggers_raw):
            if is_filter:
                trig.valid_sec = post_duration_by_index.get(idx)
            elif idx < len(triggers_raw) - 1:
                next_start = triggers_raw[idx + 1][0]
                between = [sec for a, b, sec in durations if a >= end and b <= next_start]
                if between:
                    value = float(between[-1])
                    if is_unordered:
                        unordered_windows.append(value)
                    else:
                        trig.next_window_sec = value
            trig.validate()
            triggers.append(trig)

        filter_groups: tuple[tuple[int, ...], ...] = ()
        if is_filter:
            groups: list[list[int]] = [[0]]
            for idx in range(1, len(triggers_raw)):
                prev_end = triggers_raw[idx - 1][1]
                current_start = triggers_raw[idx][0]
                gap = low[prev_end:current_start]
                if re.search(r"혹은|또는|\bor\b", gap, re.I):
                    groups[-1].append(idx)
                else:
                    groups.append([idx])
            filter_groups = tuple(tuple(group) for group in groups)

        unordered_window_sec: Optional[float] = None
        if is_unordered and unordered_windows:
            unique_windows = {round(x, 9) for x in unordered_windows}
            if len(unique_windows) > 1:
                raise ValueError("순서무관 조건 유효시간은 하나의 값으로 지정해 주세요")
            unordered_window_sec = float(unordered_windows[-1])

        required_count: Optional[int] = None
        if is_unordered:
            # 예: '3개 중 2개 순서무관'. 표기가 없으면 ALL=N-of-N입니다.
            kofn = re.search(r"(\d+)\s*개\s*(?:조건\s*)?중(?:에서)?\s*(\d+)\s*개", low)
            if kofn:
                declared_total = int(kofn.group(1))
                required_count = int(kofn.group(2))
                if declared_total != len(triggers):
                    raise ValueError(
                        f"순서무관 조건 개수 불일치: 문장={declared_total}개, 인식={len(triggers)}개"
                    )
            else:
                required_count = len(triggers)
            if required_count <= 0 or required_count > len(triggers):
                raise ValueError(f"순서무관 K-of-N 오류: {required_count}/{len(triggers)}")

        final_action = "OZ" if oz_pos >= 0 else "NOTIFY"
        final_window_sec = None
        oz_tfs: tuple[str, ...] = ()
        direction: Optional[str] = None
        validation_mode = "BLIND" if "무지성" in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        final_watch_persistent = True

        if final_action == "OZ":
            last_end = triggers_raw[-1][1] if triggers_raw else 0
            if not is_filter:
                final_durations = [sec for a, b, sec in durations if a >= last_end and b <= oz_pos]
                if not final_durations and (now_mode or not is_unordered):
                    # 기존 SEQUENTIAL 호환 및 NOW 문장만 전체 구간의 마지막 duration을 final window로 사용합니다.
                    final_durations = [sec for a, b, sec in durations if b <= oz_pos]
                if final_durations:
                    final_window_sec = float(final_durations[-1])
                elif not is_unordered and not has_opposite_cross_cancel and not direct_compound_close_oz:
                    return None

            explicit = self._explicit_oz_tfs_for_chain(clean, last_end, oz_pos, durations)
            if explicit:
                oz_tfs = explicit
            elif triggers:
                oz_tfs = (triggers[-1].tf,) if triggers[-1].tf in OZ_BASE_TFS else ()
            if not oz_tfs:
                default_oz = normalize_tf(self.config.get("CHAIN_DEFAULT_OZ_TF")) or "1m"
                oz_tfs = (default_oz,) if default_oz in OZ_BASE_TFS else ("1m",)

            final_zone = low[last_end:]
            direction = self._parse_oz_direction(final_zone)
            if direction is None:
                direction = "LONG" if "매수" in final_zone else "SHORT" if "매도" in final_zone else None
            if direction is None and triggers:
                direction = self._infer_trigger_default_direction(triggers)

            # 기간을 명시한 기존 시간연쇄는 기간 동안 반복 감시를 유지합니다.
            # 기간 없는 순서무관 '알려줘'는 직접 OZ와 동일하게 1회성이며 '계속'만 지속입니다.
            if is_filter:
                # FILTER의 최종 OZ는 각 조건의 남은 겹침시간 동안 유지합니다.
                final_watch_persistent = True
            elif (is_unordered or has_opposite_cross_cancel or direct_compound_close_oz) and final_window_sec is None:
                final_watch_persistent = "계속" in low
        else:
            if direct_compound_notify:
                final_watch_persistent = "계속" in low
            elif is_filter:
                if not triggers or "알려" not in low:
                    return None
            elif is_unordered:
                if len(triggers) < 2 or "알려" not in low:
                    return None
            else:
                # 마지막 트리거 자체를 알리는 형태는 최소 2단계 + 앞 단계 시간창이 있을 때만 시간연쇄로 처리합니다.
                if len(triggers) < 2 or not any(t.next_window_sec for t in triggers[:-1]):
                    return None
                if "알려" not in low:
                    return None

        invalidation_triggers: tuple[ChainTriggerSpec, ...] = ()
        if has_opposite_cross_cancel:
            source_cross = next((
                trig for trig in triggers
                if trig.watch_type in {"EMA_CROSS", "HMA_CROSS"} and trig.direction in {"LONG", "SHORT"}
            ), None)
            if source_cross is None:
                raise ValueError("역크로스 취소에는 방향이 명확한 EMA/HMA 골크·데크 조건이 필요합니다")
            opposite = "SHORT" if source_cross.direction == "LONG" else "LONG"
            cancel_trig = ChainTriggerSpec(
                watch_type=source_cross.watch_type, tf=source_cross.tf, direction=opposite,
                ma_family=source_cross.ma_family, fast_period=source_cross.fast_period,
                slow_period=source_cross.slow_period,
            )
            cancel_trig.validate()
            invalidation_triggers = (cancel_trig,)

        chain = TimedChainSpec(
            chain_id=f"CHAIN:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id, symbol=symbol, triggers=tuple(triggers),
            final_action=final_action, final_window_sec=final_window_sec, oz_tfs=oz_tfs,
            direction=direction, validation_mode=validation_mode, trigger_mode=trigger_mode,
            order_mode="FILTER" if is_filter else "UNORDERED" if is_unordered else "SEQUENTIAL",
            required_count=required_count, unordered_window_sec=unordered_window_sec,
            filter_groups=filter_groups, invalidation_triggers=invalidation_triggers,
            final_watch_persistent=final_watch_persistent,
            repeat_filter=(is_filter and "계속" in low),
        )
        chain.validate()
        return chain

    def _chain_trigger_payload_locked(self, chain: TimedChainSpec) -> Optional[dict]:
        return self.watch_orchestrator.trigger_payload_locked(chain)

    def _chain_trigger_payloads_locked(self, chain: TimedChainSpec) -> list[dict]:
        return self.watch_orchestrator.trigger_payloads_locked(chain)

    def _chain_final_payload_locked(self, chain: TimedChainSpec) -> Optional[dict]:
        return self.watch_orchestrator.final_payload_locked(chain)

    def _add_timed_chain(self, chain: TimedChainSpec) -> None:
        chain.validate()
        self._remember_watch_command(chain.chain_id, chain.owner_chat_id, "TIMED_CHAIN", "TIMED_CHAIN")
        self.watch_orchestrator.add_chain(chain)

    @staticmethod
    def _config_chain_event_ts(event: dict, fallback: float) -> float:
        for key in ("completion_time", "event_time", "trigger_time"):
            value = event.get(key)
            if value in {None, ""}:
                continue
            try:
                if isinstance(value, (int, float)):
                    ts = float(value)
                    if math.isfinite(ts):
                        return ts
                ts = float(pd.Timestamp(value).timestamp())
                if math.isfinite(ts):
                    return ts
            except Exception:
                continue
        return float(fallback)

    @staticmethod
    def _config_chain_completion_deadline(item: dict) -> float:
        try:
            limits = [float(item[key]) for key in ('expires_at', 'completion_deadline')
                      if item.get(key) is not None]
        except (TypeError, ValueError):
            return float('-inf')
        return min(limits) if limits and all(math.isfinite(x) for x in limits) else float('-inf')

    def _filter_config_chain_deadline_event(self, event: dict) -> tuple[dict, bool]:
        ids = list(event.get('watch_ids') or [])
        if event.get('watch_id') and event['watch_id'] not in ids:
            ids.append(event['watch_id'])
        completed = self._config_chain_event_ts(event, float('nan'))
        rejected = set()
        with self._lock:
            for wid in ids:
                if not str(wid).startswith('OZARM:CFGCHAIN'):
                    continue
                item = self._config_chain_active.get(wid)
                if (item is None or not math.isfinite(completed)
                        or completed > self._config_chain_completion_deadline(item)):
                    rejected.add(wid)
        if not rejected:
            return event, False
        remaining = [wid for wid in ids if wid not in rejected]
        event = dict(event, watch_ids=remaining)
        if event.get('watch_id') in rejected:
            event.pop('watch_id')
        return event, not remaining

    @staticmethod
    def _config_chain_event_direction(event: dict) -> Optional[str]:
        direction = str(event.get("direction") or event.get("signal_direction") or "").upper()
        if direction in {"LONG", "SHORT"}:
            return direction
        text = str(event.get("message") or event.get("trigger_name") or "").lower()
        compact = re.sub(r"\s+", "", text)
        if any(x in compact for x in ("골크", "골든크로스", "goldencross")):
            return "LONG"
        if any(x in compact for x in ("데크", "데드크로스", "deadcross")):
            return "SHORT"
        return None

    @staticmethod
    def _opposite_direction(direction: str) -> Optional[str]:
        direction = str(direction or "").upper()
        if direction == "LONG":
            return "SHORT"
        if direction == "SHORT":
            return "LONG"
        return None

    def _config_chain_direction_active_locked(
        self, spec: ConfigTimedChainSpec, state: dict, direction: str
    ) -> bool:
        """공식 TIMED_CHAIN에서 특정 방향의 현재 후보/최종 OZ가 살아 있는지 확인합니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False

        if spec.order_mode == "UNORDERED":
            latch = state.get("unordered_latch") if isinstance(state, dict) else None
            groups = latch.get("groups") if isinstance(latch, dict) else {}
            hits = groups.get(direction) if isinstance(groups, dict) else None
            if isinstance(hits, dict) and bool(hits):
                return True
        else:
            cross = state.get("latest_cross") if isinstance(state, dict) else None
            if int(state.get("stage", 0) or 0) == 1 and isinstance(cross, dict):
                if str(cross.get("direction") or "").upper() == direction:
                    return True

        now = time.time()
        post_touch = state.get("post_touch_armed") if isinstance(state, dict) else None
        if isinstance(post_touch, dict) and str(post_touch.get("direction") or "").upper() == direction:
            try:
                if now < float(post_touch.get("expires_at") or 0.0):
                    return True
            except (TypeError, ValueError):
                pass

        for item in self._config_chain_active.values():
            if str(item.get("spec_id") or "") != spec.spec_id:
                continue
            if str(item.get("direction") or "").upper() != direction:
                continue
            try:
                if now < float(item.get("expires_at") or float("inf")):
                    return True
            except (TypeError, ValueError):
                return True
        return False

    def _cancel_config_chain_direction_locked(
        self, spec: ConfigTimedChainSpec, state: dict, direction: str, reason: str, pushes: list[dict]
    ) -> bool:
        """현재 공식 TIMED_CHAIN의 한 방향 사이클만 폐기하고 전략 등록 자체는 유지합니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False
        changed = False

        if spec.order_mode == "UNORDERED":
            latch = state.get("unordered_latch")
            if isinstance(latch, dict) and UnorderedConditionLatch.clear_group(latch, direction):
                changed = True
        else:
            cross = state.get("latest_cross")
            if (
                int(state.get("stage", 0) or 0) == 1
                and isinstance(cross, dict)
                and str(cross.get("direction") or "").upper() == direction
            ):
                state["stage"] = 0
                state["latest_cross"] = None
                state["latest_fvg"] = None
                state["stage_deadline"] = None
                changed = True

        post_touch = state.get("post_touch_armed")
        if isinstance(post_touch, dict) and str(post_touch.get("direction") or "").upper() == direction:
            state["post_touch_armed"] = None
            changed = True

        for child_id, item in list(self._config_chain_active.items()):
            if str(item.get("spec_id") or "") != spec.spec_id:
                continue
            if str(item.get("direction") or "").upper() != direction:
                continue
            pushes.append({
                "action": "CANCEL_MANUAL", "watch_id": child_id,
                "request_chat_id": None,
                "validation_mode": item.get("validation_mode"),
                "trigger_mode": item.get("trigger_mode"),
            })
            self._config_chain_active.pop(child_id, None)
            changed = True

        if changed:
            state["spec_signature"] = self._config_chain_spec_signature(spec)
            self._save_config_chain_state_locked()
            logging.info(
                "↩️ [Composer SPECIAL TIMED_CHAIN] CANCEL_ON | %s | %s 취소 | %s",
                spec.name, direction, reason,
            )
        return changed

    def _maybe_cancel_config_chain_on_opposite_locked(
        self, spec: ConfigTimedChainSpec, state: dict, *, condition_key: str, tf: str,
        event_direction: str, pushes: list[dict]
    ) -> Optional[str]:
        """반대 Cross/FVG가 현재 반대방향 사이클을 무효화하면 취소 사유를 반환합니다."""
        event_direction = str(event_direction or "").upper()
        if event_direction not in {"LONG", "SHORT"}:
            return None
        condition_key = str(condition_key or "").upper()
        tf = normalize_tf(tf)

        if condition_key == "CROSS":
            if not spec.cancel_on_opposite_cross:
                return None
            reason = f"반대 {spec.cross_tf} {spec.ma_family}{spec.fast_period}/{spec.slow_period} 크로스"
        elif condition_key == "FVG":
            if tf not in spec.cancel_on_opposite_fvg_tfs:
                return None
            reason = f"반대 {tf} 신규 FVG"
        else:
            return None

        target_direction = self._opposite_direction(event_direction)
        if target_direction is None or not self._config_chain_direction_active_locked(spec, state, target_direction):
            return None
        if self._cancel_config_chain_direction_locked(spec, state, target_direction, reason, pushes):
            return "cancel_on_opposite_cross" if condition_key == "CROSS" else "cancel_on_opposite_fvg"
        return None

    def _prune_config_chain_unordered_bars_locked(
        self, spec: ConfigTimedChainSpec, latch_state: dict, bar_times: tuple[float, ...]
    ) -> bool:
        """UNORDERED hit를 CROSS_TF 실제 확정봉 수 기준으로 독립 만료합니다."""
        if spec.max_gap_bars <= 0 or not bar_times:
            return False

        def _expired(_group: str, _key: str, hit: dict) -> bool:
            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
            anchor = meta.get("bar_anchor_ts")
            age = self._config_chain_bar_age_from_times(anchor, bar_times)
            # bar data를 확보한 상태에서 anchor가 손상된 hit는 안전하게 폐기합니다.
            return age is None or int(age) > int(spec.max_gap_bars)

        return UnorderedConditionLatch.remove_where(latch_state, _expired)

    def _handle_config_chain_unordered_trigger(self, event: dict) -> dict:
        """공식 TIMED_CHAIN의 CROSS/FVG 순서무관 누적 경로."""
        chain_id = str(event.get("chain_id") or "").strip()
        spec_id = chain_id.split(":", 1)[1] if chain_id.startswith("CFGCHAIN:") else ""
        watch_id = str(event.get("watch_id") or "").strip()
        now = time.time()
        event_ts = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(event_ts):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        pushes: list[dict] = []

        with self._lock:
            pre_spec = self.official_chain_specs.get(spec_id)
        fresh_bar_times: tuple[float, ...] = ()
        if (
            pre_spec is not None and pre_spec.enabled
            and pre_spec.order_mode == "UNORDERED" and pre_spec.max_gap_bars > 0
        ):
            fresh_bar_times = self._config_chain_closed_bar_times(pre_spec, self._config_chain_event_staff)

        with self._lock:
            spec = self.official_chain_specs.get(spec_id)
            state = self._config_chain_state.get(spec_id)
            if spec is None or state is None or not spec.enabled or spec.order_mode != "UNORDERED":
                return {"ok": True, "delivered": False, "ignored": True}

            if pre_spec is None or (
                pre_spec.symbol != spec.symbol or pre_spec.cross_tf != spec.cross_tf
                or pre_spec.max_gap_bars != spec.max_gap_bars
                or pre_spec.order_mode != spec.order_mode
            ):
                fresh_bar_times = ()

            bar_times = fresh_bar_times
            if spec.max_gap_bars > 0 and not bar_times:
                bar_times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())

            trigger_payloads = self._config_chain_trigger_payloads_locked(spec)
            by_id = {str(x["watch_id"]): x for x in trigger_payloads}
            fired_payload = by_id.get(watch_id)
            if fired_payload is None:
                return {"ok": True, "delivered": False, "stale": True}

            incoming_stage = int(fired_payload.get("chain_stage", -1))
            condition_key = "CROSS" if incoming_stage == 0 else "FVG" if incoming_stage > 0 else ""
            if not condition_key:
                return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

            direction = self._config_chain_event_direction(event)
            if direction not in {"LONG", "SHORT"}:
                pushes.append(fired_payload)
                result = {
                    "ok": True, "delivered": False, "ignored": True,
                    "reason": "direction_missing", "chain_id": chain_id,
                }
            else:
                tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
                cancel_reason = self._maybe_cancel_config_chain_on_opposite_locked(
                    spec, state, condition_key=condition_key, tf=tf,
                    event_direction=direction, pushes=pushes,
                )
                if cancel_reason:
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "cancelled": True,
                        "reason": cancel_reason, "cancel_event_direction": direction,
                    }
                    for payload in pushes:
                        self._push(payload)
                    return result

                latch_state = state.get("unordered_latch")
                if not isinstance(latch_state, dict):
                    latch_state = UnorderedConditionLatch.new_state()
                    state["unordered_latch"] = latch_state

                changed = UnorderedConditionLatch.prune(latch_state, spec.max_gap_sec, event_ts)
                if spec.max_gap_bars > 0 and bar_times:
                    changed = self._prune_config_chain_unordered_bars_locked(spec, latch_state, bar_times) or changed

                event_token = str(
                    event.get("event_id") or event.get("trigger_id") or event.get("event_time")
                    or event.get("completion_time") or event.get("bar_time") or event.get("time") or f"{event_ts:.6f}"
                )
                bar_anchor_ts: Optional[float] = None
                if spec.max_gap_bars > 0:
                    if fresh_bar_times:
                        bar_anchor_ts = float(fresh_bar_times[-1])
                    else:
                        # 이벤트 순간 fresh STAFF 조회 실패 시 기존 순서형과 동일한 보수적 fallback을 사용합니다.
                        bar_anchor_ts = float(event_ts) - float(tf_seconds(spec.cross_tf))

                latch_result = UnorderedConditionLatch.register(
                    latch_state,
                    correlation_key=direction,
                    condition_key=condition_key,
                    event_ts=float(event_ts),
                    token=event_token,
                    required_count=2,
                    window_sec=spec.max_gap_sec,
                    now=event_ts,
                    meta={
                        "tf": tf, "watch_id": watch_id,
                        "chain_stage": incoming_stage, "bar_anchor_ts": bar_anchor_ts,
                    },
                )
                changed = True

                # 봉 수 expiry가 켜진 경우 현재 실제 확정봉으로 다시 정리한 뒤 match를 재평가합니다.
                if spec.max_gap_bars > 0 and bar_times:
                    if self._prune_config_chain_unordered_bars_locked(spec, latch_state, bar_times):
                        changed = True
                    groups = latch_state.get("groups") if isinstance(latch_state, dict) else {}
                    group_hits = groups.get(direction, {}) if isinstance(groups, dict) else {}
                    if len(group_hits) < 2:
                        latch_result = dict(latch_result)
                        latch_result["matched"] = False
                        latch_result["count"] = len(group_hits)

                groups = latch_state.get("groups") if isinstance(latch_state, dict) else {}
                group_hits = groups.get(direction, {}) if isinstance(groups, dict) else {}
                cross_hit = group_hits.get("CROSS") if isinstance(group_hits, dict) else None
                fvg_hit = group_hits.get("FVG") if isinstance(group_hits, dict) else None
                matched = bool(latch_result.get("matched")) and isinstance(cross_hit, dict) and isinstance(fvg_hit, dict)

                bar_gap = None
                if matched and spec.max_gap_bars > 0:
                    if not bar_times:
                        matched = False
                    else:
                        anchors = []
                        for hit in (cross_hit, fvg_hit):
                            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
                            anchor = self._config_chain_value_epoch(meta.get("bar_anchor_ts"))
                            if anchor is not None:
                                anchors.append(anchor)
                        if len(anchors) != 2:
                            matched = False
                        else:
                            first_anchor = min(anchors)
                            bar_gap = self._config_chain_bar_age_from_times(first_anchor, bar_times)
                            if bar_gap is None or int(bar_gap) > int(spec.max_gap_bars):
                                matched = False

                if matched:
                    pair_completed = max(float(cross_hit['ts']), float(fvg_hit['ts']))
                    pair_deadline = (min(float(cross_hit['ts']), float(fvg_hit['ts'])) + spec.max_gap_sec
                                     if spec.max_gap_sec > 0 else None)
                    final_deadline = pair_completed + spec.final_window_sec
                    if pair_deadline is not None:
                        final_deadline = min(final_deadline, pair_deadline)
                    signature = stable_id(
                        "CFGPAIR", spec.spec_id, direction, latch_result.get("signature"), length=24,
                    )
                    duplicate = state.get("last_pair_signature") == signature
                    time_allowed = self._time_policy.allows(spec.time_filters)
                    should_advance = not duplicate and time_allowed

                    if should_advance:
                        # 같은 메인 TIMED_CHAIN의 이전 기간제 OZ 감시는 새 setup이 오면 교체합니다.
                        for child_id, item in list(self._config_chain_active.items()):
                            if str(item.get("spec_id") or "") != spec.spec_id:
                                continue
                            pushes.append({
                                "action": "CANCEL_MANUAL", "watch_id": child_id,
                                "request_chat_id": None,
                                "validation_mode": item.get("validation_mode"),
                                "trigger_mode": item.get("trigger_mode"),
                            })
                            self._config_chain_active.pop(child_id, None)

                        state["last_pair_signature"] = signature
                        UnorderedConditionLatch.clear_group(latch_state, direction)
                        if spec.final_fvg_touch_tfs:
                            state["post_touch_armed"] = {
                                "direction": direction,
                                "expires_at": pair_completed + spec.final_window_sec,
                                "completion_deadline": final_deadline,
                                "pair_signature": signature,
                            }
                            self._save_config_chain_state_locked()
                        else:
                            child_id = stable_id("OZARM:CFGCHAIN", spec.spec_id, direction, signature, length=20)
                            child_payload = {
                                "action": "MANUAL_WATCH", "watch_id": child_id,
                                "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                                "direction": direction, "persistent": True,
                                "request_chat_id": None,
                                "validation_mode": spec.validation_mode,
                                "trigger_mode": spec.trigger_mode,
                                "source_spec_id": spec.spec_id,
                                "source_spec_ids": [spec.spec_id],
                                "source_name": spec.name,
                            }
                            active_item = dict(child_payload)
                            active_item.update({
                                "spec_id": spec.spec_id,
                                "expires_at": pair_completed + spec.final_window_sec,
                                "completion_deadline": final_deadline,
                            })
                            self._config_chain_active[child_id] = active_item
                            pushes.append(child_payload)

                        cross_ts = float(cross_hit.get("ts"))
                        fvg_ts = float(fvg_hit.get("ts"))
                        fvg_meta = fvg_hit.get("meta") if isinstance(fvg_hit.get("meta"), dict) else {}
                        logging.info(
                            "⏱️ [Composer SPECIAL TIMED_CHAIN] 순서무관 성립→NEXT | %s | %s | "
                            "CROSS=%s %s%d/%d · FVG=%s | gap=%.1fs%s | %s",
                            spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                            fvg_meta.get("tf") or "-", abs(fvg_ts - cross_ts),
                            "" if bar_gap is None else f"/{bar_gap}봉",
                            (
                                f"{format_duration_ko(spec.final_window_sec)} 안에 "
                                f"{'/'.join(spec.final_fvg_touch_tfs)} 동방향 FVG TOUCH 대기"
                                if spec.final_fvg_touch_tfs
                                else f"{format_duration_ko(spec.final_window_sec)} 동안 OZ"
                            ),
                        )
                        result = {
                            "ok": True, "delivered": False, "chain_id": chain_id,
                            "config_timed_chain": True,
                            "stage": "WAIT_FVG_TOUCH" if spec.final_fvg_touch_tfs else "WAIT_ANY",
                            "advanced": True, "order_mode": "UNORDERED",
                        }
                    else:
                        result = {
                            "ok": True, "delivered": False, "chain_id": chain_id,
                            "config_timed_chain": True, "stage": "WAIT_ANY",
                            "advanced": False, "order_mode": "UNORDERED",
                            "reason": "duplicate_pair" if duplicate else "time_filter_blocked",
                            "latched": len(group_hits),
                        }
                else:
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_ANY",
                        "advanced": False, "order_mode": "UNORDERED",
                        "latched": len(group_hits), "required": 2,
                        "reason": (
                            "bar_expiry_unavailable"
                            if spec.max_gap_bars > 0 and not bar_times and len(group_hits) >= 2
                            else "waiting_for_pair"
                        ),
                    }

                state["spec_signature"] = self._config_chain_spec_signature(spec)
                if changed:
                    self._save_config_chain_state_locked()
                pushes.append(fired_payload)

        for payload in pushes:
            self._push(payload)
        return result

    def _handle_config_chain_trigger(self, event: dict) -> dict:
        """공식 TIMED_CHAIN: SEQUENTIAL은 기존 A→B, UNORDERED는 별도 latch 경로로 처리합니다."""
        chain_id = str(event.get("chain_id") or "").strip()
        spec_id = chain_id.split(":", 1)[1] if chain_id.startswith("CFGCHAIN:") else ""
        watch_id = str(event.get("watch_id") or "").strip()
        now = time.time()
        event_ts = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(event_ts):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        pushes: list[dict] = []

        # 봉 수 expiry가 켜진 체인은 이벤트 스레드 전용 STAFF client로 현재 실제 확정봉을 먼저 읽습니다.
        # manager lock을 잡은 채 ZMQ I/O를 하지 않아 maintenance worker와의 교착을 피합니다.
        with self._lock:
            pre_spec = self.official_chain_specs.get(spec_id)
        if pre_spec is not None and pre_spec.enabled and pre_spec.order_mode == "UNORDERED":
            return self._handle_config_chain_unordered_trigger(event)
        fresh_bar_times: tuple[float, ...] = ()
        if pre_spec is not None and pre_spec.enabled and pre_spec.max_gap_bars > 0:
            fresh_bar_times = self._config_chain_closed_bar_times(pre_spec, self._config_chain_event_staff)

        with self._lock:
            spec = self.official_chain_specs.get(spec_id)
            state = self._config_chain_state.get(spec_id)
            if spec is None or state is None or not spec.enabled:
                return {"ok": True, "delivered": False, "ignored": True}
            if spec.order_mode != "SEQUENTIAL":
                # hot reload가 이벤트 I/O 사이에 mode를 바꾼 경우 이번 옛 경로 이벤트는 폐기합니다.
                return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

            # 조회 도중 hot reload로 체인 핵심축이 바뀌었으면 오래된 bar snapshot은 사용하지 않습니다.
            if pre_spec is None or (
                pre_spec.symbol != spec.symbol or pre_spec.cross_tf != spec.cross_tf
                or pre_spec.max_gap_bars != spec.max_gap_bars
            ):
                fresh_bar_times = ()

            # 이벤트 시점의 fresh 조회가 실패하면 WAIT_B 검증에는 maintenance의 마지막 정상 cache를 사용합니다.
            # A 기준봉을 잡을 때는 stale cache를 쓰지 않고 아래에서 event_ts 기반 fallback을 사용합니다.
            bar_times = fresh_bar_times
            if spec.max_gap_bars > 0 and not bar_times:
                bar_times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())

            trigger_payloads = self._config_chain_trigger_payloads_locked(spec)
            by_id = {str(x["watch_id"]): x for x in trigger_payloads}
            fired_payload = by_id.get(watch_id)
            if fired_payload is None:
                return {"ok": True, "delivered": False, "stale": True}

            incoming_stage = int(fired_payload.get("chain_stage", -1))
            current_stage = int(state.get("stage", 0))
            event_token = str(
                event.get("event_id") or event.get("trigger_id") or event.get("event_time")
                or event.get("completion_time") or event.get("bar_time") or event.get("time") or f"{event_ts:.6f}"
            )
            condition_key = "CROSS" if incoming_stage == 0 else "FVG" if incoming_stage > 0 else ""
            event_direction = self._config_chain_event_direction(event)
            event_tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
            if condition_key and event_direction in {"LONG", "SHORT"}:
                cancel_reason = self._maybe_cancel_config_chain_on_opposite_locked(
                    spec, state, condition_key=condition_key, tf=event_tf,
                    event_direction=event_direction, pushes=pushes,
                )
                if cancel_reason:
                    pushes.append(fired_payload)
                    for payload in pushes:
                        self._push(payload)
                    return {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "cancelled": True,
                        "reason": cancel_reason, "cancel_event_direction": event_direction,
                    }

            # 완료 시각으로 유효성을 판정합니다. 만료 뒤의 새 A는 다음 cycle을
            # 시작하지만, 늦은 B는 지연 수신 증거인 기존 A를 삭제하지 않습니다.
            if current_stage == 1:
                cross_for_expiry = state.get("latest_cross")
                bar_age = None
                if spec.max_gap_bars > 0 and isinstance(cross_for_expiry, dict):
                    bar_age = self._config_chain_bar_age_from_times(
                        cross_for_expiry.get("bar_anchor_ts"), bar_times,
                    )
                expired, expiry_reason = self._config_chain_expiry_status(spec, state, event_ts, bar_age)
                if expired:
                    # A late B cannot destroy the evidence for another delayed,
                    # on-time B. New A still starts a new cycle after expiry.
                    if incoming_stage != 0:
                        return {'ok': True, 'delivered': False, 'expired': True,
                                'reason': f'a_to_b_expired_{expiry_reason}', 'chain_id': chain_id}
                    last_signature = state.get("last_pair_signature")
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    current_stage = 0
                    logging.info(
                        "⌛ [Composer SPECIAL TIMED_CHAIN] A→B 조건 만료 | %s | 기준=%s%s | 다음 A 대기",
                        spec.spec_id, expiry_reason or "unknown",
                        "" if bar_age is None else f" | 경과={bar_age}봉",
                    )
                    # 만료시킨 이벤트가 B라면 과거 A와 결합하지 않고 폐기합니다.
                    if incoming_stage != 0:
                        return {
                            "ok": True, "delivered": False, "expired": True,
                            "reason": f"a_to_b_expired_{expiry_reason or 'unknown'}",
                            "chain_id": chain_id,
                        }

            # stage 0 = WAIT_A. B가 먼저 와도 저장하거나 조합하지 않습니다.
            if current_stage == 0:
                if incoming_stage != 0:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "waiting_for_a", "chain_id": chain_id,
                    }

                direction = self._config_chain_event_direction(event)
                if direction not in {"LONG", "SHORT"}:
                    pushes.append(fired_payload)
                    for payload in pushes:
                        self._push(payload)
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "cross_direction_missing", "chain_id": chain_id,
                    }

                bar_anchor_ts: Optional[float] = None
                if spec.max_gap_bars > 0:
                    if fresh_bar_times:
                        # A 이벤트 수신 시점의 최신 확정 CROSS_TF 봉이 A가 발생한 기준봉입니다.
                        bar_anchor_ts = float(fresh_bar_times[-1])
                    else:
                        # STAFF 일시 실패 시에도 bar-only 체인이 영구 정지하지 않도록 보수적 fallback을 저장합니다.
                        bar_anchor_ts = float(event_ts) - float(tf_seconds(spec.cross_tf))

                # A를 먼저 영속 저장한 뒤에만 B 대기 stage로 전환합니다.
                state["spec_signature"] = self._config_chain_spec_signature(spec)
                state["latest_cross"] = {
                    "ts": float(event_ts),
                    "direction": direction,
                    "token": event_token,
                    "bar_anchor_ts": bar_anchor_ts,
                }
                state["latest_fvg"] = None
                state["stage"] = 1
                state["stage_deadline"] = (
                    float(event_ts) + float(spec.max_gap_sec) if spec.max_gap_sec > 0 else None
                )
                self._save_config_chain_state_locked()
                logging.info(
                    "⏱️ [Composer SPECIAL TIMED_CHAIN] A 저장 | %s | %s | %s %s%d/%d | B 유효=%s",
                    spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                    spec.expiry_label(),
                )

                # Cross Watch는 persistent이지만 재전송해 프로세스 시작 순서/큐 유실에도 기존 복구성을 유지합니다.
                pushes.append(fired_payload)
                result = {
                    "ok": True, "delivered": False, "chain_id": chain_id,
                    "config_timed_chain": True, "stage": "WAIT_B",
                }

            # stage 1 = WAIT_B. A가 다시 와도 현재 저장된 A를 바꾸지 않고 B만 기다립니다.
            else:
                if incoming_stage == 0:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "waiting_for_b", "chain_id": chain_id,
                    }
                if incoming_stage < 1:
                    return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

                cross = state.get("latest_cross")
                deadline = state.get("stage_deadline")
                if not isinstance(cross, dict):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "missing_a_state", "chain_id": chain_id,
                    }

                try:
                    cross_ts = float(cross.get("ts"))
                    if not math.isfinite(cross_ts):
                        raise ValueError("cross ts")
                    if spec.max_gap_sec > 0:
                        deadline = float(deadline)
                        if not math.isfinite(deadline):
                            raise ValueError("deadline")
                    if spec.max_gap_bars > 0:
                        bar_anchor = float(cross.get("bar_anchor_ts"))
                        if not math.isfinite(bar_anchor):
                            raise ValueError("bar anchor")
                except (TypeError, ValueError):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "invalid_a_state", "chain_id": chain_id,
                    }

                bar_age = None
                if spec.max_gap_bars > 0:
                    bar_age = self._config_chain_bar_age_from_times(cross.get("bar_anchor_ts"), bar_times)
                    if bar_age is None:
                        return {
                            "ok": True, "delivered": False, "ignored": True,
                            "reason": "bar_expiry_unavailable", "chain_id": chain_id,
                        }

                # 과거에 이미 존재하던 B는 A 이후 사건이 아니므로 절대 결합하지 않습니다.
                if float(event_ts) < cross_ts:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "b_before_a", "chain_id": chain_id,
                    }

                # 이벤트 자체의 발생시각도 시간 expiry를 넘었으면 B로 인정하지 않습니다.
                if spec.max_gap_sec > 0 and float(event_ts) > float(deadline):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "expired": True,
                        "reason": "a_to_b_expired_time", "chain_id": chain_id,
                    }

                tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
                fvg = {"ts": float(event_ts), "tf": tf, "token": event_token}
                state["latest_fvg"] = fvg
                signature = stable_id(
                    "CFGPAIR", spec.spec_id, cross.get("direction"), cross.get("token"),
                    fvg.get("tf"), fvg.get("token"), length=24,
                )
                direction = str(cross.get("direction") or "").upper()
                duplicate = state.get("last_pair_signature") == signature
                time_allowed = self._time_policy.allows(spec.time_filters)
                should_advance = direction in {"LONG", "SHORT"} and not duplicate and time_allowed

                # 기존 TIME_FILTER 의미를 유지합니다. 허용시간 밖의 B는 A를 소비하지 않고
                # 같은 expiry 범위 안에서 다음 B를 계속 기다립니다.
                if not should_advance:
                    self._save_config_chain_state_locked()
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_B", "advanced": False,
                        "reason": "duplicate_b" if duplicate else "time_filter_blocked",
                    }
                else:
                    # 같은 메인 TIMED_CHAIN의 이전 기간제 OZ 감시는 새 setup이 오면 교체합니다.
                    for child_id, item in list(self._config_chain_active.items()):
                        if str(item.get("spec_id") or "") != spec.spec_id:
                            continue
                        pushes.append({
                            "action": "CANCEL_MANUAL", "watch_id": child_id,
                            "request_chat_id": None,
                            "validation_mode": item.get("validation_mode"),
                            "trigger_mode": item.get("trigger_mode"),
                        })
                        self._config_chain_active.pop(child_id, None)

                    final_deadline = event_ts + spec.final_window_sec
                    if spec.max_gap_sec > 0:
                        final_deadline = min(final_deadline, float(deadline))
                    state["last_pair_signature"] = signature
                    if spec.final_fvg_touch_tfs:
                        state["post_touch_armed"] = {
                            "direction": direction,
                            "expires_at": event_ts + spec.final_window_sec,
                            "completion_deadline": final_deadline,
                            "pair_signature": signature,
                        }
                        self._save_config_chain_state_locked()
                    else:
                        child_id = stable_id("OZARM:CFGCHAIN", spec.spec_id, direction, signature, length=20)
                        child_payload = {
                            "action": "MANUAL_WATCH", "watch_id": child_id,
                            "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                            "direction": direction, "persistent": True,
                            "request_chat_id": None,
                            "validation_mode": spec.validation_mode,
                            "trigger_mode": spec.trigger_mode,
                            "source_spec_id": spec.spec_id,
                            "source_spec_ids": [spec.spec_id],
                            "source_name": spec.name,
                        }
                        active_item = dict(child_payload)
                        active_item.update({
                            "spec_id": spec.spec_id,
                            "expires_at": event_ts + spec.final_window_sec,
                            "completion_deadline": final_deadline,
                        })
                        self._config_chain_active[child_id] = active_item
                        pushes.append(child_payload)
                    logging.info(
                        "⏱️ [Composer SPECIAL TIMED_CHAIN] B 성립→NEXT | %s | %s | A=%s %s%d/%d → B=%s FVG | gap=%.1fs%s | %s",
                        spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                        fvg.get("tf"), float(fvg["ts"]) - cross_ts,
                        "" if bar_age is None else f"/{bar_age}봉",
                        (
                            f"{format_duration_ko(spec.final_window_sec)} 안에 "
                            f"{'/'.join(spec.final_fvg_touch_tfs)} 동방향 FVG TOUCH 대기"
                            if spec.final_fvg_touch_tfs
                            else f"{format_duration_ko(spec.final_window_sec)} 동안 OZ"
                        ),
                    )

                    # B가 성립해 다음 단계로 넘겼으므로 이번 A→B 사이클을 소비하고 새 A를 기다립니다.
                    last_signature = state.get("last_pair_signature")
                    post_touch_armed = state.get('post_touch_armed')
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state['post_touch_armed'] = post_touch_armed
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_A", "advanced": True,
                    }

        for payload in pushes:
            self._push(payload)
        return result

    def _dispatch_official_timed_chain_event(self, event: dict) -> Optional[dict]:
        """공식 시간연쇄에 들어오는 런타임 이벤트의 단일 진입점입니다.

        fact-store는 시장 사실만 저장하고, 공식 시간연쇄의 stage 진행/OZ arm은
        이 경계 아래에서 처리합니다.
        """
        kind = str(event.get("kind") or "").upper()
        chain_id = str(event.get("chain_id") or "").strip()

        if kind == "GENERIC_TRIGGER" and chain_id.startswith("CFGCHAIN:"):
            return self._handle_config_chain_trigger(event)
        if kind == "FVG_TOUCH":
            return self._handle_official_timed_chain_fvg_touch(event)
        return None

    def _handle_official_timed_chain_fvg_touch(self, event: dict) -> dict:
        """pair 성립 후 지정된 동방향 FVG TOUCH를 공식 시간연쇄 다음 단계로 처리합니다."""
        completed = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(completed):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        symbol = str(event.get("symbol") or "").strip()
        tf = normalize_tf(event.get("source_tf") or event.get("tf"))
        zone_id = str(event.get("zone_id") or "").strip()
        fvg_side = str(event.get("fvg_side") or "").upper()
        direction = str(event.get("direction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            direction = "LONG" if fvg_side == "BULL" else "SHORT" if fvg_side == "BEAR" else ""
        if not symbol or not tf or direction not in {"LONG", "SHORT"}:
            return {"ok": True, "delivered": False, "ignored": True}

        now = time.time()
        pushes: list[dict] = []
        state_changed = False
        matched_specs = 0
        armed_children = 0

        with self._lock:
            for spec_id, spec in self.official_chain_specs.items():
                if (
                    not spec.enabled or not spec.final_fvg_touch_tfs
                    or spec.symbol != symbol or tf not in spec.final_fvg_touch_tfs
                ):
                    continue
                matched_specs += 1
                state = self._config_chain_state.get(spec_id)
                armed = state.get("post_touch_armed") if isinstance(state, dict) else None
                if not isinstance(armed, dict):
                    continue
                try:
                    expires_at = float(armed.get("expires_at") or 0.0)
                except (TypeError, ValueError):
                    expires_at = 0.0
                armed_direction = str(armed.get("direction") or "").upper()
                if completed > self._config_chain_completion_deadline(armed):
                    continue
                if armed_direction != direction:
                    continue
                if not self._time_policy.allows(spec.final_time_filters):
                    logging.info(
                        "⏰ [Composer SPECIAL TIMED_CHAIN] 최종 FVG TOUCH 시간필터 차단 | %s | %s %s",
                        spec.name, direction, tf,
                    )
                    continue

                # 같은 spec의 이전 최종 OZ 감시는 새 유효 TOUCH가 오면 교체합니다.
                for child_id, item in list(self._config_chain_active.items()):
                    if str(item.get("spec_id") or "") != spec.spec_id:
                        continue
                    pushes.append({
                        "action": "CANCEL_MANUAL", "watch_id": child_id,
                        "request_chat_id": None,
                        "validation_mode": item.get("validation_mode"),
                        "trigger_mode": item.get("trigger_mode"),
                    })
                    self._config_chain_active.pop(child_id, None)

                pair_signature = str(armed.get("pair_signature") or "")
                child_id = stable_id(
                    "OZARM:CFGCHAIN:FVGTOUCH", spec.spec_id, direction, pair_signature, zone_id or tf, length=20
                )
                child_payload = {
                    "action": "MANUAL_WATCH", "watch_id": child_id,
                    "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                    "direction": direction, "persistent": True,
                    "request_chat_id": None,
                    "validation_mode": spec.validation_mode,
                    "trigger_mode": spec.trigger_mode,
                    "source_spec_id": spec.spec_id,
                    "source_spec_ids": [spec.spec_id],
                    "source_name": spec.name,
                    "source_fvg_zone_id": zone_id,
                    "source_fvg_tf": tf,
                }
                active_item = dict(child_payload)
                active_item.update({
                    "spec_id": spec.spec_id,
                    # pair 자격의 남은 시간만 최종 OZ 감시에 사용합니다.
                    "expires_at": expires_at,
                    "completion_deadline": self._config_chain_completion_deadline(armed),
                })
                self._config_chain_active[child_id] = active_item
                state["post_touch_armed"] = None
                state_changed = True
                armed_children += 1
                pushes.append(child_payload)
                logging.info(
                    "🎯 [Composer SPECIAL TIMED_CHAIN] 동방향 FVG TOUCH→OZ | %s | %s | FVG=%s %s | OZ=%s",
                    spec.name, direction, tf, zone_id or "-", ",".join(spec.oz_tfs),
                )

            if state_changed:
                self._save_config_chain_state_locked()

        # OZ queue I/O는 Composer state lock 밖에서 수행합니다.
        for payload in pushes:
            self._push(payload)

        return {
            "ok": True, "delivered": False,
            "official_timed_chain": True,
            "matched_specs": matched_specs,
            "armed_children": armed_children,
        }

    def _handle_generic_trigger(self, event: dict) -> dict:
        item = canonical_watch_payload(event)
        chain_id = str(item.get("chain_id") or "").strip()
        if chain_id.startswith("CFGCHAIN:"):
            result = self._dispatch_official_timed_chain_event(item)
            return result or {"ok": True, "delivered": False, "ignored": True}

        # 저장 호환이 필요한 BAR/WONBI CLOSE suffix만 legacy 형태로 변환합니다.
        # 사전 기반 복합조건은 COMPOUND_CONDITION 공용 계약을 그대로 사용합니다.
        forwarded = dict(item)
        if chain_id:
            forwarded["watch_type"] = legacy_chain_watch_type(
                item.get("watch_type"), item.get("evaluation_mode")
            )
        return self.watch_orchestrator.handle_generic_trigger(forwarded)

    @staticmethod
    def _local_chain_epoch(value: object) -> Optional[float]:
        if value in {None, ""}:
            return None
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out = float(value)
            else:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp):
                    return None
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                else:
                    stamp = stamp.tz_convert("UTC")
                out = float(stamp.timestamp())
            return out if math.isfinite(out) else None
        except Exception:
            return None

    def _compound_close_bar_times(self, targets: Iterable[dict]) -> dict[tuple[str, str], str]:
        """CLOSE 복합조건의 봉 전환 판정용 현재 진행봉 시각을 STAFF에서 읽습니다."""
        by_symbol: dict[str, set[str]] = {}
        for target in targets:
            if str(target.get("evaluation_mode") or "LIVE").upper() != "CLOSE":
                continue
            symbol = str(target.get("symbol") or "").strip()
            tf = normalize_tf(target.get("tf"))
            if symbol and tf:
                by_symbol.setdefault(symbol, set()).add(tf)
        out: dict[tuple[str, str], str] = {}
        for symbol, tf_set in by_symbol.items():
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), [])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is None or len(df) < 1:
                    continue
                raw_time = self._condition_row(df,-1).get("time")
                try:
                    stamp = pd.Timestamp(raw_time)
                    if pd.isna(stamp):
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.tz_localize("UTC")
                    else:
                        stamp = stamp.tz_convert("UTC")
                    out[(symbol, tf)] = stamp.isoformat()
                except Exception:
                    continue
        return out

    def _compound_events_for_target(
        self, target: dict, close_bar_times: dict[tuple[str, str], str]
    ) -> list[dict]:
        chain_id = str(target.get("chain_id") or "").strip()
        watch_id = str(target.get("watch_id") or "").strip()
        try:
            index = int(target.get("index"))
        except (TypeError, ValueError):
            return []
        events: list[dict] = []
        with self._lock:
            chain = self.timed_chains.get(chain_id)
            if (
                chain is None or not chain.enabled or not chain.started
                or chain.order_mode not in {"FILTER", "SEQUENTIAL"}
                or index < 0 or index >= len(chain.triggers)
                or (chain.order_mode == "SEQUENTIAL" and index != int(chain.stage))
                or chain.current_watch_ids.get(str(index)) != watch_id
            ):
                self._compound_watch_state.pop(watch_id, None)
                return []
            trig = chain.triggers[index]
            condition_type, evaluation_mode = trigger_watch_contract(trig)
            if condition_type != "COMPOUND_CONDITION":
                return []
            spec = self._compound_spec_for_trigger(chain, trig)
            if spec is None:
                return []

            directions = (trig.direction,) if trig.direction in {"LONG", "SHORT"} else ("LONG", "SHORT")
            signatures = {
                direction: self._evaluate_spec_direction_locked(spec, direction)
                for direction in directions
            }
            state = self._compound_watch_state.setdefault(watch_id, {})

            if evaluation_mode == "CLOSE":
                bar_token = close_bar_times.get((chain.symbol, trig.tf))
                if not bar_token:
                    return []
                previous_bar = state.get("bar_token")
                previous_signatures = dict(state.get("bar_signatures") or {})
                if previous_bar is None:
                    state["bar_token"] = bar_token
                    state["bar_signatures"] = dict(signatures)
                    return []
                if previous_bar == bar_token:
                    state["bar_signatures"] = dict(signatures)
                    return []

                # 새 진행봉으로 넘어온 순간, 직전 봉의 마지막 관측 상태를 봉마감 결과로 확정합니다.
                for direction, signature in previous_signatures.items():
                    if not signature:
                        continue
                    events.append({
                        "kind": "GENERIC_TRIGGER",
                        "strategy": "COMPOSER",
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_id": watch_id,
                        "watch_type": "COMPOUND_CONDITION",
                        "evaluation_mode": "CLOSE",
                        "direction": direction,
                        # 봉마감 이벤트 시각은 새 봉 시작시각(=직전 봉 마감시각)을 사용합니다.
                        "event_time": bar_token,
                        "event_id": f"COMPOUND:{watch_id}:{direction}:{previous_bar}",
                    })
                state["bar_token"] = bar_token
                state["bar_signatures"] = dict(signatures)
                return events

            active = dict(state.get("live_active") or {})
            for direction, signature in signatures.items():
                now_active = bool(signature)
                was_active = bool(active.get(direction))
                if now_active and not was_active:
                    events.append({
                        "kind": "GENERIC_TRIGGER",
                        "strategy": "COMPOSER",
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_id": watch_id,
                        "watch_type": "COMPOUND_CONDITION",
                        "evaluation_mode": "LIVE",
                        "direction": direction,
                        "event_time": time.time(),
                        "event_id": f"COMPOUND:{watch_id}:{direction}:{time.time_ns()}",
                    })
                active[direction] = now_active
            state["live_active"] = active
        return events

    def _update_local_chain_events(self) -> None:
        """LOCAL_EVENT를 공용 복합조건 실행기 또는 SPECIAL handler에 위임합니다."""
        grouped: dict[str, list[dict]] = {}
        compound_targets: list[dict] = []
        with self._lock:
            for chain in self.timed_chains.values():
                if not chain.enabled or not chain.started:
                    continue
                if chain.order_mode == "FILTER":
                    indices = range(len(chain.triggers))
                elif chain.order_mode == "SEQUENTIAL":
                    stage = int(chain.stage)
                    indices = (stage,) if 0 <= stage < len(chain.triggers) else ()
                else:
                    continue

                for index in indices:
                    trig = chain.triggers[index]
                    condition_type, evaluation_mode = trigger_watch_contract(trig)
                    watch_id = chain.current_watch_ids.get(str(index))
                    if not watch_id:
                        continue
                    target = {
                        "chain_id": chain.chain_id,
                        "index": index,
                        "watch_id": watch_id,
                        "symbol": chain.symbol,
                        "tf": trig.tf,
                        "watch_type": condition_type,
                        "evaluation_mode": evaluation_mode,
                        "direction": trig.direction,
                        "level_side": trig.level_side,
                    }
                    if condition_type == "COMPOUND_CONDITION":
                        compound_targets.append(target)
                        continue
                    # SPECIAL local poller는 기존 FILTER 의미만 유지합니다.
                    if chain.order_mode == "FILTER":
                        handler = self._special_watch_handlers.get(condition_type)
                        if handler is not None:
                            grouped.setdefault(condition_type, []).append(target)

        close_bar_times = self._compound_close_bar_times(compound_targets)
        for target in compound_targets:
            try:
                events = self._compound_events_for_target(target, close_bar_times)
            except Exception:
                logging.exception("[Composer 복합조건] poll 실패 | %s", target.get("watch_id"))
                continue
            for event in events:
                try:
                    self._handle_generic_trigger(event)
                except Exception:
                    logging.exception("[Composer 복합조건] trigger 전달 실패 | %s", target.get("watch_id"))

        for condition_type, handler in tuple(self._special_watch_handlers.items()):
            targets = grouped.get(condition_type, [])
            poll = getattr(handler, "poll", None)
            if not callable(poll):
                continue
            try:
                poll(tuple(targets))
            except Exception:
                logging.exception(
                    "[SPECIAL Watch] poll 실패 | %s | targets=%d",
                    condition_type, len(targets),
                )

    def _maintain_timed_chains(self) -> None:
        self.watch_orchestrator.maintenance()

    def _maintain_config_timed_chains(self) -> None:
        now = time.time()
        mono = time.monotonic()
        pushes: list[dict] = []
        chain_state_changed = False

        # 봉 수 expiry가 필요한 CROSS_TF만 maintenance worker의 STAFF client로 주기 조회합니다.
        with self._lock:
            bar_specs: dict[tuple[str, str], ConfigTimedChainSpec] = {}
            for spec_id, state in self._config_chain_state.items():
                spec = self.official_chain_specs.get(spec_id)
                if spec is None or spec.max_gap_bars <= 0:
                    continue
                if spec.order_mode == "UNORDERED":
                    latch = state.get("unordered_latch")
                    groups = latch.get("groups") if isinstance(latch, dict) else {}
                    has_hits = any(bool(x) for x in groups.values()) if isinstance(groups, dict) else False
                    if not has_hits:
                        continue
                elif int(state.get("stage", 0)) != 1:
                    continue
                bar_specs[(spec.symbol, spec.cross_tf)] = spec

        bar_poll_sec = float(self.config.get("CHAIN_BAR_EXPIRY_POLL_SEC", "1.0"))
        if bar_specs and mono - self._last_config_chain_bar_poll >= max(0.25, bar_poll_sec):
            refreshed: dict[tuple[str, str], tuple[float, ...]] = {}
            for key, spec in bar_specs.items():
                refreshed[key] = self._config_chain_closed_bar_times(spec, self.staff)
            with self._lock:
                for key, times in refreshed.items():
                    if times:
                        self._config_chain_bar_times_cache[key] = times
                self._last_config_chain_bar_poll = mono

        with self._lock:
            # SEQUENTIAL은 A 저장 후 B 대기를, UNORDERED는 각 latch hit를 독립 expiry합니다.
            for spec_id, state in self._config_chain_state.items():
                spec = self.official_chain_specs.get(spec_id)
                if spec is None:
                    continue
                post_touch = state.get("post_touch_armed")
                if isinstance(post_touch, dict):
                    try:
                        touch_expires = float(post_touch.get("expires_at") or 0.0)
                    except (TypeError, ValueError):
                        touch_expires = 0.0
                    if now > touch_expires and not post_touch.get('deadline_elapsed'):
                        post_touch['deadline_elapsed'] = True
                        chain_state_changed = True
                        logging.info("⌛ [Composer SPECIAL TIMED_CHAIN] FVG TOUCH 시간창 경과 · 완료시각 판정 상태 보존 | %s", spec_id)
                if spec.order_mode == "UNORDERED":
                    latch = state.get("unordered_latch")
                    if not isinstance(latch, dict):
                        latch = UnorderedConditionLatch.new_state()
                        state["unordered_latch"] = latch
                    # Event-time pruning belongs to the callback. Wall time
                    # cannot prove that an on-time completion will not arrive.
                    changed = False
                    if spec.max_gap_bars > 0:
                        times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())
                        if times:
                            changed = self._prune_config_chain_unordered_bars_locked(spec, latch, times) or changed
                    if changed:
                        chain_state_changed = True
                        logging.info(
                            "⌛ [Composer SPECIAL TIMED_CHAIN] 순서무관 조건 expiry 정리 | %s | 남은=%s",
                            spec_id, {k: sorted(v) for k, v in (latch.get("groups") or {}).items()},
                        )
                    continue
                if int(state.get("stage", 0)) != 1:
                    continue
                cross = state.get("latest_cross")
                bar_age = None
                if spec.max_gap_bars > 0 and isinstance(cross, dict):
                    times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())
                    bar_age = self._config_chain_bar_age_from_times(cross.get("bar_anchor_ts"), times)
                expired, expiry_reason = self._config_chain_expiry_status(spec, state, now, bar_age)
                if not expired:
                    continue

                if not state.get('deadline_elapsed'):
                    state['deadline_elapsed'] = True
                    chain_state_changed = True

            if chain_state_changed:
                self._save_config_chain_state_locked()

            # Keep official child identities/deadlines for delayed FINAL_ALERTs.
            # Completion timestamps, not maintenance scheduling, decide expiry.

            refresh_sec = float(self.config.get("CHAIN_REFRESH_SEC", DEFAULT_CHAIN_REFRESH_SEC))
            if mono - self._last_config_chain_refresh >= max(5.0, refresh_sec):
                for spec in self.official_chain_specs.values():
                    pushes.extend(self._config_chain_trigger_payloads_locked(spec))
                for item in self._config_chain_active.values():
                    payload = {k: v for k, v in item.items()
                               if k not in {"expires_at", "spec_id", "completion_deadline", "spec_signature"}}
                    pushes.append(payload)
                self._last_config_chain_refresh = mono

        for payload in pushes:
            self._push(payload)

    # -------------------------------
    # external natural-language aliases
    # -------------------------------
    def _command_language(self) -> dict:
        return self.command_interpreter.language()

    def _command_default(self, key: str, fallback):
        return self.command_interpreter.command_default(key, fallback)

    def _command_default_tf(self) -> str:
        return self.command_interpreter.default_tf()

    def _command_default_symbol(self) -> str:
        return self.command_interpreter.default_symbol()

    @staticmethod
    def _alias_present(text: str, alias: str) -> bool:
        return CommandInterpreter.alias_present(text, alias)

    def _explicit_symbol_from_text(self, text: str) -> Optional[str]:
        return self.command_interpreter.explicit_symbol_from_text(text)

    def _normalize_command_text(self, text: str) -> str:
        return self.command_interpreter.normalize_command_text(text)

    def _condition_macro_matches(self, text: str) -> list[dict]:
        return self.command_interpreter.condition_macro_matches(text)

    def _expand_condition_macro(self, name: str, tf: str, direction: object = None) -> dict:
        return self.command_interpreter.expand_condition_macro(name, tf, direction)

    @staticmethod
    def _condition_from_descriptor(raw: dict, default_tf: str) -> ConditionSpec:
        """사전의 primitive condition descriptor를 공용 ConditionSpec으로 변환합니다."""
        item = dict(raw or {})
        tf = normalize_tf(item.pop("tf", None) or default_tf)
        allowed = {
            "kind", "direction", "side", "ma_family", "fast_period", "slow_period",
            "metric", "metric_operator", "metric_value", "metric_rhs",
        }
        kwargs = {k: v for k, v in item.items() if k in allowed and k != "kind"}
        return ConditionSpec(kind=item.get("kind"), tf=tf, **kwargs)

    @staticmethod
    def _macro_direction_near(text: str, start: int, end: int) -> Optional[str]:
        low = str(text or "").lower()
        local = low[max(0, start - 20):min(len(low), end + 12)]
        if any(x in local for x in ("상승", "매수", "롱")):
            return "LONG"
        if any(x in local for x in ("하락", "매도", "숏")):
            return "SHORT"
        return None

    def _reload_command_aliases_if_needed(self, now_mono: float) -> None:
        if self.command_interpreter.reload_if_needed(now_mono):
            self.command_aliases = self.command_interpreter.language()

    def _gemini_canonicalize_command(self, text):
        # Host resolves the request, then re-enters through EXTERNAL_REPLY.
        from event_commands import ExternalCommandPending
        raise ExternalCommandPending(text)


    # -------------------------------
    # natural-language private strategy parser
    # -------------------------------
    def _allowed_symbols(self) -> list[str]:
        values = []
        for key in ("STAFF_ALLOWED_SYMBOLS", "ACTIVE_SYMBOLS", "TARGET_SYMBOLS"):
            values.extend(split_csv(self.config.get(key)))
        with self._lock:
            values.extend(s.symbol for s in self.official_specs.values())
            values.extend(s.symbol for s in self.official_chain_specs.values())
            values.extend(s.symbol for s in self.manual_specs.values())
        return list(dict.fromkeys(x for x in values if x))

    def _default_gold_symbol(self, allowed: Optional[list[str]] = None) -> str:
        return self.command_interpreter.default_gold_symbol(allowed)

    def _parse_symbol(self, text: str) -> Optional[str]:
        return self.command_interpreter.parse_symbol(text)

    @staticmethod
    def _tf_occurrences(text: str) -> list[tuple[int, int, str]]:
        return CommandInterpreter.tf_occurrences(text)

    @staticmethod
    def _nearest_tf_before(occurrences: list[tuple[int, int, str]], pos: int) -> str:
        return CommandInterpreter.nearest_tf_before(occurrences, pos)

    @staticmethod
    def _keyword_pos(text: str, words: Iterable[str]) -> int:
        return CommandInterpreter.keyword_pos(text, words)

    def _parse_oz_direction(self, text: str) -> Optional[str]:
        if "레짐" in str(text or ""):
            text = str(text).replace("레짐", "")
        return self.command_interpreter.parse_oz_direction(text)

    def _extract_oz_tfs(self, text: str, occurrences: list[tuple[int, int, str]], last_condition_pos: int) -> tuple[str, ...]:
        return self.command_interpreter.extract_oz_tfs(text, occurrences, last_condition_pos)

    def _parse_time_filters(self, text: str) -> tuple[str, ...]:
        return self.command_interpreter.parse_time_filters(text)

    @staticmethod
    def _condition_default_direction(cond: ConditionSpec) -> Optional[str]:
        """조건 자체가 암시하는 일반적인 OZ 기본 방향을 반환합니다."""
        if cond.kind in {"MA_STATE", "MA_PRICE_STATE"}:
            if cond.side == "ABOVE":
                return "LONG"
            if cond.side == "BELOW":
                return "SHORT"
        if cond.direction in {"LONG", "SHORT"}:
            return cond.direction
        if cond.kind in {"WONBI", "PERCENTILE"}:
            if cond.side == "LOWER":
                return "LONG"
            if cond.side == "UPPER":
                return "SHORT"
        if cond.kind == "FVG":
            if cond.side == "BULL":
                return "LONG"
            if cond.side == "BEAR":
                return "SHORT"
        return None

    @classmethod
    def _infer_condition_default_direction(cls, conditions: Iterable[ConditionSpec]) -> Optional[str]:
        directions = {
            direction
            for cond in conditions
            for direction in [cls._condition_default_direction(cond)]
            if direction in {"LONG", "SHORT"}
        }
        return next(iter(directions)) if len(directions) == 1 else None

    @staticmethod
    def _infer_trigger_default_direction(triggers: Iterable[ChainTriggerSpec]) -> Optional[str]:
        directions = {
            str(trig.direction)
            for trig in triggers
            if str(trig.direction) in {"LONG", "SHORT"}
        }
        return next(iter(directions)) if len(directions) == 1 else None

    @staticmethod
    def _condition_tfs_before(
        text: str, occurrences: list[tuple[int, int, str]], condition_pos: int
    ) -> tuple[str, ...]:
        """조건 바로 앞에 연속해서 나열된 TF들을 같은 조건의 대상 TF로 묶습니다.

        예: ``15분 30분 1시간 하락추세`` -> (15m, 30m, 1h)
            ``15분과 30분, 1시간 복합조건`` -> (15m, 30m, 1h)

        TF 사이에 다른 조건/단어가 끼면 거기서 묶음을 끊습니다. 따라서 기존
        ``15분 하락추세 30분 하단원비`` 같은 문장은 각 조건의 최근접 TF 하나만
        유지합니다.
        """
        before = [x for x in occurrences if x[1] <= condition_pos]
        if not before:
            return ()

        selected = [before[-1]]
        left = before[-1][0]

        def _is_tf_list_gap(gap: str) -> bool:
            # 일반적인 나열 구분자와 조사/접속사만 허용합니다. 다른 단어가 있으면
            # 이전 TF는 다른 조건에 속한 것으로 보고 확장을 중단합니다.
            compact = re.sub(r"[\s,，/·+&]+", "", str(gap or "").lower())
            for connector in ("그리고", "이랑", "랑", "하고", "및", "와", "과"):
                compact = compact.replace(connector, "")
            return not compact

        for item in reversed(before[:-1]):
            gap = str(text or "")[item[1]:left]
            if not _is_tf_list_gap(gap):
                break
            selected.append(item)
            left = item[0]

        selected.reverse()
        return tuple(dict.fromkeys(tf for _start, _end, tf in selected))

    @staticmethod
    def _ma_watch_condition_matches(text: str) -> list[dict]:
        """STAFF가 소유한 MA 값을 공용 primitive Condition descriptor로 변환합니다.

        HMA6/17/50/168 방향은 MT5→STAFF 원본 HMA의 현재값과 2봉 전 값을 비교합니다.
        기존 50 HMA 가격 위/아래 조건도 TREND 재계산값이 아니라 STAFF hma_50을 직접 사용합니다.
        """
        low = str(text or "").lower()
        hits: list[dict] = []
        price_prefix = r"(?:(?:봉(?:\s*(?:마감|확정))?|확정봉|가격|현재가)\s*(?:이|가)?\s*)?"
        hma50 = r"50\s*(?:hma|헐수트|헐\s*이평|헐이평|헐)"
        hma_any = r"(?P<hma_period>6|17|50|168)\s*(?:hma|헐수트|헐\s*이평|헐이평|헐)"
        ema200 = r"200\s*(?:ema|지수\s*이평|지수이평|지수이동평균|지수)"

        for m in re.finditer(
            rf"{price_prefix}{hma50}\s*(?P<side>위쪽|위|아래쪽|아래)", low, re.I
        ):
            above = str(m.group("side")).startswith("위")
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": "50헐 위" if above else "50헐 아래",
                "direction": "LONG" if above else "SHORT",
                "descriptor": {
                    "kind": "MA_PRICE_STATE",
                    "direction": "LONG" if above else "SHORT",
                    "side": "ABOVE" if above else "BELOW",
                    "ma_family": "HMA",
                    "slow_period": 50,
                },
            })

        for m in re.finditer(rf"{hma_any}\s*(?P<slope>우상향|우하향)", low, re.I):
            period = int(m.group("hma_period"))
            up = str(m.group("slope")) == "우상향"
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": f"{period}헐 우상향" if up else f"{period}헐 우하향",
                "direction": "LONG" if up else "SHORT",
                "descriptor": {
                    "kind": "MA_SLOPE_STATE",
                    "direction": "LONG" if up else "SHORT",
                    "side": "UP" if up else "DOWN",
                    "ma_family": "HMA",
                    "slow_period": period,
                },
            })

        for m in re.finditer(
            rf"{price_prefix}{ema200}\s*(?P<side>위쪽|위|아래쪽|아래)", low, re.I
        ):
            above = str(m.group("side")).startswith("위")
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": "200지수 위" if above else "200지수 아래",
                "direction": "LONG" if above else "SHORT",
                "descriptor": {
                    "kind": "MA_PRICE_STATE",
                    "direction": "LONG" if above else "SHORT",
                    "side": "ABOVE" if above else "BELOW",
                    "ma_family": "EMA",
                    "slow_period": 200,
                },
            })

        hits.sort(key=lambda x: (int(x["start"]), int(x["end"])))
        return hits

    def _parse_private_strategy_local(self, text: str, owner_chat_id: str) -> Optional[StrategySpec]:
        clean = str(text or "").strip()
        low = clean.lower()
        if "올존" not in low:
            return None
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        occurrences = self._tf_occurrences(clean)
        conditions: list[ConditionSpec] = []
        last_condition_pos = -1
        final_direction = self._parse_oz_direction(clean)
        final_direction_explicit = final_direction in {"LONG", "SHORT"}

        # "하단올존/상단올존/매수올존/매도올존"의 방향어가 바로 앞 조건의
        # WONBI/SWEEP 방향 표식으로 오인되지 않도록 marker 검색에서만 가립니다.
        marker_low = low
        oz_dir_alias_re = re.compile(
            r"(?:매수|매도|하단|상단)(?:\s*(?:무지성|브레이커|레짐|슈퍼|일반))*\s*올존",
            re.I,
        )
        marker_low = oz_dir_alias_re.sub(lambda m: " " * len(m.group(0)), marker_low)

        # 사전 기반 조건 매크로를 primitive ConditionSpec으로 확장합니다.
        # 김매니저에는 매크로별 투자 개념이나 조합식이 하드코딩되지 않습니다.
        for macro_hit in self._condition_macro_matches(clean):
            macro_pos = int(macro_hit["start"])
            macro_end = int(macro_hit["end"])
            tfs = self._condition_tfs_before(clean, occurrences, macro_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, macro_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            macro_direction = self._macro_direction_near(clean, macro_pos, macro_end) or "AUTO"
            for tf in tfs:
                expanded = self._expand_condition_macro(
                    str(macro_hit["name"]), tf, macro_direction
                )
                for raw_condition in expanded.get("conditions") or ():
                    cond = self._condition_from_descriptor(raw_condition, tf)
                    if cond not in conditions:
                        conditions.append(cond)
            if tfs:
                last_condition_pos = max(last_condition_pos, macro_pos)

        def _append_condition(cond: ConditionSpec) -> None:
            # 같은 조건이 표현 중복으로 두 번 잡혀도 한 번만 유지합니다.
            if cond not in conditions:
                conditions.append(cond)

        def _nearest_marker(start: int, end: int, groups: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
            """조건 표현 주변에서 가장 가까운 방향/side 표식을 찾습니다."""
            window_start = max(0, start - 18)
            window_end = min(len(low), end + 18)
            segment = marker_low[window_start:window_end]
            best_distance = 10**9
            best_label = ""
            for label, words in groups:
                for word in words:
                    for mm in re.finditer(re.escape(word), segment, re.I):
                        abs_start = window_start + mm.start()
                        abs_end = window_start + mm.end()
                        if abs_end <= start:
                            distance = start - abs_end
                        elif abs_start >= end:
                            distance = abs_start - end
                        else:
                            distance = 0
                        if distance < best_distance:
                            best_distance = distance
                            best_label = label
            return best_label

        # STAFF MA 현재상태 조건도 다른 Composer 조건과 동일한 ConditionSpec으로 보존합니다.
        for hit in self._ma_watch_condition_matches(low):
            pos = int(hit["start"])
            tfs = self._condition_tfs_before(clean, occurrences, pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            for tf in tfs:
                raw = dict(hit["descriptor"])
                raw["tf"] = tf
                _append_condition(self._condition_from_descriptor(raw, tf))
            if tfs:
                last_condition_pos = max(last_condition_pos, pos)

        # 현재 MA 배열 상태 Gate. CROSS 발생을 기다리는 이벤트 조건과 분리합니다.
        # 예: "1분 EMA50/200 역배열일 때 1분 매도 올존 알려줘"
        #     "1분 50 200 지수이평 정배열일 때 1분 매수 올존 알려줘"
        ma_family_alias = r"(?:ema|hma|지수이평|지수이동평균|지수평균|헐이평|헐이동평균)"
        ma_state_re = re.compile(
            rf"(?:(?P<prefix>{ma_family_alias})\s*)?"
            r"(?P<fast>\d{1,4})\s*(?:/|,|·|와|과|및|\s)+\s*(?P<slow>\d{1,4})\s*"
            rf"(?:(?P<suffix>{ma_family_alias})\s*)?"
            r"(?P<state>정배열|역배열)",
            re.I,
        )

        def _ma_state_family(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            token = re.sub(r"\s+", "", str(value).lower())
            if token in {"hma", "헐이평", "헐이동평균"}:
                return "HMA"
            if token in {"ema", "지수이평", "지수이동평균", "지수평균"}:
                return "EMA"
            return None

        for m in ma_state_re.finditer(low):
            state_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, state_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, state_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()

            prefix_family = _ma_state_family(m.group("prefix"))
            suffix_family = _ma_state_family(m.group("suffix"))
            if prefix_family and suffix_family and prefix_family != suffix_family:
                raise ValueError("MA 배열 family 표현이 서로 충돌합니다")
            family = prefix_family or suffix_family or str(self._command_default("ma_family", "EMA")).upper()
            fast = int(m.group("fast"))
            slow = int(m.group("slow"))
            side = "ABOVE" if m.group("state") == "정배열" else "BELOW"

            for tf in tfs:
                _append_condition(ConditionSpec(
                    "MA_STATE", tf, direction="AUTO", side=side,
                    ma_family=family, fast_period=fast, slow_period=slow,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, state_pos)

        # TREND 내부 지표값을 필요할 때만 query하는 범용 metric Gate.
        # 예: "15분 ADX 25 이상일때 1분 매수 올존 알려줘"
        #     "15분 MACD가 시그널보다 높을때 1분 올존 알려줘"
        #     "15분 슈퍼트랜드 상승일때 1분 올존 알려줘"
        metric_aliases = {
            "롱점수": "long_score", "매수점수": "long_score", "long score": "long_score",
            "숏점수": "short_score", "매도점수": "short_score", "short score": "short_score",
            "추세점수": "trend_score", "트렌드점수": "trend_score", "trend score": "trend_score",
            "adx": "adx", "+di": "plus_di", "plus di": "plus_di", "plusdi": "plus_di", "플러스di": "plus_di",
            "-di": "minus_di", "minus di": "minus_di", "minusdi": "minus_di", "마이너스di": "minus_di",
            "rsi": "rsi14", "rsi14": "rsi14", "cci": "cci20", "cci20": "cci20",
            "mfi": "mfi14", "mfi14": "mfi14", "cmf": "cmf20", "cmf20": "cmf20",
            "chop": "chop14", "chop14": "chop14", "choppiness": "chop14",
            "bop": "bop", "hv": "hv20", "hv20": "hv20", "hvma": "hvma20", "hvma20": "hvma20",
            "macd signal": "macd_signal", "macd 시그널": "macd_signal", "macd_signal": "macd_signal",
            "시그널": "macd_signal", "signal": "macd_signal", "macd": "macd",
            "aroon up": "aroon_up", "aroon_up": "aroon_up", "아룬업": "aroon_up",
            "aroon down": "aroon_down", "aroon_down": "aroon_down", "아룬다운": "aroon_down",
            "vortex plus": "vortex_plus", "vortex_plus": "vortex_plus", "볼텍스플러스": "vortex_plus",
            "vortex minus": "vortex_minus", "vortex_minus": "vortex_minus", "볼텍스마이너스": "vortex_minus",
            "vwap": "vwap", "브이왑": "vwap", "price": "price", "가격": "price",
            "psar": "psar", "sar": "psar", "파라볼릭sar": "psar",
            "linreg20": "linreg20", "mss": "mss", "vol_state": "vol_state", "vol_surge": "vol_surge",
            "ema10_open": "ema10_open", "ema50_open": "ema50_open", "sma20_open": "sma20_open",
            "wma17_open": "wma17_open",
            "supertrend": "supertrend", "슈퍼트랜드": "supertrend", "슈퍼트렌드": "supertrend",
        }
        metric_alias_pattern = "|".join(
            re.escape(x) for x in sorted(metric_aliases, key=len, reverse=True)
        )

        def _metric_tfs(pos: int) -> tuple[str, ...]:
            tfs = self._condition_tfs_before(clean, occurrences, pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            return tfs

        def _metric_op_from_text(raw: str) -> str:
            token = re.sub(r"\s+", "", str(raw or "").lower())
            if token in {">", "초과"} or any(x in token for x in ("보다높", "보다크", "보다많")):
                return "GT"
            if token in {">=", "이상"}:
                return "GTE"
            if token in {"<", "미만"} or any(x in token for x in ("보다낮", "보다작", "보다적")):
                return "LT"
            if token in {"<=", "이하"}:
                return "LTE"
            if token in {"=", "==", "같음", "동일"}:
                return "EQ"
            if token in {"!=", "다름"}:
                return "NE"
            return ""

        # 1) 숫자 임계값 비교
        metric_numeric_re = re.compile(
            rf"(?P<metric>{metric_alias_pattern})\s*(?:값\s*)?(?:이|가)?\s*"
            r"(?P<value>-?\d+(?:\.\d+)?)\s*"
            r"(?P<op>이상|이하|초과|미만|>=|<=|>|<|보다\s*(?:높|크|많|낮|작|적)[^\s,]*)",
            re.I,
        )
        for m in metric_numeric_re.finditer(low):
            metric = metric_aliases.get(m.group("metric").lower())
            operator = _metric_op_from_text(m.group("op"))
            if not metric or not operator:
                continue
            direction = "LONG" if metric == "long_score" else "SHORT" if metric == "short_score" else "AUTO"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric=metric, metric_operator=operator, metric_value=float(m.group("value")),
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 2) 현재 지표 A와 지표 B 비교. 계산은 TREND에 둘 다 요청합니다.
        metric_relation_re = re.compile(
            rf"(?P<left>{metric_alias_pattern})\s*(?:이|가)?\s*"
            rf"(?P<right>{metric_alias_pattern})\s*(?:보다)?\s*"
            r"(?P<rel>위|위쪽|높|높음|높을|크|큰|아래|아래쪽|낮|낮음|낮을|작|작은|>=|<=|>|<)",
            re.I,
        )
        directional_pairs = {
            ("macd", "macd_signal"), ("plus_di", "minus_di"),
            ("aroon_up", "aroon_down"), ("vortex_plus", "vortex_minus"),
            ("price", "vwap"),
        }
        reverse_pairs = {(b, a) for a, b in directional_pairs}
        for m in metric_relation_re.finditer(low):
            left = metric_aliases.get(m.group("left").lower())
            right = metric_aliases.get(m.group("right").lower())
            rel = str(m.group("rel") or "").lower()
            if not left or not right or left == right:
                continue
            operator = "GT" if any(x in rel for x in ("위", "높", "크")) or rel in {">", ">="} else "LT"
            if rel in {">=", "<="}:
                operator = "GTE" if rel == ">=" else "LTE"
            direction = "AUTO"
            if (left, right) in directional_pairs:
                direction = "LONG" if operator in {"GT", "GTE"} else "SHORT"
            elif (left, right) in reverse_pairs:
                direction = "SHORT" if operator in {"GT", "GTE"} else "LONG"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric=left, metric_operator=operator, metric_rhs=right,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 3) Supertrend 방향 상태. TREND 내부 bool을 1=상승, 0=하락으로 query합니다.
        supertrend_re = re.compile(
            r"(?:supertrend|슈퍼트랜드|슈퍼트렌드)\s*(?:이|가)?\s*(상승|하락|매수|매도|롱|숏)", re.I
        )
        for m in supertrend_re.finditer(low):
            bullish = m.group(1) in {"상승", "매수", "롱"}
            direction = "LONG" if bullish else "SHORT"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric="supertrend", metric_operator="EQ", metric_value=1.0 if bullish else 0.0,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 같은 종류 조건이 여러 번 있어도 전부 보존합니다.
        # 각 조건의 방향/side는 문장 전체가 아니라 해당 표현 주변에서 판정합니다.
        trend_re = re.compile(
            r"상승\s*(?:추세|중)|하락\s*(?:추세|중)|(?<!상승)(?<!하락)추세", re.I
        )
        for m in trend_re.finditer(low):
            trend_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, trend_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, trend_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            token = re.sub(r"\s+", "", m.group(0).lower())
            direction = "LONG" if token.startswith("상승") else "SHORT" if token.startswith("하락") else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("TREND", tf, direction=direction))
            if tfs:
                last_condition_pos = max(last_condition_pos, trend_pos)

        for m in re.finditer(r"원비", low, re.I):
            wonbi_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, wonbi_pos)
            if not tfs and conditions:
                tfs = (conditions[-1].tf,)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            side = _nearest_marker(
                wonbi_pos, m.end(),
                (("LOWER", ("하단",)), ("UPPER", ("상단",))),
            )
            direction = "LONG" if side == "LOWER" else "SHORT" if side == "UPPER" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("WONBI", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, wonbi_pos)

        # Percentile OUT은 방향을 조건 자체에서 맵핑합니다.
        # 하단 OUT=LONG, 상단 OUT=SHORT, 단순 '아웃'=양방향(AUTO).
        out_re = re.compile(r"(?<!아웃)아웃(?!인)|(?<![a-z])out(?!\s*(?:-|→)?\s*in)", re.I)
        for out_match in out_re.finditer(low):
            out_pos = out_match.start()
            tfs = self._condition_tfs_before(clean, occurrences, out_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, out_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            percentile_side = self._command_language().get("percentile_side") or {}
            lower_terms = ("하단", *(str(x) for x in (percentile_side.get("LOWER") or [])))
            upper_terms = ("상단", *(str(x) for x in (percentile_side.get("UPPER") or [])))
            side = _nearest_marker(
                out_pos, out_match.end(),
                (("LOWER", lower_terms), ("UPPER", upper_terms)),
            )
            direction = "LONG" if side == "LOWER" else "SHORT" if side == "UPPER" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("PERCENTILE", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, out_pos)

        for m in re.finditer(r"fvg", low, re.I):
            fvg_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, fvg_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, fvg_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            side = _nearest_marker(
                fvg_pos, m.end(),
                (("BULL", ("상승",)), ("BEAR", ("하락",))),
            )
            direction = "LONG" if side == "BULL" else "SHORT" if side == "BEAR" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("FVG", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, fvg_pos)

        # 외부유동성은 레벨 자체가 방향을 결정할 수 있습니다.
        # 전일저가/PDL=LONG, 전일고가/PDH=SHORT 등.
        liquidity_aliases = (
            (("전일저가",), "PDL", "LONG"),
            (("전일고가",), "PDH", "SHORT"),
            (("이전세션저가",), "SESSION_LOW", "LONG"),
            (("이전세션고가",), "SESSION_HIGH", "SHORT"),
        )
        matched_liquidity = False
        liquidity_hits: list[tuple[int, int, str, str]] = []
        for aliases, selector, mapped_direction in liquidity_aliases:
            pattern = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
            for m in re.finditer(pattern, low, re.I):
                liquidity_hits.append((m.start(), m.end(), selector, mapped_direction))
        for pos, _end, selector, mapped_direction in sorted(liquidity_hits):
            tf = self._nearest_tf_before(occurrences, pos)
            if not tf:
                tf = self._command_default_tf()
            _append_condition(ConditionSpec("SWEEP", tf, direction=mapped_direction, side=selector))
            last_condition_pos = max(last_condition_pos, pos)
            matched_liquidity = True

        if not matched_liquidity:
            sweep_re = re.compile(r"외부유동성", re.I)
            for m in sweep_re.finditer(low):
                sweep_pos = m.start()
                tfs = self._condition_tfs_before(clean, occurrences, sweep_pos)
                if not tfs and occurrences:
                    nearest = self._nearest_tf_before(occurrences, sweep_pos)
                    tfs = (nearest,) if nearest else (occurrences[0][2],)
                if not tfs:
                    default_tf = self._command_default_tf()
                    tfs = (default_tf,) if default_tf else ()
                marker = _nearest_marker(
                    sweep_pos, m.end(),
                    (("LONG", ("매수",)), ("SHORT", ("매도",))),
                )
                direction = marker or "AUTO"
                for tf in tfs:
                    _append_condition(ConditionSpec("SWEEP", tf, direction=direction))
                if tfs:
                    last_condition_pos = max(last_condition_pos, sweep_pos)

        if not conditions:
            return None
        if not final_direction_explicit:
            final_direction = self._infer_condition_default_direction(conditions)
        oz_tfs = self._extract_oz_tfs(clean, occurrences, last_condition_pos)
        if not oz_tfs:
            return None

        validation_mode = "BLIND" if "무지성" in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        combination = "ANY" if "또는" in low else "ALL"
        persistent = "계속" in low
        spec_id = f"PRIVATE:{owner_chat_id}:{time.time_ns()}"
        spec = StrategySpec(
            spec_id=spec_id,
            name="개인전략",
            symbol=symbol,
            conditions=tuple(conditions),
            oz_tfs=oz_tfs,
            combination=combination,
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
            final_direction=final_direction,
            final_direction_explicit=final_direction_explicit,
            destination="PRIVATE",
            owner_chat_id=owner_chat_id,
            persistent=persistent,
            enabled=True,
            time_filters=self._parse_time_filters(clean),
            source="PRIVATE",
        )
        spec.validate()
        return spec

    def _parse_condition_notify_local(self, text: str, owner_chat_id: str) -> Optional[StrategySpec]:
        """올존 없이 Composer 조건 자체가 성립하면 바로 알려주는 1회성/지속 알림."""
        clean = str(text or "").strip()
        low = clean.lower()
        if "올존" in low or not "알려" in low:
            return None

        # 기존 Composer 조건 파서를 재사용하되 최종 행동만 OZ가 아닌 NOTIFY로 바꿉니다.
        synthetic = clean + f" {self._command_default_tf()} 올존"
        spec = self._parse_private_strategy_local(synthetic, owner_chat_id)
        if spec is None:
            return None
        spec.spec_id = f"NOTIFY:{owner_chat_id}:{time.time_ns()}"
        macro_hits = self._condition_macro_matches(clean)
        spec.name = str(macro_hits[0]["name"]) if len(macro_hits) == 1 else "조건알림"
        spec.final_action = "NOTIFY"
        spec.oz_tfs = ()
        spec.validation_mode = "NORMAL"
        spec.trigger_mode = "OZ"
        spec.persistent = "계속" in low
        spec.validate()
        return spec


    def _add_private_spec(self, spec: StrategySpec) -> None:
        with self._lock:
            self._remember_watch_command(spec.spec_id, spec.owner_chat_id, "PRIVATE", "PRIVATE")
            self.manual_specs[spec.spec_id] = spec
            self._save_private_state_locked()
            self._subscription_dirty = True
        title = "✅ 조건알림 등록" if spec.final_action == "NOTIFY" else "✅ 개인전략 등록"
        self.send_telegram(title + "\n" + spec.summary(), spec.owner_chat_id,
                           watch_id=spec.spec_id, watch_registration=True)
        logging.info("🟣 [Composer 개인전략] 등록 | %s | %s", spec.spec_id, spec.summary())

    def _reset_owner(self, owner_chat_id: str) -> int:
        with self._lock:
            keys = [k for k, s in self.manual_specs.items() if s.owner_chat_id == owner_chat_id]
            chain_keys = [k for k, c in self.timed_chains.items() if c.owner_chat_id == owner_chat_id]
            fvg_watch_keys = [
                k for k, w in self.fvg_created_watches.items() if w.request_chat_id == owner_chat_id
            ]
            for key in keys:
                self.manual_specs.pop(key, None)
                for direction in ("LONG", "SHORT"):
                    self._last_signatures.pop((key, direction), None)
            active_changed = False
            for child_id, payload in list(self._active_children.items()):
                if str(payload.get("request_chat_id") or "") == owner_chat_id:
                    self._active_children.pop(child_id, None)
                    active_changed = True
            if active_changed:
                self._save_active_children_state_locked()
            for key in chain_keys:
                self.timed_chains.pop(key, None)
            for key in fvg_watch_keys:
                self.fvg_created_watches.pop(key, None)
            self._save_private_state_locked()
            self._save_timed_chain_state_locked()
            self._save_fvg_created_watch_state_locked()
            self._subscription_dirty = True
        with self._lock:
            for link in self._watch_message_links.values():
                if link.get("owner_chat_id") == owner_chat_id:
                    link["status"] = "cancelled"
            self._save_private_state_locked()
        # monitor_OZ의 owner scoped reset. 공식/다른 사용자에는 영향 없음.
        self._push({"action": "RESET_ALL", "request_chat_id": owner_chat_id})
        return len(keys) + len(chain_keys) + len(fvg_watch_keys)

    def _list_owner(self, owner_chat_id: str) -> None:
        with self._lock:
            specs = [s for s in self.manual_specs.values() if s.owner_chat_id == owner_chat_id]
            chains = [c for c in self.timed_chains.values() if c.owner_chat_id == owner_chat_id]
            fvg_watches = [w for w in self.fvg_created_watches.values() if w.request_chat_id == owner_chat_id]
        if not specs and not chains and not fvg_watches:
            self.send_telegram("ℹ️ 등록된 개인전략/시간연쇄가 없습니다.", owner_chat_id)
            return
        rows = [f"• 전략 {idx}. {s.summary()}" for idx, s in enumerate(specs, 1)]
        rows.extend(f"• 시간연쇄 {idx}. {c.summary()}" for idx, c in enumerate(chains, 1))
        rows.extend(f"• FVG 생성감시 {idx}. {w.symbol} · {w.label()}" for idx, w in enumerate(fvg_watches, 1))
        self.send_telegram("📋 개인전략/시간연쇄\n" + "\n".join(rows), owner_chat_id)

    def _detect_intent(self, text: str) -> str:
        intent = self.command_interpreter.detect_intent(text, self._parse_chain_triggers)
        low = str(text or "").lower()
        # 공용 복합조건이 단독 알림으로 들어오면 기존 intent 사전에 새 문구를 매번
        # 하드코딩하지 않고 canonical COMPOUND_CONDITION 계약으로 라우팅합니다.
        if "알려" in low and "올존" not in low and not self._duration_matches(text):
            triggers = self._parse_chain_triggers(text, len(text), [])
            if len(triggers) == 1:
                condition_type, evaluation_mode = trigger_watch_contract(triggers[0][2])
                if condition_type == "COMPOUND_CONDITION":
                    if evaluation_mode == "CLOSE" or intent in {"FALLBACK", "GENERIC_WATCH", "GENERIC_INVALID"}:
                        return "TIMED_CHAIN"
        return intent

    def _query_target_from_text(self, text: str, label: str) -> tuple[str, str]:
        """조회 명령의 종목/TF를 검증하여 잘못된 요청이 엔진으로 넘어가지 않게 합니다."""
        symbol = self._parse_symbol(text)
        if not symbol:
            raise ValueError(f"{label} 조회 종목을 찾지 못했습니다")
        occ = self._tf_occurrences(text)
        tf = occ[0][2] if occ else self._command_default_tf()
        if tf not in CONDITION_DATA_TFS:
            raise ValueError(
                f"{label} 조회가 지원하지 않는 시간봉: {tf} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        return symbol, tf

    def _generic_invalid_message(self, text: str) -> str:
        return self.command_interpreter.generic_invalid_message(text)

    def _trend_query_from_text(self, text: str, owner_chat_id: str) -> bool:
        low = str(text).lower()
        if "추세" not in low or not any(x in low for x in ("알려", "조회")):
            return False
        symbol, tf = self._query_target_from_text(text, "추세")
        self._push({
            "action": "TREND_QUERY",
            "request_id": stable_id("QUERY", owner_chat_id, time.time_ns()),
            "request_chat_id": owner_chat_id,
            "symbol": symbol,
            "source_tf": tf,
        })
        return True

    def _trend_score_query_from_text(self, text: str, owner_chat_id: str) -> bool:
        """'추세점수 몇 점?' 요청 때만 INDICATOR에 전체 지표 추세점수를 on-demand 계산시킵니다."""
        if not self.command_interpreter.is_trend_score_query(text):
            return False
        symbol, tf = self._query_target_from_text(text, "추세점수")
        self._push({
            "action": "TREND_QUERY",
            "request_id": stable_id("QUERY", owner_chat_id, time.time_ns()),
            "request_chat_id": owner_chat_id,
            "purpose": "TREND_SCORE_QUERY",
            "symbol": symbol,
            "source_tf": tf,
            "requested_fields": ["trend_score", "long_score", "short_score"],
        })
        return True

    def _fvg_query_from_text(self, text: str, owner_chat_id: str) -> bool:
        low = str(text).lower()
        if not any(x in low for x in ("fvg",)):
            return False
        if not any(x in low for x in ("알려", "조회")):
            return False
        symbol, tf = self._query_target_from_text(text, "FVG")
        self._push({
            "action": "FVG_QUERY",
            "request_id": stable_id("QUERY", owner_chat_id, time.time_ns()),
            "request_chat_id": owner_chat_id,
            "symbol": symbol,
            "source_tf": tf,
        })
        return True

    def _sweep_query_from_text(self, text: str, owner_chat_id: str) -> bool:
        low = str(text).lower()
        if not any(x in low for x in ("외부유동성",)):
            return False
        if not any(x in low for x in ("알려", "조회")):
            return False
        symbol, tf = self._query_target_from_text(text, "SWEEP")
        london = str(self.config.get("LONDON") or self.config.get("MAIN_LONDON") or "").strip()
        newyork = str(self.config.get("NEWYORK") or self.config.get("MAIN_NEWYORK") or "").strip()
        self._push({
            "action": "SWEEP_QUERY",
            "request_id": stable_id("QUERY", owner_chat_id, time.time_ns()),
            "request_chat_id": owner_chat_id,
            "symbol": symbol,
            "source_tf": tf,
            "levels": list(DEFAULT_SWEEP_LEVELS),
            "session_london": london,
            "session_newyork": newyork,
        })
        return True

    def _parse_canonical_ma_watch(self, text: str, owner_chat_id: str) -> Optional[dict]:
        from watch_ma import parse_canonical_command
        value = parse_canonical_command(text, self.command_interpreter)
        if value is None:
            return None
        return dict(value, action="GENERIC_WATCH",
                    watch_id=stable_id("GENMANUAL", owner_chat_id, time.time_ns(), length=20),
                    persistent=False, request_chat_id=owner_chat_id, silent=False)

    def _parse_generic_watch_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """시간연쇄/OZ가 아닌 단일 조건 알림을 GenericWatch payload로 변환합니다."""
        canonical_ma = self._parse_canonical_ma_watch(text, owner_chat_id)
        if canonical_ma is not None:
            return canonical_ma
        clean = str(text or "").strip()
        low = clean.lower()
        if not "알려" in low:
            return None
        if "올존" in low or "지금부터" in low:
            return None
        # 'OUT→IN' 자체의 화살표는 단일 조건 표현이므로 화살표 유무로 배제하지 않습니다.
        if self._duration_matches(clean):
            return None

        triggers_raw = self._parse_chain_triggers(clean, len(clean), [])
        if len(triggers_raw) != 1:
            return None
        trig = triggers_raw[0][2]
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        condition_type, evaluation_mode = trigger_watch_contract(trig)
        return {
            "action": "GENERIC_WATCH",
            "watch_id": stable_id("GENMANUAL", owner_chat_id, time.time_ns(), length=20),
            "watch_type": condition_type,
            "evaluation_mode": evaluation_mode,
            "timeframes": [trig.tf],
            "symbol": symbol,
            "direction": trig.direction,
            "level_side": trig.level_side,
            "ma_family": trig.ma_family,
            "fast_period": trig.fast_period,
            "slow_period": trig.slow_period,
            "persistent": False,
            "request_chat_id": owner_chat_id,
            "silent": False,
        }

    def _parse_direct_oz_watch_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """조건식 없이 직접 요청한 OZ 감시를 MANUAL_WATCH payload로 변환합니다.

        예: "골드 1분 올존 알려줘", "골드 1분 5분 무지성 브레이커 올존 알려줘".
        시간연쇄 문장은 앞 단계 파서가 담당하므로 여기서는 받지 않습니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        if "올존" not in low:
            return None
        if not "알려" in low:
            return None
        if "지금부터" in low or self._duration_matches(clean):
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        occurrences = self._tf_occurrences(clean)
        invalid_explicit = [tf for _a, _b, tf in occurrences if tf not in OZ_BASE_TFS]
        if invalid_explicit:
            requested = ",".join(dict.fromkeys(invalid_explicit))
            raise ValueError(
                f"지원하지 않는 올존 시간봉입니다: {requested} "
                f"(지원: {','.join(OZ_BASE_TFS)})"
            )
        oz_tfs = self._extract_oz_tfs(clean, occurrences, -1)
        if not oz_tfs:
            oz_tfs = (self._command_default_tf(),)

        validation_mode = "BLIND" if "무지성" in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        direction = self._parse_oz_direction(clean)
        persistent = "계속" in low
        watch_id = stable_id("DIRECTOZ", owner_chat_id, time.time_ns(), length=20)

        # 사용자가 직접 지정한 가격(예: "골드 4310에서 올존 나오면 알려줘")은
        # SWEEP이 계산한 공식 레벨과 섞지 않고, 개인 Watch 전용 수동 외부유동성 레벨로 표시합니다.
        # 실제 터치 판독과 ATR14 x1.5 자격 심사는 monitor_OZ의 기존 외부유동성 Gate가 담당합니다.
        manual_level_price = None

        # 숫자가 포함된 종목명(예: US30/NAS100/US500)을 수동 가격으로 오인하지 않도록
        # 현재 문장에서 실제 종목으로 인식되는 토큰만 가격 검색 대상에서 마스킹합니다.
        # 종목 뒤에 별도 가격을 쓴 "US500 7000에서"의 7000은 그대로 남겨둡니다.
        price_scan_text = low
        protected_symbol_terms: set[str] = set()

        explicit_symbol = self._explicit_symbol_from_text(clean)
        if explicit_symbol and any(ch.isdigit() for ch in explicit_symbol):
            protected_symbol_terms.add(str(explicit_symbol).strip().lower())

        for canonical, aliases in (self._command_language().get("symbols") or {}).items():
            canonical_text = str(canonical or "").strip().lower()
            terms = [canonical_text, *(str(x or "").strip().lower() for x in (aliases or []))]
            terms = [x for x in terms if x]
            if not terms or not any(self._alias_present(low, term) for term in terms):
                continue

            suffix_match = re.search(r"(\d+)$", canonical_text)
            numeric_suffix = suffix_match.group(1) if suffix_match is not None else ""
            for term in terms:
                if any(ch.isdigit() for ch in term):
                    protected_symbol_terms.add(term)
                elif numeric_suffix:
                    # "나스닥100", "다우30", "에스앤피500"처럼 alias와
                    # canonical 숫자 suffix를 붙여 쓰는 표현도 종목명으로 보호합니다.
                    protected_symbol_terms.add(term + numeric_suffix)

        for term in sorted(protected_symbol_terms, key=len, reverse=True):
            price_scan_text = re.sub(
                re.escape(term),
                lambda m: " " * (m.end() - m.start()),
                price_scan_text,
                flags=re.I,
            )

        # "지정가 4300"처럼 사용자가 수동 외부유동성 가격임을 명시하면
        # 뒤에 "에서"가 없어도 가격으로 확정합니다. 다른 동의어는 command_aliases에서
        # "지정가"로 정규화해 이 단일 문법으로 들어오게 합니다.
        level_match = re.search(
            r"지정가\s*(?P<price>\d[\d,]*(?:\.\d+)?)",
            price_scan_text,
        )
        if level_match is None:
            level_match = re.search(
                r"(?<![\d.])(?P<price>\d[\d,]*(?:\.\d+)?)\s*"
                r"(?:가격|레벨)?\s*(?:부근|근처)?\s*에서",
                price_scan_text,
            )
        if level_match is not None:
            try:
                candidate = float(level_match.group("price").replace(",", ""))
            except (TypeError, ValueError):
                candidate = float("nan")
            if math.isfinite(candidate) and candidate > 0.0:
                manual_level_price = candidate

        payload = {
            "action": "MANUAL_WATCH",
            "watch_id": watch_id,
            "timeframes": list(oz_tfs),
            "symbol": symbol,
            "direction": direction,
            "persistent": persistent,
            "request_chat_id": owner_chat_id,
            "validation_mode": validation_mode,
            "trigger_mode": trigger_mode,
            "source_spec_id": watch_id,
            "source_name": "직접 OZ 감시",
        }
        if manual_level_price is not None:
            # 별도 setup TF 문법을 새로 만들지 않습니다. 직접 OZ 요청의 첫 TF를
            # 수동 외부유동성의 source/setup TF로 사용합니다. TF 미지정 시 기존 기본 TF입니다.
            external_source_tf = oz_tfs[0]
            payload.update({
                "external_watch_id": watch_id,
                "external_source_tf": external_source_tf,
                "external_liquidity_required": True,
                "external_source_kind": "MANUAL_LEVEL",
                "external_level_price": manual_level_price,
                "external_level_name": f"수동 가격 {manual_level_price:g}",
                "external_registered_at": time.time(),
                "source_name": f"직접 OZ 감시 · 수동 외부유동성 {manual_level_price:g}",
            })
        return payload

    def _handle_fallback_strategy(self, clean: str, owner: str) -> None:
        try:
            spec = self._parse_private_strategy_local(clean, owner)
        except Exception:
            logging.exception("[Composer] 로컬 개인전략 파싱 오류")
            spec = None
        if spec is None:
            self.send_telegram(
                "❓ 전략을 이해하지 못했습니다. 예: 골드 1시간 상승추세중 하단 원비에서 1분 무지성 브레이커 올존 알려줘",
                owner,
            )
            return
        self._add_private_spec(spec)

    def _parse_fvg_created_oz_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """'신규 FVG 생성 → OZ'를 FVG_CREATED 이벤트 기반으로 직접 연결합니다.

        기존 'FVG에서 올존'은 FVG_TOUCH 조건이므로 생성/신규 표현이 있을 때만 이 경로를 사용합니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        if "올존" not in low:
            return None

        fvg_match = None
        for m in re.finditer(r"fvg", low, re.I):
            local = low[max(0, m.start() - 18):min(len(low), m.end() + 22)]
            if any(word in local for word in ("생성", "신규")):
                fvg_match = m
                break
        if fvg_match is None:
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        occurrences = self._tf_occurrences(clean)
        fvg_tf = self._nearest_tf_before(occurrences, fvg_match.start()) or self._command_default_tf()
        if fvg_tf not in CONDITION_DATA_TFS:
            raise ValueError(f"FVG가 지원하지 않는 시간봉: {fvg_tf}")

        local = low[max(0, fvg_match.start() - 18):min(len(low), fvg_match.end() + 22)]
        fvg_direction = "LONG" if "상승" in local else "SHORT" if "하락" in local else None
        oz_tfs = self._extract_oz_tfs(clean, occurrences, fvg_match.end())
        if not oz_tfs:
            default_oz = self._command_default_tf()
            oz_tfs = (default_oz,) if default_oz in OZ_BASE_TFS else ("1m",)

        explicit_oz_direction = self._parse_oz_direction(clean)
        oz_direction = explicit_oz_direction or fvg_direction

        return {
            "watch_id": stable_id("FVGCREATEDOZ", owner_chat_id, time.time_ns(), length=20),
            "symbol": symbol, "timeframes": [fvg_tf], "direction": fvg_direction,
            "persistent": "계속" in low, "request_chat_id": owner_chat_id, "silent": False,
            "final_action": "OZ", "oz_tfs": list(oz_tfs),
            "oz_direction": oz_direction,
            "validation_mode": "BLIND" if "무지성" in low else "NORMAL",
            "trigger_mode": _oz_trigger_mode(low),
        }

    def _handle_oz_intent(self, clean: str, owner: str, raw_clean: Optional[str] = None, gemini_retry: bool = False) -> None:
        # OZ 문장은 OZ→OZ/예약 Watch -> 기존 시간연쇄 -> 조건부 Composer 전략 -> 직접 OZ 순으로 해석합니다.
        try:
            oz_chain = self._parse_oz_watch_chain_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] OZ 연쇄/예약 명령 거부 | %s", exc)
            self.send_telegram(f"❌ OZ 연쇄/예약 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] OZ 연쇄/예약 로컬 파싱 오류")
            oz_chain = None
        if oz_chain is not None:
            self._add_timed_chain(oz_chain)
            return

        try:
            chain = self._parse_timed_chain_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] 시간연쇄 명령 거부 | %s", exc)
            self.send_telegram(f"❌ 시간연쇄 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] 시간연쇄 로컬 파싱 오류")
            chain = None
        if chain is not None:
            self._add_timed_chain(chain)
            return

        try:
            fvg_created_oz = self._parse_fvg_created_oz_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] FVG 생성→OZ 명령 거부 | %s", exc)
            self.send_telegram(f"❌ FVG 생성→올존 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] FVG 생성→OZ 로컬 파싱 오류")
            fvg_created_oz = None
        if fvg_created_oz is not None:
            self._add_fvg_created_watch(fvg_created_oz)
            return

        try:
            spec = self._parse_private_strategy_local(clean, owner)
        except Exception:
            logging.exception("[Composer] 로컬 개인전략 파싱 오류")
            spec = None
        if spec is not None:
            self._add_private_spec(spec)
            return

        try:
            direct_oz = self._parse_direct_oz_watch_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] 직접 OZ 명령 거부 | %s", exc)
            self.send_telegram(f"❌ 올존 감시 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] 직접 OZ 파싱 오류")
            direct_oz = None
        if direct_oz:
            self._push(direct_oz)
            return

        # 로컬/외부 alias로도 해석되지 않은 OZ 표현만 Gemini가 자연어를 한 번 정규화합니다.
        # Gemini가 payload를 직접 만들지는 않으며, 결과는 다시 동일한 로컬 parser/validator를 통과합니다.
        if not gemini_retry:
            canonical = self._gemini_canonicalize_command(raw_clean or clean)
            if canonical and canonical.strip() and canonical.strip() != clean.strip():
                self.handle_command(canonical, incoming_chat_id=owner, _gemini_retry=True)
                return
        self.send_telegram(
            "❓ 전략을 이해하지 못했습니다. 예: 골드 1시간 상승추세중 하단 원비에서 1분 무지성 브레이커 올존 알려줘",
            owner,
        )

    def handle_command(
        self, text: str, incoming_chat_id: Optional[str] = None, _gemini_retry: bool = False,
        *, message_id: Optional[int] = None, reply_to_message_id: Optional[int] = None,
    ) -> None:
        owner = str(incoming_chat_id or self.chat_id or "").strip()
        previous = getattr(self._command_context, "current", None)
        context = previous if _gemini_retry and previous else {
            "owner_chat_id": owner, "message_id": self._telegram_message_id(message_id),
            "original_text": str(text or ""),
        }
        self._command_context.current = context
        try:
            if str(text or "").strip() == "취소":
                if owner:
                    self._cancel_watch_reply(owner, self._telegram_message_id(reply_to_message_id))
                return
            self._handle_command_text(text, incoming_chat_id, _gemini_retry)
        finally:
            self._command_context.current = previous

    def _handle_command_text(
        self, text: str, incoming_chat_id: Optional[str] = None, _gemini_retry: bool = False
    ) -> None:
        owner = str(incoming_chat_id or self.chat_id or "").strip()
        raw_clean = str(text or "").strip()
        # Canonical expressions must be validated before natural-language aliases
        # can rewrite spacing/names. Noncanonical commands follow the old path.
        if owner:
            try:
                canonical_ma = self._parse_canonical_ma_watch(raw_clean, owner)
            except ValueError as exc:
                self.send_telegram(f"❌ 조건 감시 명령 오류 · {exc}", owner)
                return
            if canonical_ma is not None:
                self._push(canonical_ma)
                return
        if re.search(r"(?i)(?<![A-Za-z])VA[HL](?:_TOUCH)?(?![A-Za-z])", raw_clean):
            self.send_telegram("❌ 지원하지 않는 SWEEP selector입니다.", owner)
            return
        clean = self._normalize_command_text(raw_clean)
        if not clean or not owner:
            return

        try:
            intent = self._detect_intent(clean)
        except ValueError as exc:
            # 현재 ValueError는 Generic MA period 검증 등 명령 자체의 유효성 오류입니다.
            logging.warning("[Composer] 명령 의도 판정 거부 | %s", exc)
            self.send_telegram(f"❌ 조건 감시 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] 명령 의도 판정 오류")
            intent = "FALLBACK"

        if intent == "RESET":
            count = self._reset_owner(owner)
            self.send_telegram(f"♻️ 개인전략 리셋 완료 · {count}건", owner)
            return
        if intent == "LIST":
            self._list_owner(owner)
            return
        if intent == "OZ":
            self._handle_oz_intent(clean, owner, raw_clean=raw_clean, gemini_retry=_gemini_retry)
            return

        # `2시부터 ... 알려줘` 같은 단일 조건 Watch는 예약시각 전에는 arm하지 않습니다.
        try:
            scheduled_generic = self._parse_scheduled_generic_chain_local(clean, owner)
        except ValueError as exc:
            self.send_telegram(f"❌ 예약 감시 명령 오류 · {exc}", owner)
            return
        except Exception:
            logging.exception("[Composer] 예약 Generic Watch 파싱 오류")
            scheduled_generic = None
        if scheduled_generic is not None:
            self._add_timed_chain(scheduled_generic)
            return

        if intent == "CONDITION_NOTIFY":
            try:
                notify_spec = self._parse_condition_notify_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 조건알림 명령 거부 | %s", exc)
                self.send_telegram(f"❌ 조건알림 명령 오류 · {exc}", owner)
                return
            except Exception:
                logging.exception("[Composer] 조건알림 파싱 오류")
                notify_spec = None
            if notify_spec is not None:
                self._add_private_spec(notify_spec)
                return
            self.send_telegram("❌ 조건알림 명령 형식이 불완전합니다", owner)
            return

        if intent == "GENERIC_WATCH":
            try:
                generic_payload = self._parse_generic_watch_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 단일 조건 명령 거부 | %s", exc)
                self.send_telegram(f"❌ 조건 감시 명령 오류 · {exc}", owner)
                return
            except Exception:
                logging.exception("[Composer] 단일 조건 파싱 오류")
                generic_payload = None
            if generic_payload:
                if str(generic_payload.get("watch_type") or "").upper() == "FVG_NEW":
                    self._add_fvg_created_watch(generic_payload)
                else:
                    self._push(generic_payload)
                return
            self.send_telegram(f"❌ 조건 감시 명령 오류 · {self._generic_invalid_message(clean)}", owner)
            return

        if intent == "GENERIC_INVALID":
            self.send_telegram(f"❌ 조건 감시 명령 오류 · {self._generic_invalid_message(clean)}", owner)
            return

        if intent == "TREND_SCORE_QUERY":
            try:
                handled = self._trend_score_query_from_text(clean, owner)
            except ValueError as exc:
                self.send_telegram(f"❌ 추세점수 조회 명령 오류 · {exc}", owner)
                return
            if handled:
                return
            self.send_telegram("❌ 추세점수 조회 명령 형식이 불완전합니다", owner)
            return
        if intent == "TREND_QUERY":
            try:
                handled = self._trend_query_from_text(clean, owner)
            except ValueError as exc:
                self.send_telegram(f"❌ 추세 조회 명령 오류 · {exc}", owner)
                return
            if handled:
                return
            self.send_telegram("❌ 추세 조회 명령 형식이 불완전합니다", owner)
            return
        if intent == "FVG_QUERY":
            try:
                handled = self._fvg_query_from_text(clean, owner)
            except ValueError as exc:
                self.send_telegram(f"❌ FVG 조회 명령 오류 · {exc}", owner)
                return
            if handled:
                return
            self.send_telegram("❌ FVG 조회 명령 형식이 불완전합니다", owner)
            return
        if intent == "SWEEP_QUERY":
            try:
                handled = self._sweep_query_from_text(clean, owner)
            except ValueError as exc:
                self.send_telegram(f"❌ SWEEP 조회 명령 오류 · {exc}", owner)
                return
            if handled:
                return
            self.send_telegram("❌ SWEEP 조회 명령 형식이 불완전합니다", owner)
            return

        if intent == "TIMED_CHAIN":
            try:
                chain = self._parse_timed_chain_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 시간연쇄 명령 거부 | %s", exc)
                self.send_telegram(f"❌ 시간연쇄 명령 오류 · {exc}", owner)
                return
            except Exception:
                logging.exception("[Composer] 시간연쇄 로컬 파싱 오류")
                chain = None
            if chain is not None:
                self._add_timed_chain(chain)
                return

        # 외부 alias 사전까지 통과했는데도 FALLBACK이면 Gemini가 자연어만 한 번 정규화합니다.
        # Gemini 결과도 다시 이 함수로 들어와 동일한 로컬 parser/validator를 통과합니다.
        if not _gemini_retry:
            canonical = self._gemini_canonicalize_command(raw_clean)
            if canonical and canonical.strip() and canonical.strip() != clean.strip():
                self.handle_command(canonical, incoming_chat_id=owner, _gemini_retry=True)
                return
        self._handle_fallback_strategy(clean, owner)

    # -------------------------------
    # maintenance / server loop
    # -------------------------------







# ---------------------------------------------------------------------

# ---------------------------------------------------------------------


