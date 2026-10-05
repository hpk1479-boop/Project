# -*- coding: utf-8 -*-
"""
OZ Pattern Monitor
==================

Purpose
-------
Find the user's trend-internal double-bottom / double-top environment using
MT5 Staff data served by manager_KIM_PIPE over ZMQ.

No order execution. Telegram alert only.

LONG core
---------
1) Track four 1X Percentile OUT->IN engines independently: RSI / STO / DI / PRICE.
2) Track the latest 1X HMA6/HMA17 golden-cross and reconstruct the lowest price
   from the contiguous pre-cross bearish-alignment structure.
3) TRUE B0 is the lower of the Percentile OUT->IN extreme and the pre-GC HMA extreme.
4) The same Percentile family is preserved into the mapped middle/upper TF checks:
   - middle TF: same-side OUT
   - upper TF: same Percentile IN.
5) 1X: HMA6 > HMA17 must remain aligned.
7) Expiry uses OR clocks: TRUE B0 age, HMA6/17 cross age, and each Percentile registration age.
   Within the active window the OZ trigger family is accumulated (touch only):
      - TRUE B0 touch passes immediately
      - otherwise two or more of HMA17(open) touch / 원비 touch / candle pullback /
        HMA6 color turn (hull vs hull[2], closed candles) must have been TRUE
        while the candidate is alive.
8) HMA6 reclaim is intentionally NOT required because the alert is a leading entry alert.

SHORT is the exact mirror.

External-liquidity qualification
--------------------------------
SWEEP supplies only the external-liquidity event. Staff supplies ATR14/Wonbi values.
This monitor snapshots ATR14 at the sweep bar, applies the fixed 1.5x first/survival/TRUE-B0
qualification, then and only then allows the existing OZ final-trigger logic.

OZ profiles: NORMAL/BLIND x OZ/BREAKER (four profiles)
--------------------------------------------------
NORMAL = mapped middle/upper timeframe validation; BLIND = base timeframe only.
OZ = accumulated OZ trigger family; BREAKER = unchanged strict TRUE B0 break.
OZ evaluation uses only NORMAL/BLIND validation with OZ/BREAKER triggers.
Recipe strategies can combine these OZ events with closed-bar MA conditions.

OZ trigger family (non-BREAKER), touch only (no NEAR), accumulated while the
candidate is alive:
  1) TRUE B0 touch                      -> passes immediately
  2) HMA17(open) touch
  3) 원비 touch
  4) LONG: bearish engulf + bearish / 3 bearish,  SHORT: mirror
  5) LONG: HMA6 turns down (green->red),  SHORT: HMA6 turns up (red->green)
     Pine rule on closed candles: hull > hull[2] = green.
  2)~5): two or more distinct accumulated conditions pass.

Base-TF cancellation is common to every combination:
  opposite HMA6/17 cross / one-way 3 closed candles / timer expiry.
"""

from __future__ import annotations
from watch_ma import parse_ma_expression
import oz_profiles
from oz_profile_loader import load_saved_oz
from durable_protocol import Records, identity, atomic_json, read_json
import domain_memory
import functools
from domain_clock import uuid

from domain_clock import datetime as dt
import json
import logging
import os
import re
import sys
import threading
from domain_clock import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_ENDPOINT = "tcp://127.0.0.1:5555"
LOOP_SLEEP_SEC = 0.50

PERCENTILES = ("RSI", "STO", "DI", "PRICE")
REQUIRED_INDS = list(PERCENTILES)

# OZ base timeframe -> mapped middle / upper timeframe.
# Base OZ monitoring is supported up to 4h. Higher TFs are confirmation feeds only.
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


# -----------------------------------------------------------------------------
# Logging / config
# -----------------------------------------------------------------------------
def script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


LOG_DIR = Path(os.environ["MOSES_LOG_DIRECTORY"]) if os.environ.get("MOSES_LOG_DIRECTORY") else script_dir() / "logs"
LOG_PATH = LOG_DIR / "OZ_monitor.log"


def _atomic_write_json(path: Path, payload) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


