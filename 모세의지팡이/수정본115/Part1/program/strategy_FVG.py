# -*- coding: utf-8 -*-
from __future__ import annotations
import domain_memory
from durable_protocol import FactStream, source_health, atomic_json, read_json

import json
import logging
import math
import os
import queue
import threading
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# FVG 2.0 - pure FVG detector
# =============================================================================
# 역할:
#   - 요청받은 타임프레임의 3-candle FVG 생성/방향/영역/유효/터치만 판독합니다.
#   - 특정 타임프레임 목록에 고정되지 않습니다. STAFF에 존재하는 TF라면 그대로 요청합니다.
#   - FVG 터치 역시 별도 1m 가격을 사용하지 않습니다. 요청한 TF의 live bar로 판독합니다.
#   - FVG는 live bar에서 채워져도 즉시 삭제하지 않습니다.
#     해당 TF의 봉이 마감된 뒤, 마감된 봉의 고가/저가가 FVG 반대편 경계까지 도달해
#     gap 전체를 채운 것이 확인되면 유효 FVG에서 제거합니다.
#   - 확정봉 기준 최근 30개 봉 안에서 생성된 FVG만 유효 후보로 봅니다.
#   - 채워지지 않은 후보 중 BULL/BEAR 각각 가장 최근 3개만 실제 추적합니다.
#   - 순수 FVG gap이 1번봉 마감 Wilder ATR의 설정 배수 이상인 FVG만 인정합니다.
#   - TREND/WONBI/SWEEP/시간필터/OZ/등급/최종진입/채널 정책을 포함하지 않습니다.
#
# 이 파일은 앞으로 어떤 메인전략/Watch와 조합될지 알지 못합니다.
# 사실(event/state)만 김매니저에 전달하고, 조합의 의미는 Composer가 결정합니다.
# =============================================================================

STAFF_ENDPOINT = "tcp://127.0.0.1:5555"
MANAGER_ALERT_ENDPOINT = "tcp://127.0.0.1:5556"
ZMQ_TIMEOUT_MS = 5000
MANAGER_TIMEOUT_MS = 15000
LOOP_SLEEP_SEC = 0.5
FVG_MAX_AGE_BARS = 30
FVG_MAX_TRACKED_PER_SIDE = 3
REQUIRED_INDS: list[str] = []


# ============================================================================
# [FVG 사용자 설정]
# ============================================================================
# ATR 기간. FVG가 확정되는 3번봉이 아니라, FVG 3개 봉 중 1번봉 마감 시점의 ATR을 사용합니다.
FVG_ATR_PERIOD = 14

# 최소 FVG 크기. 순수 FVG gap >= 1번봉 마감 ATR × 이 배수인 경우만 유효 FVG로 인정합니다.
FVG_MIN_ATR_RATIO = 0.25

# 최대 FVG 크기. 순수 FVG gap <= 1번봉 마감 ATR × 이 배수인 경우만 유효 FVG로 인정합니다.
FVG_MAX_ATR_RATIO = 1.75
# ============================================================================


def load_runtime_config() -> dict[str, str]:
    """전략 통신 설정만 config.txt에서 읽습니다. 미설정 시 기존 기본값을 유지합니다."""
    path = Path(__file__).resolve().parent / "config.txt"
    config: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return config


def runtime_connection_settings(config: dict[str, str]) -> tuple[str, str, int, int]:
    staff_endpoint = str(
        config.get("STAFF_ENDPOINT", config.get("STAFF_BIND_ENDPOINT", STAFF_ENDPOINT))
    ).strip() or STAFF_ENDPOINT
    manager_endpoint = str(config.get("MANAGER_ALERT_ENDPOINT", MANAGER_ALERT_ENDPOINT)).strip() or MANAGER_ALERT_ENDPOINT
    staff_timeout_ms = int(config.get("ZMQ_TIMEOUT_MS", str(ZMQ_TIMEOUT_MS)))
    manager_timeout_ms = int(
        config.get("MANAGER_ALERT_TIMEOUT_MS", config.get("MANAGER_TIMEOUT_MS", str(MANAGER_TIMEOUT_MS)))
    )
    return staff_endpoint, manager_endpoint, staff_timeout_ms, manager_timeout_ms


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def normalize_tf(value) -> str:
    return str(value or "").strip().lower()


