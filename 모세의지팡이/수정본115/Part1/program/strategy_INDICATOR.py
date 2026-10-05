# -*- coding: utf-8 -*-
from __future__ import annotations
import domain_memory
from durable_protocol import FactStream, source_health, atomic_json, read_json
from durable_protocol import Records, identity
from domain_clock import uuid

import json
import logging
import math
import queue
import threading
from domain_clock import time
from pathlib import Path

import numpy as np
import pandas as pd

from indicator_facts import (
    MT5_TIMEFRAMES, TREND_BASIS, FactFrame, FactStore, finite, normalize_tf, sort_timeframes,
    standalone_frame, tf_seconds,
    # 지표 함수 공개 API (watch_ma_features / 백테스트 / 기존 import 호환)
    ema, sma, wma, hma, true_range, rma, dmi, rsi, cci, linreg, psar, supertrend_dir,
    atr_trend_state, mss_state, anchored_vwap, mfi, cmf,
)
from indicator_score import (
    DEFAULT_TREND_THRESHOLD, TREND_DENOMINATOR, TREND_MAX_SCORE, evaluate_score, score_from_facts,
)

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

STAFF_ENDPOINT = "tcp://127.0.0.1:5555"
MANAGER_ALERT_ENDPOINT = "tcp://127.0.0.1:5556"
ZMQ_TIMEOUT_MS = 5000
MANAGER_TIMEOUT_MS = 15000
LOOP_SLEEP_SEC = 0.5
TREND_METRIC_PUSH_SEC = 1.0

REQUIRED_INDS: list[str] = ["HMA"]
PROTOCOL_STRATEGY = "TREND"   # 김매니저 fact scope / 이벤트 strategy 이름 (wire contract)
SCORE_FIELDS = frozenset({"long_score", "short_score", "trend_score"})


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