def load_config(file_name: str = "config.txt") -> dict[str, str]:
    path = script_dir() / file_name
    cfg: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def csv_items(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def finite_number(v) -> bool:
    try:
        x = float(v)
        return pd.notna(x) and x != float("inf") and x != float("-inf")
    except (TypeError, ValueError):
        return False


# -----------------------------------------------------------------------------
# Trading time filter (KST by default)
# -----------------------------------------------------------------------------
def _parse_hhmm(text: str) -> int:
    s = text.strip().replace(":", "")
    if len(s) != 4 or not s.isdigit():
        raise ValueError(f"시간 형식 오류: {text} (HHMM 필요)")
    hh, mm = int(s[:2]), int(s[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"시간 값 오류: {text}")
    return hh * 60 + mm


def in_window(spec: str, now: dt.datetime) -> bool:
    if "-" not in spec:
        return False
    a, b = spec.split("-", 1)
    start, end = _parse_hhmm(a), _parse_hhmm(b)
    cur = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def within_trading_window(config: dict[str, str], now: Optional[dt.datetime] = None) -> bool:
    windows = csv_items(config.get("TRADING_WINDOWS"))
    if not windows:
        return True
    now = now or dt.datetime.now(KST)
    if now.tzinfo is not None:
        now = now.astimezone(KST)
    return any(in_window(w, now) for w in windows)


# -----------------------------------------------------------------------------
# On-demand OZ Watch command
# -----------------------------------------------------------------------------
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


def parse_oz_watch_command(text: str) -> list[str]:
    """Parse commands such as '1분 올존 알려줘' or '1분 2분 올존 알려줘'."""
    raw = str(text or "").strip().lower()
    if "올존" not in raw:
        return []
    if "알려줘" not in raw.replace(" ", ""):
        return []

    found: list[str] = []
    for num_s, unit in re.findall(r"(\d+)\s*(분|시간)", raw):
        tf = f"{int(num_s)}m" if unit == "분" else f"{int(num_s)}h"
        if tf in TF_MAP and tf not in found:
            found.append(tf)
    return found


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


# OZ 프로필 어휘는 oz_profiles.py 단일 정의를 사용합니다.
# NORMAL/BLIND × OZ/BREAKER의 네 프로필입니다.
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


class ExternalLiquidityController:
    """External-liquidity qualification owned by monitor_OZ.

    SWEEP contributes only fact events. ATR14 is read from Staff's ``atr_14`` column.
    The fixed 1.5 multiplier and all setup qualification state live here.
    """

    def __init__(self, *, event_state=None, sweep_registry=None):
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._specs = state.setdefault('specs', {})
        self._states = state.setdefault('states', {})
        self._invalidated = state.setdefault('invalidated', {})
        self._event_registry = sweep_registry
        self._state_path = LOG_DIR / "oz_external_liquidity_state.json"
        self._sweep_registry_path = LOG_DIR / "sweep_watch_state.json"
        self._sweep_registry_mtime_ns: Optional[int] = None
        self._sweep_registry_cache: dict[str, ExternalLiquiditySpec] = {}
        if event_state is None:
            self._load_state()
            self._bootstrap_sweep_registry()

    @staticmethod
    def _norm_tf(value) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _event_epoch(value) -> Optional[float]:
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            ts = pd.Timestamp(value)
            if pd.isna(ts):
                return None
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return float(ts.timestamp())
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _state_key(watch_id: str, direction: str, level_id: str) -> str:
        return "|".join((
            str(watch_id or "").strip(),
            str(direction or "").strip().upper(),
            str(level_id or "").strip(),
        ))

    def _states_for_watch_locked(
        self, watch_id: str, direction: Optional[str] = None
    ) -> list[ExternalLiquidityState]:
        wid = str(watch_id or "").strip()
        wanted_direction = str(direction or "").strip().upper()
        return [
            st for st in self._states.values()
            if st.watch_id == wid
            and st.direction in {"LONG", "SHORT"}
            and (wanted_direction not in {"LONG", "SHORT"} or st.direction == wanted_direction)
        ]

    def _state_for_direction_locked(self, watch_id: str, direction: str) -> Optional[ExternalLiquidityState]:
        direction = str(direction or "").strip().upper()
        if direction not in {"LONG", "SHORT"}:
            return None
        states = [
            st for st in self._states_for_watch_locked(watch_id, direction)
            if st.level_price is not None
        ]
        if not states:
            return None
        if direction == "LONG":
            # 실제 터치된 하단 유동성 중 가장 낮은 가격을 사용합니다.
            return min(states, key=lambda st: (float(st.level_price), -float(st.event_time or 0.0)))
        # 실제 터치된 상단 유동성 중 가장 높은 가격을 사용합니다.
        return max(states, key=lambda st: (float(st.level_price), float(st.event_time or 0.0)))

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            specs: dict[str, ExternalLiquiditySpec] = {}
            states: dict[str, ExternalLiquidityState] = {}
            for wid, item in (raw.get("specs", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").strip()
                source_tf = self._norm_tf(item.get("source_tf"))
                if wid and symbol and source_tf:
                    source_kind = str(item.get("source_kind") or "SWEEP").strip().upper()
                    manual_direction = str(item.get("manual_direction") or "").strip().upper() or None
                    if manual_direction not in {None, "LONG", "SHORT"}:
                        manual_direction = None
                    specs[str(wid)] = ExternalLiquiditySpec(
                        str(wid), symbol, source_tf,
                        source_kind=source_kind,
                        manual_level_price=(
                            float(item["manual_level_price"])
                            if finite_number(item.get("manual_level_price")) else None
                        ),
                        manual_direction=manual_direction,
                        registered_at=self._event_epoch(item.get("registered_at")),
                    )
            for stored_key, item in (raw.get("states", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                direction = str(item.get("direction") or "").upper() or None
                if direction not in {"LONG", "SHORT"}:
                    continue
                base_watch_id = str(item.get("watch_id") or "").strip()
                if not base_watch_id:
                    # 이전 저장 형식은 상태 사전의 키 자체가 감시 번호였습니다.
                    base_watch_id = str(stored_key).split("|", 1)[0]
                level_id = str(item.get("level_id") or "").strip()
                if not level_id:
                    # 이전 저장본에는 level_id가 없으므로 기존 상태를 잃지 않도록 임시 식별자를 만듭니다.
                    level_id = f"LEGACY:{item.get('level_code') or ''}:{item.get('level_price') or ''}"
                state_key = self._state_key(base_watch_id, direction, level_id)
                states[state_key] = ExternalLiquidityState(
                    watch_id=base_watch_id,
                    status=str(item.get("status") or "WAIT_SWEEP"),
                    direction=direction,
                    level_id=level_id,
                    level_code=str(item.get("level_code") or "") or None,
                    level_name=str(item.get("level_name") or "") or None,
                    level_price=float(item["level_price"]) if finite_number(item.get("level_price")) else None,
                    event_time=self._event_epoch(item.get("event_time")),
                    touch_high=float(item["touch_high"]) if finite_number(item.get("touch_high")) else None,
                    touch_low=float(item["touch_low"]) if finite_number(item.get("touch_low")) else None,
                    atr_snapshot=float(item["atr_snapshot"]) if finite_number(item.get("atr_snapshot")) else None,
                    max_distance=float(item["max_distance"]) if finite_number(item.get("max_distance")) else None,
                    reason=str(item.get("reason") or "") or None,
                )
            with self._lock:
                self._specs = specs
                self._states = states
                self._invalidated = {str(k): float(v) for k,v in raw.get('invalidated', {}).items()}
        except Exception:
            logging.exception("[OZ 외부유동성] 상태 복원 실패 | %s", self._state_path)

    def _current_sweep_registry(self, force: bool = False) -> dict[str, ExternalLiquiditySpec]:
        """현재 strategy_SWEEP registry만 읽어 SWEEP watch 존재 여부의 기준으로 사용합니다."""
        if getattr(self, '_event_registry', None) is not None:
            return dict(self._event_registry)
        path = self._sweep_registry_path
        try:
            stat = path.stat()
        except FileNotFoundError:
            with self._lock:
                self._sweep_registry_mtime_ns = None
                self._sweep_registry_cache = {}
            return {}

        with self._lock:
            if not force and self._sweep_registry_mtime_ns == stat.st_mtime_ns:
                return dict(self._sweep_registry_cache)

        try:
            raw = read_json(path)
            current: dict[str, ExternalLiquiditySpec] = {}
            for wid, item in (raw.get("watches", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").strip()
                source_tf = self._norm_tf(item.get("source_tf") or item.get("setup_tf"))
                if not wid or not symbol or not source_tf:
                    continue
                current[str(wid)] = ExternalLiquiditySpec(str(wid), symbol, source_tf)
            with self._lock:
                self._sweep_registry_mtime_ns = stat.st_mtime_ns
                self._sweep_registry_cache = dict(current)
            return current
        except Exception:
            logging.exception("[OZ 외부유동성] SWEEP registry 확인 실패 | %s", path)
            with self._lock:
                return dict(self._sweep_registry_cache)

    def _bootstrap_sweep_registry(self) -> None:
        """재시작 시 OZ의 SWEEP spec을 현재 strategy_SWEEP registry와 정확히 동기화합니다."""
        current = self._current_sweep_registry(force=True)
        changed = False
        with self._lock:
            current_ids = set(current)

            # 이전 실행에서 남은 SWEEP spec/state는 현재 registry에 없으면 제거합니다.
            stale_ids = {
                wid for wid, spec in self._specs.items()
                if spec.source_kind != "MANUAL_LEVEL" and wid not in current_ids
            }
            if stale_ids:
                for wid in sorted(stale_ids):
                    self._specs.pop(wid, None)
                self._states = {
                    key: st for key, st in self._states.items()
                    if st.watch_id not in stale_ids
                }
                changed = True

            for wid, spec in current.items():
                if self._specs.get(wid) != spec:
                    self._specs[wid] = spec
                    changed = True

            if changed:
                self._save_locked()

    def _save_locked(self) -> None:
        payload = {
            "version": 5,
            "invalidated": self._invalidated,
            "atr_period": EXTERNAL_ATR_PERIOD,
            "atr_multiplier": EXTERNAL_ATR_MULT,
            "specs": {
                wid: {
                    "symbol": spec.symbol,
                    "source_tf": spec.source_tf,
                    "source_kind": spec.source_kind,
                    "manual_level_price": spec.manual_level_price,
                    "manual_direction": spec.manual_direction,
                    "registered_at": spec.registered_at,
                }
                for wid, spec in self._specs.items()
            },
            "states": {
                state_key: {
                    "watch_id": st.watch_id,
                    "status": st.status,
                    "direction": st.direction,
                    "level_id": st.level_id,
                    "level_code": st.level_code,
                    "level_name": st.level_name,
                    "level_price": st.level_price,
                    "event_time": st.event_time,
                    "touch_high": st.touch_high,
                    "touch_low": st.touch_low,
                    "atr_snapshot": st.atr_snapshot,
                    "max_distance": st.max_distance,
                    "reason": st.reason,
                }
                for state_key, st in self._states.items()
            },
        }
        _atomic_write_json(self._state_path, payload)

    def register(self, payload: dict) -> None:
        wid = str(payload.get("watch_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        source_tf = self._norm_tf(
            payload.get("source_tf") or payload.get("setup_tf") or payload.get("external_source_tf")
        )
        if not wid or not symbol or not source_tf:
            return

        source_kind = str(payload.get("external_source_kind") or "").strip().upper()
        source_kind = "MANUAL_LEVEL" if source_kind == "MANUAL_LEVEL" else "SWEEP"
        manual_level = (
            float(payload.get("external_level_price"))
            if source_kind == "MANUAL_LEVEL" and finite_number(payload.get("external_level_price"))
            else None
        )
        if source_kind == "MANUAL_LEVEL" and manual_level is None:
            return

        manual_direction = str(payload.get("direction") or "").strip().upper() or None
        if manual_direction not in {None, "LONG", "SHORT"}:
            manual_direction = None
        registered_at = None
        if source_kind == "MANUAL_LEVEL":
            # command queue의 issued_at은 time_ns()이므로 수동 레벨의 epoch seconds로 사용하지 않습니다.
            registered_at = self._event_epoch(payload.get("external_registered_at"))
            if registered_at is None:
                registered_at = time.time()

        with self._lock:
            previous = self._specs.get(wid)
            if (
                previous is not None
                and previous.source_kind == "MANUAL_LEVEL"
                and source_kind == "MANUAL_LEVEL"
                and previous.symbol == symbol
                and previous.source_tf == source_tf
                and previous.manual_level_price == manual_level
            ):
                # 동일 수동 레벨의 중복 등록은 기존 시작시각/자동 판정 방향을 보존합니다.
                registered_at = previous.registered_at or registered_at
                if manual_direction is None:
                    manual_direction = previous.manual_direction

            spec = ExternalLiquiditySpec(
                wid, symbol, source_tf,
                source_kind=source_kind,
                manual_level_price=manual_level,
                manual_direction=manual_direction,
                registered_at=registered_at,
            )
            self._specs[wid] = spec

            identity_changed = (
                previous is not None
                and (
                    previous.symbol != spec.symbol
                    or previous.source_tf != spec.source_tf
                    or previous.source_kind != spec.source_kind
                    or previous.manual_level_price != spec.manual_level_price
                )
            )
            if identity_changed:
                self._states = {
                    key: st for key, st in self._states.items()
                    if st.watch_id != wid
                }
            self._save_locked()

        if source_kind == "MANUAL_LEVEL":
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🌐 [OZ 수동 외부유동성 Gate 등록] %s | %s %s | level=%.6f | direction=%s",
                    wid, symbol, source_tf, float(manual_level), manual_direction or "AUTO",
                )
        else:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🌐 [OZ 외부유동성 Gate 등록] %s | %s %s", wid, symbol, source_tf)

    def is_manual_level(self, watch_id: str) -> bool:
        with self._lock:
            spec = self._specs.get(str(watch_id or "").strip())
            return bool(spec is not None and spec.source_kind == "MANUAL_LEVEL")

    def cancel(self, watch_id: str) -> None:
        wid = str(watch_id or "").strip()
        if not wid:
            return
        with self._lock:
            removed = self._specs.pop(wid, None)
            before = len(self._states)
            self._states = {
                key: st for key, st in self._states.items()
                if st.watch_id != wid
            }
            if removed is not None or len(self._states) != before:
                self._save_locked()

    def reset(self) -> None:
        with self._lock:
            if not self._specs and not self._states:
                return
            self._specs.clear()
            self._states.clear()
            self._save_locked()

    def has_spec(self, watch_id: str) -> bool:
        with self._lock:
            return str(watch_id or "") in self._specs

    def spec(self, watch_id: str) -> Optional[ExternalLiquiditySpec]:
        with self._lock:
            return self._specs.get(str(watch_id or ""))

    def status(self, watch_id: str, direction: Optional[str] = None) -> str:
        with self._lock:
            if direction in {"LONG", "SHORT"}:
                st = self._state_for_direction_locked(watch_id, direction)
                return st.status if st is not None else "WAIT_SWEEP"
            states = self._states_for_watch_locked(watch_id)
            if not states:
                return "WAIT_SWEEP"
            latest = max(states, key=lambda x: float(x.event_time or 0.0))
            return latest.status

    def state(self, watch_id: str, direction: Optional[str] = None) -> Optional[ExternalLiquidityState]:
        with self._lock:
            if direction in {"LONG", "SHORT"}:
                st = self._state_for_direction_locked(watch_id, direction)
            else:
                states = self._states_for_watch_locked(watch_id)
                st = max(states, key=lambda x: float(x.event_time or 0.0)) if states else None
            if st is None:
                return None
            return ExternalLiquidityState(**st.__dict__)

    def matching_ids(self, symbol: Optional[str], source_tf: Optional[str] = None) -> list[str]:
        symbol = str(symbol or "").strip()
        source_tf = self._norm_tf(source_tf)
        with self._lock:
            out = []
            for wid, spec in self._specs.items():
                if symbol and spec.symbol != symbol:
                    continue
                if source_tf and spec.source_tf != source_tf:
                    continue
                out.append(wid)
            return out

    def apply_event(self, payload: dict) -> None:
        kind = str(payload.get("kind") or "").upper()
        if kind not in {"SWEEP_TOUCH", "SWEEP_INVALIDATED"}:
            return
        wid = str(payload.get("watch_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        source_tf = self._norm_tf(payload.get("source_tf"))
        direction = str(payload.get("direction") or "").strip().upper()
        level_id = str(payload.get("level_id") or "").strip()
        event_time = self._event_epoch(payload.get("event_time") or payload.get("touch_time"))
        if (
            not wid or not symbol or not source_tf or not level_id
            or direction not in {"LONG", "SHORT"} or event_time is None
        ):
            return

        # SWEEP event는 현재 strategy_SWEEP registry에 살아 있는 watch만 처리합니다.
        # 과거 sweep_event.jsonl replay가 이미 끝난 watch를 OZ에 부활시키지 못하게 합니다.
        current = self._current_sweep_registry()
        current_spec = current.get(wid)
        if current_spec is None:
            with self._lock:
                stale = self._specs.get(wid)
                if stale is not None and stale.source_kind != "MANUAL_LEVEL":
                    self._specs.pop(wid, None)
                    self._states = {
                        key: st for key, st in self._states.items()
                        if st.watch_id != wid
                    }
                    self._save_locked()
            return
        if current_spec.symbol != symbol or current_spec.source_tf != source_tf:
            return

        with self._lock:
            existing = self._specs.get(wid)
            if existing is not None and existing.source_kind == "MANUAL_LEVEL":
                return
            if existing != current_spec:
                self._specs[wid] = current_spec
            state_key = self._state_key(wid, direction, level_id)

            if kind == "SWEEP_INVALIDATED":
                prev = self._states.get(state_key)
                if prev is not None and prev.event_time is not None and event_time < prev.event_time:
                    return
                self._invalidated[state_key] = max(event_time, self._invalidated.get(state_key, float('-inf')))
                self._states.pop(state_key, None)
                self._save_locked()
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "🧹 [OZ 외부유동성 해제] %s | %s %s | %s | %s",
                        wid, symbol, source_tf, direction, level_id,
                    )
                return

            if not finite_number(payload.get("level_price")):
                return
            if event_time <= self._invalidated.get(state_key, float('-inf')):
                return
            prev = self._states.get(state_key)
            if prev is not None and prev.event_time is not None and event_time <= prev.event_time:
                return
            self._states[state_key] = ExternalLiquidityState(
                watch_id=wid,
                status="PENDING_ATR",
                direction=direction,
                level_id=level_id,
                level_code=str(payload.get("level_code") or "") or None,
                level_name=str(payload.get("level_name") or "") or None,
                level_price=float(payload["level_price"]),
                event_time=event_time,
                touch_high=float(payload["touch_high"]) if finite_number(payload.get("touch_high")) else None,
                touch_low=float(payload["touch_low"]) if finite_number(payload.get("touch_low")) else None,
            )
            self._save_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📥 [OZ 외부유동성 SWEEP 수신] %s | %s %s | %s %.6f | %s | %s",
                wid, symbol, source_tf, direction, float(payload["level_price"]), level_id, event_time,
            )

    @staticmethod
    def _row_epochs(df: pd.DataFrame) -> pd.Series:
        times = pd.to_datetime(df.get("time"), errors="coerce", utc=True)
        return times.map(lambda x: float(x.timestamp()) if not pd.isna(x) else float("nan"))

    def _register_manual_touch_locked(self, spec: ExternalLiquiditySpec, df: pd.DataFrame) -> bool:
        """Create the same PENDING_ATR state that a SWEEP_TOUCH event would create.

        A user supplied price is a synthetic external-liquidity level. It never enters
        strategy_SWEEP; monitor_OZ observes the source TF and feeds the existing ATR gate.
        """
        if spec.source_kind != "MANUAL_LEVEL" or not finite_number(spec.manual_level_price):
            return False
        if self._states_for_watch_locked(spec.watch_id):
            return False
        if df is None or df.empty:
            return False

        row = df.iloc[-1]
        level = float(spec.manual_level_price)
        changed = False

        # live bar의 high/low는 봉 시작 이후 누적값이므로, Watch 등록이 현재 봉 시작보다
        # 늦었다면 그 봉 안에서 "등록 전 터치"와 "등록 후 터치"를 구분할 수 없습니다.
        # 소급 오탐을 막기 위해 등록 이전에 이미 시작된 봉 전체를 제외하고,
        # 등록시각 이후에 새로 시작한 첫 봉부터 수동 레벨 터치를 인정합니다.
        event_time = self._event_epoch(row.get("time"))
        if event_time is None:
            return False
        if spec.registered_at is not None and event_time < float(spec.registered_at):
            return False

        direction = spec.manual_direction if spec.manual_direction in {"LONG", "SHORT"} else None
        if direction is None:
            # 접근 방향은 직전 종가를 우선 사용합니다. 현재 봉에서 이미 레벨을 관통한 경우에도
            # 봉 시작 전 어느 쪽에서 접근했는지 보존하기 위해서입니다.
            refs = []
            if len(df) >= 2:
                refs.append(df.iloc[-2].get("close"))
            refs.extend((row.get("open"), row.get("close")))
            for raw in refs:
                if not finite_number(raw):
                    continue
                ref = float(raw)
                if ref > level:
                    direction = "LONG"
                    break
                if ref < level:
                    direction = "SHORT"
                    break
            if direction is None:
                return False
            spec.manual_direction = direction
            changed = True

        row_low = float(row.get("low")) if finite_number(row.get("low")) else None
        row_high = float(row.get("high")) if finite_number(row.get("high")) else None

        touched = (
            direction == "LONG" and row_low is not None and row_low <= level
        ) or (
            direction == "SHORT" and row_high is not None and row_high >= level
        )
        if not touched:
            return changed

        level_id = f"MANUAL:{level:.12g}"
        state_key = self._state_key(spec.watch_id, direction, level_id)
        self._states[state_key] = ExternalLiquidityState(
            watch_id=spec.watch_id,
            status="PENDING_ATR",
            direction=direction,
            level_id=level_id,
            level_code="MANUAL_PRICE",
            level_name=f"수동 가격 {level:g}",
            level_price=level,
            event_time=event_time,
            touch_high=row_high,
            touch_low=row_low,
        )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📥 [OZ 수동 외부유동성 터치] %s | %s %s | %s %.6f | %s",
                spec.watch_id, spec.symbol, spec.source_tf, direction, level, event_time,
            )
        return True

    def source_timeframes(self, watch_ids: Iterable[str]) -> set[str]:
        ids = {str(x) for x in watch_ids if str(x)}
        with self._lock:
            out: set[str] = set()
            for wid in sorted(ids):
                spec = self._specs.get(wid)
                if spec is None:
                    continue
                states = self._states_for_watch_locked(wid)
                # 수동 가격 레벨은 SWEEP 이벤트가 따로 오지 않으므로 최초 터치를 잡기 전부터
                # source TF를 계속 받아야 합니다. 터치 후에는 기존 PENDING/ACTIVE ATR 감시를 그대로 사용합니다.
                if spec.source_kind == "MANUAL_LEVEL" and not states:
                    out.add(spec.source_tf)
                    continue
                if any(st.status in {"PENDING_ATR", "ACTIVE"} for st in states):
                    out.add(spec.source_tf)
            return out

    def update_market(self, symbol: str, data: dict[str, pd.DataFrame], watch_ids: Iterable[str]) -> None:
        ids = {str(x) for x in watch_ids if str(x)}
        changed = False
        with self._lock:
            for wid in sorted(ids):
                spec = self._specs.get(wid)
                if spec is None or spec.symbol != symbol:
                    continue
                df = data.get(spec.source_tf)
                if df is None or df.empty or "time" not in df.columns or EXTERNAL_ATR_COLUMN not in df.columns:
                    continue
                epochs = self._row_epochs(df)

                if self._register_manual_touch_locked(spec, df):
                    changed = True

                for st in self._states_for_watch_locked(wid):
                    if st.status not in {"PENDING_ATR", "ACTIVE"}:
                        continue

                    if st.status == "PENDING_ATR":
                        if st.event_time is None or st.level_price is None or st.direction not in {"LONG", "SHORT"}:
                            continue
                        diff = (epochs - float(st.event_time)).abs()
                        if diff.dropna().empty:
                            continue
                        idx = diff.idxmin()
                        if not finite_number(diff.loc[idx]) or float(diff.loc[idx]) > 0.5:
                            continue
                        row = df.loc[idx]
                        atr = row.get(EXTERNAL_ATR_COLUMN)
                        if not finite_number(atr) or float(atr) <= 0.0:
                            continue
                        st.atr_snapshot = float(atr)
                        st.max_distance = float(atr) * EXTERNAL_ATR_MULT
                        level = float(st.level_price)
                        row_low = float(row.get("low")) if finite_number(row.get("low")) else None
                        row_high = float(row.get("high")) if finite_number(row.get("high")) else None
                        touch_low = min(x for x in (row_low, st.touch_low) if x is not None) if (row_low is not None or st.touch_low is not None) else None
                        touch_high = max(x for x in (row_high, st.touch_high) if x is not None) if (row_high is not None or st.touch_high is not None) else None
                        exceeded = (
                            st.direction == "LONG" and touch_low is not None and touch_low < level - st.max_distance
                        ) or (
                            st.direction == "SHORT" and touch_high is not None and touch_high > level + st.max_distance
                        )
                        if exceeded:
                            st.status = "INVALID"
                            st.reason = "ATR_1P5_FIRST_CHECK"
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "🛑 [외부유동성 1차 탈락] %s | %s %s | level=%.6f ATR14=%.6f x1.5",
                                    wid, spec.symbol, spec.source_tf, level, st.atr_snapshot,
                                )
                        else:
                            st.status = "ACTIVE"
                            st.reason = None
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "✅ [외부유동성 1차 통과] %s | %s %s | %s %.6f | ATR14=%.6f x1.5",
                                    wid, spec.symbol, spec.source_tf, st.direction, level, st.atr_snapshot,
                                )
                        changed = True

                    if st.status == "ACTIVE":
                        mask = epochs >= float(st.event_time or 0.0)
                        segment = df.loc[mask]
                        if segment.empty or st.max_distance is None or st.level_price is None:
                            continue
                        level = float(st.level_price)
                        if st.direction == "LONG":
                            lows = pd.to_numeric(segment.get("low"), errors="coerce")
                            breached = (not lows.dropna().empty) and float(lows.min()) < level - float(st.max_distance)
                        else:
                            highs = pd.to_numeric(segment.get("high"), errors="coerce")
                            breached = (not highs.dropna().empty) and float(highs.max()) > level + float(st.max_distance)
                        if breached:
                            st.status = "INVALID"
                            st.reason = "ATR_1P5_SURVIVAL"
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "🛑 [외부유동성 생존 탈락] %s | %s %s | %s %.6f | ATR14=%.6f x1.5",
                                    wid, spec.symbol, spec.source_tf, st.direction, level, float(st.atr_snapshot or 0.0),
                                )
            if changed:
                self._save_locked()

    def validate_true_b0(self, watch_id: str, direction: str, true_b0_price: float) -> bool:
        wid = str(watch_id or "").strip()
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"} or not finite_number(true_b0_price):
            return False
        with self._lock:
            st = self._state_for_direction_locked(wid, direction)
            spec = self._specs.get(wid)
            if st is None or spec is None or st.direction != direction:
                return False
            if st.status not in {"ACTIVE", "CONFIRMED"} or st.level_price is None or st.max_distance is None:
                return False
            distance = abs(float(true_b0_price) - float(st.level_price))
            if distance <= float(st.max_distance):
                st.status = "CONFIRMED"
                st.reason = None
                self._save_locked()
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "✅ [외부유동성 2차 통과] %s | %s %s | level=%.6f TRUE_B0=%.6f dist=%.6f <= %.6f",
                        wid, spec.symbol, spec.source_tf, float(st.level_price), float(true_b0_price),
                        distance, float(st.max_distance),
                    )
                return True
            st.status = "INVALID"
            st.reason = "ATR_1P5_TRUE_B0_DISTANCE"
            self._save_locked()
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🛑 [외부유동성 2차 탈락] %s | %s %s | level=%.6f TRUE_B0=%.6f dist=%.6f > %.6f",
                    wid, spec.symbol, spec.source_tf, float(st.level_price), float(true_b0_price),
                    distance, float(st.max_distance),
                )
            return False


class OZWatchController:
    """On-demand OZ watches keyed by validation mode and final-trigger mode."""

    def __init__(self, telegram: "DomainEventSender", external: ExternalLiquidityController, *, event_state=None):
        self.rejected_watches = []
        self.telegram = telegram
        self.external = external
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._watches = state.setdefault('watches', {})
        self._revision = state.setdefault('revision', {_profile_key(*profile): 0 for profile in PROFILE_KEYS})
        self._state_path = LOG_DIR / "oz_manual_watch_state.json"
        if event_state is None:self._load_state()

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            self.rejected_watches = list(raw.get("rejected_watches", [])) if isinstance(raw,dict) else []
            items = raw.get("watches", []) if isinstance(raw, dict) else []
            restored: dict[str, WatchSpec] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                source = str(item.get("source") or "MANUAL").strip() or "MANUAL"
                requested = {str(x).strip().lower() for x in item.get("timeframes", [])}
                ordered = tuple(tf for tf in TF_MAP if tf in requested)
                symbol = str(item.get("symbol") or "").strip() or None
                direction = str(item.get("direction") or "").upper() or None
                request_chat_id = str(item.get("request_chat_id") or "").strip() or None
                external_watch_id = str(item.get("external_watch_id") or "").strip() or None
                external_source_tf = str(item.get("external_source_tf") or "").strip().lower() or None
                source_spec_id = str(item.get("source_spec_id") or "").strip() or None
                source_name = str(item.get("source_name") or "").strip() or None
                try:
                    canonical = load_saved_oz(item, path=f"watches.{watch_id}")
                    validation_mode, trigger_mode = canonical['validation_mode'], canonical['trigger_mode']
                except oz_profiles.ProfileError as exc:
                    if not hasattr(self, 'rejected_watches'):
                        self.rejected_watches = []
                    self.rejected_watches.append({'watch_id': watch_id, 'reason': str(exc), 'original': dict(item)})
                    logging.error('[OZ 복원 격리] watch_id=%s | %s', watch_id, exc)
                    continue
                if not watch_id or not ordered or direction not in {None, "LONG", "SHORT"}:
                    continue
                restored[watch_id] = WatchSpec(
                    watch_id=watch_id,
                    watch_owner=str(item.get("watch_owner") or ("KIM" if source_spec_id else "OZ")),
                    source=source,
                    timeframes=ordered,
                    symbol=symbol,
                    direction=direction,
                    persistent=bool(item.get("persistent", False)),
                    request_chat_id=request_chat_id,
                    external_watch_id=external_watch_id,
                    external_source_tf=external_source_tf,
                    source_spec_id=source_spec_id,
                    source_name=source_name,
                    validation_mode=validation_mode,
                    trigger_mode=trigger_mode,
                )
            with self._lock:
                self._watches = restored
                for vm, tm in PROFILE_KEYS:
                    if any(
                        w.validation_mode == vm and w.trigger_mode == tm
                        for w in restored.values()
                    ):
                        self._revision[_profile_key(vm, tm)] += 1
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [OZ Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[OZ Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 3,
            "rejected_watches": list(self.rejected_watches),
            "watches": [
                {
                    "watch_id": w.watch_id,
                    "source": w.source,
                    "watch_owner": w.watch_owner,
                    "timeframes": list(w.timeframes),
                    "symbol": w.symbol,
                    "direction": w.direction,
                    "persistent": w.persistent,
                    "request_chat_id": w.request_chat_id,
                    "external_watch_id": w.external_watch_id,
                    "external_source_tf": w.external_source_tf,
                    "source_spec_id": w.source_spec_id,
                    "source_name": w.source_name,
                    "validation_mode": w.validation_mode,
                    "trigger_mode": w.trigger_mode,
                }
                for w in self._watches.values()
            ],
        }
        try:
            _atomic_write_json(self._state_path, payload)
        except Exception:
            logging.exception("[OZ Watch] 상태 저장 실패 | %s", self._state_path)

    def _bump(self, validation_mode: str, trigger_mode: str) -> None:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        key = _profile_key(vm, tm)
        self._revision[key] = self._revision.get(key, 0) + 1

    def register_external(self, payload: dict) -> None:
        self.external.register(payload)

    def cancel_external(self, watch_id: str) -> None:
        self.external.cancel(watch_id)

    def reset_external(self) -> None:
        self.external.reset()

    def apply_external_event(self, payload: dict) -> None:
        self.external.apply_event(payload)

    def _external_id_for_watch_locked(self, w: WatchSpec) -> Optional[str]:
        if w.external_watch_id:
            return w.external_watch_id
        if self.external.has_spec(w.watch_id):
            return w.watch_id
        if w.external_source_tf:
            matches = self.external.matching_ids(w.symbol, w.external_source_tf)
            if len(matches) == 1:
                return matches[0]
        return None

    def _profile_watches_locked(
        self, symbol: str, validation_mode: str, trigger_mode: str, tf: Optional[str] = None
    ) -> list[WatchSpec]:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        out: list[WatchSpec] = []
        for w in self._watches.values():
            if w.validation_mode != vm or w.trigger_mode != tm:
                continue
            if w.symbol is not None and w.symbol != symbol:
                continue
            if tf is not None and tf not in w.timeframes:
                continue
            out.append(w)
        return out

    def external_watch_ids_for_profile(
        self, symbol: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        with self._lock:
            out: set[str] = set()
            for w in self._profile_watches_locked(symbol, validation_mode, trigger_mode):
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id:
                    out.add(ext_id)
            return out

    def external_source_tfs_for_profile(
        self, symbol: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        ids = self.external_watch_ids_for_profile(symbol, validation_mode, trigger_mode)
        return self.external.source_timeframes(ids)

    def _remove_invalid_manual_one_shots(self, external_ids: Iterable[str]) -> None:
        """Remove dead one-shot personal watches after their manual liquidity gate is INVALID."""
        target_ids = {str(x) for x in external_ids if str(x)}
        if not target_ids:
            return

        removed_external_ids: set[str] = set()
        affected_profiles: set[tuple[str, str]] = set()
        removed_watch_ids: list[str] = []
        with self._lock:
            for key, w in list(self._watches.items()):
                if w.source != "MANUAL" or w.persistent:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None or ext_id not in target_ids:
                    continue
                if not self.external.is_manual_level(ext_id):
                    continue
                status = self.external.status(
                    ext_id, w.direction if w.direction in {"LONG", "SHORT"} else None
                )
                if status != "INVALID":
                    continue
                self._watches.pop(key, None)
                removed_watch_ids.append(w.watch_id)
                removed_external_ids.add(ext_id)
                affected_profiles.add((w.validation_mode, w.trigger_mode))

            if removed_watch_ids:
                for vm, tm in sorted(affected_profiles):
                    self._bump(vm, tm)
                self._save_state_locked()

        for ext_id in removed_external_ids:
            self.external.cancel(ext_id)

        for watch_id in removed_watch_ids:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🧹 [OZ 수동 Watch 자동정리] %s | 수동 외부유동성 ATR 자격 탈락",
                    watch_id,
                )

    def update_external_market(
        self, symbol: str, validation_mode: str, trigger_mode: str, data: dict[str, pd.DataFrame]
    ) -> None:
        ids = self.external_watch_ids_for_profile(symbol, validation_mode, trigger_mode)
        if ids:
            self.external.update_market(symbol, data, ids)
            self._remove_invalid_manual_one_shots(ids)

    def allowed_directions(
        self, symbol: str, tf: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        allowed: set[str] = set()
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            for w in watches:
                requested = {w.direction} if w.direction in {"LONG", "SHORT"} else {"LONG", "SHORT"}
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    allowed.update(requested)
                    continue
                spec = self.external.spec(ext_id)
                if spec is None:
                    continue
                for requested_direction in requested:
                    st = self.external.state(ext_id, requested_direction)
                    if st is None or st.direction != requested_direction:
                        continue
                    # Source/setup TF may build OUT→IN + HMA structure after the 1st ATR pass.
                    # Derived lower-TF OZ watches stay blocked until the source setup passes
                    # the finalized TRUE B0 distance check and becomes CONFIRMED.
                    if st.status == "CONFIRMED":
                        allowed.add(requested_direction)
                    elif st.status == "ACTIVE" and tf == spec.source_tf:
                        allowed.add(requested_direction)
        return allowed

    def allowed_environment_identities(
        self, symbol: str, tf: str, direction: str, validation_mode: str, trigger_mode: str
    ) -> frozenset[tuple[str, Optional[str], Optional[str], Optional[float]]]:
        """Return identities for the environment states that currently make this direction eligible.

        Non-external watches keep a stable Watch identity. External watches include the exact
        liquidity state selected by the existing ``external.state()`` rule, so a new SWEEP cycle
        is treated as fresh only when that selected environment state itself changes.
        """
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return frozenset()

        eligible: set[tuple[str, Optional[str], Optional[str], Optional[float]]] = set()
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            for w in watches:
                if w.direction is not None and w.direction != direction:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    eligible.add((w.watch_id, None, None, None))
                    continue
                spec = self.external.spec(ext_id)
                if spec is None:
                    continue
                st = self.external.state(ext_id, direction)
                if st is None or st.direction != direction:
                    continue
                if st.status == "CONFIRMED" or (st.status == "ACTIVE" and tf == spec.source_tf):
                    eligible.add((w.watch_id, ext_id, st.level_id, st.event_time))
        return frozenset(eligible)

    def validate_external_true_b0(
        self, symbol: str, tf: str, direction: str, validation_mode: str, trigger_mode: str, true_b0_price: float
    ) -> bool:
        """Second ATR qualification. Non-external/manual watches preserve existing behavior."""
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            relevant = [
                w for w in watches
                if w.direction is None or w.direction == direction
            ]
            if not relevant:
                return False
            linked: list[str] = []
            for w in relevant:
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    return True
                linked.append(ext_id)

        passed = False
        for ext_id in dict.fromkeys(linked):
            st = self.external.state(ext_id, direction)
            spec = self.external.spec(ext_id)
            if st is None or spec is None or st.direction != direction:
                continue
            # Only the external-liquidity source/setup TF is allowed to perform
            # the 2nd ATR qualification. Derived lower TFs can run only after it.
            if tf != spec.source_tf:
                if st.status == "CONFIRMED":
                    passed = True
                continue
            if st.status in {"ACTIVE", "CONFIRMED"} and self.external.validate_true_b0(
                ext_id, direction, true_b0_price
            ):
                passed = True
        return passed

    def add_manual(
        self,
        timeframes: Iterable[str],
        issued_at=None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        persistent: bool = False,
        request_chat_id: Optional[str] = None,
        validation_mode: Optional[str] = None,
        trigger_mode: Optional[str] = None,
        oz_mode: Optional[str] = None,
        watch_id: Optional[str] = None,
        external_watch_id: Optional[str] = None,
        external_source_tf: Optional[str] = None,
        external_required: bool = False,
        source_spec_id: Optional[str] = None,
        source_name: Optional[str] = None,
        watch_owner: str = "OZ",
    ) -> None:
        ordered = tuple(
            tf for tf in TF_MAP
            if tf in set(str(x).lower() for x in timeframes)
        )
        if not ordered:
            return

        symbol = str(symbol or "").strip() or None
        direction = str(direction or "").upper() or None
        request_chat_id = str(request_chat_id or "").strip() or None
        external_watch_id = str(external_watch_id or "").strip() or None
        external_source_tf = str(external_source_tf or "").strip().lower() or None
        source_spec_id = str(source_spec_id or "").strip() or None
        source_name = str(source_name or "").strip() or None
        validation_mode, trigger_mode = _resolve_oz_modes(
            validation_mode, trigger_mode, oz_mode
        )
        if direction not in {None, "LONG", "SHORT"}:
            return

        explicit_wid = str(watch_id or "").strip()
        chain_scoped_wid = explicit_wid.startswith("OZARM:CHAIN:")
        replaced_manual_external_ids: set[str] = set()

        with self._lock:
            if chain_scoped_wid and explicit_wid in self._watches:
                return
            # One-shot replacement is scoped to the same owner and same OZ profile only.
            if not persistent:
                for key in [
                    k for k, w in self._watches.items()
                    if (
                        w.source == "MANUAL"
                        and not w.persistent
                        and w.request_chat_id == request_chat_id
                        and w.validation_mode == validation_mode
                        and w.trigger_mode == trigger_mode
                    )
                ]:
                    old_watch = self._watches.pop(key, None)
                    if old_watch is not None and old_watch.external_watch_id:
                        replaced_manual_external_ids.add(old_watch.external_watch_id)

            if persistent and not chain_scoped_wid:
                for w in self._watches.values():
                    if (
                        w.source == "MANUAL"
                        and w.persistent
                        and w.timeframes == ordered
                        and w.symbol == symbol
                        and w.direction == direction
                        and w.request_chat_id == request_chat_id
                        and w.validation_mode == validation_mode
                        and w.trigger_mode == trigger_mode
                        and w.external_watch_id == external_watch_id
                        and w.external_source_tf == external_source_tf
                    ):
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "ℹ️ [OZ 지속 Watch] 동일 감시 이미 존재 | validation=%s trigger=%s TF=%s symbol=%s direction=%s",
                                validation_mode, trigger_mode, ",".join(ordered), symbol or "*", direction or "BOTH",
                            )
                        return

            wid = explicit_wid
            if not wid:
                wid = (
                    f"MANUAL:{validation_mode}:{trigger_mode}:"
                    f"{'P' if persistent else 'ONE'}:{issued_at or time.time_ns()}"
                )
            if external_required and external_watch_id is None:
                external_watch_id = wid
            self._watches[wid] = WatchSpec(
                watch_id=wid,
                watch_owner=str(watch_owner or "OZ"),
                source="MANUAL",
                timeframes=ordered,
                symbol=symbol,
                direction=direction,
                persistent=bool(persistent),
                request_chat_id=request_chat_id,
                external_watch_id=external_watch_id,
                external_source_tf=external_source_tf,
                source_spec_id=source_spec_id,
                source_name=source_name,
                validation_mode=validation_mode,
                trigger_mode=trigger_mode,
            )
            self._bump(validation_mode, trigger_mode)
            self._save_state_locked()

        for ext_id in replaced_manual_external_ids:
            if ext_id != external_watch_id and self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        labels = "·".join(TF_LABELS.get(tf, tf) for tf in ordered)
        scope = symbol or "XAUUSD"
        side = " LONG" if direction == "LONG" else " SHORT" if direction == "SHORT" else ""
        persistence_label = " 지속" if persistent else ""
        oz_label = _oz_profile_label(validation_mode, trigger_mode)
        external_label = ""
        if external_watch_id:
            ext_spec = self.external.spec(external_watch_id)
            if (
                ext_spec is not None
                and ext_spec.source_kind == "MANUAL_LEVEL"
                and finite_number(ext_spec.manual_level_price)
            ):
                external_label = f" · 외부유동성 {float(ext_spec.manual_level_price):g} ATR×{EXTERNAL_ATR_MULT:g}"

        self.telegram.send(
            f"✅ {scope} · {labels} {oz_label}{side}{persistence_label}{external_label} 감시",
            kind="CONTROL_ACK",
            require_delivery=True,
            request_chat_id=request_chat_id,
            watch_id=wid, watch_event="REGISTERED",
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
        )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🟣 [OZ 수동 Watch] validation=%s trigger=%s persistent=%s TF=%s symbol=%s direction=%s watch_id=%s",
                validation_mode, trigger_mode, persistent,
                ",".join(ordered), symbol or "*", direction or "BOTH", wid,
            )

    def cancel_manual(
        self,
        timeframes: Iterable[str] = (),
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        persistent_only: bool = False,
        request_chat_id: Optional[str] = None,
        validation_mode: Optional[str] = None,
        trigger_mode: Optional[str] = None,
        oz_mode: Optional[str] = None,
        watch_id: Optional[str] = None,
        reply_cancel: bool = False,
    ) -> None:
        tf_filter = {
            str(x).lower()
            for x in timeframes
            if str(x).lower() in TF_MAP
        }
        symbol = str(symbol or "").strip() or None
        direction = str(direction or "").upper() or None
        request_chat_id = str(request_chat_id or "").strip() or None
        watch_id = str(watch_id or "").strip() or None

        filter_vm = str(validation_mode or "").strip().upper() or None
        filter_tm = str(trigger_mode or "").strip().upper() or None
        legacy = str(oz_mode or "").strip().upper() or None
        if legacy and filter_vm is None and filter_tm is None:
            filter_vm, filter_tm = _resolve_oz_modes(None, None, legacy)
        if filter_vm is not None and filter_vm not in VALIDATION_MODES:
            return
        if filter_tm is not None:
            filter_flags = oz_profiles.trigger_flags(filter_tm)
            if filter_flags is None:
                return
            filter_tm = oz_profiles.canonical_trigger_mode(filter_flags)

        removed_manual_external_ids: set[str] = set()
        with self._lock:
            keys = []
            for key, w in self._watches.items():
                if w.source != "MANUAL":
                    continue
                if watch_id is not None and w.watch_id != watch_id:
                    continue
                if request_chat_id is not None and w.request_chat_id != request_chat_id:
                    continue
                if filter_vm is not None and w.validation_mode != filter_vm:
                    continue
                if filter_tm is not None and w.trigger_mode != filter_tm:
                    continue
                if persistent_only and not w.persistent:
                    continue
                if tf_filter and not (tf_filter & set(w.timeframes)):
                    continue
                if symbol is not None and w.symbol != symbol:
                    continue
                if direction is not None and w.direction != direction:
                    continue
                keys.append(key)

            removed_profiles = {
                (self._watches[key].validation_mode, self._watches[key].trigger_mode)
                for key in keys if key in self._watches
            }
            for key in keys:
                removed_watch = self._watches.pop(key, None)
                if removed_watch is not None and removed_watch.external_watch_id:
                    removed_manual_external_ids.add(removed_watch.external_watch_id)

            for vm, tm in sorted(removed_profiles):
                self._bump(vm, tm)
            if keys:
                self._save_state_locked()

        for ext_id in removed_manual_external_ids:
            if self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🛑 [OZ 수동 Watch 취소] TF=%s symbol=%s direction=%s validation=%s trigger=%s persistent_only=%s | %d건",
                ",".join(sorted(tf_filter)) if tf_filter else "*",
                symbol or "*", direction or "*", filter_vm or "*", filter_tm or "*",
                persistent_only, len(keys),
            )

        if keys or (reply_cancel and watch_id and request_chat_id):
            labels = {_oz_profile_label(vm, tm) for vm, tm in removed_profiles}
            oz_label = "/".join(sorted(labels)) if labels else "OZ"
            ack_watch_id = watch_id or (keys[0] if len(keys) == 1 else None)
            self.telegram.send(
                f"✅ {oz_label} 감시 중지 · {len(keys)}건",
                kind="CONTROL_ACK",
                require_delivery=True,
                request_chat_id=request_chat_id,
                watch_id=ack_watch_id, watch_event="CANCELLED",
                removed_count=len(keys), reply_cancel=reply_cancel,
            )

    def reset_all(self, request_chat_id: Optional[str] = None) -> None:
        """Reset only the supplied owner's watches when request_chat_id is present."""
        request_chat_id = str(request_chat_id or "").strip() or None
        with self._lock:
            before = dict(self._watches)
            if request_chat_id is None:
                self._watches.clear()
            else:
                self._watches = {
                    k: w for k, w in self._watches.items()
                    if w.request_chat_id != request_chat_id
                }
            removed_watches = [
                w for key, w in before.items()
                if key not in self._watches
            ]
            affected = {
                (w.validation_mode, w.trigger_mode)
                for w in removed_watches
            }
            for vm, tm in sorted(affected):
                self._bump(vm, tm)
            self._save_state_locked()
        for w in removed_watches:
            if w.external_watch_id and self.external.is_manual_level(w.external_watch_id):
                self.external.cancel(w.external_watch_id)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "♻️ [OZ Watch 리셋] request_chat_id=%s | removed=%d",
                request_chat_id or "*", len(before) - len(self._watches),
            )

    def snapshot_for_symbol(
        self,
        symbol: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
    ) -> tuple[int, tuple[str, ...]]:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        with self._lock:
            tfs = set()
            for w in self._watches.values():
                if w.validation_mode != vm or w.trigger_mode != tm:
                    continue
                if w.symbol is not None and w.symbol != symbol:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is not None and w.direction in {"LONG", "SHORT"} and self.external.status(ext_id, w.direction) == "INVALID":
                    continue
                tfs.update(w.timeframes)
            return self._revision.get(_profile_key(vm, tm), 0), tuple(
                tf for tf in TF_MAP if tf in tfs
            )

    def active_symbols(self) -> set[str]:
        """현재 등록된 수동 OZ Watch의 명시 종목만 반환합니다."""
        with self._lock:
            return {w.symbol for w in self._watches.values() if w.symbol}

    def _matching(
        self,
        symbol: str,
        tf: str,
        direction: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
    ) -> list[str]:
        keys = []
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        for key, w in self._watches.items():
            if w.validation_mode != vm or w.trigger_mode != tm:
                continue
            if tf not in w.timeframes:
                continue
            if w.symbol is not None and w.symbol != symbol:
                continue
            if w.direction is not None and w.direction != direction:
                continue
            ext_id = self._external_id_for_watch_locked(w)
            if ext_id is not None:
                st = self.external.state(ext_id, direction)
                if st is None or st.status != "CONFIRMED" or st.direction != direction:
                    continue
            keys.append(key)
        return keys

    def try_fire(
        self,
        symbol: str,
        tf: str,
        direction: str,
        grade: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
        trigger_name: Optional[str] = None,
        indicators_text: Optional[str] = None,
        current_price: Optional[float] = None,
        alert_identity: Optional[str] = None,
        completion_time: Optional[float] = None,
    ) -> bool:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        with self._lock:
            keys = self._matching(symbol, tf, direction, vm, tm)
            if not keys:
                return False

            tf_label = TF_LABELS.get(tf, tf)
            oz_label = _oz_profile_label(vm, tm)
            trigger_text = f" · {trigger_name}" if trigger_name else ""
            message = f"🔔 {tf_label} {oz_label} {grade}급 {direction} · {symbol}{trigger_text}"
            by_recipient: dict[Optional[str], list[str]] = {}
            for key in keys:
                w = self._watches.get(key)
                if w is not None:
                    by_recipient.setdefault(w.request_chat_id, []).append(key)

            delivered_any = False
            delivered_all = True
            removed = 0
            persistent_count = 0
            removed_manual_external_ids: set[str] = set()
            for request_chat_id, recipient_keys in by_recipient.items():
                recipient_specs = [self._watches.get(key) for key in recipient_keys]
                source_spec_ids = list(dict.fromkeys(
                    w.source_spec_id for w in recipient_specs
                    if w is not None and w.source_spec_id
                ))
                source_names = list(dict.fromkeys(
                    w.source_name for w in recipient_specs
                    if w is not None and w.source_name
                ))
                ok = self.telegram.send(
                    message,
                    event_id=identity('OZ', alert_identity, request_chat_id, sorted(recipient_keys)) if alert_identity else None,
                    event_time=completion_time,
                    direction=direction,
                    require_delivery=True,
                    request_chat_id=request_chat_id,
                    watch_ids=list(recipient_keys),
                    source_spec_id=source_spec_ids[0] if source_spec_ids else None,
                    source_spec_ids=source_spec_ids or None,
                    source_name=source_names[0] if source_names else None,
                    validation_mode=vm,
                    trigger_mode=tm,
                    trigger_name=trigger_name,
                    source_tf=tf,
                    symbol=symbol,
                    grade=grade,
                    indicators_text=str(indicators_text or ""),
                    current_price=current_price,
                )
                if not ok:
                    delivered_all = False
                    logging.error(
                        "[OZ] 최종 알림 실패 - Watch 유지 | %s %s %s chat_id=%s",
                        symbol, tf, direction, request_chat_id or "official",
                    )
                    continue
                delivered_any = True
                for key in recipient_keys:
                    w = self._watches.get(key)
                    if w is None:
                        continue
                    if w.persistent:
                        persistent_count += 1
                    else:
                        removed_watch = self._watches.pop(key, None)
                        if removed_watch is not None and removed_watch.external_watch_id:
                            removed_manual_external_ids.add(removed_watch.external_watch_id)
                        removed += 1

            if removed:
                self._bump(vm, tm)
                self._save_state_locked()

        for ext_id in removed_manual_external_ids:
            if self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        if delivered_any:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "✅ [OZ 알림] %s %s %s | %s급 | validation=%s trigger=%s/%s | matched=%d | 종료=%d | 지속=%d | recipients=%d",
                    symbol, tf, direction, grade, vm, tm, trigger_name or "-",
                    len(keys), removed, persistent_count, len(by_recipient),
                )
        return delivered_any and delivered_all