def as_epoch(value):
    """STAFF/MT5 time을 UTC epoch로 정규화합니다."""
    if value is None:
        return None
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
        return ts.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def FVG_WILDER_ATR(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """OHLC만으로 Wilder ATR을 계산합니다. 각 값은 해당 봉 마감 시점 기준입니다."""
    period = int(period)
    if period <= 0:
        raise ValueError("FVG_ATR_PERIOD는 1 이상이어야 합니다")

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = pd.Series(float("nan"), index=tr.index, dtype="float64")
    if len(tr) < period:
        return atr

    seed = pd.to_numeric(tr.iloc[:period], errors="coerce")
    if seed.isna().any():
        return atr

    # 같은 Wilder 재귀식을 NumPy 배열 위에서 계산합니다(pandas 행 단위 접근 제거, 값 동일).
    values = atr.to_numpy(dtype="float64", copy=True)
    tr_values = tr.to_numpy(dtype="float64")
    values[period - 1] = float(seed.mean())
    for pos in range(period, len(tr_values)):
        current_tr = tr_values[pos]
        previous_atr = values[pos - 1]
        if not math.isfinite(current_tr) or not math.isfinite(previous_atr):
            continue
        values[pos] = (float(previous_atr) * (period - 1) + float(current_tr)) / period
    return pd.Series(values, index=tr.index, dtype="float64")


_wilder_atr = FVG_WILDER_ATR


def add_fvg_local(df: pd.DataFrame) -> pd.DataFrame:
    """
    기존 1.0 FVG의 3-candle FVG 계산식을 유지하되,
    순수 FVG gap이 1번봉 마감 ATR의 설정 배수 이상인 경우만 유효 FVG로 인정합니다.

    Bull FVG: 현재 low > 2봉 전 high
    Bear FVG: 현재 high < 2봉 전 low
    ATR 기준: FVG 3개 봉 중 1번봉(현재 행 기준 2봉 전) 마감 ATR
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")

    atr = _wilder_atr(high, low, close, FVG_ATR_PERIOD)
    base_atr = atr.shift(2)
    min_gap = base_atr * float(FVG_MIN_ATR_RATIO)
    max_gap = base_atr * float(FVG_MAX_ATR_RATIO)

    out["bull_fvg_gap"] = low - high.shift(2)
    out["is_bull_fvg"] = (
        (out["bull_fvg_gap"] > 0)
        & (out["bull_fvg_gap"] >= min_gap)
        & (out["bull_fvg_gap"] <= max_gap)
    )
    out["bull_fvg_top"] = low
    out["bull_fvg_bot"] = high.shift(2)

    out["bear_fvg_gap"] = low.shift(2) - high
    out["is_bear_fvg"] = (
        (out["bear_fvg_gap"] > 0)
        & (out["bear_fvg_gap"] >= min_gap)
        & (out["bear_fvg_gap"] <= max_gap)
    )
    out["bear_fvg_top"] = low.shift(2)
    out["bear_fvg_bot"] = high
    return out


def candle_overlaps_zone(candle_low, candle_high, zone_bot, zone_top) -> bool:
    """한 캔들의 고저가 범위가 FVG 가격영역과 겹치는지 판독합니다."""
    if not all(finite(x) for x in (candle_low, candle_high, zone_bot, zone_top)):
        return False
    lo = min(float(zone_bot), float(zone_top))
    hi = max(float(zone_bot), float(zone_top))
    return float(candle_high) >= lo and float(candle_low) <= hi


def make_zone_id(symbol: str, tf: str, side: str, fvg_time: float, bot: float, top: float) -> str:
    """프로세스 재시작 후에도 같은 FVG를 동일하게 표현하는 안정적인 ID입니다."""
    return (
        f"{str(symbol)}|{normalize_tf(tf)}|{str(side).upper()}|"
        f"{int(float(fvg_time))}|{float(bot):.12g}|{float(top):.12g}"
    )


def _zone_from_row(symbol: str, tf: str, row, side: str) -> dict | None:
    fvg_time = as_epoch(row.get("time"))
    if fvg_time is None:
        return None

    if side == "BULL":
        if not bool(row.get("is_bull_fvg", False)):
            return None
        bot = row.get("bull_fvg_bot")
        top = row.get("bull_fvg_top")
        gap = row.get("bull_fvg_gap")
        direction = "LONG"
    else:
        if not bool(row.get("is_bear_fvg", False)):
            return None
        bot = row.get("bear_fvg_bot")
        top = row.get("bear_fvg_top")
        gap = row.get("bear_fvg_gap")
        direction = "SHORT"

    if not all(finite(x) for x in (bot, top, gap)):
        return None

    zone = {
        "symbol": str(symbol),
        "source_tf": normalize_tf(tf),
        "fvg_side": side,
        "direction": direction,
        "fvg_time": float(fvg_time),
        "zone_bot": float(bot),
        "zone_top": float(top),
        "gap": float(gap),
    }
    zone["zone_id"] = make_zone_id(
        symbol, tf, side, float(fvg_time), float(bot), float(top)
    )
    return zone


def _filled_by_closed_bar(zone: dict, later_closed: pd.DataFrame) -> tuple[bool, float | None]:
    """
    FVG가 이후 '마감된 봉'에 의해 완전히 채워졌는지 판독합니다.

    Bull FVG: 이후 확정봉 low <= zone_bot 이면 gap 하단까지 전부 채움.
    Bear FVG: 이후 확정봉 high >= zone_top 이면 gap 상단까지 전부 채움.

    live bar는 이 함수에 들어오지 않으므로 장중 순간 돌파만으로 FVG를 삭제하지 않습니다.
    """
    if later_closed is None or later_closed.empty:
        return False, None

    if zone["fvg_side"] == "BULL":
        lows = pd.to_numeric(later_closed["low"], errors="coerce")
        hits = later_closed.loc[lows <= float(zone["zone_bot"])]
    else:
        highs = pd.to_numeric(later_closed["high"], errors="coerce")
        hits = later_closed.loc[highs >= float(zone["zone_top"])]

    if hits.empty:
        return False, None
    fill_time = as_epoch(hits.iloc[0].get("time"))
    return True, fill_time


class FVGStructureCache:
    """FVG 구조(ATR·생성·채움·만료 후보)를 확정봉 입력이 바뀔 때만 다시 계산합니다.

    구조 계산은 확정봉(df.iloc[:-1])의 time/high/low/close만 사용합니다. 같은 (symbol, TF)의
    다음 호출에서 이 값과 설정이 모두 같으면 이전 구조를 재사용하고, 진행봉 터치만 다시 판정합니다.
    새 봉이 생기거나 확정봉 값이 정정되면 비교가 달라져 전체를 다시 계산합니다.
    """

    def __init__(self, max_keys: int = 512):
        self.max_keys = int(max_keys)
        self._entries: dict[tuple[str, str], tuple] = {}
        self._lock = threading.RLock()
        self.structure_builds = 0
        self.structure_reuses = 0

    @staticmethod
    def _signature(df: pd.DataFrame):
        closed = df.iloc[:-1]
        arrays = [closed["time"].to_numpy()]
        for column in ("high", "low", "close"):
            arrays.append(pd.to_numeric(closed[column], errors="coerce").to_numpy(dtype=float))
        settings = (int(FVG_ATR_PERIOD), float(FVG_MIN_ATR_RATIO), float(FVG_MAX_ATR_RATIO),
                    int(FVG_MAX_AGE_BARS))
        return settings, arrays

    @staticmethod
    def _same(a, b) -> bool:
        if a[0] != b[0] or len(a[1]) != len(b[1]):
            return False
        for x, y in zip(a[1], b[1]):
            if x.shape != y.shape or x.dtype != y.dtype:
                return False
            if x.dtype.kind == "f":
                if not np.array_equal(x, y, equal_nan=True):
                    return False
            elif not np.array_equal(x, y):
                return False
        return True

    def structure(self, symbol: str, tf: str, df: pd.DataFrame):
        key = (str(symbol), str(tf))
        try:
            signature = self._signature(df)
        except Exception:
            return _build_fvg_structure(symbol, tf, df)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and self._same(entry[0], signature):
                self.structure_reuses += 1
                return entry[1]
        structure = _build_fvg_structure(symbol, tf, df)
        with self._lock:
            if key not in self._entries and len(self._entries) >= self.max_keys:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (signature, structure)
            self.structure_builds += 1
        return structure

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


STRUCTURE_CACHE = FVGStructureCache()


def _build_fvg_structure(symbol: str, tf: str, df: pd.DataFrame) -> dict | None:
    """확정봉만으로 정해지는 FVG 구조. 진행봉 터치는 포함하지 않습니다(불변 값으로 보관)."""
    local = add_fvg_local(df)
    closed = local.iloc[:-1].reset_index(drop=True)
    if len(closed) < 3:
        return None

    # 최근 N개 확정봉 안에서 생성된 FVG만 후보로 봅니다.
    # FVG 계산은 2봉 전 데이터가 필요하므로 최소 pos=2를 보장합니다.
    first_candidate_pos = max(2, len(closed) - int(FVG_MAX_AGE_BARS))
    oldest_allowed_time = as_epoch(closed.iloc[first_candidate_pos].get("time"))

    active_candidates: list[dict] = []
    filled_zones: list[dict] = []

    for pos in range(first_candidate_pos, len(closed)):
        row = closed.iloc[pos]
        later_closed = closed.iloc[pos + 1 :]

        for side in ("BULL", "BEAR"):
            zone = _zone_from_row(symbol, tf, row, side)
            if zone is None:
                continue

            # 최신 확정봉을 age=0으로 둡니다. tail(30)에 포함되는 후보는 0~29봉 전입니다.
            zone["age_bars"] = int(len(closed) - 1 - pos)

            filled, fill_time = _filled_by_closed_bar(zone, later_closed)
            if filled:
                filled_zone = dict(zone)
                filled_zone["fill_time"] = fill_time
                filled_zones.append(filled_zone)
                continue
            active_candidates.append(zone)

    filled_zones.sort(key=lambda z: z["fvg_time"], reverse=True)
    return {
        "latest_closed_time": as_epoch(closed.iloc[-1].get("time")),
        "oldest_allowed_time": oldest_allowed_time,
        "active_candidates": tuple(active_candidates),
        "filled_zones": tuple(filled_zones),
    }


def build_fvg_state(symbol: str, tf: str, df: pd.DataFrame, *, cache: "FVGStructureCache | None" = STRUCTURE_CACHE) -> dict | None:
    """
    요청 TF의 현재 FVG 상태를 재구성합니다.

    - FVG 생성 후보: 확정봉만 사용(df.iloc[:-1])
    - 최대 나이: 최근 FVG_MAX_AGE_BARS개 확정봉 안에서 생성된 FVG만 후보
    - 유효성: 생성 이후 확정봉으로 gap이 완전히 채워졌으면 제거
    - 추적 수: 채워지지 않은 후보 중 BULL/BEAR 각각 최신 FVG_MAX_TRACKED_PER_SIDE개
    - 터치: 동일 요청 TF의 현재 live bar(df.iloc[-1])로 판독

    최신 3개 필터는 "삭제"가 아니라 외부 추적 대상 제한입니다.
    30봉 안의 더 오래된 유효 FVG는 매 계산 때 재구성되므로, 최신 3개 중 하나가
    채워지면 다음 순번의 유효 FVG가 자동으로 추적 대상에 올라올 수 있습니다.

    구조(ATR·생성·채움·만료)는 확정봉 입력이 바뀔 때만 계산하고(FVGStructureCache),
    0.5초 루프에서는 진행봉 터치만 다시 판정합니다. cache=None이면 매번 전체 계산합니다.
    """
    tf = normalize_tf(tf)
    if not tf or df is None or len(df) < 4:
        return None

    structure = cache.structure(symbol, tf, df) if cache is not None else _build_fvg_structure(symbol, tf, df)
    if structure is None:
        return None

    live_row = df.iloc[-1]
    live_low = live_row.get("low")
    live_high = live_row.get("high")
    live_close = live_row.get("close")
    live_time = as_epoch(live_row.get("time"))
    live_ok = all(finite(x) for x in (live_low, live_high, live_close))

    active_candidates: list[dict] = []
    for cached in structure["active_candidates"]:
        zone = dict(cached)
        zone["touched_now"] = bool(
            live_ok
            and candle_overlaps_zone(
                live_low, live_high, zone["zone_bot"], zone["zone_top"]
            )
        )
        active_candidates.append(zone)

    # 방향별로 가장 최근 발생한 FVG만 실제 추적합니다.
    tracked_zones: list[dict] = []
    for side in ("BULL", "BEAR"):
        side_zones = [z for z in active_candidates if z["fvg_side"] == side]
        side_zones.sort(key=lambda z: z["fvg_time"], reverse=True)
        tracked_zones.extend(side_zones[: int(FVG_MAX_TRACKED_PER_SIDE)])

    tracked_zones.sort(key=lambda z: z["fvg_time"], reverse=True)

    return {
        "symbol": str(symbol),
        "source_tf": tf,
        "latest_closed_time": structure["latest_closed_time"],
        "oldest_allowed_time": structure["oldest_allowed_time"],
        "live_price": float(live_close) if live_ok else None,
        "live_time": live_time,
        "zones": tracked_zones,
        "filled_zones": [dict(z) for z in structure["filled_zones"]],
        "eligible_zone_ids": [z["zone_id"] for z in active_candidates],
    }






class FVGWatchRegistry:
    """김매니저가 요청한 FVG 판독 Watch만 보관합니다. TF는 고정 목록을 두지 않습니다."""

    def __init__(self):
        self._lock = threading.RLock()
        self._watches: dict[str, tuple[str, str]] = {}
        root = domain_memory.module_directory(__file__)
        self._state_path = root / "logs" / "fvg_watch_state.json"
        self._load_state()

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            items = raw.get("watches", {}) if isinstance(raw, dict) else {}
            restored: dict[str, tuple[str, str]] = {}
            if isinstance(items, dict):
                for watch_id, value in items.items():
                    if not isinstance(value, (list, tuple)) or len(value) != 2:
                        continue
                    symbol = str(value[0] or "").strip()
                    tf = normalize_tf(value[1])
                    if watch_id and symbol and tf:
                        restored[str(watch_id)] = (symbol, tf)
            with self._lock:
                self._watches = restored
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [FVG Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[FVG Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 2,
            "watches": {k: [v[0], v[1]] for k, v in self._watches.items()},
        }
        atomic_json(self._state_path, payload, default=None, allow_nan=True, indent=2)

    def add(self, watch_id: str, symbol: str, source_tf: str) -> None:
        symbol = str(symbol or "").strip()
        tf = normalize_tf(source_tf)
        if not watch_id or not symbol or not tf:
            return
        with self._lock:
            self._watches[str(watch_id)] = (symbol, tf)
            self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟣 [FVG Watch] 등록 | %s | %s %s", watch_id, symbol, tf)

    def cancel(self, watch_id: str) -> None:
        with self._lock:
            removed = self._watches.pop(str(watch_id or ""), None)
            if removed:
                self._save_state_locked()
        if removed:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🛑 [FVG Watch] 취소 | %s | %s %s", watch_id, removed[0], removed[1])

    def reset(self) -> None:
        with self._lock:
            count = len(self._watches)
            self._watches.clear()
            if count:
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("♻️ [FVG Watch] 전체 초기화 | %d건", count)

    def snapshot(self) -> dict[str, tuple[str, ...]]:
        with self._lock:
            grouped: dict[str, list[str]] = {}
            for symbol, tf in self._watches.values():
                bucket = grouped.setdefault(symbol, [])
                if tf not in bucket:
                    bucket.append(tf)
        return {symbol: tuple(tfs) for symbol, tfs in grouped.items()}




class FVGEngine:
    """다른 전략 의미를 전혀 갖지 않는 순수 FVG 판독 엔진입니다."""

    def __init__(
        self,
        *,
        staff_endpoint: str = STAFF_ENDPOINT,
        staff_timeout_ms: int = ZMQ_TIMEOUT_MS,
        manager_endpoint: str = MANAGER_ALERT_ENDPOINT,
        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
        staff_client=None, manager_client=None, event_state=None,
    ):
        self.staff = staff_client
        self.manager = manager_client
        state = {} if event_state is None else event_state
        self._last_closed_time = state.setdefault('last_closed_time', {})
        self._touch_state = state.setdefault('touch_state', {})
        self._active_zones = state.setdefault('active_zones', {})
        self._seen_created = state.setdefault('seen_created', set())
        self._initialized_keys = state.setdefault('initialized_keys', set())

    def evaluate(self, symbol: str, tf: str, df: pd.DataFrame):
        result = build_fvg_state(symbol, tf, df)
        if result is not None:
            result['source_health'] = source_health({tf:df}, REQUIRED_INDS)
        return result

    def _send_created_event(self, zone: dict) -> None:
        self.manager.send({
            "kind": "FVG_CREATED", "strategy": "FVG", **zone,
            "event_time": zone.get("fvg_time"),
        })
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📌 [FVG 생성] %s %s | %s | %.5f~%.5f",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"],
            )

    def _send_touch_event(self, zone: dict, result: dict, touched: bool) -> None:
        event = {
            "kind": "FVG_TOUCH" if touched else "FVG_TOUCH_END",
            "strategy": "FVG",
            **zone,
            "price": result.get("live_price"),
            "event_time": result.get("live_time"),
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "%s [FVG 터치%s] %s %s | %s | %.5f~%.5f | 가격=%s",
                "✅" if touched else "↩️",
                "" if touched else " 종료",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"],
                "-" if result.get("live_price") is None else f"{result['live_price']:.5f}",
            )

    def _send_filled_event(self, zone: dict, fill_time: float | None) -> None:
        event = {
            "kind": "FVG_FILLED",
            "strategy": "FVG",
            **zone,
            "fill_time": fill_time,
            "event_time": fill_time,
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🗑️ [FVG 채움/삭제] %s %s | %s | %.5f~%.5f | fill_time=%s",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"], fill_time,
            )

    def _send_expired_event(self, zone: dict, expired_at: float | None) -> None:
        event = {
            "kind": "FVG_EXPIRED",
            "strategy": "FVG",
            **zone,
            "expired_at": expired_at,
            "event_time": expired_at,
            "max_age_bars": int(FVG_MAX_AGE_BARS),
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "⌛ [FVG 만료/삭제] %s %s | %s | %.5f~%.5f | 최근 %d봉 범위 이탈",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"], int(FVG_MAX_AGE_BARS),
            )

    def process_watch_result(self, result: dict) -> None:
        if not result:
            return

        key = (result["symbol"], result["source_tf"])
        latest_closed_time = result.get("latest_closed_time")
        previous_closed_time = self._last_closed_time.get(key)
        is_new_closed_bar = (
            latest_closed_time is not None
            and (previous_closed_time is None or latest_closed_time > previous_closed_time)
        )
        if latest_closed_time is not None:
            self._last_closed_time[key] = float(latest_closed_time)

        current_active = {z["zone_id"]: z for z in result.get("zones", [])}
        filled_lookup = {z["zone_id"]: z for z in result.get("filled_zones", [])}
        eligible_zone_ids = set(result.get("eligible_zone_ids", []))
        oldest_allowed_time = result.get("oldest_allowed_time")
        previous_active = self._active_zones.get(key, {})
        first_snapshot = key not in self._initialized_keys

        # 시작 시 과거 상태를 이벤트로 재발송하지 않습니다.
        # 실행 이후 추적 중이던 영역이 사라졌을 때만 원인을 구분합니다.
        if not first_snapshot and is_new_closed_bar:
            for zone_id, old_zone in previous_active.items():
                if zone_id in current_active:
                    continue

                filled = filled_lookup.get(zone_id)
                if filled is not None:
                    self._send_filled_event(old_zone, filled.get("fill_time"))
                elif (
                    oldest_allowed_time is not None
                    and float(old_zone.get("fvg_time", 0.0)) < float(oldest_allowed_time)
                ):
                    self._send_expired_event(old_zone, latest_closed_time)
                else:
                    # 채움/만료가 아닌 사유로 추적 대상에서 빠졌다면,
                    # KIM에 남아 있을 수 있는 터치 기록부터 종료합니다.
                    # 여기에는 최신 3개 제한으로 밀려난 경우와 데이터 재구성으로
                    # 후보에서 빠지는 드문 경우가 포함됩니다.
                    if bool(self._touch_state.get(zone_id, False)):
                        self._send_touch_event(old_zone, result, False)
                self._touch_state.pop(zone_id, None)

        for zone_id, zone in current_active.items():
            # 새로운 확정봉에서 막 생성된 FVG만 생성 이벤트로 전달합니다.
            if (
                not first_snapshot
                and is_new_closed_bar
                and latest_closed_time is not None
                and zone["fvg_time"] == float(latest_closed_time)
                and zone_id not in self._seen_created
            ):
                self._seen_created.add(zone_id)
                self._send_created_event(zone)

            touched_now = bool(zone.get("touched_now", False))
            touched_before = bool(self._touch_state.get(zone_id, False))
            if touched_now != touched_before:
                self._touch_state[zone_id] = touched_now
                self._send_touch_event(zone, result, touched_now)
            elif zone_id not in self._touch_state:
                self._touch_state[zone_id] = touched_now

        # snapshot 역사 범위 밖으로 밀려난 영역도 내부 상태에서는 정리합니다.
        # FVG_FILLED는 현재 확정봉 데이터로 실제 채움이 확인된 경우에만 발생합니다.
        for zone_id in list(self._touch_state):
            if zone_id.startswith(f"{result['symbol']}|{result['source_tf']}|") and zone_id not in current_active:
                self._touch_state.pop(zone_id, None)

        self._active_zones[key] = current_active
        self._initialized_keys.add(key)
        self.manager.send(self.manager.stream.snapshot(result['symbol'], result['source_tf'],
            [dict(zone, kind='FVG_TOUCH' if zone.get('touched_now') else 'FVG_CURRENT', strategy='FVG',
                  event_time=result.get('live_time'), price=result.get('live_price')) for zone in current_active.values()],
            source_health=result.get('source_health')))

        if len(self._seen_created) > 10000:
            active_ids = {zone_id for zones in self._active_zones.values() for zone_id in zones}
            self._seen_created.intersection_update(active_ids)

    def run_watch_once(self, active: dict[str, tuple[str, ...]]) -> None:
        for symbol, tfs in active.items():
            if not tfs:
                continue
            data = self.staff.request(symbol, list(tfs), REQUIRED_INDS)
            if not data:
                continue
            for tf in tfs:
                result = self.evaluate(symbol, tf, data.get(tf))
                if result is not None:
                    self.process_watch_result(result)

    def run_query(self, payload: dict) -> None:
        symbol = str(payload.get("symbol") or "").strip()
        tf = normalize_tf(payload.get("source_tf"))
        if not symbol or not tf:
            return

        data = self.staff.request(symbol, [tf], REQUIRED_INDS)
        result = self.evaluate(symbol, tf, data.get(tf) if data else None)

        event = {
            "kind": "FVG_QUERY_RESULT",
            "strategy": "FVG",
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "symbol": symbol,
            "source_tf": tf,
        }
        if result is None:
            event.update({"ok": False, "error": "fvg_data_unavailable", "zones": []})
        else:
            # 조회 응답에는 현재 살아 있는 FVG만 노출합니다.
            clean = dict(result)
            clean.pop("filled_zones", None)
            clean.pop("eligible_zone_ids", None)
            clean.pop("oldest_allowed_time", None)
            event.update({"ok": True, **clean})
        self.manager.send(event)






# S1: pure legacy STAFF facts, function bodies preserved verbatim.
def add_fvg_features(df: pd.DataFrame) -> pd.DataFrame:
    df["bull_fvg_gap"] = df["low"] - df["high"].shift(2)
    df["is_bull_fvg"] = df["bull_fvg_gap"] > 0
    df["bull_fvg_top"] = df["low"]
    df["bull_fvg_bot"] = df["high"].shift(2)

    df["bear_fvg_gap"] = df["low"].shift(2) - df["high"]
    df["is_bear_fvg"] = df["bear_fvg_gap"] > 0
    df["bear_fvg_top"] = df["low"].shift(2)
    df["bear_fvg_bot"] = df["high"]
    return df


legacy_staff_fvg_features = add_fvg_features

