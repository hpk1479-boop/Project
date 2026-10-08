# -*- coding: utf-8 -*-
from __future__ import annotations
import domain_memory
from durable_protocol import FactStream, source_health, atomic_json, read_json
from sweep_selectors import DEFAULT_LEVEL_SELECTORS, LEVEL_GROUPS, ALL_LEVEL_CODES, normalize_level_selectors, selector_codes

from domain_clock import datetime
import json
import logging
import os
import queue
import threading
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# SWEEP 2.0 - pure external-liquidity detector
# =============================================================================
# 역할:
#   - 외부유동성 레벨을 계산합니다.
#   - 요청받은 source_tf의 확정봉이 외부 레벨을 최초 터치/돌파했는지 판독합니다.
#   - sweep 사실, 방향, 레벨 가격, 발생 시점만 SWEEP_TOUCH 이벤트로 전달합니다.
#   - 이미 전달한 sweep의 기준 레벨이 사라지면 SWEEP_INVALIDATED 이벤트로 해제합니다.
#
# 이 파일에 포함하지 않는 것:
#   - ATR 계산 / ATR snapshot / ATR multiplier 판정 / 생존 감시
#   - Percentile OUT->IN
#   - HMA6/17 gate
#   - TRUE B0 / extreme re-sweep
#   - mapped TF filter
#   - OZ / grade / 최종 진입
#   - 거래시간 필터 / 알림 채널 / 메인전략 조합
#
# 외부유동성 레벨 정의.
#   1) PDH / PDL
#   2) 직전 4H 확정봉 High / Low
#   3) 직전 8H 확정봉 High / Low
#   4) PWH / PWL (현재 Staff의 1D 데이터에서 직전 주 High / Low 계산)
#   5) 이전 세션 High / Low
#
# 이전 세션 H/L의 경계를 만들기 위한 런던/뉴욕 시각은 '거래 허용 필터'가 아니라
# 레벨 산출 파라미터입니다. 이후 김매니저/공통 설정이 값을 넘겨주도록 설계합니다.
# =============================================================================

KST = datetime.timezone(datetime.timedelta(hours=9))
REQUIRED_INDS: list[str] = []


# 기본 감시 레벨 집합. 필터를 전달하지 않으면 현재 외부유동성 전체를 계산합니다.



def normalize_tf(value) -> str:
    return str(value or "").strip().lower()


def as_epoch(value):
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