WATCH_EVALUATION_MODES = {"LIVE", "CLOSE"}
GENERIC_WATCH_LEGACY_CONTRACTS: dict[str, tuple[str, str]] = {
    "WONBI_TOUCH_CLOSE": ("WONBI_TOUCH", "CLOSE"),
    "BAR_CLOSE": ("BAR", "CLOSE"),
}
GENERIC_WATCH_DEFAULT_EVALUATION: dict[str, str] = {
    "EMA_CROSS": "CLOSE",
    "HMA_CROSS": "CLOSE",
    "BAR": "CLOSE",
    "WONBI_TOUCH": "LIVE",
    "PREV_DAY_TOUCH": "LIVE",
    "PERCENTILE_OUT": "LIVE",
    "PERCENTILE_OUT_IN": "LIVE",
}


def normalize_generic_watch_contract(watch_type: object, evaluation_mode: object = None) -> tuple[str, str]:
    raw_type = str(watch_type or "").strip().upper()
    raw_mode = str(evaluation_mode or "").strip().upper()
    legacy = GENERIC_WATCH_LEGACY_CONTRACTS.get(raw_type)
    if legacy is not None:
        condition_type, legacy_mode = legacy
        mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else legacy_mode
        return condition_type, mode
    mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else GENERIC_WATCH_DEFAULT_EVALUATION.get(raw_type, "LIVE")
    return raw_type, mode


@dataclass
class GenericWatchSpec:
    watch_id: str
    watch_type: str
    timeframes: tuple[str, ...]
    symbol: Optional[str] = None
    direction: Optional[str] = None
    level_side: Optional[str] = None
    ma_family: Optional[str] = None
    fast_period: Optional[int] = None
    slow_period: Optional[int] = None
    persistent: bool = False
    request_chat_id: Optional[str] = None
    # 시간연쇄 내부 Watch는 사용자에게 직접 알리지 않고 김매니저로 이벤트를 콜백합니다.
    chain_id: Optional[str] = None
    chain_stage: Optional[int] = None
    silent: bool = False
    evaluation_mode: str = "LIVE"
    watch_owner: str = "OZ"
    ma_expression: Optional[str] = None


