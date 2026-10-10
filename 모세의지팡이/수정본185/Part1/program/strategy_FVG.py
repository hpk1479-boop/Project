# -*- coding: utf-8 -*-
from __future__ import annotations
import domain_memory
from durable_protocol import FactStream, atomic_json, read_json

import json
import logging
import math
import os
import queue
import threading

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


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def normalize_tf(value) -> str:
    return str(value or "").strip().lower()


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