def _hhmm_start(value: str):
    """'HHMM-HHMM' 또는 'HHMM'에서 시작시각(HH, MM)을 반환합니다."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    raw = raw_value.split("-", 1)[0].strip().replace(":", "")
    if len(raw) != 4 or not raw.isdigit():
        return None
    hh, mm = int(raw[:2]), int(raw[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _session_end_hm(value: str):
    raw_value = str(value or "").strip()
    if "-" not in raw_value:
        return None
    raw = raw_value.split("-", 1)[1].strip().replace(":", "")
    if len(raw) != 4 or not raw.isdigit():
        return None
    hh, mm = int(raw[:2]), int(raw[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _append_previous_bar_levels(levels: list, df: pd.DataFrame, tf_code: str, tf_name: str):
    if df is None or len(df) < 2 or not {"time", "high", "low"}.issubset(df.columns):
        return
    prev_bar = df.iloc[-2]
    high = float(prev_bar["high"])
    low = float(prev_bar["low"])
    if not np.isfinite(high) or not np.isfinite(low):
        return
    bar_key = str(pd.Timestamp(prev_bar["time"]))
    levels.extend([
        {
            "id": f"PREV_{tf_code}_HIGH:{bar_key}", "direction": "SHORT",
            "level_code": f"PREV_{tf_code}_HIGH",
            "level_name": f"{tf_name} 이전봉 고가", "price": high,
        },
        {
            "id": f"PREV_{tf_code}_LOW:{bar_key}", "direction": "LONG",
            "level_code": f"PREV_{tf_code}_LOW",
            "level_name": f"{tf_name} 이전봉 저가", "price": low,
        },
    ])


def _append_previous_week_levels(levels: list, df_daily: pd.DataFrame):
    """현재 1D feed만 사용해 직전 완료 주의 High/Low(PWH/PWL)를 주봉고가/저가로 추가합니다.

    마지막 1D 행은 현재 진행 중인 일봉으로 보고, 그 일봉의 주(Monday 00:00 기준)
    바로 이전 7일 구간에 속한 확정 일봉들만 집계합니다.
    """
    if (
        df_daily is None or len(df_daily) < 3
        or not {"time", "high", "low"}.issubset(df_daily.columns)
    ):
        return

    try:
        times = pd.to_datetime(df_daily["time"], errors="coerce", utc=True)
        current_day = times.iloc[-1]
        if pd.isna(current_day):
            return

        current_week_start = current_day.normalize() - pd.Timedelta(days=int(current_day.weekday()))
        previous_week_start = current_week_start - pd.Timedelta(days=7)

        closed = df_daily.iloc[:-1].copy()
        closed_times = times.iloc[:-1]
        previous_week = closed.loc[
            (closed_times >= previous_week_start)
            & (closed_times < current_week_start)
        ]
    except Exception:
        logging.exception("주봉고가/저가 계산용 1D 시간 변환 실패")
        return

    if previous_week.empty:
        return

    highs = pd.to_numeric(previous_week["high"], errors="coerce")
    lows = pd.to_numeric(previous_week["low"], errors="coerce")
    pwh = float(highs.max())
    pwl = float(lows.min())
    if not np.isfinite(pwh) or not np.isfinite(pwl):
        return

    week_key = previous_week_start.isoformat()
    levels.extend([
        {
            "id": f"PWH:{week_key}", "direction": "SHORT",
            "level_code": "PWH", "level_name": "주봉고가", "price": pwh,
        },
        {
            "id": f"PWL:{week_key}", "direction": "LONG",
            "level_code": "PWL", "level_name": "주봉저가", "price": pwl,
        },
    ])


def build_external_levels(
    df_daily: pd.DataFrame | None,
    df_session_5m: pd.DataFrame | None,
    df_4h: pd.DataFrame | None,
    df_8h: pd.DataFrame | None,
    session_london: str = "",
    session_newyork: str = "",
) -> list[dict]:
    """현재 시점의 외부유동성 레벨을 계산합니다."""
    levels: list[dict] = []

    if df_daily is not None and len(df_daily) >= 2 and {"time", "high", "low"}.issubset(df_daily.columns):
        prev_day = df_daily.iloc[-2]
        pdh = float(prev_day["high"])
        pdl = float(prev_day["low"])
        if np.isfinite(pdh) and np.isfinite(pdl):
            day_key = str(pd.Timestamp(prev_day["time"]))
            levels.extend([
                {
                    "id": f"PDH:{day_key}", "direction": "SHORT", "level_code": "PDH",
                    "level_name": "전일고가(PDH)", "price": pdh,
                },
                {
                    "id": f"PDL:{day_key}", "direction": "LONG", "level_code": "PDL",
                    "level_name": "전일저가(PDL)", "price": pdl,
                },
            ])

    _append_previous_bar_levels(levels, df_4h, "4H", "4시간봉")
    _append_previous_bar_levels(levels, df_8h, "8H", "8시간봉")
    _append_previous_week_levels(levels, df_daily)

    if (
        df_session_5m is None or len(df_session_5m) < 3
        or not {"time", "high", "low"}.issubset(df_session_5m.columns)
    ):
        return levels

    london_hm = _hhmm_start(session_london)
    newyork_hm = _hhmm_start(session_newyork)
    if london_hm is None or newyork_hm is None:
        return levels

    closed_5m = df_session_5m.iloc[:-1].copy()
    if closed_5m.empty:
        return levels

    try:
        kst_times = pd.to_datetime(closed_5m["time"], utc=True).dt.tz_convert(KST)
    except Exception:
        logging.exception("이전 세션 H/L 계산용 5m 시간 변환 실패")
        return levels

    latest_kst = kst_times.iloc[-1]
    day_start = latest_kst.normalize()
    london_open = day_start + pd.Timedelta(hours=london_hm[0], minutes=london_hm[1])
    newyork_open = day_start + pd.Timedelta(hours=newyork_hm[0], minutes=newyork_hm[1])

    newyork_end_hm = _session_end_hm(session_newyork)
    newyork_crosses_midnight = bool(newyork_end_hm and newyork_hm > newyork_end_hm)
    latest_hm = (latest_kst.hour, latest_kst.minute)
    ny_after_midnight = bool(
        newyork_crosses_midnight and newyork_end_hm is not None and latest_hm < newyork_end_hm
    )

    if ny_after_midnight:
        day_start = day_start - pd.Timedelta(days=1)
        newyork_open = day_start + pd.Timedelta(hours=newyork_hm[0], minutes=newyork_hm[1])
        anchor = newyork_open
        session_code = "NY"
    elif latest_kst >= newyork_open:
        anchor = newyork_open
        session_code = "NY"
    elif latest_kst >= london_open:
        anchor = london_open
        session_code = "LONDON"
    else:
        return levels

    prior = closed_5m.loc[(kst_times >= day_start) & (kst_times < anchor)]
    if prior.empty:
        return levels

    prev_session_high = float(pd.to_numeric(prior["high"], errors="coerce").max())
    prev_session_low = float(pd.to_numeric(prior["low"], errors="coerce").min())
    if not np.isfinite(prev_session_high) or not np.isfinite(prev_session_low):
        return levels

    anchor_key = anchor.isoformat()
    levels.extend([
        {
            "id": f"PREV_SESSION_HIGH:{session_code}:{anchor_key}",
            "direction": "SHORT", "level_code": "PREV_SESSION_HIGH",
            "level_name": "이전 세션 고가", "price": prev_session_high,
        },
        {
            "id": f"PREV_SESSION_LOW:{session_code}:{anchor_key}",
            "direction": "LONG", "level_code": "PREV_SESSION_LOW",
            "level_name": "이전 세션 저가", "price": prev_session_low,
        },
    ])
    return levels


def filter_external_levels(levels: list[dict], selectors) -> list[dict]:
    codes = selector_codes(selectors)
    return [lv for lv in levels if str(lv.get("level_code", "")).upper() in codes]


@dataclass(frozen=True)
class SweepSpec:
    watch_id: str
    symbol: str
    source_tf: str
    levels: tuple[str, ...] = DEFAULT_LEVEL_SELECTORS
    session_london: str = ""
    session_newyork: str = ""

    @staticmethod
    def from_payload(payload: dict) -> "SweepSpec | None":
        watch_id = str(payload.get("watch_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        source_tf = normalize_tf(payload.get("source_tf") or payload.get("setup_tf"))
        if not watch_id or not symbol or not source_tf:
            return None
        return SweepSpec(
            watch_id=watch_id,
            symbol=symbol,
            source_tf=source_tf,
            levels=normalize_level_selectors(payload.get("levels")),
            session_london=str(payload.get("session_london") or payload.get("london") or "").strip(),
            session_newyork=str(payload.get("session_newyork") or payload.get("newyork") or "").strip(),
        )






class SweepWatchRegistry:
    """판독 요청만 보관합니다. 메인전략/개인Watch의 의미는 알지 못합니다."""

    def __init__(self):
        self._lock = threading.RLock()
        self._watches: dict[str, SweepSpec] = {}
        root = domain_memory.module_directory(__file__)
        self._state_path = root / "logs" / "sweep_watch_state.json"
        self._load_state()

    def _load_state(self):
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            restored: dict[str, SweepSpec] = {}
            for watch_id, item in (raw.get("watches", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                item["watch_id"] = watch_id
                try:
                    spec = SweepSpec.from_payload(item)
                except ValueError as exc:
                    logging.error("[SWEEP] saved watch rejected | %s | %s", watch_id, exc)
                    continue
                if spec:
                    restored[watch_id] = spec
            with self._lock:
                self._watches = restored
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [SWEEP Watch 복원] %d건", len(restored))
        except Exception:
            logging.exception("[SWEEP Watch] 상태 복원 실패")

    def _save_state_locked(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "watches": {}}
        for watch_id, spec in self._watches.items():
            item = asdict(spec)
            item.pop("watch_id", None)
            item["levels"] = list(spec.levels)
            payload["watches"][watch_id] = item
        atomic_json(self._state_path, payload, default=None, allow_nan=True, indent=2)

    def add(self, payload: dict):
        spec = SweepSpec.from_payload(payload)
        if spec is None:
            logging.warning("[SWEEP Watch] 잘못된 등록 요청 | %s", payload)
            return
        with self._lock:
            self._watches[spec.watch_id] = spec
            self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🌐 [SWEEP Watch] 등록 | %s | %s %s | levels=%s",
                spec.watch_id, spec.symbol, spec.source_tf, ",".join(spec.levels),
            )

    def cancel(self, watch_id: str):
        with self._lock:
            removed = self._watches.pop(str(watch_id or ""), None)
            if removed:
                self._save_state_locked()
        if removed:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🛑 [SWEEP Watch] 취소 | %s", watch_id)

    def reset(self):
        with self._lock:
            count = len(self._watches)
            self._watches.clear()
            if count:
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("♻️ [SWEEP Watch] 전체 초기화 | %d건", count)

    def snapshot(self) -> tuple[SweepSpec, ...]:
        with self._lock:
            return tuple(self._watches.values())




class ExternalLiquidityDetector:
    """하나의 판독 요청(spec)에 대한 순수 외부유동성 sweep 이벤트 상태 머신."""

    def __init__(self, spec: SweepSpec, restored: dict | None = None):
        self.spec = spec
        self.last_closed_time = None
        self.states: dict[str, dict] = {}
        self._dirty = False
        if restored:
            self._restore(restored)

    def _new_state(self, level: dict) -> dict:
        return {
            "id": level["id"],
            "direction": level["direction"],
            "level_code": level["level_code"],
            "level_name": level["level_name"],
            "level_price": float(level["price"]),
            "touched": False,
            "consumed": False,
            "touch_time": None,
            "touch_high": None,
            "touch_low": None,
            "touch_close": None,
        }

    def _restore(self, payload: dict) -> None:
        restored_states: dict[str, dict] = {}
        for level_id, raw in (payload.get("states", {}) if isinstance(payload, dict) else {}).items():
            if not isinstance(raw, dict):
                continue
            direction = str(raw.get("direction") or "").upper()
            if direction not in {"LONG", "SHORT"}:
                continue
            try:
                level_price = float(raw.get("level_price"))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(level_price):
                continue
            touched = bool(raw.get("touched", False))
            consumed = bool(raw.get("consumed", False)) and touched
            touch_time = as_epoch(raw.get("touch_time"))
            def optional_float(value):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                return number if np.isfinite(number) else None

            restored_states[str(level_id)] = {
                "id": str(level_id),
                "direction": direction,
                "level_code": str(raw.get("level_code") or ""),
                "level_name": str(raw.get("level_name") or ""),
                "level_price": level_price,
                "touched": touched,
                "consumed": consumed,
                "touch_time": touch_time,
                "touch_high": optional_float(raw.get("touch_high")),
                "touch_low": optional_float(raw.get("touch_low")),
                "touch_close": optional_float(raw.get("touch_close")),
            }
        self.states = restored_states
        self.last_closed_time = as_epoch(payload.get("last_closed_time")) if isinstance(payload, dict) else None
        self._dirty = False

    def export_state(self) -> dict:
        return {
            "last_closed_time": self.last_closed_time,
            "states": {
                level_id: {
                    "direction": state["direction"],
                    "level_code": state["level_code"],
                    "level_name": state["level_name"],
                    "level_price": float(state["level_price"]),
                    "touched": bool(state["touched"]),
                    "consumed": bool(state["consumed"]),
                    "touch_time": state.get("touch_time"),
                    "touch_high": state.get("touch_high"),
                    "touch_low": state.get("touch_low"),
                    "touch_close": state.get("touch_close"),
                }
                for level_id, state in self.states.items()
            },
        }

    def consume_dirty(self) -> bool:
        dirty = self._dirty
        self._dirty = False
        return dirty

    def _sync_levels(self, levels: list[dict]) -> list[dict]:
        active_ids = {lv["id"] for lv in levels}
        removed_states = [
            state for key, state in self.states.items()
            if key not in active_ids
        ]
        if removed_states:
            self._dirty = True
        self.states = {
            key: state for key, state in self.states.items()
            if key in active_ids
        }
        for level in levels:
            if level["id"] not in self.states:
                self.states[level["id"]] = self._new_state(level)
                self._dirty = True
        return removed_states

    def _context(self, state: dict) -> dict:
        return {
            "watch_id": self.spec.watch_id,
            "symbol": self.spec.symbol,
            "source_tf": self.spec.source_tf,
            "direction": state["direction"],
            "level_code": state["level_code"],
            "level_name": state["level_name"],
            "level_id": state["id"],
            "level_price": float(state["level_price"]),
            "touch_time": state.get("touch_time"),
        }

    def _touch_event(self, state: dict, restored: bool = False) -> dict:
        event = {
            "kind": "SWEEP_TOUCH",
            **self._context(state),
            "event_time": float(state["touch_time"]),
            "touch_high": state.get("touch_high"),
            "touch_low": state.get("touch_low"),
            "touch_close": state.get("touch_close"),
        }
        if restored:
            event["restored"] = True
        return event

    def active_touch_events(self, restored: bool = False) -> list[dict]:
        return [
            self._touch_event(state, restored=restored)
            for state in self.states.values()
            if state["touched"] and not state["consumed"] and state.get("touch_time") is not None
        ]

    def _invalidation_event(self, state: dict, event_time: float) -> dict:
        return {
            "kind": "SWEEP_INVALIDATED",
            **self._context(state),
            "event_time": float(event_time),
        }

    def invalidate_all(self, event_time: float | None = None) -> list[dict]:
        when = (
            float(event_time)
            if event_time is not None
            else datetime.datetime.now(datetime.timezone.utc).timestamp()
        )
        return [
            self._invalidation_event(state, when)
            for state in self.states.values()
            if state["touched"] and not state["consumed"]
        ]

    def process(self, df_base: pd.DataFrame, levels: list[dict]) -> list[dict]:
        events: list[dict] = []
        if df_base is None or len(df_base) < 3:
            return events
        if not {"time", "high", "low", "close"}.issubset(df_base.columns):
            return events

        closed = df_base.iloc[-2]
        closed_time = as_epoch(closed.get("time"))
        if closed_time is None:
            return events

        removed_states = self._sync_levels(levels)
        for state in removed_states:
            # KIM에 SWEEP_TOUCH를 실제로 전달했던 대표 레벨만 해제합니다.
            # 동일 봉에서 함께 닿아 consumed 처리된 비대표 레벨은
            # 애초 KIM에 전달되지 않았으므로 해제 이벤트도 만들지 않습니다.
            if state["touched"] and not state["consumed"]:
                events.append(self._invalidation_event(state, float(closed_time)))

        if not self.states:
            return events
        if self.last_closed_time is not None and closed_time <= float(self.last_closed_time):
            return events
        self.last_closed_time = float(closed_time)
        self._dirty = True

        candidates = {"LONG": [], "SHORT": []}
        for state in self.states.values():
            if state["consumed"] or state["touched"]:
                continue
            level = float(state["level_price"])
            touched = (
                float(closed["high"]) >= level
                if state["direction"] == "SHORT"
                else float(closed["low"]) <= level
            )
            if touched:
                candidates[state["direction"]].append(state)

        selected: dict[str, dict] = {}
        if candidates["LONG"]:
            selected["LONG"] = min(candidates["LONG"], key=lambda x: float(x["level_price"]))
        if candidates["SHORT"]:
            selected["SHORT"] = max(candidates["SHORT"], key=lambda x: float(x["level_price"]))

        # 기존 동일봉 다중레벨 대표선정 규칙은 유지합니다.
        for direction, group in candidates.items():
            winner = selected.get(direction)
            if winner is None:
                continue
            for state in group:
                state["touched"] = True
                state["touch_time"] = float(closed_time)
                state["touch_high"] = float(closed["high"])
                state["touch_low"] = float(closed["low"])
                state["touch_close"] = float(closed["close"])
                if state is not winner:
                    state["consumed"] = True
                self._dirty = True

        for state in selected.values():
            events.append(self._touch_event(state))

        return events

    def snapshot(self, levels: list[dict]) -> dict:
        self._sync_levels(levels)
        states = []
        for state in self.states.values():
            item = self._context(state)
            item.update({
                "touched": bool(state["touched"]),
                "consumed": bool(state["consumed"]),
            })
            states.append(item)
        states.sort(key=lambda x: (x["direction"], x["level_price"]))
        return {"levels": states}




class SweepEngine:
    def __init__(
        self,
        state_path: Path | None = None,
        *,
        staff_client=None, manager_client=None, event_publisher=None,
        event_state=None, source_time=None, level_cache=None,
    ):
        self.staff = staff_client
        self.manager = manager_client
        self.events = event_publisher
        self._source_time = source_time
        self._level_cache = level_cache
        root = Path(__file__).parent if event_state is not None else Path(__file__).resolve().parent
        self._state_path = Path(state_path) if state_path is not None else root / "logs" / "sweep_detector_state.json"
        state = {} if event_state is None else event_state
        self.detectors = state.setdefault('detectors', {})
        self._fingerprints = state.setdefault('fingerprints', {})
        self._restored_pending_sync = state.setdefault('restored_pending_sync', set())
        self._state_dirty = False
        self._source_health = state.setdefault('source_health', {})
        if event_state is None:
            self._load_detector_state()

    @staticmethod
    def _fingerprint(spec: SweepSpec) -> tuple:
        return (
            spec.symbol, spec.source_tf, spec.levels,
            spec.session_london, spec.session_newyork,
        )

    @staticmethod
    def _spec_payload(spec: SweepSpec) -> dict:
        return {
            "symbol": spec.symbol,
            "source_tf": spec.source_tf,
            "levels": list(spec.levels),
            "session_london": spec.session_london,
            "session_newyork": spec.session_newyork,
        }

    def _load_detector_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            restored = 0
            for watch_id, item in (raw.get("detectors", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                spec_payload = dict(item.get("spec") or {})
                spec_payload["watch_id"] = str(watch_id)
                try:
                    spec = SweepSpec.from_payload(spec_payload)
                except ValueError as exc:
                    logging.error("[SWEEP] saved detector rejected | %s | %s", watch_id, exc)
                    continue
                if spec is None:
                    continue
                detector = ExternalLiquidityDetector(spec, restored=item)
                self.detectors[spec.watch_id] = detector
                self._fingerprints[spec.watch_id] = self._fingerprint(spec)
                self._restored_pending_sync.add(spec.watch_id)
                restored += 1
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [SWEEP 터치기억 복원] 감시 %d건 | %s", restored, self._state_path)
        except Exception:
            logging.exception("[SWEEP 터치기억] 상태 복원 실패 | %s", self._state_path)

    def _save_detector_state(self, force: bool = False) -> None:
        if not force and not self._state_dirty:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "detectors": {
                watch_id: {
                    "spec": self._spec_payload(detector.spec),
                    **detector.export_state(),
                }
                for watch_id, detector in self.detectors.items()
            },
        }
        atomic_json(self._state_path, payload, default=None, allow_nan=True, indent=2)
        self._state_dirty = False

    def _publish_event(self, spec: SweepSpec, event: dict) -> None:
        payload = {"strategy": "SWEEP", **event}
        self.manager.stream.prepare(payload)
        self.events.publish(payload)
        self.manager.send(payload)
        if event.get("kind") == "SWEEP_INVALIDATED":
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🧹 [외부유동성 해제] %s %s | %s %.5f | %s | time=%s",
                    spec.symbol, spec.source_tf, event["level_code"], event["level_price"],
                    event["direction"], event["event_time"],
                )
        else:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "📌 [외부유동성 SWEEP] %s %s | %s %.5f | %s | time=%s",
                    spec.symbol, spec.source_tf, event["level_code"], event["level_price"],
                    event["direction"], event["event_time"],
                )

    def _sync_restored_facts(self, detector: ExternalLiquidityDetector) -> None:
        watch_id = detector.spec.watch_id
        if watch_id not in self._restored_pending_sync:
            return
        active_events = detector.active_touch_events(restored=True)
        if not active_events:
            self._restored_pending_sync.discard(watch_id)
            return

        delivered = 0
        for event in active_events:
            # 재시작 후 KIM의 사실 저장소만 원래 발생시각으로 복구합니다.
            # monitor_OZ 쪽 이벤트 버스에는 다시 쓰지 않아 새로운 터치로 만들지 않습니다.
            payload = {"strategy": "SWEEP", **event}
            reply = self.manager.send(payload)
            if not isinstance(reply, dict) or not reply.get("ok"):
                logging.warning(
                    "[SWEEP 터치사실 동기화] KIM 응답 실패 - 다음 주기에 재시도 | %s",
                    watch_id,
                )
                return
            delivered += 1

        self._restored_pending_sync.discard(watch_id)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "♻️ [SWEEP 터치사실 동기화] %s | 살아있는 터치 %d건",
                watch_id, delivered,
            )

    def _invalidate_detector(self, detector: ExternalLiquidityDetector) -> None:
        for event in detector.invalidate_all(event_time=getattr(self, '_source_time', None)):
            self._publish_event(detector.spec, event)

    def _detector(self, spec: SweepSpec) -> ExternalLiquidityDetector:
        fp = self._fingerprint(spec)
        old_detector = self.detectors.get(spec.watch_id)
        if old_detector is None or self._fingerprints.get(spec.watch_id) != fp:
            if old_detector is not None:
                self._invalidate_detector(old_detector)
            self.detectors[spec.watch_id] = ExternalLiquidityDetector(spec)
            self._fingerprints[spec.watch_id] = fp
            self._restored_pending_sync.discard(spec.watch_id)
            self._state_dirty = True
        return self.detectors[spec.watch_id]

    @staticmethod
    def _required_tfs(spec: SweepSpec) -> list[str]:
        # 외부유동성 레벨 산출에 필요한 원본 TF입니다. source_tf는 sweep 판독용입니다.
        return list(dict.fromkeys([spec.source_tf, "1d", "4h", "8h", "5m"]))

    def _load(self, spec: SweepSpec):
        data = self.staff.request(spec.symbol, self._required_tfs(spec), REQUIRED_INDS)
        if not data:
            return None, []
        compute = build_external_levels if self._level_cache is None else lambda *args, **kwargs: self._level_cache.build(build_external_levels, spec.symbol, *args, **kwargs)
        levels = compute(
            data.get("1d"), data.get("5m"), data.get("4h"), data.get("8h"),
            session_london=spec.session_london,
            session_newyork=spec.session_newyork,
        )
        levels = filter_external_levels(levels, spec.levels)
        self._source_health[spec.watch_id] = source_health(data, REQUIRED_INDS)
        return data.get(spec.source_tf), levels

    def run_spec_once(self, spec: SweepSpec):
        df_base, levels = self._load(spec)
        if df_base is None:
            return
        detector = self._detector(spec)
        events = detector.process(df_base, levels)
        for event in events:
            self._publish_event(spec, event)
        if detector.consume_dirty():
            self._state_dirty = True
        # 현재 레벨과 한 번 대조된 뒤에만 과거 터치 사실을 KIM에 복구합니다.
        # 시스템이 꺼진 동안 사라진 레벨을 잘못 되살리는 것을 막기 위함입니다.
        self._sync_restored_facts(detector)
        self.manager.send(self.manager.stream.snapshot(spec.symbol, spec.source_tf,
            [dict(event, strategy='SWEEP') for event in detector.active_touch_events(restored=True)],
            watch_id=spec.watch_id, source_health=self._source_health.get(spec.watch_id)))

    def run_query(self, payload: dict):
        query_payload = dict(payload)
        query_payload["watch_id"] = str(payload.get("watch_id") or payload.get("request_id") or "QUERY")
        try:
            spec = SweepSpec.from_payload(query_payload)
        except ValueError as exc:
            self.manager.send({"kind":"SWEEP_QUERY_RESULT","strategy":"SWEEP","ok":False,
                               "request_id":payload.get("request_id"),"request_chat_id":payload.get("request_chat_id"),
                               "error":str(exc),"levels":[]})
            return
        if spec is None:
            return
        df_base, levels = self._load(spec)
        event = {
            "kind": "SWEEP_QUERY_RESULT",
            "strategy": "SWEEP",
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "symbol": spec.symbol,
            "source_tf": spec.source_tf,
        }
        if df_base is None:
            event.update({"ok": False, "error": "sweep_data_unavailable", "levels": []})
        else:
            detector = ExternalLiquidityDetector(spec)
            # 조회는 현재 외부유동성 레벨 컨텍스트만 반환하며 과거 sweep을 재생하지 않습니다.
            snapshot = detector.snapshot(levels)
            event.update({
                "ok": True,
                "levels": snapshot["levels"],
            })
        self.manager.send(event)

    def cleanup(self, active_watch_ids: set[str]):
        changed = False
        for watch_id in list(self.detectors):
            if watch_id not in active_watch_ids:
                detector = self.detectors.pop(watch_id, None)
                self._fingerprints.pop(watch_id, None)
                self._restored_pending_sync.discard(watch_id)
                if detector is not None:
                    self._invalidate_detector(detector)
                    changed = True
        if changed:
            self._state_dirty = True