class GenericWatchController:
    """Telegram 자연어로 등록된 단순 조건 Watch를 관리합니다."""

    def __init__(self, telegram: "DomainEventSender", *, event_state=None):
        self.telegram = telegram
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._watches = state.setdefault('watches', {})
        self._revision = state.get('revision', 0)
        self._state_path = LOG_DIR / "oz_generic_watch_state.json"
        if event_state is None:self._load_state()

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            items = raw.get("watches", []) if isinstance(raw, dict) else []
            restored: dict[str, GenericWatchSpec] = {}
            allowed_types = {
                "EMA_CROSS", "HMA_CROSS", "PREV_DAY_TOUCH", "BAR",
                "WONBI_TOUCH", "PERCENTILE_OUT", "PERCENTILE_OUT_IN", "MA_EXPRESSION",
            }
            for item in items:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                watch_type, evaluation_mode = normalize_generic_watch_contract(
                    item.get("watch_type"), item.get("evaluation_mode")
                )
                ordered = tuple(str(x).strip().lower() for x in item.get("timeframes", []) if str(x).strip())
                symbol = str(item.get("symbol") or "").strip() or None
                direction = str(item.get("direction") or "").upper() or None
                level_side = str(item.get("level_side") or "").upper() or None
                ma_family = str(item.get("ma_family") or "").upper() or None
                request_chat_id = str(item.get("request_chat_id") or "").strip() or None
                chain_id = str(item.get("chain_id") or "").strip() or None
                try:
                    fast_period = int(item["fast_period"]) if item.get("fast_period") is not None else None
                    slow_period = int(item["slow_period"]) if item.get("slow_period") is not None else None
                    chain_stage = int(item["chain_stage"]) if item.get("chain_stage") is not None else None
                except (TypeError, ValueError):
                    continue
                if not watch_id or watch_type not in allowed_types or not ordered:
                    continue
                if direction not in {None, "LONG", "SHORT"} or level_side not in {None, "HIGH", "LOW", "BOTH"}:
                    continue
                ma_expression = None
                if watch_type == "MA_EXPRESSION":
                    ma_expression = parse_ma_expression(item.get("ma_expression")).canonical
                restored[watch_id] = GenericWatchSpec(
                    watch_id, watch_type, ordered, symbol=symbol, direction=direction,
                    level_side=level_side, ma_family=ma_family, fast_period=fast_period,
                    slow_period=slow_period, persistent=bool(item.get("persistent", False)),
                    request_chat_id=request_chat_id, chain_id=chain_id, chain_stage=chain_stage,
                    silent=bool(item.get("silent", False)), evaluation_mode=evaluation_mode,
                    watch_owner=str(item.get("watch_owner") or ("KIM" if chain_id else "OZ")),
                    ma_expression=ma_expression,
                )
            with self._lock:
                self._watches = restored
                if restored:
                    self._revision += 1
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [조건 Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[조건 Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 2,
            "watches": [
                {
                    "watch_id": w.watch_id, "watch_type": w.watch_type, "timeframes": list(w.timeframes),
                    "symbol": w.symbol, "direction": w.direction, "level_side": w.level_side,
                    "ma_family": w.ma_family, "fast_period": w.fast_period, "slow_period": w.slow_period,
                    "persistent": w.persistent, "request_chat_id": w.request_chat_id,
                    "chain_id": w.chain_id, "chain_stage": w.chain_stage, "silent": w.silent,
                    "evaluation_mode": w.evaluation_mode,
                    "watch_owner": w.watch_owner,
                    **({"ma_expression": w.ma_expression} if w.ma_expression is not None else {}),
                }
                for w in self._watches.values()
            ],
        }
        try:
            _atomic_write_json(self._state_path, payload)
        except Exception:
            logging.exception("[조건 Watch] 상태 저장 실패 | %s", self._state_path)

    def _bump(self):
        self._revision += 1

    def add(self, payload: dict) -> None:
        watch_type, evaluation_mode = normalize_generic_watch_contract(
            payload.get("watch_type"), payload.get("evaluation_mode")
        )
        ordered = tuple(str(x).strip().lower() for x in payload.get("timeframes", []) if str(x).strip())
        symbol = str(payload.get("symbol") or "").strip() or None
        direction = str(payload.get("direction") or "").upper() or None
        level_side = str(payload.get("level_side") or "").upper() or None
        ma_family = str(payload.get("ma_family") or "").upper() or None
        fast_period = payload.get("fast_period")
        slow_period = payload.get("slow_period")
        if fast_period is not None:
            try: fast_period = int(fast_period)
            except (TypeError, ValueError): return
        if slow_period is not None:
            try: slow_period = int(slow_period)
            except (TypeError, ValueError): return
        persistent = bool(payload.get("persistent", False))
        request_chat_id = str(payload.get("request_chat_id") or "").strip() or None
        requested_watch_id = str(payload.get("watch_id") or "").strip() or None
        chain_id = str(payload.get("chain_id") or "").strip() or None
        try:
            chain_stage = int(payload["chain_stage"]) if payload.get("chain_stage") is not None else None
        except (TypeError, ValueError):
            return
        silent = bool(payload.get("silent", False))
        ma_expression = None
        if watch_type == "MA_EXPRESSION":
            expression = parse_ma_expression(payload.get("ma_expression"))
            ma_expression = expression.canonical
            if not payload.get("evaluation_mode"):
                evaluation_mode = expression.default_evaluation_mode
        if watch_type not in {
            "EMA_CROSS", "HMA_CROSS", "PREV_DAY_TOUCH", "BAR",
            "WONBI_TOUCH", "PERCENTILE_OUT", "PERCENTILE_OUT_IN", "MA_EXPRESSION",
        } or not ordered:
            return
        if direction not in {None, "LONG", "SHORT"}:
            return
        if level_side not in {None, "HIGH", "LOW", "BOTH"}:
            return

        with self._lock:
            # 시간연쇄는 고유 watch_id를 재전송하여 프로세스 재시작/큐 시작 순서에도 복구할 수 있습니다.
            if requested_watch_id and requested_watch_id in self._watches:
                return
            # 기존 일반 Watch의 중복 방지 의미는 그대로 유지합니다. 시간연쇄 고유 ID Watch끼리는 별개로 취급합니다.
            if not requested_watch_id:
                for w in self._watches.values():
                    if (
                        w.watch_type == watch_type
                        and w.evaluation_mode == evaluation_mode
                        and w.timeframes == ordered
                        and w.symbol == symbol
                        and w.direction == direction
                        and w.level_side == level_side
                        and w.ma_family == ma_family
                        and w.fast_period == fast_period
                        and w.slow_period == slow_period
                        and w.ma_expression == ma_expression
                        and w.persistent == persistent
                        and w.request_chat_id == request_chat_id
                    ):
                        return
            wid = requested_watch_id or f"GEN:{watch_type}:{time.time_ns()}"
            self._watches[wid] = GenericWatchSpec(
                wid, watch_type, ordered, symbol=symbol, direction=direction,
                level_side=level_side, ma_family=ma_family, fast_period=fast_period,
                slow_period=slow_period, persistent=persistent, request_chat_id=request_chat_id,
                chain_id=chain_id, chain_stage=chain_stage, silent=silent,
                evaluation_mode=evaluation_mode,
                watch_owner=str(payload.get("watch_owner") or ("KIM" if chain_id else "OZ")),
                ma_expression=ma_expression,
            )
            self._bump()
            self._save_state_locked()

        tf_label = "·".join(TF_LABELS.get(tf, tf) for tf in ordered)
        scope = symbol or "전체 종목"
        mode = " 지속" if persistent else ""
        if watch_type in {"EMA_CROSS", "HMA_CROSS"}:
            family = ma_family or ("EMA" if watch_type == "EMA_CROSS" else "HMA")
            pair = f"{family}{fast_period}/{slow_period}"
            cond = f"{pair} 골크" if direction == "LONG" else f"{pair} 데크" if direction == "SHORT" else f"{pair} 크로스"
        elif watch_type == "WONBI_TOUCH":
            cond = "상단 원비 터치" if level_side == "HIGH" else "하단 원비 터치" if level_side == "LOW" else "원비 터치"
            if evaluation_mode == "CLOSE":
                cond += " 봉마감"
        elif watch_type == "MA_EXPRESSION":
            cond = ma_expression + (" 봉마감" if evaluation_mode == "CLOSE" else "")
        elif watch_type == "BAR":
            cond = "봉마감"
        elif watch_type == "PERCENTILE_OUT":
            cond = "상단 OUT" if level_side == "HIGH" else "하단 OUT" if level_side == "LOW" else "OUT"
        elif watch_type == "PERCENTILE_OUT_IN":
            cond = "상단 OUT→IN" if level_side == "HIGH" else "하단 OUT→IN" if level_side == "LOW" else "OUT→IN"
        else:
            cond = "전일 고가 터치" if level_side == "HIGH" else "전일 저가 터치" if level_side == "LOW" else "전일 고가/저가 터치"
        if not silent:
            self.telegram.send(
                f"✅ {scope} · {tf_label} {cond}{mode} 감시",
                kind="CONTROL_ACK", require_delivery=True, request_chat_id=request_chat_id,
                watch_id=wid, watch_type=watch_type, watch_event="REGISTERED",
            )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🟦 [조건 Watch] 시작 | %s/%s | %s | %s | persistent=%s chain=%s stage=%s",
                watch_type, evaluation_mode, scope, tf_label, persistent, chain_id or "-", chain_stage if chain_stage is not None else "-",
            )

    def cancel(self, payload: dict) -> None:
        watch_id = str(payload.get("watch_id") or "").strip() or None
        raw_watch_type = str(payload.get("watch_type") or "").strip()
        raw_evaluation_mode = str(payload.get("evaluation_mode") or "").strip().upper()
        if raw_watch_type:
            watch_type, evaluation_mode = normalize_generic_watch_contract(
                raw_watch_type, raw_evaluation_mode or None
            )
        else:
            watch_type = None
            evaluation_mode = raw_evaluation_mode if raw_evaluation_mode in WATCH_EVALUATION_MODES else None
        tf_filter = {str(x).strip().lower() for x in payload.get("timeframes", []) if str(x).strip()}
        symbol = str(payload.get("symbol") or "").strip() or None
        direction = str(payload.get("direction") or "").upper() or None
        level_side = str(payload.get("level_side") or "").upper() or None
        ma_family = str(payload.get("ma_family") or "").upper() or None
        fast_period = payload.get("fast_period")
        slow_period = payload.get("slow_period")
        try:
            fast_period = int(fast_period) if fast_period is not None else None
            slow_period = int(slow_period) if slow_period is not None else None
        except (TypeError, ValueError):
            return
        persistent_only = bool(payload.get("persistent_only", False))
        request_chat_id = str(payload.get("request_chat_id") or "").strip() or None
        with self._lock:
            keys = []
            for k, w in self._watches.items():
                if watch_id is not None and k != watch_id:
                    continue
                if request_chat_id is not None and w.request_chat_id != request_chat_id:
                    continue
                if watch_type and w.watch_type != watch_type:
                    continue
                if evaluation_mode and w.evaluation_mode != evaluation_mode:
                    continue
                if tf_filter and not (tf_filter & set(w.timeframes)):
                    continue
                if symbol is not None and w.symbol != symbol:
                    continue
                if direction is not None and w.direction != direction:
                    continue
                if level_side is not None and w.level_side != level_side:
                    continue
                if ma_family is not None and w.ma_family != ma_family:
                    continue
                if fast_period is not None and w.fast_period != fast_period:
                    continue
                if slow_period is not None and w.slow_period != slow_period:
                    continue
                if persistent_only and not w.persistent:
                    continue
                keys.append(k)
            for k in keys:
                self._watches.pop(k, None)
            if keys:
                self._bump()
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🛑 [조건 Watch] 취소 | type=%s mode=%s | %d건",
                watch_type or "*", evaluation_mode or "*", len(keys),
            )
        if keys or (payload.get("reply_cancel") and watch_id and request_chat_id):
            ack_watch_id = watch_id or (keys[0] if len(keys) == 1 else None)
            self.telegram.send(
                f"✅ 조건 감시 중지 · {len(keys)}건",
                kind="CONTROL_ACK", require_delivery=True,
                request_chat_id=request_chat_id, watch_id=ack_watch_id,
                watch_event="CANCELLED", removed_count=len(keys),
                reply_cancel=bool(payload.get("reply_cancel")),
            )

    def reset_all(self, request_chat_id: Optional[str] = None) -> None:
        request_chat_id = str(request_chat_id or "").strip() or None
        with self._lock:
            if request_chat_id is None:
                count = len(self._watches)
                self._watches.clear()
            else:
                keys = [k for k, w in self._watches.items() if w.request_chat_id == request_chat_id]
                count = len(keys)
                for k in keys:
                    self._watches.pop(k, None)
            if count:
                self._bump()
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("♻️ [조건 Watch] 전체 취소 | %d건 | request_chat_id=%s", count, request_chat_id or "*")

    def active_symbols(self) -> set[str]:
        """현재 등록된 범용 Watch의 명시 종목만 반환합니다."""
        with self._lock:
            return {w.symbol for w in self._watches.values() if w.symbol}

    def snapshot_for_symbol(self, symbol: str) -> tuple[int, tuple[GenericWatchSpec, ...]]:
        with self._lock:
            items = tuple(w for w in self._watches.values() if w.symbol is None or w.symbol == symbol)
            return self._revision, items

    def fire(self, watch_id: str, message: str, **event_meta) -> bool:
        with self._lock:
            w = self._watches.get(watch_id)
            if w is None:
                return False

            if w.chain_id:
                direction = event_meta.get("direction") or w.direction
                ok = self.telegram.send(
                    message, direction=direction, kind="GENERIC_TRIGGER", require_delivery=False,
                    request_chat_id=w.request_chat_id, watch_id=w.watch_id, chain_id=w.chain_id,
                    chain_stage=w.chain_stage, watch_type=w.watch_type,
                    evaluation_mode=w.evaluation_mode, symbol=w.symbol,
                    source_tf=event_meta.get("source_tf"), level_side=event_meta.get("level_side") or w.level_side,
                    ma_family=w.ma_family, fast_period=w.fast_period, slow_period=w.slow_period,
                    event_time=event_meta.get("event_time"), event_id=event_meta.get("event_id"),
                )
            else:
                ok = self.telegram.send(
                    message, kind="CONTROL_ACK", require_delivery=True, request_chat_id=w.request_chat_id,
                    watch_id=w.watch_id, watch_type=w.watch_type, watch_event="ALERT",
                    event_id=event_meta.get("event_id"),
                )

            if not ok:
                return False
            if not w.persistent:
                self._watches.pop(watch_id, None)
                self._bump()
                self._save_state_locked()
            return True