class IndicatorEngine:
    """원비/FVG/시간과 분리된 지표 Fact 제공 엔진.

    * 전략 추세   : evaluate_trend()   - 경량 Fact (SMA20 시가 / HMA50 기울기)
    * Watch metric: metric_snapshot()  - 요청된 지표 Fact만
    * 전체 추세점수: evaluate_score()   - 명시적 요청 때만 (on-demand)
    같은 심볼·TF·입력의 Fact는 FactStore가 재사용합니다.
    """

    def __init__(
        self,
        threshold: float = DEFAULT_TREND_THRESHOLD,
        *,
        staff_endpoint: str = STAFF_ENDPOINT,
        staff_timeout_ms: int = ZMQ_TIMEOUT_MS,
        manager_endpoint: str = MANAGER_ALERT_ENDPOINT,
        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
        staff_client=None, manager_client=None, event_state=None, pending_records=None,
    ):
        self.staff = staff_client
        self.manager = manager_client
        self.threshold = float(threshold)
        self.facts = FactStore()
        state = {} if event_state is None else event_state
        self._last_watch_state = state.setdefault('last_watch_state', {})
        self._pending_states = Records(Path(__file__).resolve().parent / 'logs' / 'trend_pending_events.json') if pending_records is None else pending_records
        self._last_metric_push = state.setdefault('last_metric_push', {})

    # ------------------------------------------------------------------
    # Fact access
    # ------------------------------------------------------------------
    def fact_frame(self, symbol: str, df: pd.DataFrame, tf: str) -> FactFrame | None:
        """(symbol, TF) 기준으로 입력이 같으면 이전 Fact를 재사용하는 FactFrame."""
        if df is None:
            return None
        tf = normalize_tf(tf)
        if not tf:
            return None
        store = getattr(self, "facts", None)
        if store is None:          # 생성자를 거치지 않은 인스턴스(백테스트 어댑터 등)
            store = self.facts = FactStore()
        return store.frame(str(symbol), tf, df)

    # ------------------------------------------------------------------
    # 전략 추세 (경량 Fact)
    # ------------------------------------------------------------------
    def evaluate_trend(self, symbol: str, df: pd.DataFrame, tf: str, frame: FactFrame | None = None):
        """SMA20(시가) 기울기와 HMA50 기울기가 같은 방향일 때만 상승/하락, 아니면 중립."""
        if df is None or len(df) < 3:
            return None
        tf = normalize_tf(tf)
        if not tf:
            return None
        live = df.iloc[-1]
        bar_time = pd.to_datetime(live.get("time"), errors="coerce")
        close = live.get("close")
        if pd.isna(bar_time) or not finite(close):
            return None
        f = frame if frame is not None else self.fact_frame(symbol, df, tf)
        decision = f["trend"]
        if decision is None:
            return None
        return {
            "symbol": str(symbol),
            "source_tf": tf,
            "trend": decision["trend"],
            "direction": decision["direction"],
            "trend_basis": TREND_BASIS,
            "sma20_slope": float(decision["sma20_slope"]),
            "hma50_slope": float(decision["hma50_slope"]),
            "price": float(close),
            "bar_time": pd.Timestamp(bar_time),
        }

    # ------------------------------------------------------------------
    # 전체 지표 추세점수 (on-demand)
    # ------------------------------------------------------------------
    def score(self, df: pd.DataFrame, tf: str, direction: str):
        """기존 DOUBLEB의 추세 앙상블 조건을 방향별 0~100점으로 계산합니다 (단발 호출)."""
        if df is None:
            return None
        return score_from_facts(standalone_frame(df, tf), direction)

    def evaluate_score(self, symbol: str, df: pd.DataFrame, tf: str, frame: FactFrame | None = None):
        """전체 지표 기반 추세점수 판정. Watch가 명시적으로 요청할 때만 호출됩니다."""
        if df is None or len(df) < 60:
            return None
        f = frame if frame is not None else self.fact_frame(symbol, df, tf)
        if f is None:
            return None
        return evaluate_score(symbol, f, self.threshold)

    # ------------------------------------------------------------------
    # Watch metric
    # ------------------------------------------------------------------
    @staticmethod
    def _metric_number(value):
        """JSON/ZMQ로 전달 가능한 유한 실수만 반환합니다."""
        try:
            if isinstance(value, (bool, np.bool_)):
                return 1.0 if bool(value) else 0.0
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def metric_snapshot(self, df: pd.DataFrame, tf: str, requested_fields, *,
                        frame: FactFrame | None = None) -> dict | None:
        """요청된 지표만 계산하여 최신 진행봉 값으로 반환합니다.

        같은 계산군(DMI, MACD 등)의 필드가 여러 개 요청되어도 Fact는 한 번만 계산합니다.
        점수 필드(long/short/trend_score)가 요청된 경우에만 전체 지표를 계산합니다.
        """
        if df is None or len(df) < 60:
            return None
        tf = normalize_tf(tf)
        if not tf:
            return None

        requested = tuple(dict.fromkeys(str(x or "").strip().lower() for x in requested_fields if str(x or "").strip()))
        if not requested:
            return {}
        invalid = [x for x in requested if x not in TREND_METRIC_FIELDS]
        if invalid:
            raise ValueError("unsupported metric fields: " + ",".join(invalid))

        live = df.iloc[-1]
        bar_time = pd.to_datetime(live.get("time"), errors="coerce")
        if pd.isna(bar_time):
            return None

        # frame이 없으면 단발 계산(재사용 캐시 없음), 있으면 LIVE 구독 FactFrame을 공유합니다.
        f = frame if frame is not None else standalone_frame(df, tf)
        values: dict[str, object] = {}
        wanted = set(requested)

        if wanted & SCORE_FIELDS:
            result = evaluate_score("_metric_", f, self.threshold)
            if result is None:
                return None
            values.update({
                "long_score": result.get("long_score"),
                "short_score": result.get("short_score"),
                "trend_score": result.get("score"),
            })
        for name in requested:
            fact_name = METRIC_FACTS.get(name)
            if fact_name is not None:
                values[name] = f[fact_name].iloc[-1]
        if "hma50_slope" in wanted:
            # Hull 컬러체인지 기준과 동일: 현재 HMA50 vs 2봉 전 HMA50 (hull > hull[2]).
            h50 = f["h50"]
            values["hma50_slope"] = h50.iloc[-1] - h50.iloc[-3]

        cleaned = {name: self._metric_number(values.get(name)) for name in requested}
        return {
            "bar_time": pd.Timestamp(bar_time),
            "metrics": {name: value for name, value in cleaned.items() if value is not None},
        }

    # ------------------------------------------------------------------
    # runtime
    # ------------------------------------------------------------------
    def _send_watch_state_if_changed(self, result: dict) -> None:
        key = (result["symbol"], result["source_tf"])
        trend = result["trend"]
        scope = identity(*key)
        pending = self._pending_states.get(scope, [])
        observed = pending[-1]['trend'] if pending else self._last_watch_state.get(key)
        if observed != trend:
            pending.append({'kind': 'TREND_STATE', 'strategy': PROTOCOL_STRATEGY, **result,
                            'event_id': uuid.uuid4().hex})
            self.manager.stream.prepare(pending[-1])
            self._pending_states.put(scope, pending)
        if not pending:
            return
        while pending:
            event = pending[0]
            reply = self.manager.send(event)
            if not reply.get('ok'):
                return
            self._last_watch_state[key] = event['trend']
            pending.pop(0)
            self._pending_states.put(scope, pending)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📈 [INDICATOR 추세] %s %s | %s | SMA20(시가) 기울기 %+.6g / HMA50 기울기 %+.6g",
                result["symbol"], result["source_tf"], result["trend"],
                result.get("sma20_slope", float("nan")), result.get("hma50_slope", float("nan")),
            )

    def run_watch_once(self, active: dict[str, dict[str, tuple[str, ...]]]) -> None:
        now_mono = time.monotonic()
        for symbol, tf_map in active.items():
            if not tf_map:
                continue
            tfs = tuple(tf_map)
            data = self.staff.request(symbol, tfs, REQUIRED_INDS)
            if not data:
                continue
            for tf, requested_fields in tf_map.items():
                df = data.get(tf)
                frame = self.fact_frame(symbol, df, tf)
                result = self.evaluate_trend(symbol, df, tf, frame=frame) if frame is not None else None
                if result is not None:
                    result['source_health'] = source_health({tf:df}, REQUIRED_INDS)
                    self._send_watch_state_if_changed(result)
                    if not self._pending_states.get(identity(symbol, tf), []):
                        self.manager.send(self.manager.stream.snapshot(symbol, tf, [dict(kind='TREND_STATE', strategy=PROTOCOL_STRATEGY, **result)], source_health=result['source_health']))

                requested_fields = tuple(requested_fields or ())
                if not requested_fields:
                    continue
                last_fields, last_sent = self._last_metric_push.get((symbol, tf), ((), 0.0))
                if requested_fields == last_fields and now_mono - last_sent < TREND_METRIC_PUSH_SEC:
                    continue
                try:
                    snapshot = self.metric_snapshot(df, tf, requested_fields, frame=frame)
                except Exception:
                    logging.exception(
                        "[INDICATOR] metric 구독 계산 실패 | %s %s | %s",
                        symbol, tf, requested_fields,
                    )
                    continue
                if snapshot is None:
                    continue
                self.manager.send({
                    "kind": "TREND_METRIC_STATE",
                    "strategy": PROTOCOL_STRATEGY,
                    "symbol": symbol,
                    "source_tf": tf,
                    "requested_fields": list(requested_fields),
                    **snapshot,
                })
                self._last_metric_push[(symbol, tf)] = (requested_fields, now_mono)

    def run_query(self, payload: dict) -> None:
        symbol = str(payload.get("symbol") or "").strip()
        tf = normalize_tf(payload.get("source_tf"))
        if not symbol or not tf:
            return
        data = self.staff.request(symbol, [tf], REQUIRED_INDS)
        df = data.get(tf) if data else None
        bar_mode = str(payload.get("bar_mode") or "LIVE").strip().upper()
        if bar_mode == "CLOSED":
            # STAFF 마지막 행은 진행봉입니다. query에서만 -2 확정봉을 마지막 행으로 보이게 잘라
            # 같은 계산 로직을 그대로 재사용합니다. 일반 TREND_WATCH 의미는 변경하지 않습니다.
            if df is not None and len(df) >= 2:
                df = df.iloc[:-1].copy()
            else:
                df = None
        requested_fields = tuple(dict.fromkeys(
            str(x or "").strip().lower()
            for x in (payload.get("requested_fields") or [])
            if str(x or "").strip()
        ))
        event = {
            "kind": "TREND_QUERY_RESULT",
            "strategy": PROTOCOL_STRATEGY,
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "purpose": payload.get("purpose"),
            "symbol": symbol,
            "source_tf": tf,
            "bar_mode": bar_mode,
        }
        if requested_fields:
            event["requested_fields"] = list(requested_fields)
            try:
                # 조회는 단발 요청이므로 LIVE 구독 캐시와 섞지 않고 독립 계산합니다.
                snapshot = self.metric_snapshot(df, tf, requested_fields)
            except ValueError as exc:
                event.update({"ok": False, "error": str(exc)})
            except Exception:
                logging.exception("[INDICATOR] metric query 계산 실패 | %s %s | %s", symbol, tf, requested_fields)
                event.update({"ok": False, "error": "trend_metric_query_failed"})
            else:
                if snapshot is None:
                    event.update({"ok": False, "error": "trend_data_unavailable"})
                else:
                    event.update({"ok": True, **snapshot})
        else:
            frame = standalone_frame(df, tf) if df is not None else None
            result = self.evaluate_trend(symbol, df, tf, frame=frame) if frame is not None else None
            if result is None:
                event.update({"ok": False, "error": "trend_data_unavailable"})
            else:
                event.update({"ok": True, **result})
        self.manager.send(event)




