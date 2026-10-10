# -*- coding: utf-8 -*-
from __future__ import annotations
import domain_memory
from durable_protocol import FactStream, atomic_json, read_json

import json
import logging
import queue
import threading

from indicator_facts import (
    MT5_TIMEFRAMES, sort_timeframes, tf_seconds,
    # 지표 함수 공개 API (watch_ma_features / 백테스트 / 기존 import 호환)
    ema, sma, wma, hma, true_range, rma, dmi, rsi, cci, linreg, psar, supertrend_dir,
    atr_trend_state, mss_state, anchored_vwap, mfi, cmf,
)
from indicator_score import TREND_DENOMINATOR, TREND_MAX_SCORE

# =============================================================================
# INDICATOR (former strategy_TREND.py / TREND 2.0)
# =============================================================================
# 역할:
#   - 종목/TF별 지표 Fact를 요청받은 것만 계산해 김매니저에 제공합니다.
#       * 전략 추세(TREND_STATE): SMA20(시가) 기울기와 HMA50 기울기가 같은 방향일 때만
#         상승/하락, 다르면 중립. 새 봉(OPEN 기준 입력 변화) 때만 다시 계산합니다.
#       * Watch metric(TREND_METRIC_STATE / TREND_QUERY 필드): 요청된 지표 Fact만 계산.
#       * 전체 지표 추세점수: Watch가 명시적으로 요청할 때만 on-demand 계산.
#   - WONBI/FVG/SWEEP/시간필터/OZ 로직을 포함하지 않습니다.
#   - 지표 계산식: indicator_facts.py, 전체 추세점수: indicator_score.py
#
# 김매니저와의 통신 계약(action/kind 이름, strategy="TREND", 상태 파일명)은
# 기존과 동일하게 유지합니다. 재시작 시 기존 Watch/pending 상태를 그대로 이어받습니다.
# =============================================================================

TREND_METRIC_PUSH_SEC = 1.0

REQUIRED_INDS: list[str] = ["HMA"]
SCORE_FIELDS = frozenset({"long_score", "short_score", "trend_score"})


# manager_KIM의 일반 Watch가 필요할 때만 요청할 수 있는 지표 metric 계약.
# 값 계산은 INDICATOR가 소유하고, manager_KIM은 비교/조합만 담당합니다.
TREND_METRIC_FIELDS = {
    "price", "long_score", "short_score", "trend_score",
    "ema10_open", "ema50_open", "sma20_open", "wma17_open", "hma50_open", "hma50_slope",
    "supertrend", "psar", "plus_di", "minus_di", "adx",
    "linreg20", "rsi14", "cci20", "macd", "macd_signal",
    "aroon_up", "aroon_down", "vortex_plus", "vortex_minus", "vwap",
    "bop", "cmf20", "mfi14", "chop14", "hv20", "hvma20",
    "mss", "vol_state", "vol_surge",
}

# metric 이름 -> 지표 Fact 이름 (마지막 행 값을 사용)
METRIC_FACTS = {
    "price": "c", "ema10_open": "e10", "ema50_open": "e50", "sma20_open": "s20", "wma17_open": "w17",
    "hma50_open": "h50", "supertrend": "st", "psar": "sar", "plus_di": "plus", "minus_di": "minus",
    "adx": "adx", "linreg20": "lr", "rsi14": "rv", "cci20": "cv", "macd": "macd", "macd_signal": "sig",
    "aroon_up": "aup", "aroon_down": "adn", "vortex_plus": "vip", "vortex_minus": "vim", "vwap": "vw",
    "bop": "bop", "cmf20": "cmfv", "mfi14": "mfiv", "chop14": "chop", "hv20": "hv", "hvma20": "hvma",
    "mss": "mss", "vol_state": "vol_state", "vol_surge": "vol_surge",
}






class IndicatorWatchRegistry:
    """김매니저가 요청한 추세 상태/지표 metric 구독을 보관합니다."""

    def __init__(self):
        self._lock = threading.RLock()
        self._watches: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        root = domain_memory.module_directory(__file__)
        # 기존 파일명을 유지해 이전 버전의 구독을 그대로 복원합니다.
        self._state_path = root / "logs" / "trend_watch_state.json"
        self._load_state()

    @staticmethod
    def _normalize_fields(values) -> tuple[str, ...]:
        return tuple(sorted({
            str(x or "").strip().lower()
            for x in (values or ())
            if str(x or "").strip().lower() in TREND_METRIC_FIELDS
        }))

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            items = raw.get("watches", {}) if isinstance(raw, dict) else {}
            restored: dict[str, tuple[str, str, tuple[str, ...]]] = {}
            if isinstance(items, dict):
                for watch_id, value in items.items():
                    if not isinstance(value, (list, tuple)) or len(value) < 2:
                        continue
                    symbol = str(value[0] or "").strip()
                    tf = str(value[1] or "").strip().lower()
                    fields = self._normalize_fields(value[2] if len(value) >= 3 else ())
                    if watch_id and symbol and tf:
                        restored[str(watch_id)] = (symbol, tf, fields)
            with self._lock:
                self._watches = restored
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [INDICATOR Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[INDICATOR Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 3,
            "watches": {k: [v[0], v[1], list(v[2])] for k, v in self._watches.items()},
        }
        atomic_json(self._state_path, payload, default=None, allow_nan=True, indent=2)

    def add(self, watch_id: str, symbol: str, source_tf: str, requested_fields=()) -> None:
        symbol = str(symbol or "").strip()
        tf = str(source_tf or "").strip().lower()
        fields = self._normalize_fields(requested_fields)
        if not watch_id or not symbol or not tf:
            return
        with self._lock:
            self._watches[str(watch_id)] = (symbol, tf, fields)
            self._save_state_locked()
        metric_text = f" | metrics={','.join(fields)}" if fields else ""
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟣 [INDICATOR Watch] 등록 | %s | %s %s%s", watch_id, symbol, tf, metric_text)

    def cancel(self, watch_id: str) -> None:
        with self._lock:
            removed = self._watches.pop(str(watch_id or ""), None)
            if removed:
                self._save_state_locked()
        if removed:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🛑 [INDICATOR Watch] 취소 | %s | %s %s", watch_id, removed[0], removed[1])

    def reset(self) -> None:
        with self._lock:
            count = len(self._watches)
            self._watches.clear()
            if count:
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("♻️ [INDICATOR Watch] 전체 초기화 | %d건", count)

    def snapshot(self) -> dict[str, dict[str, tuple[str, ...]]]:
        with self._lock:
            grouped: dict[str, dict[str, set[str]]] = {}
            for symbol, tf, fields in self._watches.values():
                grouped.setdefault(symbol, {}).setdefault(tf, set()).update(fields)
        return {
            symbol: {
                tf: tuple(sorted(fields))
                for tf, fields in sorted(tf_map.items(), key=lambda x: (tf_seconds(x[0]), x[0]))
            }
            for symbol, tf_map in grouped.items()
        }