class GenericConditionMonitor:
    """Telegram 범용 조건 watcher: 원비, Percentile, EMA/HMA cross, 전일 고저."""

    def __init__(self, symbol: str, config: dict[str, str], controller: GenericWatchController, *, staff_client=None, event_state=None):
        state = {} if event_state is None else event_state
        self.symbol = symbol
        self.controller = controller
        self.client = staff_client
        self.loop_sleep = float(config.get("LOOP_SLEEP_SEC", str(LOOP_SLEEP_SEC)))
        self.last_bar= state.setdefault('last_bar', {})
        self.touch_state= state.setdefault('touch_state', {})
        self.percentile_state= state.setdefault('percentile_state', {})
        self.known_ids= state.setdefault('known_ids', set())

    @staticmethod
    def _closed_time(df: pd.DataFrame) -> Optional[pd.Timestamp]:
        if df is None or len(df) < 3:
            return None
        t = pd.to_datetime(df.iloc[-2].get("time"), errors="coerce")
        return None if pd.isna(t) else pd.Timestamp(t)

    def _closed_row_once(
        self, w: GenericWatchSpec, tf: str, df: pd.DataFrame
    ) -> Optional[tuple[pd.Series, pd.Timestamp]]:
        if df is None or len(df) < 2:
            return None
        t = pd.to_datetime(df.iloc[-2].get("time"), errors="coerce")
        if pd.isna(t):
            return None
        ct = pd.Timestamp(t)
        key = (w.watch_id, tf)
        previous = self.last_bar.get(key)
        if previous is None:
            # 등록/재시작 직후 이미 닫힌 과거 봉은 새 이벤트로 재생하지 않습니다.
            self.last_bar[key] = ct
            return None
        if ct <= previous:
            return None
        self.last_bar[key] = ct
        return df.iloc[-2], ct

    @staticmethod
    def _close_event_epoch(ct: pd.Timestamp, tf: str) -> float:
        try:
            stamp = pd.Timestamp(ct)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            return float(stamp.timestamp()) + float(tf_seconds(tf))
        except Exception:
            return time.time()

    def _bar_close(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        closed = self._closed_row_once(w, tf, df)
        if closed is None:
            return
        _, ct = closed
        event_time = self._close_event_epoch(ct, tf)
        bar_open_ts = event_time - float(tf_seconds(tf))
        event_id = f"BAR_CLOSE:{self.symbol}:{tf}:{bar_open_ts:.6f}"
        msg = f"✅ {self.symbol} · {tf} 봉마감"
        if self.controller.fire(
            w.watch_id, msg, source_tf=tf, direction=w.direction,
            event_time=event_time, event_id=event_id,
        ):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [BAR Watch] %s %s CLOSE", self.symbol, tf)

    def _ma_expression_event(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        expression = parse_ma_expression(w.ma_expression)
        key = (w.watch_id, tf)
        if w.evaluation_mode == "CLOSE":
            closed = self._closed_row_once(w, tf, df)
            if closed is None:
                return
            _, bar_time = closed
            current = -2
            event_time = self._close_event_epoch(bar_time, tf)
        else:
            current = -1
            bar_time = pd.Timestamp(df.iloc[-1]["time"])
            event_time = time.time()
        features = {name: df[name] for name in expression.dependencies if name in df}
        matched = expression.evaluate(features, current)
        if matched is None:
            self.touch_state.pop(key, None)
            return
        if w.evaluation_mode == "LIVE":
            previous = self.touch_state.get(key, False)
            self.touch_state[key] = matched
            if previous or not matched:
                return
        elif not matched:
            return
        direction = w.direction
        if direction is None:
            kinds = {condition.kind for group in expression.groups for condition in group}
            direction = "LONG" if kinds == {"GOLDEN"} else "SHORT" if kinds == {"DEAD"} else None
        event_id = f"WATCH_MA:{self.symbol}:{tf}:{w.watch_id}:{bar_time.value}:{event_time:.6f}"
        self.controller.fire(w.watch_id, f"✅ {self.symbol} · {tf} {expression.canonical}",
                             source_tf=tf, direction=direction,
                             event_time=event_time, event_id=event_id)

    def _ma_cross_event(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 3:
            return
        key = (w.watch_id, tf)
        ct = self._closed_time(df)
        if ct is None:
            return
        if key not in self.last_bar:
            self.last_bar[key] = ct
            return
        if ct == self.last_bar[key]:
            return
        self.last_bar[key] = ct

        family = (w.ma_family or ("EMA" if w.watch_type == "EMA_CROSS" else "HMA")).upper()
        fast = int(w.fast_period or (50 if family == "EMA" else 6))
        slow = int(w.slow_period or (200 if family == "EMA" else 17))
        fast_col = f"{family.lower()}_{fast}"
        slow_col = f"{family.lower()}_{slow}"

        prev, curr = df.iloc[-3], df.iloc[-2]
        vals = (prev.get(fast_col), prev.get(slow_col), curr.get(fast_col), curr.get(slow_col))
        if not all(finite_number(x) for x in vals):
            return
        p_fast, p_slow, c_fast, c_slow = map(float, vals)
        direction = None
        if p_fast <= p_slow and c_fast > c_slow:
            direction = "LONG"
        elif p_fast >= p_slow and c_fast < c_slow:
            direction = "SHORT"
        if direction is None or (w.direction is not None and w.direction != direction):
            return

        if direction == "LONG":
            msg = f"🟢 [{family}{fast}/{slow} 골든크로스]\n{self.symbol} · {tf}"
        else:
            msg = f"🔴 [{family}{fast}/{slow} 데드크로스]\n{self.symbol} · {tf}"
        # ct는 확정봉의 시작시각이므로 유효시간 기준은 실제 봉마감 시각입니다.
        try:
            stamp = pd.Timestamp(ct)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            close_event_ts = float(stamp.timestamp()) + float(tf_seconds(tf))
        except Exception:
            close_event_ts = time.time()
        event_id = f"{family}_CROSS:{self.symbol}:{tf}:{fast}:{slow}:{direction}:{int(close_event_ts)}"
        if self.controller.fire(
            w.watch_id, msg, source_tf=tf, direction=direction,
            event_time=close_event_ts, event_id=event_id,
        ):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [%s Cross Watch] %s %s %s/%s %s", family, self.symbol, tf, fast, slow, direction)

    def _prev_day_touch(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame, daily: pd.DataFrame) -> None:
        if df is None or len(df) < 1 or daily is None or len(daily) < 2:
            return
        live = df.iloc[-1]
        prev_day = daily.iloc[-2]
        hi, lo = live.get("high"), live.get("low")
        pdh, pdl = prev_day.get("high"), prev_day.get("low")
        if not all(finite_number(x) for x in (hi, lo, pdh, pdl)):
            return
        hi, lo, pdh, pdl = map(float, (hi, lo, pdh, pdl))
        sides = []
        if w.level_side in {"HIGH", "BOTH"} and lo <= pdh <= hi:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= pdl <= hi:
            sides.append("LOW")
        current = bool(sides)
        key = (w.watch_id, tf)
        if key not in self.touch_state:
            self.touch_state[key] = current
            return
        previous = self.touch_state[key]
        self.touch_state[key] = current
        if not current or previous:
            return
        side = sides[0]
        if side == "HIGH":
            msg = f"🔔 전일고가 터치 · {self.symbol}"
        else:
            msg = f"🔔 전일저가 터치 · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=side, direction=("SHORT" if side == "HIGH" else "LONG")):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [PDH/PDL Watch] %s %s", self.symbol, side)

    def _wonbi_touch_close(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        closed = self._closed_row_once(w, tf, df)
        if closed is None:
            return
        row, ct = closed
        hi, lo = row.get("high"), row.get("low")
        upper, lower = row.get("wonbi_upper"), row.get("wonbi_lower")
        if not all(finite_number(x) for x in (hi, lo, upper, lower)):
            return
        hi, lo, upper, lower = map(float, (hi, lo, upper, lower))
        sides: list[str] = []
        if w.level_side in {"HIGH", "BOTH"} and hi >= upper:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= lower:
            sides.append("LOW")
        if w.direction == "LONG":
            sides = [x for x in sides if x == "LOW"]
        elif w.direction == "SHORT":
            sides = [x for x in sides if x == "HIGH"]
        if not sides:
            return

        event_time = self._close_event_epoch(ct, tf)
        bar_open_ts = event_time - float(tf_seconds(tf))
        tf_label = TF_LABELS.get(tf, tf)
        for side in sides:
            direction = "SHORT" if side == "HIGH" else "LONG"
            side_label = "상단" if side == "HIGH" else "하단"
            msg = (
                f"✅ {self.symbol} · {tf} {direction} 원비 터치 봉마감"
                if w.chain_id else
                f"🔔 {tf_label} {side_label} 원비 터치 봉마감 · {self.symbol}"
            )
            event_id = f"WONBI_CLOSE:{self.symbol}:{tf}:{direction}:{bar_open_ts:.6f}"
            if self.controller.fire(
                w.watch_id, msg, source_tf=tf, level_side=side, direction=direction,
                event_time=event_time, event_id=event_id,
            ):
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("✅ [원비 Watch] %s %s %s CLOSE", self.symbol, tf, side)

    def _wonbi_touch(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if w.evaluation_mode == "CLOSE":
            self._wonbi_touch_close(w, tf, df)
            return
        if df is None or len(df) < 1:
            return
        live = df.iloc[-1]
        hi, lo = live.get("high"), live.get("low")
        upper, lower = live.get("wonbi_upper"), live.get("wonbi_lower")
        if not all(finite_number(x) for x in (hi, lo, upper, lower)):
            return
        hi, lo, upper, lower = map(float, (hi, lo, upper, lower))
        sides = []
        if w.level_side in {"HIGH", "BOTH"} and hi >= upper:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= lower:
            sides.append("LOW")

        current = bool(sides)
        key = (w.watch_id, tf)
        if key not in self.touch_state:
            self.touch_state[key] = current
            return
        previous = self.touch_state[key]
        self.touch_state[key] = current
        if not current or previous:
            return

        side_label = "상단" if sides[0] == "HIGH" else "하단"
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {side_label} 원비 터치 · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=sides[0], direction=("SHORT" if sides[0] == "HIGH" else "LONG")):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [원비 Watch] %s %s %s", self.symbol, tf, sides[0])

    @staticmethod
    def _percentile_matches(states: dict[str, str], level_side: Optional[str]) -> list[tuple[str, str]]:
        allowed = {"UPPER_OUT"} if level_side == "HIGH" else {"LOWER_OUT"} if level_side == "LOW" else {"UPPER_OUT", "LOWER_OUT"}
        return [(name, state) for name, state in states.items() if state in allowed]

    def _percentile_out(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 1:
            return
        current = percentile_states(df.iloc[-1])
        key = (w.watch_id, tf)
        previous = self.percentile_state.get(key)
        self.percentile_state[key] = current
        if previous is None:
            return

        matches = []
        for name, state in self._percentile_matches(current, w.level_side):
            if previous.get(name) != state:
                matches.append((name, state))
        if not matches:
            return

        details = " · ".join(f"{'상단' if state == 'UPPER_OUT' else '하단'} OUT · {name}" for name, state in matches)
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {details} · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=w.level_side):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [Percentile OUT Watch] %s %s | %s", self.symbol, tf, details)

    def _percentile_out_in(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 1:
            return
        current = percentile_states(df.iloc[-1])
        key = (w.watch_id, tf)
        previous = self.percentile_state.get(key)
        self.percentile_state[key] = current
        if previous is None:
            return

        allowed_prev = {"UPPER_OUT"} if w.level_side == "HIGH" else {"LOWER_OUT"} if w.level_side == "LOW" else {"UPPER_OUT", "LOWER_OUT"}
        matches = []
        for name in PERCENTILES:
            prev_state = previous.get(name)
            if prev_state in allowed_prev and current.get(name) == "IN":
                matches.append((name, prev_state))
        if not matches:
            return

        details = " · ".join(f"{'상단' if state == 'UPPER_OUT' else '하단'} OUT→IN · {name}" for name, state in matches)
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {details} · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=w.level_side):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [Percentile OUT→IN Watch] %s %s | %s", self.symbol, tf, details)

    @staticmethod
    def _indicators_for_watch(w: GenericWatchSpec) -> tuple[str, ...]:
        """Watch 종류별 STAFF 요청 지표를 최소 계약으로 분리합니다."""
        if w.watch_type == "MA_EXPRESSION":
            return tuple(parse_ma_expression(w.ma_expression).dependencies)
        if w.watch_type == "EMA_CROSS":
            return ("EMA",)
        if w.watch_type == "HMA_CROSS":
            return ("HMA",)
        if w.watch_type == "WONBI_TOUCH":
            return ("WONBI",)
        if w.watch_type in {"PERCENTILE_OUT", "PERCENTILE_OUT_IN"}:
            return ("RSI", "STO", "DI", "PRICE")
        # PREV_DAY_TOUCH는 OHLC만 사용합니다.
        return ()

    def evaluate_once(self) -> None:
        """One committed observation; the canonical Watch decision body."""
        _, watches = self.controller.snapshot_for_symbol(self.symbol)
        ids = {w.watch_id for w in watches}
        # 종료된 Watch 상태 정리
        for key in list(self.last_bar):
            if key[0] not in ids:
                self.last_bar.pop(key, None)
        for key in list(self.touch_state):
            if key[0] not in ids:
                self.touch_state.pop(key, None)
        for key in list(self.percentile_state):
            if key[0] not in ids:
                self.percentile_state.pop(key, None)
        if not watches:
            return

        # 서로 다른 Watch 종류를 하나의 과도한 indicator 요청으로 묶지 않습니다.
        # 예: 원비 감시는 RSI/DI 이상 때문에 함께 실패하면 안 됩니다.
        grouped_tfs: dict[tuple[str, ...], set[str]] = {}
        ma_history_by_req: dict[tuple[str, ...], int] = {}
        for w in watches:
            req = self._indicators_for_watch(w)
            grouped_tfs.setdefault(req, set()).update(w.timeframes)
            if w.watch_type == "MA_EXPRESSION":
                rows = parse_ma_expression(w.ma_expression).history_rows(w.evaluation_mode)
                ma_history_by_req[req] = max(rows, ma_history_by_req.get(req, 0))
            if w.watch_type == "PREV_DAY_TOUCH":
                grouped_tfs.setdefault((), set()).add("1d")

        data_by_req: dict[tuple[str, ...], dict[str, pd.DataFrame]] = {}
        for req, tfs in grouped_tfs.items():
            try:
                extra = {"watch_ma_history_rows": ma_history_by_req[req]} if req in ma_history_by_req else {}
                result = self.client.request(
                    self.symbol, sorted(tfs), indicators=list(req), **extra,
                )
            except Exception:
                logging.exception(
                    "[%s] 조건 Watch 데이터 요청 실패 | indicators=%s | TF=%s",
                    self.symbol, ",".join(req) or "OHLCV", ",".join(sorted(tfs)),
                )
                result = None
            data_by_req[req] = result if isinstance(result, dict) else {}

        for w in watches:
            req = self._indicators_for_watch(w)
            data = data_by_req.get(req, {})
            for tf in w.timeframes:
                df = data.get(tf)
                if w.watch_type == "MA_EXPRESSION":
                    self._ma_expression_event(w, tf, df)
                elif w.watch_type in {"EMA_CROSS", "HMA_CROSS"}:
                    self._ma_cross_event(w, tf, df)
                elif w.watch_type == "BAR":
                    self._bar_close(w, tf, df)
                elif w.watch_type == "PREV_DAY_TOUCH":
                    daily = data_by_req.get((), {}).get("1d")
                    self._prev_day_touch(w, tf, df, daily)
                elif w.watch_type == "WONBI_TOUCH":
                    self._wonbi_touch(w, tf, df)
                elif w.watch_type == "PERCENTILE_OUT":
                    self._percentile_out(w, tf, df)
                elif w.watch_type == "PERCENTILE_OUT_IN":
                    self._percentile_out_in(w, tf, df)




class OZCommandHandler:
    """Tail the manager JSONL command queue. Existing lines are skipped on startup."""


    def _apply(self, payload: dict) -> None:
        action = str(payload.get("action", "")).upper()
        if action == "MANUAL_WATCH":
            payload = oz_profiles.normalize_watch_payload(payload)
            validation_mode = payload['validation_mode']
            trigger_mode = payload['trigger_mode']
            external_required = str(
                payload.get("external_liquidity_required", payload.get("external_required", ""))
            ).strip().lower() in {"1", "true", "yes", "y", "on"}
            # 수동 가격은 external_source_kind가 MANUAL_LEVEL로 명시된 경우에만 직접 등록합니다.
            # SWEEP이 넘긴 external_level_price는 추적/로그용 값이며 새 수동 Gate를 만들지 않습니다.
            external_source_kind = str(payload.get("external_source_kind") or "").strip().upper()
            if (
                external_required
                and external_source_kind == "MANUAL_LEVEL"
                and finite_number(payload.get("external_level_price"))
            ):
                self.controller.register_external(payload)
            self.controller.add_manual(
                payload.get("timeframes", []),
                payload.get("issued_at"),
                symbol=payload.get("symbol"),
                direction=payload.get("direction"),
                persistent=bool(payload.get("persistent", False)),
                request_chat_id=payload.get("request_chat_id"),
                validation_mode=validation_mode,
                trigger_mode=trigger_mode,
                oz_mode=payload.get("oz_mode"),
                watch_id=payload.get("watch_id"),
                external_watch_id=(
                    payload.get("external_watch_id")
                    or payload.get("sweep_watch_id")
                    or payload.get("parent_watch_id")
                    or payload.get("setup_watch_id")
                ),
                external_source_tf=(
                    payload.get("external_source_tf")
                    or payload.get("setup_tf")
                    or payload.get("source_tf")
                ),
                external_required=external_required,
                source_spec_id=payload.get("source_spec_id"),
                watch_owner=str(payload.get("watch_owner") or ("KIM" if payload.get("source_spec_id") else "OZ")),
                source_name=payload.get("source_name"),
            )
        elif action == "SWEEP_WATCH":
            self.controller.register_external(payload)
        elif action == "CANCEL_SWEEP":
            self.controller.cancel_external(str(payload.get("watch_id") or ""))
        elif action == "RESET_SWEEP":
            self.controller.reset_external()
        elif action == "CANCEL_MANUAL":
            self.controller.cancel_manual(
                payload.get("timeframes", []),
                symbol=payload.get("symbol"),
                direction=payload.get("direction"),
                persistent_only=bool(payload.get("persistent_only", False)),
                request_chat_id=payload.get("request_chat_id"),
                validation_mode=payload.get("validation_mode"),
                trigger_mode=payload.get("trigger_mode"),
                oz_mode=payload.get("oz_mode"),
                watch_id=payload.get("watch_id"),
                reply_cancel=bool(payload.get("reply_cancel")),
            )
        elif action == "GENERIC_WATCH":
            self.generic.add(payload)
        elif action == "CANCEL_GENERIC":
            self.generic.cancel(payload)
        elif action == "RESET_ALL":
            request_chat_id = payload.get("request_chat_id")
            self.controller.reset_all(request_chat_id=request_chat_id)
            self.generic.reset_all(request_chat_id=request_chat_id)
            if not request_chat_id:
                self.controller.reset_external()





# -----------------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------------
class DomainEventSender:
    """최종 알림을 김매니저에게 REQ로 전달하고 실제 처리 ACK를 확인합니다."""

    def __init__(self, config: dict[str, str], *, transport=None):
        self._transport = transport
        self.endpoint = config.get("MANAGER_ALERT_ENDPOINT", "tcp://127.0.0.1:5556")
        self.timeout_ms = int(config.get("MANAGER_ALERT_TIMEOUT_MS", "15000"))
        if transport is None: raise ValueError("event output port required")
        self.local = threading.local()
        self._events = Records(LOG_DIR / 'oz_outgoing_events.json')



    def _request(self, event):
        return self._transport(event)



    def send(self, text, direction=None, kind="FINAL_ALERT", require_delivery=False, request_chat_id=None, **meta):
        event = {
            "kind": kind,
            "strategy": "OZ",
            "direction": str(direction or "").upper(),
            "message": text,
        }
        target = str(request_chat_id or "").strip()
        if target:
            event["request_chat_id"] = target
        event.update({k: v for k, v in meta.items() if v is not None})
        if kind == "GENERIC_TRIGGER":
            event.setdefault("event_time", time.time())
        if kind == 'FINAL_ALERT':
            event.setdefault('event_id', uuid.uuid4().hex)
            key = identity(event['event_id'], event.get('request_chat_id'))
            saved = self._events.get(key)
            if saved is None:
                self._events.put(key, event)
            else:
                event = saved
        reply = self._request(event)

        if not reply.get("ok"):
            logging.error(
                "[OZ] 김매니저 알림 처리 실패 | %s",
                reply.get("error", "unknown"),
            )
            return False

        if reply.get("filtered"):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "[OZ] 김매니저 알림 필터로 미발송 | direction=%s",
                    str(direction or "").upper(),
                )

        if require_delivery:
            return bool(reply.get("delivered"))

        # 일반 전략은 '필터로 차단됨'도 김매니저가 정상 처리한 것으로 봅니다.
        return True


# -----------------------------------------------------------------------------
# Staff client (same ZMQ protocol as existing strategies)
# -----------------------------------------------------------------------------


class OZFactMemo:
    """공통 OZ Fact: 같은 STAFF 봉(DataFrame 객체)에서 나온 순수 계산값을 1회만 계산합니다.

    16개 OZ 프로필은 같은 SharedOZStaffClient 응답(같은 DataFrame 객체)을 받습니다.
    프로필 상태와 무관한 계산(마지막 봉 행, 퍼센타일 상태, HMA6/17 cross 관측,
    pre-cross 극값, 봉 수, 트리거 터치 등)은 이 메모에 한 번 저장하고
    프로필별 상태머신은 그 값을 조합만 합니다.

    * 키는 DataFrame 객체 자체입니다(weakref). 새 STAFF 응답은 새 객체이므로 새로 계산하고,
      객체가 사라지면 해당 Fact도 함께 사라집니다. STAFF 응답 DataFrame은 OZ에서 수정하지 않습니다.
    * weakref를 만들 수 없는 입력(백테스트 전용 배열 프레임 등)은 캐시 없이 그대로 계산합니다.
    """

    def __init__(self):
        self._frames: dict[int, tuple] = {}
        self._lock = threading.RLock()
        self.computed = 0
        self.reused = 0

    def _forget(self, ident: int, ref) -> None:
        with self._lock:
            entry = self._frames.get(ident)
            if entry is not None and entry[0] is ref:
                self._frames.pop(ident, None)

    def get(self, df, key, compute):
        if df is None:
            return compute()
        ident = id(df)
        with self._lock:
            entry = self._frames.get(ident)
            if entry is None or entry[0]() is not df:
                try:
                    ref = weakref.ref(df, lambda r, i=ident: self._forget(i, r))
                except TypeError:
                    ref = None
                if ref is None:
                    store = None
                else:
                    entry = (ref, {})
                    self._frames[ident] = entry
                    store = entry[1]
            else:
                store = entry[1]
            if store is not None and key in store:
                self.reused += 1
                return store[key]
        value = compute()
        if store is not None:
            with self._lock:
                store.setdefault(key, value)
                self.computed += 1
        return value


class OZSnapshotFeatures:
    """OZ owns its band/regime facts; same publication reuses the shared memo."""
    def __init__(self, client, *, facts=None):
        self.client = client
        self.facts = facts if facts is not None else OZFactMemo()
        self._raw = {}
        self._lock = threading.RLock()

    @staticmethod
    def compose(raw, tf, sigma):
        from indicator_facts import standalone_frame, add_mt5_basis_slopes
        out = raw.copy(deep=True)
        # These S1 owners and their order are the exact original STAFF sequence.
        for owner in (add_price_band_state_features, add_rsi_band_state_features,
                      add_sto_band_state_features, add_di_band_state_features):
            out = owner(out)
        out = add_mt5_basis_slopes(out)
        out[EXTERNAL_ATR_COLUMN] = standalone_frame(raw, tf).get('ATR14_GENERAL')
        # Temporary Python wonbi arithmetic is unchanged until S7.
        return out

    def request(self, req):
        with self._lock:
            batch = self.client.request(req)
            if batch.error is not None:
                return dict(batch.error)
            if batch.closed:
                return {}
            result = {}
            for tf, snapshot in batch.feeds.items():
                key = (snapshot.symbol, tf)
                cached = self._raw.get(key)
                if cached is None or cached[0] != snapshot.identity:
                    cached = (snapshot.identity, self.client.frame(snapshot))
                    self._raw[key] = cached
                raw = cached[1]
                enriched = self.facts.get(raw, ('snapshot_features', batch.sigma),
                    lambda: self.compose(raw, tf, batch.sigma))
                result[tf] = enriched.copy(deep=True)
            return result




# -----------------------------------------------------------------------------
# Percentile state
# -----------------------------------------------------------------------------
def percentile_states(row: pd.Series) -> dict[str, str]:
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
        v, lo, hi = float(v), float(lo), float(hi)
        if v < lo:
            out[name] = "LOWER_OUT"
        elif v > hi:
            out[name] = "UPPER_OUT"
        else:
            out[name] = "IN"
    return out


def same_side_out_reasons(states: dict[str, str], direction: str) -> list[str]:
    target = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"
    return [name for name, state in states.items() if state == target]


def all_in(states: dict[str, str]) -> bool:
    return bool(states) and all(states.get(name) == "IN" for name in PERCENTILES)













# -----------------------------------------------------------------------------
# State machine data
# -----------------------------------------------------------------------------
@dataclass
class IndicatorEpisode:
    active: bool = False
    extreme_price: Optional[float] = None
    extreme_time: Optional[pd.Timestamp] = None


@dataclass
class PercentileCandidate:
    """Independent OZ registration state for one trigger family."""
    percentile: str
    direction: str
    outin_b0_price: float
    outin_b0_time: pd.Timestamp
    trigger_time: pd.Timestamp
    mid_validated: bool = False
    invalidated: bool = False


@dataclass
class Candidate:
    direction: str
    outin_b0_price: float
    outin_b0_time: pd.Timestamp
    trigger_time: pd.Timestamp
    trigger_indicators: set[str] = field(default_factory=set)
    hma_b0_price: Optional[float] = None
    hma_b0_time: Optional[pd.Timestamp] = None
    hma_cross_time: Optional[pd.Timestamp] = None
    true_b0_price: Optional[float] = None
    true_b0_time: Optional[pd.Timestamp] = None
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
    true_b0_time: pd.Timestamp


# -----------------------------------------------------------------------------
# OZ Monitor per symbol
# -----------------------------------------------------------------------------
_ENCODE_PASSTHROUGH = frozenset({str, int, float, bool, type(None)})

def _observed_encode(value):
    # 기본형은 기존과 똑같이 그대로 반환합니다(아래 isinstance 연쇄를 건너뛰는 빠른 경로).
    if type(value) in _ENCODE_PASSTHROUGH: return value
    if isinstance(value, pd.Timestamp): return {'type':'timestamp','value':value.isoformat()}
    if isinstance(value, (IndicatorEpisode, PercentileCandidate, Candidate)):
        return {'type':type(value).__name__, 'value':_observed_encode(vars(value))}
    if isinstance(value, dict): return {'type':'dict','value':[[_observed_encode(k),_observed_encode(v)] for k,v in value.items()]}
    if isinstance(value, (tuple,set,frozenset,list)):
        return {'type':type(value).__name__, 'value':[_observed_encode(x) for x in value]}
    return value

def _observed_decode(value):
    if not isinstance(value, dict): return value
    kind, raw = value['type'], value['value']
    if kind == 'timestamp': return pd.Timestamp(raw)
    if kind == 'dict': return {_observed_decode(k):_observed_decode(v) for k,v in raw}
    if kind in {'tuple','set','frozenset','list'}:
        return {'tuple':tuple,'set':set,'frozenset':frozenset,'list':list}[kind](_observed_decode(x) for x in raw)
    cls = {'IndicatorEpisode':IndicatorEpisode,'PercentileCandidate':PercentileCandidate,'Candidate':Candidate}[kind]
    return cls(**_observed_decode(raw))

def _checkpoint_observation(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        result = fn(self, *args, **kwargs)
        if not self._in_cycle: self._checkpoint()
        return result
    return wrapped

class OZMonitor:
    def __init__(
        self,
        symbol: str,
        config: dict[str, str],
        telegram: DomainEventSender,
        watch: OZWatchController,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
        staff_client=None,
        *, persistence=True, source_time=None,
    ):
        self._persistence = persistence
        self._source_time = source_time
        self.symbol = symbol
        self.config = config
        self.telegram = telegram
        self.watch = watch
        self.validation_mode, self.trigger_mode = _resolve_oz_modes(validation_mode, trigger_mode)
        self.watch_generation = -1
        self.endpoint = config.get("STAFF_ENDPOINT", DEFAULT_ENDPOINT)
        self.timeout_ms = int(config.get("ZMQ_TIMEOUT_MS", "3000"))
        self.client = staff_client
        # 공통 OZ Fact 메모: 같은 종목의 4개 프로필이 공유합니다(SharedOZStaffClient.facts).
        # 공유 STAFF 클라이언트가 아니면 메모 없이 기존처럼 매번 계산합니다.
        self._oz_facts = getattr(self.client, "facts", None)
        self._last_checkpoint_payload = None
        self.max_bars = int(config.get("MAX_BARS_AFTER_B0", "10"))
        self.max_bars_after_hma_cross = int(config.get("MAX_BARS_AFTER_HMA_CROSS", "7"))
        self.max_bars_after_outin = int(config.get("MAX_BARS_AFTER_OUTIN", "7"))
        self.min_bars = int(config.get("MIN_BARS_AFTER_B0", "1"))
        self.loop_sleep = float(config.get("LOOP_SLEEP_SEC", str(LOOP_SLEEP_SEC)))
        self.alert_long = as_bool(config.get("ALERT_LONG"), True)
        self.alert_short = as_bool(config.get("ALERT_SHORT"), True)

        # Base OZ structure is tracked 24/7 on every base TF supported by TF_MAP.
        # Watch timeframes are environment/final-evaluation gates only.
        self.base_tfs: list[str] = list(TF_MAP.keys())

        # Per base-TF / direction / indicator OUT episode state.
        self.episodes: dict[tuple[str, str, str], IndicatorEpisode] = {}
        for tf in self.base_tfs:
            for direction in ("LONG", "SHORT"):
                for ind in PERCENTILES:
                    self.episodes[(tf, direction, ind)] = IndicatorEpisode()


        self.prev_states: dict[str, dict[str, str]] = {}
        self.prev_bar_time: dict[str, Optional[pd.Timestamp]] = {tf: None for tf in self.base_tfs}
        # Common HMA/price structure per TF/direction.
        self.candidates: dict[tuple[str, str], Optional[Candidate]] = {
            (tf, d): None for tf in self.base_tfs for d in ("LONG", "SHORT")
        }
        # Each of the four Percentile OZ families registers independently.
        self.percentile_candidates: dict[tuple[str, str, str], Optional[PercentileCandidate]] = {
            (tf, d, percentile): None
            for tf in self.base_tfs
            for d in ("LONG", "SHORT")
            for percentile in PERCENTILES
        }
        self.alert_keys: set[tuple[str, str, pd.Timestamp]] = set()
        self.bootstrapped: set[str] = set()
        # Environment eligibility is tracked independently from base-OZ state.
        # Identity includes the selected external SWEEP cycle when present, so direct
        # Watch replacement and same-Watch/new-cycle replacement are both detectable
        # without treating ordinary ACTIVE->CONFIRMED status changes as a new environment.
        self.environment_identities: dict[
            tuple[str, str],
            frozenset[tuple[str, Optional[str], Optional[str], Optional[float]]],
        ] = {
            (tf, d): frozenset() for tf in self.base_tfs for d in ("LONG", "SHORT")
        }

        # Latest HMA cross structure extreme per TF/direction.
        # LONG stores the lowest price from the contiguous HMA6<=HMA17 region before GC.
        # SHORT stores the highest price from the contiguous HMA6>=HMA17 region before DC.
        self.hma_cross_extremes: dict[tuple[str, str], Optional[tuple[float, pd.Timestamp, pd.Timestamp]]] = {
            (tf, d): None for tf in self.base_tfs for d in ("LONG", "SHORT")
        }
        self._in_cycle = False
        self._resume_outin = set()
        self._resume_cross = set()
        self._resume_cross_seen = {}
        self._checkpoint_path = LOG_DIR / ('oz_observed_' + identity(symbol, self.validation_mode, self.trigger_mode)[:24] + '.json')
        if persistence and domain_memory.exists(self._checkpoint_path,file_only=False):
            self.restore_observed_checkpoint()

    def restore_observed_checkpoint(self):
        """Canonical startup restore, also callable through the event memory port."""
        raw = read_json(self._checkpoint_path)
        if raw.get('version') != 1: raise ValueError('Unsupported OZ checkpoint')
        for name in self._checkpoint_fields:
            setattr(self, name, _observed_decode(raw['observed'][name]))
        self._resume_outin = set(self.prev_states)
        self._resume_cross = set(self.base_tfs)

    _checkpoint_fields = ('episodes','prev_states','prev_bar_time','candidates','percentile_candidates',
                          'alert_keys','bootstrapped','environment_identities','hma_cross_extremes')

    # 프로필 수식어. trigger_mode 문자열에서 매번 계산하므로 테스트/재설정 시에도 일관됩니다.
    @property
    def use_breaker(self) -> bool:
        return oz_profiles.has_flag(self.trigger_mode, "BREAKER")



    def export_event_state(self):
        """All observed state, suitable for engine-owned memory checkpoints."""
        fields = self._checkpoint_fields + ('watch_generation', '_resume_outin', '_resume_cross', '_resume_cross_seen')
        return {name: _observed_encode(getattr(self, name)) for name in fields}

    def restore_event_state(self, state):
        """Exact continuation, unlike a live restart across an unobserved gap."""
        for name in self._checkpoint_fields + ('watch_generation', '_resume_outin', '_resume_cross', '_resume_cross_seen'):
            setattr(self, name, _observed_decode(state[name]))

    def _checkpoint(self):
        if not getattr(self, '_persistence', True):
            return
        payload = {'version':1, 'symbol':self.symbol,
                   'profile':[self.validation_mode,self.trigger_mode],
                   'observed':{name:_observed_encode(getattr(self,name)) for name in self._checkpoint_fields}}
        # 내용이 마지막 기록과 같으면 파일을 다시 쓰지 않습니다(재시작 복원 내용은 동일).
        if payload == getattr(self, '_last_checkpoint_payload', None):
            return
        atomic_json(self._checkpoint_path, payload)
        self._last_checkpoint_payload = payload

    # ------------------------------------------------------------------
    # 공통 OZ Fact (프로필 무관 순수 계산, OZFactMemo로 1회 계산 후 공유)
    # ------------------------------------------------------------------
    def _fact(self, df, key, compute):
        # 메모는 run_once 주기 안에서, 공유 STAFF 클라이언트가 돌려준 프레임에만 씁니다.
        # 메서드를 직접 호출하는 경우(테스트·재생 도구가 프레임을 수정하며 호출)는 매번 계산합니다.
        memo = getattr(self, '_oz_facts', None)
        if memo is None or not getattr(self, '_in_cycle', False):
            return compute()
        return memo.get(df, key, compute)


    def _live_row(self, df):
        return self._fact(df, ('row', -1), lambda: df.iloc[-1])

    def _live_states(self, df) -> dict[str, str]:
        return dict(self._fact(df, 'percentile_states', lambda: percentile_states(self._live_row(df))))

    def _bars_since(self, df, event_time) -> Optional[int]:
        return self._fact(df, ('bars_since', event_time), lambda: self._bars_since_event(df, event_time))

    @staticmethod
    def _hma_cross_observation(df):
        """(prev HMA6, prev HMA17, live HMA6, live HMA17, live time) or None."""
        prev = df.iloc[-2]
        live = df.iloc[-1]
        p6, p17 = prev.get("hma_6"), prev.get("hma_17")
        c6, c17 = live.get("hma_6"), live.get("hma_17")
        if not all(finite_number(x) for x in (p6, p17, c6, c17)):
            return None
        p6, p17, c6, c17 = map(float, (p6, p17, c6, c17))
        return p6, p17, c6, c17, pd.Timestamp(live.get("time"))

    def _reset_watch_state(self, timeframes: Iterable[str], generation: int) -> None:
        """Refresh Watch bookkeeping without touching live base-OZ structure."""
        self.watch_generation = generation
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "[%s] OZ Watch revision 반영 generation=%d | TF=%s | base state preserved",
                self.symbol, generation, ",".join(timeframes),
            )

    @staticmethod
    def _row_time(row: pd.Series) -> pd.Timestamp:
        return pd.Timestamp(row.get("time"))

    @staticmethod
    def _update_episode_extreme(ep: IndicatorEpisode, direction: str, row: pd.Series) -> None:
        t = OZMonitor._row_time(row)
        px_raw = row.get("low") if direction == "LONG" else row.get("high")
        if not finite_number(px_raw):
            return
        px = float(px_raw)
        if ep.extreme_price is None:
            ep.extreme_price, ep.extreme_time = px, t
        elif direction == "LONG" and px < ep.extreme_price:
            ep.extreme_price, ep.extreme_time = px, t
        elif direction == "SHORT" and px > ep.extreme_price:
            ep.extreme_price, ep.extreme_time = px, t

    def _active_percentile_candidates(self, tf: str, direction: str) -> list[PercentileCandidate]:
        out: list[PercentileCandidate] = []
        for percentile in PERCENTILES:
            item = self.percentile_candidates.get((tf, direction, percentile))
            if item is not None and not item.invalidated:
                out.append(item)
        return out

    def _clear_percentile_candidates(self, tf: str, direction: str) -> None:
        for percentile in PERCENTILES:
            self.percentile_candidates[(tf, direction, percentile)] = None

    def _sync_common_candidate(self, tf: str, direction: str) -> Optional[Candidate]:
        """Build/update the common price structure from independent Percentile registrations."""
        active = self._active_percentile_candidates(tf, direction)
        if not active:
            return self.candidates.get((tf, direction))

        chosen = (
            min(active, key=lambda x: x.outin_b0_price)
            if direction == "LONG"
            else max(active, key=lambda x: x.outin_b0_price)
        )
        percentiles = {x.percentile for x in active}
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            cand = Candidate(
                direction=direction,
                outin_b0_price=float(chosen.outin_b0_price),
                outin_b0_time=pd.Timestamp(chosen.outin_b0_time),
                trigger_time=max(pd.Timestamp(x.trigger_time) for x in active),
                trigger_indicators=percentiles,
            )
            self._apply_latest_hma_extreme(tf, cand)
            self.candidates[(tf, direction)] = cand
        else:
            cand.outin_b0_price = float(chosen.outin_b0_price)
            cand.outin_b0_time = pd.Timestamp(chosen.outin_b0_time)
            cand.trigger_indicators = percentiles
            cand.trigger_time = max(pd.Timestamp(x.trigger_time) for x in active)
        self._refresh_true_b0(cand)
        return cand

    def _register_percentile_candidate(
        self, tf: str, direction: str, percentile: str,
        b0_price: float, b0_time: pd.Timestamp, trigger_time: pd.Timestamp,
    ) -> Candidate:
        self.percentile_candidates[(tf, direction, percentile)] = PercentileCandidate(
            percentile=percentile,
            direction=direction,
            outin_b0_price=float(b0_price),
            outin_b0_time=pd.Timestamp(b0_time),
            trigger_time=pd.Timestamp(trigger_time),
        )
        cand = self._sync_common_candidate(tf, direction)
        assert cand is not None
        return cand

    @_checkpoint_observation
    def _process_out_in(self, tf: str, df: pd.DataFrame) -> None:
        """Track live intra-bar OUT->IN transitions for each percentile independently."""
        if df is None or len(df) < 2:
            return
        live = self._live_row(df)
        current = self._live_states(df)
        if tf in self._resume_outin:
            self._resume_outin.remove(tf)
            self.prev_states[tf] = current
            for direction in ('LONG','SHORT'):
                target = 'LOWER_OUT' if direction == 'LONG' else 'UPPER_OUT'
                for ind in PERCENTILES:
                    ep = self.episodes[(tf,direction,ind)]
                    if current.get(ind) == target:
                        ep.active = True
                        self._update_episode_extreme(ep,direction,live)
                    else:
                        self.episodes[(tf,direction,ind)] = IndicatorEpisode()
            return  # No inferred OUT->IN spanning an unobserved restart gap.
        previous_observed = self.prev_states.get(tf)

        # On first observation, seed active OUT episodes but do not invent an OUT->IN transition.
        if previous_observed is None:
            for direction in ("LONG", "SHORT"):
                target = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"
                for ind in PERCENTILES:
                    if current.get(ind) == target:
                        ep = self.episodes[(tf, direction, ind)]
                        ep.active = True
                        self._update_episode_extreme(ep, direction, live)
            self.prev_states[tf] = current
            return

        events: dict[str, list[tuple[str, float, pd.Timestamp]]] = {"LONG": [], "SHORT": []}

        for direction in ("LONG", "SHORT"):
            target = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"
            for ind in PERCENTILES:
                ep = self.episodes[(tf, direction, ind)]
                cur_state = current.get(ind, "NA")
                prev_state = previous_observed.get(ind, "NA")

                if cur_state == target:
                    if not ep.active:
                        ep.active = True
                        ep.extreme_price = None
                        ep.extreme_time = None
                    self._update_episode_extreme(ep, direction, live)
                    continue

                # Include the return-IN candle's full high/low in B0 extreme.
                if ep.active and prev_state == target and cur_state == "IN":
                    self._update_episode_extreme(ep, direction, live)
                    if ep.extreme_price is not None and ep.extreme_time is not None:
                        events[direction].append((ind, ep.extreme_price, ep.extreme_time))
                    ep.active = False
                    ep.extreme_price = None
                    ep.extreme_time = None
                elif ep.active and cur_state not in (target, "IN"):
                    # Opposite-side jump / unavailable value: reset the episode safely.
                    ep.active = False
                    ep.extreme_price = None
                    ep.extreme_time = None

        self.prev_states[tf] = current

        # Each of the four Percentiles registers its own OZ family candidate.
        # The HMA/price structure is common and uses the most extreme registered OUT->IN price.
        trigger_time = self._row_time(live)
        for direction, evs in events.items():
            for ind, b0_price, b0_time in evs:
                cand = self._register_percentile_candidate(
                    tf, direction, ind, float(b0_price), pd.Timestamp(b0_time), trigger_time,
                )
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "🟡 [OZ 후보등록] %s %s %s | %s OUT→IN | OUT/IN B0=%s @ %s | TRUE B0=%s @ %s",
                        self.symbol, tf, direction, ind,
                        f"{float(b0_price):.6f}", b0_time,
                        f"{cand.true_b0_price:.6f}" if cand.true_b0_price is not None else "-", cand.true_b0_time,
                    )

    @staticmethod
    def _refresh_true_b0(cand: Candidate) -> None:
        """Combine OUT->IN and HMA pre-cross extremes into the true structural B0."""
        choices: list[tuple[float, pd.Timestamp]] = [
            (float(cand.outin_b0_price), pd.Timestamp(cand.outin_b0_time))
        ]
        if cand.hma_b0_price is not None and cand.hma_b0_time is not None:
            choices.append((float(cand.hma_b0_price), pd.Timestamp(cand.hma_b0_time)))

        if cand.direction == "LONG":
            price, when = min(choices, key=lambda x: x[0])
        else:
            price, when = max(choices, key=lambda x: x[0])
        cand.true_b0_price = float(price)
        cand.true_b0_time = pd.Timestamp(when)

    @staticmethod
    def _family_true_b0_time(cand: Candidate, reg: PercentileCandidate) -> Optional[pd.Timestamp]:
        """해당 Percentile family의 첫 번째 저점(LONG)/고점(SHORT) 시각 = family TRUE B0."""
        choices: list[tuple[float, pd.Timestamp]] = [
            (float(reg.outin_b0_price), pd.Timestamp(reg.outin_b0_time))
        ]
        if cand.hma_b0_price is not None and cand.hma_b0_time is not None:
            choices.append((float(cand.hma_b0_price), pd.Timestamp(cand.hma_b0_time)))
        if cand.direction == "LONG":
            return min(choices, key=lambda x: x[0])[1]
        return max(choices, key=lambda x: x[0])[1]

    def _apply_latest_hma_extreme(self, tf: str, cand: Candidate) -> None:
        item = self.hma_cross_extremes.get((tf, cand.direction))
        if item is None:
            return
        price, extreme_time, cross_time = item
        cand.hma_b0_price = float(price)
        cand.hma_b0_time = pd.Timestamp(extreme_time)
        cand.hma_cross_time = pd.Timestamp(cross_time)

    @staticmethod
    def _reconstruct_pre_cross_extreme(df: pd.DataFrame, direction: str) -> Optional[tuple[float, pd.Timestamp]]:
        """Return the price extreme from the contiguous opposite-alignment region before the latest cross."""
        if df is None or len(df) < 2:
            return None
        h6 = pd.to_numeric(df['hma_6'].iloc[:-1], errors='coerce').to_numpy(float)
        h17 = pd.to_numeric(df['hma_17'].iloc[:-1], errors='coerce').to_numpy(float)
        valid = np.isfinite(h6) & np.isfinite(h17)
        aligned = h6 <= h17 if direction == "LONG" else h6 >= h17
        stops = np.flatnonzero(~(valid & aligned))
        first = int(stops[-1]+1) if len(stops) else 0
        column = 'low' if direction == "LONG" else 'high'
        prices = pd.to_numeric(df[column].iloc[first:-1], errors='coerce').to_numpy(float)
        valid_prices = np.flatnonzero(np.isfinite(prices))
        if not len(valid_prices):
            return None
        # Original backward scan breaks ties at the most recent equal extreme.
        positions = valid_prices[::-1]
        pick = np.argmin(prices[positions]) if direction == "LONG" else np.argmax(prices[positions])
        index = first + int(positions[pick])
        return float(prices[index-first]), pd.Timestamp(df['time'].iloc[index])

    def _reset_direction_cycle(self, tf: str, direction: str) -> None:
        """Clear one base-TF/direction OZ cycle and wait for a fresh structure."""
        self.candidates[(tf, direction)] = None
        self.hma_cross_extremes[(tf, direction)] = None
        self._clear_percentile_candidates(tf, direction)
        for ind in PERCENTILES:
            self.episodes[(tf, direction, ind)] = IndicatorEpisode()

    def _cancel_base_candidate(self, tf: str, direction: str, reason: str) -> None:
        """Common base-TF invalidation used by every validation/trigger profile."""
        cand = self.candidates.get((tf, direction))
        had_state = cand is not None or bool(self._active_percentile_candidates(tf, direction))
        if not had_state and self.hma_cross_extremes.get((tf, direction)) is None:
            return
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🛑 [OZ BASE 취소] %s %s %s | validation=%s trigger=%s | reason=%s",
                self.symbol, tf, direction, self.validation_mode, self.trigger_mode, reason,
            )
        self._reset_direction_cycle(tf, direction)

    @staticmethod
    def _one_way_reason(
        df: pd.DataFrame,
        direction: str,
        hma_cross_time: Optional[pd.Timestamp],
    ) -> Optional[str]:
        """GC→3 bullish closes / DC→3 bearish closes means one-way and kills the OZ pullback."""
        if df is None or len(df) < 4 or hma_cross_time is None:
            return None
        closed = df.iloc[:-1]
        if len(closed) < 3:
            return None
        recent = closed.tail(3)
        times = pd.to_datetime(recent.get("time"), errors="coerce")
        if len(times) != 3 or times.isna().any():
            return None
        cross_time = pd.Timestamp(hma_cross_time)
        if any(pd.Timestamp(t) <= cross_time for t in times):
            return None

        opens = pd.to_numeric(recent.get("open"), errors="coerce")
        closes = pd.to_numeric(recent.get("close"), errors="coerce")
        if opens.isna().any() or closes.isna().any():
            return None

        if direction == "LONG" and bool((closes > opens).all()):
            return "ONE_WAY_UP"
        if direction == "SHORT" and bool((closes < opens).all()):
            return "ONE_WAY_DOWN"
        return None

    def _check_base_invalidation(
        self,
        tf: str,
        direction: str,
        df: pd.DataFrame,
        cand: Candidate,
    ) -> tuple[bool, Optional[int], Optional[int]]:
        """Return (cancelled, true_b0_bars, hma_cross_bars).

        Base invalidation is independent of NORMAL/BLIND and OZ/BREAKER:
          1) opposite HMA cross (handled at cross event time),
          2) one-way 3-candle continuation,
          3) existing TRUE-B0/HMA-cross timers.
        """
        one_way = self._fact(df, ('one_way', direction, cand.hma_cross_time),
                             lambda: self._one_way_reason(df, direction, cand.hma_cross_time))
        if one_way:
            self._cancel_base_candidate(tf, direction, one_way)
            return True, None, None

        bars_since = self._bars_since(df, cand.true_b0_time)
        if bars_since is None:
            return False, None, None
        if bars_since > self.max_bars:
            self._cancel_base_candidate(
                tf, direction,
                f"TIMER_TRUE_B0:{bars_since}>{self.max_bars}",
            )
            return True, bars_since, None

        cross_bars = self._bars_since(df, cand.hma_cross_time)
        if cross_bars is None:
            return False, bars_since, None
        if cross_bars > self.max_bars_after_hma_cross:
            self._cancel_base_candidate(
                tf, direction,
                f"TIMER_HMA_CROSS:{cross_bars}>{self.max_bars_after_hma_cross}",
            )
            return True, bars_since, cross_bars

        return False, bars_since, cross_bars

    @_checkpoint_observation
    def _maintain_base_candidate(self, tf: str, direction: str, df: pd.DataFrame) -> None:
        """Maintain base-OZ survival independently of environment Watch eligibility."""
        # Each registered 1X family keeps its existing own OUT->IN timer.
        for percentile in PERCENTILES:
            reg = self.percentile_candidates.get((tf, direction, percentile))
            if reg is None or reg.invalidated:
                continue
            outin_bars = self._bars_since(df, reg.trigger_time)
            if outin_bars is None:
                continue
            if outin_bars > self.max_bars_after_outin:
                reg.invalidated = True
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "⚪ [OZ Percentile 만료] %s %s %s | %s | validation=%s trigger=%s | 후보등록 이후 %d봉 > 제한 %d봉",
                        self.symbol, tf, direction, percentile,
                        self.validation_mode, self.trigger_mode,
                        outin_bars, self.max_bars_after_outin,
                    )

        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return

        # 등록된 Percentile family가 모두 만료/탈락하면 후보의 트리거 누적도 초기화합니다.
        # (TRUE B0 / HMA cross 타이머 판정은 아래에서 기존과 동일하게 계속 수행)
        alive = bool(self._active_percentile_candidates(tf, direction))
        if not alive and cand.trigger_hits:
            cand.trigger_hits = {}

        # Preserve the existing meaning of TRUE-B0/HMA-cross survival: those clocks
        # become actionable only after both structural timing events are present.
        if cand.hma_cross_time is None or cand.hma_b0_price is None:
            return
        self._refresh_true_b0(cand)
        if cand.true_b0_price is None or cand.true_b0_time is None:
            return
        cancelled, bars_since, _cross_bars = self._check_base_invalidation(tf, direction, df, cand)
        if cancelled:
            return
        if alive and not self.use_breaker:
            self._accumulate_trigger_hits(tf, df, direction, cand, bars_since)

    @staticmethod
    def _breaker_bo_break_trigger(
        df: pd.DataFrame,
        direction: str,
        bo_level: Optional[float],
        hma_cross_time: Optional[pd.Timestamp],
    ) -> bool:
        """Strict BO break event for BREAKER mode.

        The structural TRUE-B0 level is the BO reference. Unlike the normal OZ
        B0 trigger, TOUCH/NEAR is not accepted: price must cross strictly through
        the level. No additional indicator calculation is used.
        """
        if df is None or len(df) < 2 or not finite_number(bo_level) or hma_cross_time is None:
            return False
        prev = df.iloc[-2]
        live = df.iloc[-1]
        live_time = pd.to_datetime(live.get("time"), errors="coerce")
        if pd.isna(live_time) or pd.Timestamp(live_time) <= pd.Timestamp(hma_cross_time):
            return False
        level = float(bo_level)
        if direction == "LONG":
            prev_low, live_low = prev.get("low"), live.get("low")
            if not finite_number(prev_low) or not finite_number(live_low):
                return False
            return float(prev_low) >= level and float(live_low) < level
        prev_high, live_high = prev.get("high"), live.get("high")
        if not finite_number(prev_high) or not finite_number(live_high):
            return False
        return float(prev_high) <= level and float(live_high) > level

    @_checkpoint_observation
    def _process_hma_cross(self, tf: str, df: pd.DataFrame) -> None:
        """Track HMA6/17 cross independently so it may occur before or after OUT->IN."""
        if df is None or len(df) < 2:
            return
        observed = self._fact(df, 'hma_cross_observation', lambda: self._hma_cross_observation(df))
        if observed is None:
            return
        p6, p17, c6, c17, cross_time = observed
        observation = (cross_time, c6 > c17, c6 < c17)
        resuming = tf in self._resume_cross
        if resuming:
            self._resume_cross.remove(tf)
            self._resume_cross_seen[tf] = observation
        elif self._resume_cross_seen.get(tf) == observation:
            return
        else:
            self._resume_cross_seen.pop(tf, None)

        direction = None
        if p6 <= p17 and c6 > c17:
            direction = "LONG"
        elif p6 >= p17 and c6 < c17:
            direction = "SHORT"
        if direction is None:
            return

        # Base-TF common invalidation: an actual opposite HMA6/17 cross kills
        # the currently tracked opposite-direction OZ cycle in every profile.
        opposite = "SHORT" if direction == "LONG" else "LONG"
        self._cancel_base_candidate(
            tf, opposite,
            "OPPOSITE_HMA_CROSS:GC" if direction == "LONG" else "OPPOSITE_HMA_CROSS:DC",
        )
        if resuming:
            return  # Cancellation is valid; new cross structures are not reconstructed on restore.

        # 같은 cross가 이미 기록돼 있으면 결과와 상관없이 반환하므로(기존 순서와 동일한 결과)
        # pre-cross 극값 재구성보다 먼저 확인해 cross 봉 동안의 반복 계산을 없앱니다.
        existing = self.hma_cross_extremes.get((tf, direction))
        if existing is not None and pd.Timestamp(existing[2]) == cross_time:
            return
        ext = self._fact(df, ('pre_cross_extreme', direction),
                         lambda: self._reconstruct_pre_cross_extreme(df, direction))
        if ext is None:
            return
        price, extreme_time = ext
        self.hma_cross_extremes[(tf, direction)] = (float(price), pd.Timestamp(extreme_time), cross_time)

        cand = self._sync_common_candidate(tf, direction)
        if cand is not None and not cand.alerted:
            before = cand.true_b0_price
            cand.hma_b0_price = float(price)
            cand.hma_b0_time = pd.Timestamp(extreme_time)
            cand.hma_cross_time = cross_time
            self._refresh_true_b0(cand)
            if before != cand.true_b0_price:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "🔄 [OZ TRUE B0 갱신] %s %s %s | 기존=%s → HMA pre-cross=%s | TRUE=%s",
                        self.symbol, tf, direction, before, price, cand.true_b0_price,
                    )

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🔵 [OZ HMA CROSS] %s %s %s | pre-cross extreme=%s @ %s | cross=%s",
                self.symbol, tf, direction, f"{price:.6f}", extreme_time, cross_time,
            )

    @staticmethod
    def _bars_since_event(df: pd.DataFrame, event_time: pd.Timestamp) -> Optional[int]:
        if df is None or df.empty or "time" not in df.columns:
            return None
        times = pd.to_datetime(df["time"], errors="coerce")
        target = pd.Timestamp(event_time)
        hits = times[times == target].index
        if len(hits) == 0:
            return None
        return int(df.index[-1] - hits[-1])

    @staticmethod
    def _hma_slope(df: pd.DataFrame) -> Optional[float]:
        if df is None or len(df) < 2:
            return None
        a = df.iloc[-2].get("hma_6")
        b = df.iloc[-1].get("hma_6")
        if not finite_number(a) or not finite_number(b):
            return None
        return float(b) - float(a)

    @staticmethod
    def _higher_tf_open_vs_hma6(df: pd.DataFrame, direction: str) -> bool:
        """Require the mapped 3X candle to open on the trend side of its HMA6."""
        if df is None or len(df) < 1:
            return False
        row = df.iloc[-1]
        o = row.get("open")
        h6 = row.get("hma_6")
        if not finite_number(o) or not finite_number(h6):
            return False
        if direction == "LONG":
            return float(o) > float(h6)
        return float(o) < float(h6)

    @staticmethod
    def _hma_aligned(df: pd.DataFrame, direction: str) -> bool:
        if df is None or len(df) < 1:
            return False
        h6, h17 = df.iloc[-1].get("hma_6"), df.iloc[-1].get("hma_17")
        if not finite_number(h6) or not finite_number(h17):
            return False
        if direction == "LONG":
            return float(h6) > float(h17)
        return float(h6) < float(h17)

    @staticmethod
    def _trigger_state(df: pd.DataFrame, direction: str, level: float, kind: str) -> str:
        """Touch only: the live candle range must include the level (NEAR 없음)."""
        if df is None or len(df) < 1 or not finite_number(level):
            return "MISS"
        row = df.iloc[-1]
        low, high = row.get("low"), row.get("high")
        if not all(finite_number(x) for x in (low, high)):
            return "MISS"
        if float(low) <= float(level) <= float(high):
            return "TOUCH"
        return "MISS"

    @staticmethod
    def _candle_pullback_trigger(
        df: pd.DataFrame,
        hma_cross_time: pd.Timestamp,
        direction: str,
    ) -> Optional[str]:
        """Closed-candle pullback triggers after the HMA6/17 cross.

        LONG : bearish engulfing + next bearish, or three bearish closes.
        SHORT: bullish engulfing + next bullish, or three bullish closes.
        The caller applies the common HMA6/17 alignment gate.
        """
        if df is None or len(df) < 5 or hma_cross_time is None:
            return None

        # -1 is the live candle. Use closed candles only.
        before = df.iloc[-4]
        first = df.iloc[-3]
        second = df.iloc[-2]

        def _oc(row: pd.Series) -> Optional[tuple[float, float]]:
            o, c = row.get("open"), row.get("close")
            if not finite_number(o) or not finite_number(c):
                return None
            return float(o), float(c)

        vals = (_oc(before), _oc(first), _oc(second))
        if any(v is None for v in vals):
            return None
        (before_o, before_c), (first_o, first_c), (second_o, second_c) = vals

        times = [pd.to_datetime(r.get("time"), errors="coerce") for r in (before, first, second)]
        if any(pd.isna(t) for t in times):
            return None
        before_time, first_time, _ = map(pd.Timestamp, times)
        cross_time = pd.Timestamp(hma_cross_time)

        if direction == "LONG":
            engulf = (
                before_c > before_o and first_c < first_o
                and first_o >= before_c and first_c <= before_o
            )
            if engulf and second_c < second_o and first_time > cross_time:
                return "ENGULF_PLUS_BEAR"
            if before_c < before_o and first_c < first_o and second_c < second_o and before_time > cross_time:
                return "THREE_BEAR"
        else:
            engulf = (
                before_c < before_o and first_c > first_o
                and first_o <= before_c and first_c >= before_o
            )
            if engulf and second_c > second_o and first_time > cross_time:
                return "ENGULF_PLUS_BULL"
            if before_c > before_o and first_c > first_o and second_c > second_o and before_time > cross_time:
                return "THREE_BULL"

        return None

    @staticmethod
    def _hma6_turn_pullback_trigger(
        df: pd.DataFrame,
        hma_cross_time: pd.Timestamp,
        direction: str,
    ) -> bool:
        """Sixth independent trigger: HMA6 Hull-color turn on a closed candle.

        Pine-equivalent color rule: hull > hull[2] = rising/green, else falling/red.
        LONG : previous closed HMA6 was green, latest closed HMA6 turns red.
        SHORT: previous closed HMA6 was red, latest closed HMA6 turns green.
        """
        if df is None or len(df) < 5 or hma_cross_time is None:
            return False

        # Latest closed color: [-2] vs [-4]. Previous closed color: [-3] vs [-5].
        h_prev_now = df.iloc[-3].get("hma_6")
        h_prev_2 = df.iloc[-5].get("hma_6")
        h_now = df.iloc[-2].get("hma_6")
        h_now_2 = df.iloc[-4].get("hma_6")
        if not all(finite_number(x) for x in (h_prev_now, h_prev_2, h_now, h_now_2)):
            return False

        prev_green = float(h_prev_now) > float(h_prev_2)
        now_green = float(h_now) > float(h_now_2)
        closed_time = pd.to_datetime(df.iloc[-2].get("time"), errors="coerce")
        if pd.isna(closed_time) or pd.Timestamp(closed_time) <= pd.Timestamp(hma_cross_time):
            return False

        return (prev_green and not now_green) if direction == "LONG" else ((not prev_green) and now_green)

    @staticmethod
    def _level_hma17(df: pd.DataFrame) -> Optional[float]:
        if df is None or len(df) < 1:
            return None
        v = df.iloc[-1].get("hma_17")
        return float(v) if finite_number(v) else None

    @staticmethod
    def _level_wonbi(df: pd.DataFrame, direction: str) -> Optional[float]:
        if df is None or len(df) < 1:
            return None
        col = "wonbi_lower" if direction == "LONG" else "wonbi_upper"
        v = df.iloc[-1].get(col)
        return float(v) if finite_number(v) else None

    # 누적 조건 표시 순서.
    TRIGGER_HIT_ORDER = ("B0", "HMA17", "WONBI", "CANDLE", "HMA6_TURN")
    TRIGGER_MIN_OTHER_HITS = 2

    def _current_trigger_hits(
        self,
        df: pd.DataFrame,
        direction: str,
        true_b0_price: Optional[float],
        hma_cross_time: Optional[pd.Timestamp],
        b0_eligible: bool,
    ) -> dict[str, str]:
        """현재 관측 시점에 TRUE인 OZ 트리거 조건 (터치만 허용)."""
        key = ('trigger_hits', direction, true_b0_price, hma_cross_time, bool(b0_eligible))
        return dict(self._fact(df, key, lambda: self._compute_trigger_hits(
            df, direction, true_b0_price, hma_cross_time, b0_eligible)))

    def _compute_trigger_hits(
        self,
        df: pd.DataFrame,
        direction: str,
        true_b0_price: Optional[float],
        hma_cross_time: Optional[pd.Timestamp],
        b0_eligible: bool,
    ) -> dict[str, str]:
        hits: dict[str, str] = {}
        if b0_eligible and self._trigger_state(df, direction, true_b0_price, "B0") == "TOUCH":
            hits["B0"] = "B0"
        hma17 = self._level_hma17(df)
        if hma17 is not None and self._trigger_state(df, direction, hma17, "HMA17") == "TOUCH":
            hits["HMA17"] = "HMA17"
        wonbi_level = self._level_wonbi(df, direction)
        if wonbi_level is not None and self._trigger_state(df, direction, wonbi_level, "WONBI") == "TOUCH":
            hits["WONBI"] = "WONBI"
        candle_trigger = self._candle_pullback_trigger(df, hma_cross_time, direction)
        if candle_trigger in {"ENGULF_PLUS_BEAR", "ENGULF_PLUS_BULL"}:
            hits["CANDLE"] = "하락장악+추가음봉" if direction == "LONG" else "상승장악+추가양봉"
        elif candle_trigger in {"THREE_BEAR", "THREE_BULL"}:
            hits["CANDLE"] = "3연속음봉" if direction == "LONG" else "3연속양봉"
        if self._hma6_turn_pullback_trigger(df, hma_cross_time, direction):
            hits["HMA6_TURN"] = "HMA6 하방꺾임" if direction == "LONG" else "HMA6 상방꺾임"
        return hits

    def _accumulate_trigger_hits(
        self,
        tf: str,
        df: pd.DataFrame,
        direction: str,
        cand: Candidate,
        bars_since: Optional[int],
    ) -> None:
        """OZ 후보가 살아있는 동안 조건 충족을 누적합니다(동시조건 아님)."""
        b0_eligible = bars_since is not None and bars_since >= self.min_bars
        current = self._current_trigger_hits(
            df, direction, cand.true_b0_price, cand.hma_cross_time, b0_eligible,
        )
        new_keys = [key for key in current if key not in cand.trigger_hits]
        for key in new_keys:
            cand.trigger_hits[key] = current[key]
        if new_keys:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🟠 [OZ 트리거 누적] %s %s %s | validation=%s trigger=%s | 신규=%s | 누적=%s",
                    self.symbol, tf, direction, self.validation_mode, self.trigger_mode,
                    ",".join(new_keys), ",".join(self._ordered_hit_keys(cand.trigger_hits)),
                )

    @classmethod
    def _ordered_hit_keys(cls, hits: dict[str, str]) -> list[str]:
        return [key for key in cls.TRIGGER_HIT_ORDER if key in hits]

    def _final_trigger_decision(
        self,
        df: pd.DataFrame,
        direction: str,
        cand: Candidate,
    ) -> Optional[FinalTriggerDecision]:
        """Evaluate the profile final-trigger family without sending anything."""
        if self.use_breaker:
            if not self._fact(df, ('bo_break', direction, cand.true_b0_price, cand.hma_cross_time),
                              lambda: self._breaker_bo_break_trigger(
                                  df, direction, cand.true_b0_price, cand.hma_cross_time)):
                return None
            return FinalTriggerDecision(trigger_name="BO_BREAK", b0_state="BREAK")

        # The caller already enforced MIN_BARS since TRUE B0, so the live B0 touch is eligible.
        current = self._current_trigger_hits(
            df, direction, cand.true_b0_price, cand.hma_cross_time, True,
        )
        hits = dict(cand.trigger_hits)
        for key, label in current.items():
            hits.setdefault(key, label)

        b0_state = "TOUCH" if "B0" in hits else "MISS"
        h17_state = "TOUCH" if "HMA17" in hits else "MISS"
        wonbi_state = "TOUCH" if "WONBI" in hits else "MISS"

        if "B0" in hits:
            trigger_name = "B0"
        else:
            others = [key for key in self._ordered_hit_keys(hits) if key != "B0"]
            if len(others) < self.TRIGGER_MIN_OTHER_HITS:
                return None
            trigger_name = ", ".join(hits[key] for key in others)

        return FinalTriggerDecision(
            trigger_name=trigger_name,
            b0_state=b0_state,
            h17_state=h17_state,
            wonbi_state=wonbi_state,
        )

    def _candidate_completion_decision(
        self,
        data: dict,
        tf: str,
        direction: str,
        *,
        require_external: bool,
        commit_validation: bool,
    ) -> Optional[CandidateCompletionDecision]:
        """Run the existing completion semantics without coupling them to Telegram delivery.

        ``commit_validation=False`` is used only by the passive/silent probe. It applies
        the same NORMAL/BLIND predicates and selected trigger-profile filters to decide whether the cycle is complete, while
        leaving the existing percentile validation state untouched until an environment
        is actually eligible for the normal evaluation path.
        """
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return None

        df = data.get(tf)
        if df is None or len(df) < 2:
            return None

        # HMA cross and OUT->IN remain independent timing events.
        if cand.hma_cross_time is None or cand.hma_b0_price is None:
            return None
        self._refresh_true_b0(cand)
        if cand.true_b0_price is None or cand.true_b0_time is None:
            return None

        # Base survival is maintained every loop before this function. MIN_BARS keeps
        # its existing final-evaluation meaning in both normal and silent completion.
        bars_since = self._bars_since(df, cand.true_b0_time)
        cross_bars = self._bars_since(df, cand.hma_cross_time)
        if bars_since is None or cross_bars is None or bars_since < self.min_bars:
            return None

        active_regs = self._active_percentile_candidates(tf, direction)
        if not active_regs:
            return None

        # ------------------------------------------------------------------
        # LAYER 2. Existing validation mode.
        # NORMAL = mapped middle OUT + upper IN + middle open/HMA6 gate.
        # BLIND  = no upper-frame validation at all.
        # ------------------------------------------------------------------
        matched_regs: list[PercentileCandidate] = []
        if self.validation_mode == "NORMAL":
            tf3, tf6 = TF_MAP[tf]
            df3, df6 = data.get(tf3), data.get(tf6)
            if any(x is None or len(x) < 2 for x in (df3, df6)):
                return None

            states3 = self._live_states(df3)
            states6 = self._live_states(df6)
            target_out = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"

            for percentile in PERCENTILES:
                reg = self.percentile_candidates.get((tf, direction, percentile))
                if reg is None or reg.invalidated:
                    continue

                mid_is_out = states3.get(percentile) == target_out
                upper_is_in = states6.get(percentile) == "IN"

                if not reg.mid_validated:
                    if not mid_is_out:
                        if commit_validation:
                            reg.invalidated = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 중위TF(%s) OUT 아님",
                                    self.symbol, tf, direction, percentile, tf3,
                                )
                        continue
                    if commit_validation:
                        reg.mid_validated = True

                if not mid_is_out:
                    if commit_validation:
                        reg.invalidated = True
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 중위TF(%s) OUT 유지 실패",
                                self.symbol, tf, direction, percentile, tf3,
                            )
                    continue

                if not upper_is_in:
                    if commit_validation:
                        reg.invalidated = True
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 상위TF(%s) IN 유지 실패",
                                self.symbol, tf, direction, percentile, tf6,
                            )
                    continue

                matched_regs.append(reg)

            if not matched_regs:
                return None
            if not self._fact(df3, ('open_vs_hma6', direction),
                              lambda: self._higher_tf_open_vs_hma6(df3, direction)):
                return None
        else:
            # BLIND: active base-TF registrations alone count.
            matched_regs = [reg for reg in active_regs if not reg.invalidated]
            if not matched_regs:
                return None

        # For the normal path, preserve the existing mutation semantics exactly:
        # failed NORMAL families were invalidated above, then the common TRUE B0 is
        # rebuilt from the surviving registrations. The silent probe computes the
        # same surviving structure on a temporary Candidate so it does not alter the
        # live validation state merely because no environment is present.
        surviving_regs = self._active_percentile_candidates(tf, direction) if commit_validation else matched_regs
        if not surviving_regs:
            return None
        chosen_reg = (
            min(surviving_regs, key=lambda x: x.outin_b0_price)
            if direction == "LONG"
            else max(surviving_regs, key=lambda x: x.outin_b0_price)
        )

        if commit_validation:
            cand.outin_b0_price = float(chosen_reg.outin_b0_price)
            cand.outin_b0_time = pd.Timestamp(chosen_reg.outin_b0_time)
            cand.trigger_indicators = {x.percentile for x in surviving_regs}
            self._refresh_true_b0(cand)
            eval_cand = cand
        else:
            eval_cand = Candidate(
                direction=cand.direction,
                outin_b0_price=float(chosen_reg.outin_b0_price),
                outin_b0_time=pd.Timestamp(chosen_reg.outin_b0_time),
                trigger_time=cand.trigger_time,
                trigger_indicators={x.percentile for x in surviving_regs},
                hma_b0_price=cand.hma_b0_price,
                hma_b0_time=cand.hma_b0_time,
                hma_cross_time=cand.hma_cross_time,
                trigger_hits=dict(cand.trigger_hits),
            )
            self._refresh_true_b0(eval_cand)

        if eval_cand.true_b0_price is None or eval_cand.true_b0_time is None:
            return None

        # External liquidity is an environment/final-evaluation gate. Silent completion
        # deliberately does not manufacture or require an external Watch; the normal
        # path keeps the existing second ATR qualification unchanged.
        if require_external and not self.watch.validate_external_true_b0(
            self.symbol, tf, direction, self.validation_mode, self.trigger_mode, eval_cand.true_b0_price
        ):
            return None

        consensus_count = len(matched_regs)
        consensus_grade = {4: "S", 3: "A", 2: "B", 1: "C"}.get(consensus_count)
        if consensus_grade is None:
            return None

        # Base HMA alignment remains the existing live entry/completion gate.
        if not self._fact(df, ('hma_aligned', direction), lambda: self._hma_aligned(df, direction)):
            return None

        trigger = self._final_trigger_decision(df, direction, eval_cand)
        if trigger is None:
            return None

        return CandidateCompletionDecision(
            grade=consensus_grade,
            consensus_count=consensus_count,
            matched_trigger_contexts=tuple(reg.percentile for reg in matched_regs),
            trigger=trigger,
            bars_since=bars_since,
            cross_bars=cross_bars,
            true_b0_price=float(eval_cand.true_b0_price),
            true_b0_time=pd.Timestamp(eval_cand.true_b0_time),
        )

    @_checkpoint_observation
    def _silent_consume_candidate(
        self,
        tf: str,
        direction: str,
        decision: CandidateCompletionDecision,
        reason: str,
    ) -> None:
        """Consume a completed OZ cycle without Telegram/Watch delivery."""
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return
        alert_key = (tf, direction, decision.true_b0_time)
        self.alert_keys.add(alert_key)
        cand.completed_outside_window = True
        self._clear_percentile_candidates(tf, direction)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "⚫ [OZ silent consume] %s %s %s | validation=%s trigger_mode=%s | reason=%s | 최종트리거=%s | TRUE_B0=%.6f | 완성Percentile=%s",
                self.symbol, tf, direction, self.validation_mode, self.trigger_mode,
                reason, decision.trigger.trigger_name, decision.true_b0_price,
                "/".join(decision.matched_trigger_contexts),
            )

    def _probe_silent_completion(self, data: dict, tf: str, direction: str, reason: str) -> bool:
        decision = self._candidate_completion_decision(
            data, tf, direction, require_external=False, commit_validation=False
        )
        if decision is None:
            return False
        self._silent_consume_candidate(tf, direction, decision, reason)
        return True

    @_checkpoint_observation
    def _evaluate_candidate(self, data: dict, tf: str, direction: str) -> None:
        decision = self._candidate_completion_decision(
            data, tf, direction, require_external=True, commit_validation=True
        )
        if decision is None:
            return

        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return

        alert_key = (tf, direction, decision.true_b0_time)
        if alert_key in self.alert_keys:
            cand.alerted = True
            return

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "[OZ 판정] %s · %s · %s | %s급 | validation=%s trigger_mode=%s | 완성올존=%d/4 | 최종트리거=%s | TRUE_B0=%s | HMA17=%s | 원비=%s | B0bars=%d | CROSSbars=%d | 완성Percentile=%s",
                self.symbol, tf, "매수" if direction == "LONG" else "매도",
                decision.grade, self.validation_mode, self.trigger_mode,
                decision.consensus_count, decision.trigger.trigger_name,
                decision.trigger.b0_state, decision.trigger.h17_state, decision.trigger.wonbi_state,
                decision.bars_since, decision.cross_bars, "/".join(decision.matched_trigger_contexts),
            )

        df = data.get(tf)
        if cand.completion_time is None:
            cand.completion_time = self._source_time if getattr(self, '_source_time', None) is not None else time.time()
        self._checkpoint()  # Persist observed completion/identity before any external effect.
        fired = self.watch.try_fire(
            self.symbol,
            tf,
            direction,
            decision.grade,
            self.validation_mode,
            self.trigger_mode,
            trigger_name=decision.trigger.trigger_name,
            alert_identity=identity(self.symbol, tf, direction, self.validation_mode,
                                    self.trigger_mode, str(decision.true_b0_time)),
            completion_time=cand.completion_time,
            indicators_text=", ".join(decision.matched_trigger_contexts),
            current_price=(
                float(df.iloc[-1].get("close"))
                if df is not None and not df.empty and finite_number(df.iloc[-1].get("close"))
                else None
            ),
        )
        if fired:
            self.alert_keys.add(alert_key)
            cand.alerted = True
            self._clear_percentile_candidates(tf, direction)

    def run_once(self, revision: int, watched_tfs: Iterable[str]):
        self._in_cycle = True
        try:
            result = self._run_once(revision, watched_tfs)
            self._checkpoint()
            return result
        finally:
            self._in_cycle = False

    def _run_once(self, revision: int, watched_tfs: Iterable[str]):
        # watched_tfs is environment eligibility only. Base OZ tracking always uses
        # the monitor's full supported base-TF range.
        watched = [tf for tf in watched_tfs if tf in TF_MAP]

        needed = set(self.base_tfs)
        if self.validation_mode == "NORMAL":
            # Silent completion must preserve the existing NORMAL meaning even when
            # no environment Watch exists. The mapped TF union adds only confirmation
            # feeds; Base OZ tracking itself remains the unchanged TF_MAP.keys() set.
            for tf in self.base_tfs:
                tf3, tf6 = TF_MAP[tf]
                needed.update((tf3, tf6))
        # Pending/active external setups also need their source TF so ATR14 snapshot and
        # 1.5x survival are evaluated here, not in SWEEP.
        needed.update(self.watch.external_source_tfs_for_profile(
            self.symbol, self.validation_mode, self.trigger_mode
        ))
        required_tfs = sorted(needed)

        data = self.client.request(self.symbol, required_tfs)
        if not data:
            return

        missing_base = [tf for tf in self.base_tfs if tf not in data]
        if missing_base:
            logging.warning("[%s] OZ BASE 누락 TF: %s", self.symbol, ",".join(missing_base))

        self.watch.update_external_market(
            self.symbol, self.validation_mode, self.trigger_mode, data
        )

        # BASE OZ ENGINE: always-on, independent of environment Watch existence/direction.
        # 계산 단계 (수정본6):
        #   1) 후보 전 최소 상태: HMA6/17 cross 관측과 4개 Percentile OUT/IN 상태만 추적합니다.
        #      이 값은 공통 OZ Fact(OZFactMemo)로 종목당 1회 계산되고 4개 프로필이 공유합니다.
        #   2) 후보 생성 후(OUT->IN 등록): B0·타이머·트리거 누적·탈락조건(_maintain_base_candidate),
        #      중상위 검증·Regime·Super·최종 트리거(_candidate_completion_decision)를 계산합니다.
        #      후보가 없으면 두 함수는 첫 줄에서 바로 반환합니다. 프로필 무관 값은 역시 공통 Fact입니다.
        for tf in self.base_tfs:
            df = data.get(tf)
            if df is None:
                continue
            self._process_hma_cross(tf, df)
            self._process_out_in(tf, df)
            for direction in ("LONG", "SHORT"):
                self._maintain_base_candidate(tf, direction, df)

        # COMPLETION / ENVIRONMENT GATE:
        # - no eligible environment: run the same profile completion semantics silently;
        # - False->True eligibility: probe once before firing, so a trigger already
        #   accumulated in the live candle is consumed instead of being back-dated;
        # - continuously eligible environment: keep the existing normal final path.
        for tf in self.base_tfs:
            if tf not in data:
                continue

            allowed: set[str] = set()
            if tf in watched:
                _current_revision, current_tfs = self.watch.snapshot_for_symbol(
                    self.symbol, self.validation_mode, self.trigger_mode
                )
                if tf in current_tfs:
                    allowed = self.watch.allowed_directions(
                        self.symbol, tf, self.validation_mode, self.trigger_mode
                    )

            for direction in ("LONG", "SHORT"):
                key = (tf, direction)
                previous_environment_ids = self.environment_identities.get(key, frozenset())
                is_allowed = direction in allowed

                if not is_allowed:
                    self.environment_identities[key] = frozenset()
                    self._probe_silent_completion(data, tf, direction, "NO_ENVIRONMENT")
                    continue

                current_environment_ids = self.watch.allowed_environment_identities(
                    self.symbol, tf, direction, self.validation_mode, self.trigger_mode
                )
                fresh_environment = (
                    not previous_environment_ids
                    or previous_environment_ids.isdisjoint(current_environment_ids)
                )

                # On first eligibility, or when every previously eligible environment identity
                # was replaced, the current OHLC snapshot may already contain a final trigger
                # that predates the new environment. A surviving identity keeps continuity.
                if fresh_environment and self._probe_silent_completion(
                    data, tf, direction, "PREEXISTING_AT_ENVIRONMENT_ACTIVATION"
                ):
                    self.environment_identities[key] = current_environment_ids
                    continue

                self.environment_identities[key] = current_environment_ids
                if direction == "LONG" and self.alert_long:
                    self._evaluate_candidate(data, tf, direction)
                elif direction == "SHORT" and self.alert_short:
                    self._evaluate_candidate(data, tf, direction)



# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------




# S1: pure legacy STAFF facts, function bodies preserved verbatim.
import numpy as np

def add_price_band_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """PRICE 추세 올존용 상태. 판정선은 PRICE 지표의 CLOSE 기반 HMA6입니다.

    zone: -1=하단 밖, 0=밴드 안, +1=상단 밖, NaN=판정 불가
    """
    value = pd.to_numeric(df["price_hma_6"], errors="coerce")

    pct_low = pd.to_numeric(df["price_band_lower"], errors="coerce")
    pct_up = pd.to_numeric(df["price_band_upper"], errors="coerce")
    pct_valid = value.notna() & pct_low.notna() & pct_up.notna()
    pct_zone = np.select([value < pct_low, value > pct_up], [-1.0, 1.0], default=0.0)
    df["price_percentile_zone"] = pd.Series(pct_zone, index=df.index).where(pct_valid, np.nan)
    df["price_percentile_in"] = pd.Series(pct_zone == 0, index=df.index).where(pct_valid)

    regime_basis = pd.to_numeric(df["price_regime_basis"], errors="coerce")
    regime_low = pd.to_numeric(df["price_regime_lower"], errors="coerce")
    regime_up = pd.to_numeric(df["price_regime_upper"], errors="coerce")
    regime_valid = value.notna() & regime_low.notna() & regime_up.notna()
    regime_zone = np.select([value < regime_low, value > regime_up], [-1.0, 1.0], default=0.0)
    df["price_regime_zone"] = pd.Series(regime_zone, index=df.index).where(regime_valid, np.nan)
    df["price_regime_in"] = pd.Series(regime_zone == 0, index=df.index).where(regime_valid)
    df["price_regime_slope"] = regime_basis.diff()
    return df


def add_rsi_band_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """RSI 추세 올존용 상태. 판정값은 RSI_of_Moses의 cRSI(smoothBuffer)입니다.

    zone: -1=하단 밖, 0=밴드 안, +1=상단 밖, NaN=판정 불가
    """
    value = pd.to_numeric(df["RSI_val"], errors="coerce")

    pct_low = pd.to_numeric(df["RSI_db"], errors="coerce")
    pct_up = pd.to_numeric(df["RSI_ub"], errors="coerce")
    pct_valid = value.notna() & pct_low.notna() & pct_up.notna()
    pct_zone = np.select([value < pct_low, value > pct_up], [-1.0, 1.0], default=0.0)
    df["RSI_percentile_zone"] = pd.Series(pct_zone, index=df.index).where(pct_valid, np.nan)
    df["RSI_percentile_in"] = pd.Series(pct_zone == 0, index=df.index).where(pct_valid)

    regime_basis = pd.to_numeric(df["RSI_basis"], errors="coerce")
    regime_low = pd.to_numeric(df["RSI_regime_lower"], errors="coerce")
    regime_up = pd.to_numeric(df["RSI_regime_upper"], errors="coerce")
    regime_valid = value.notna() & regime_low.notna() & regime_up.notna()
    regime_zone = np.select([value < regime_low, value > regime_up], [-1.0, 1.0], default=0.0)
    df["RSI_regime_zone"] = pd.Series(regime_zone, index=df.index).where(regime_valid, np.nan)
    df["RSI_regime_in"] = pd.Series(regime_zone == 0, index=df.index).where(regime_valid)
    df["RSI_regime_slope"] = regime_basis.diff()
    return df


def add_sto_band_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """STO 추세 올존용 상태. 판정값은 STO_of_Moses의 cSTO(smoothBuffer)입니다.

    zone: -1=하단 밖, 0=밴드 안, +1=상단 밖, NaN=판정 불가
    """
    value = pd.to_numeric(df["STO_val"], errors="coerce")

    pct_low = pd.to_numeric(df["STO_db"], errors="coerce")
    pct_up = pd.to_numeric(df["STO_ub"], errors="coerce")
    pct_valid = value.notna() & pct_low.notna() & pct_up.notna()
    pct_zone = np.select([value < pct_low, value > pct_up], [-1.0, 1.0], default=0.0)
    df["STO_percentile_zone"] = pd.Series(pct_zone, index=df.index).where(pct_valid, np.nan)
    df["STO_percentile_in"] = pd.Series(pct_zone == 0, index=df.index).where(pct_valid)

    regime_basis = pd.to_numeric(df["STO_basis"], errors="coerce")
    regime_low = pd.to_numeric(df["STO_regime_lower"], errors="coerce")
    regime_up = pd.to_numeric(df["STO_regime_upper"], errors="coerce")
    regime_valid = value.notna() & regime_low.notna() & regime_up.notna()
    regime_zone = np.select([value < regime_low, value > regime_up], [-1.0, 1.0], default=0.0)
    df["STO_regime_zone"] = pd.Series(regime_zone, index=df.index).where(regime_valid, np.nan)
    df["STO_regime_in"] = pd.Series(regime_zone == 0, index=df.index).where(regime_valid)
    df["STO_regime_slope"] = regime_basis.diff()
    return df


def add_di_band_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """DI 추세 올존용 상태. 판정값은 DI_of_Moses의 cDI(smoothBuffer)입니다.

    zone: -1=하단 밖, 0=밴드 안, +1=상단 밖, NaN=판정 불가
    """
    value = pd.to_numeric(df["DI_val"], errors="coerce")

    pct_low = pd.to_numeric(df["DI_db"], errors="coerce")
    pct_up = pd.to_numeric(df["DI_ub"], errors="coerce")
    pct_valid = value.notna() & pct_low.notna() & pct_up.notna()
    pct_zone = np.select([value < pct_low, value > pct_up], [-1.0, 1.0], default=0.0)
    df["DI_percentile_zone"] = pd.Series(pct_zone, index=df.index).where(pct_valid, np.nan)
    df["DI_percentile_in"] = pd.Series(pct_zone == 0, index=df.index).where(pct_valid)

    regime_basis = pd.to_numeric(df["DI_basis"], errors="coerce")
    regime_low = pd.to_numeric(df["DI_regime_lower"], errors="coerce")
    regime_up = pd.to_numeric(df["DI_regime_upper"], errors="coerce")
    regime_valid = value.notna() & regime_low.notna() & regime_up.notna()
    regime_zone = np.select([value < regime_low, value > regime_up], [-1.0, 1.0], default=0.0)
    df["DI_regime_zone"] = pd.Series(regime_zone, index=df.index).where(regime_valid, np.nan)
    df["DI_regime_in"] = pd.Series(regime_zone == 0, index=df.index).where(regime_valid)
    df["DI_regime_slope"] = regime_basis.diff()
    return df


