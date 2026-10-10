# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL4] 30분 찢들 - 진입/취소/최종 게이트 상세 메모
# ============================================================================
# 이 블록은 메모장으로 파일을 열었을 때 전략 구조를 즉시 확인하기 위한 설명용 주석입니다.
# 아래 실제 실행 코드의 조건/상수/순서에는 손대지 않았습니다.
#
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 전략 시작 시점: "30분봉 마감 = 새 30분봉 시작"마다 새 cycle
#   - 실시간으로 새 30m live open이 확인되면 직전 30m 봉이 마감된 것으로 보고 cycle을 시작합니다.
#   - 프로그램을 처음 켠 순간에는 과거 30분 Setup을 소급 생성하지 않습니다.
#   - 새 30분 cycle이 시작되면 이전 cycle의 남아 있는 SPECIAL4 Watch는 먼저 종료합니다.
#   - LONG과 SHORT는 각 방향 조건을 따로 판정합니다.
#
# ■ Setup 판정에 사용하는 확정봉
#   - 직전 확정 1m 봉 : ATR14 snapshot 확보용
#   - 직전 확정 15m 봉: WONBI touch 판정용
#   - 직전 확정 30m 봉: WONBI non-touch 판정 + anchor 종가 확보용
#   - 직전 15m 봉은 시작 minute가 반드시 15 또는 45인 봉만 인정합니다.
#
# ■ 공통 사전 조건
#   - 현재 시간이 MAIN_ASIA / MAIN_LONDON / MAIN_NEWYORK 안이어야 합니다.
#   - 직전 30m 종가를 anchor_price로 고정합니다.
#   - 같은 경계 직전 확정 1m ATR14를 atr_1m snapshot으로 고정합니다.
#   - ATR14 snapshot은 cycle 도중 새 ATR 값으로 바꾸지 않고 해당 cycle 기준값으로 사용합니다.
#
# ■ LONG Setup
#   1) 직전 확정 15m 봉이 하단 WONBI를 터치해야 합니다.
#      → low <= wonbi_lower
#   2) 직전 확정 30m 봉은 하단 WONBI를 터치하면 안 됩니다.
#      → 30m 하단 WONBI까지 터치했다면 LONG cycle 탈락
#   3) 위 조건 통과 시 LONG 전용 1m OZ Watch를 등록합니다.
#
# ■ SHORT Setup
#   1) 직전 확정 15m 봉이 상단 WONBI를 터치해야 합니다.
#      → high >= wonbi_upper
#   2) 직전 확정 30m 봉은 상단 WONBI를 터치하면 안 됩니다.
#      → 30m 상단 WONBI까지 터치했다면 SHORT cycle 탈락
#   3) 위 조건 통과 시 SHORT 전용 1m OZ Watch를 등록합니다.
#
# ■ Setup 성립 후 최종 신호 기본값
#   - 감시 TF         = 1m만 사용
#   - validation_mode = NORMAL
#   - trigger_mode    = OZ
#   - 유효시간        = 30분 마감 시점부터 6분
#     예) :00 경계 cycle → :00~:06
#         :30 경계 cycle → :30~:36
#
# ■ 6분 안이라도 즉시 취소되는 ATR 선행이동 조건
#   - 기준 거리 = Setup 시 고정한 1m ATR14 snapshot × 1.0
#   - LONG : anchor 이후 가격이 위쪽으로 기준 거리 이상 먼저 진행한 흔적이 생기면 취소
#            (anchor 이후 high의 최대값 - anchor >= ATR14×1.0)
#   - SHORT: anchor 이후 가격이 아래쪽으로 기준 거리 이상 먼저 진행한 흔적이 생기면 취소
#            (anchor - anchor 이후 low의 최소값 >= ATR14×1.0)
#   - 이 검사는 cycle 유지 중에도 하고, FINAL_ALERT 직전에도 다시 검사합니다.
#
# ■ 1m OZ FINAL_ALERT 직전 "현재 snapshot" 추세 재검증
#   - OZ가 발생했다고 바로 알림을 보내지 않습니다.
#   - 이벤트 순간 1m / 3m / 15m 데이터를 다시 받아 아래 조건을 동시에 재검증합니다.
#
#   [LONG 최종 게이트]
#   1) 15m HMA50 우상향 : 현재 HMA50 > 2봉 전 HMA50
#   2) 현재가 > 현재 15m HMA50
#      - 현재가는 가장 빠른 1m live close를 사용
#   3) 3m EMA50 > EMA200
#   4) 3m HMA168 우상향 : 현재 HMA168 > 2봉 전 HMA168
#   5) 1m EMA50 > EMA200
#
#   [SHORT 최종 게이트]
#   1) 15m HMA50 우하향 : 현재 HMA50 < 2봉 전 HMA50
#   2) 현재가 < 현재 15m HMA50
#   3) 3m EMA50 < EMA200
#   4) 3m HMA168 우하향 : 현재 HMA168 < 2봉 전 HMA168
#   5) 1m EMA50 < EMA200
#
# ■ FINAL_ALERT가 와도 알림을 막는 경우
#   - 이미 6분 유효시간이 끝난 경우
#   - FINAL_ALERT 직전 ATR 선행이동 기준을 초과한 경우
#   - 위 15m/3m/1m 추세 필터 중 하나라도 방향과 맞지 않는 경우
#   - 이 경우 해당 방향 SPECIAL4 cycle을 종료하고 SPECIAL4 알림은 suppress 합니다.
#
# ■ 최종 알림 성공 후
#   - [30분 찢들 발생!] 템플릿으로 알림을 전달합니다.
#   - 성공적으로 전달된 해당 Watch/cycle은 active 상태에서 제거합니다.
#
# ■ 상태 저장/복원
#   - SPECIAL4 상태는 전용 JSON state에 저장합니다.
#   - 현재 전략 버전 V2와 호환되는 active state만 복원합니다.
#   - 이전 버전 또는 현재 정의와 다른 persistent Watch는 등록 시 정리합니다.
#
# ■ 이 파일에서 하지 않는 것
#   - SPECIAL1의 TREND+WONBI 로직을 섞지 않습니다.
#   - SPECIAL2의 외부유동성 SWEEP/ATR Setup을 섞지 않습니다.
#   - SPECIAL3의 EMA cross + FVG 체인을 섞지 않습니다.
#   - SPECIAL5의 상위 브레이커 OZ -> 하위 1/2/3m 부모/자식 구조를 섞지 않습니다.
#   - manager_KIM / monitor_OZ에 SPECIAL4 전용 계산을 추가하는 방식이 아니라,
#     이 모듈 자체의 상태기계와 공식 Plugin API로만 처리합니다.
# ============================================================================
"""SPECIAL4 - 30분 찢들.

전략 정의
---------
30분봉 마감(새 30분봉 시작)마다 새 cycle을 시작합니다.

LONG
1) MAIN_ASIA / MAIN_LONDON / MAIN_NEWYORK 거래시간 안이어야 합니다.
2) 직전 확정 15분봉(시작 minute 15/45)이 하단 WONBI를 터치해야 합니다.
3) 직전 확정 30분봉은 하단 WONBI를 터치하면 안 됩니다. 터치하면 해당 LONG cycle은 탈락입니다.
4) 같은 30분 마감 직전 확정 1분 ATR14를 snapshot으로 고정하고, 직전 30분 종가를 anchor로 고정합니다.
5) cycle은 30분 마감 후 6분 동안만 유효합니다. (:00~:06, :30~:36)
6) 1분 NORMAL + OZ만 감시합니다.
7) 1분 OZ FINAL_ALERT 직전에 아래 추세 필터를 현재 snapshot으로 동기 재검증합니다.
   - 15m HMA50 우상향: 현재 HMA50 > 2봉 전 HMA50
   - 현재가 > 15m HMA50
   - 3m EMA50 > EMA200
   - 3m HMA168 우상향: 현재 HMA168 > 2봉 전 HMA168
   - 1m EMA50 > EMA200
8) OZ가 오기 전에 anchor에서 위로 1분 ATR14 snapshot x 1.0 이상 선행이동한 흔적이 생기면 취소합니다.

SHORT는 정확한 반대입니다.
- 15m 상단 WONBI 터치 필수
- 30m 상단 WONBI 터치 시 탈락
- 15m HMA50 우하향 + 현재가 < HMA50
- 3m EMA50 < EMA200 + HMA168 우하향
- 1m EMA50 < EMA200
- anchor에서 아래로 ATR14 snapshot x 1.0 이상 선행이동 시 취소

전략 계산/상태는 이 모듈이 소유합니다.
manager_KIM / monitor_OZ에는 SPECIAL4 전용 계산을 추가하지 않습니다.
"""
from __future__ import annotations
import domain_memory
from durable_protocol import atomic_json, read_json
import oz_profiles

import hashlib
import json
import logging
import math
import os
import threading
from domain_clock import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pandas as pd


SPEC_ID = "PIPELINE_4"
STRATEGY_NAME = "30분 찢들"
STRATEGY_VERSION = 2

SYMBOLS = ("XAUUSD+", "NAS100")
LEGACY_COMBINED_SYMBOL = ",".join(SYMBOLS)

OZ_TFS = ("1m",)
TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")

# ============================================================================
# [전략 4 사용자 설정]
# ============================================================================
# 최종 OZ 트리거: "BREAKER" 또는 "OZ"
FINAL_TRIGGER_MODE = "OZ"

# 최종 OZ 검증: "NORMAL"(일반) 또는 "BLIND"(무지성)
FINAL_VALIDATION_MODE = "NORMAL"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL4", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 최종 Telegram 알림 송출 거래시간.
# 24시간 송출하려면 0으로 변경합니다.
FINAL_ALERT_TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
# ============================================================================

OZ_VALIDATION_MODE = FINAL_VALIDATION_MODE

ATR_MULT = 1.0
ACTIVE_WINDOW_SEC = 6 * 60
POLL_SEC = 0.50


_FINAL_TRIGGER_ALERT_TEXT = (
    oz_profiles.profile_label(FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE)
    if FINAL_TRIGGER_OVERRIDDEN
    else "브레이커 올존"
    if str(FINAL_TRIGGER_MODE).strip().upper() == "BREAKER"
    else "올존"
    if str(FINAL_TRIGGER_MODE).strip().upper() == "OZ"
    else str(FINAL_TRIGGER_MODE).strip()
)


MAIN_4_ALERT_TEMPLATE = (
    "[4. 30분 찢들 발생!] {side_icon} {side_text} 신호 발생\n"
    "──────────────────\n"
    "• 종목: {sym}\n"
    "• 위치: 15분봉 원비\n"
    f"• 주기: {{b_tf}} {_FINAL_TRIGGER_ALERT_TEXT}\n"
    "• 지표: {indicators_text}\n"
    "• 현재 가격: {current_price:,.2f}\n"
    "──────────────────"
)


def _final_alert_filters() -> tuple[str, ...]:
    """0이면 24시간, 그 외에는 지정된 SPECIAL4 최종 송출 세션을 반환합니다."""
    if FINAL_ALERT_TIME_FILTERS == 0:
        return ()
    if isinstance(FINAL_ALERT_TIME_FILTERS, str):
        value = FINAL_ALERT_TIME_FILTERS.strip()
        return (value,) if value else ()
    return tuple(str(x).strip() for x in FINAL_ALERT_TIME_FILTERS if str(x).strip())


def _final_alert_time_allowed(api) -> bool:
    filters = _final_alert_filters()
    if not filters:
        return True
    return bool(api.time_allowed(filters))


def _project_root() -> Path:
    here = domain_memory.module_directory(__file__)
    return here.parent if here.name.upper() == "SPECIAL" else here


def _atomic_write_json(path: Path, payload: dict) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _epoch(value: object) -> Optional[float]:
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


def _latest_row_before(df: pd.DataFrame, before_epoch: float) -> Optional[pd.Series]:
    """before_epoch보다 엄격히 이전에 시작한 마지막 row를 반환합니다."""
    if df is None or df.empty or "time" not in df.columns:
        return None
    times = pd.to_datetime(df["time"], errors="coerce", utc=True)
    seconds = times.map(lambda x: float(x.timestamp()) if not pd.isna(x) else float("nan"))
    mask = seconds < float(before_epoch) - 1e-6
    if not bool(mask.any()):
        return None
    return df.loc[mask].iloc[-1]


def _live_open_epoch(df: pd.DataFrame) -> Optional[float]:
    if df is None or df.empty:
        return None
    return _epoch(df.iloc[-1].get("time"))


def _touches(row: Optional[pd.Series], side: str) -> bool:
    if row is None:
        return False
    side = str(side or "").upper()
    if side == "LOWER":
        low, level = row.get("low"), row.get("wonbi_lower")
        return _finite(low) and _finite(level) and float(low) <= float(level)
    if side == "UPPER":
        high, level = row.get("high"), row.get("wonbi_upper")
        return _finite(high) and _finite(level) and float(high) >= float(level)
    return False


def _symbol_key(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(symbol)).strip("_") or "SYMBOL"


def _watch_id(symbol: str, direction: str, boundary_epoch: float) -> str:
    raw = (
        f"{SPEC_ID}|V{STRATEGY_VERSION}|{direction}|{int(boundary_epoch)}"
        if symbol == "XAUUSD+"
        else f"{SPEC_ID}|V{STRATEGY_VERSION}|{symbol}|{direction}|{int(boundary_epoch)}"
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]
    return f"OZARM:SP4:{digest}"


def _event_watch_ids(event: object) -> list[str]:
    if not isinstance(event, dict):
        return []
    ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
    single = str(event.get("watch_id") or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def _strip_special4_source(event: dict) -> dict:
    out = dict(event)
    raw_ids = out.get("source_spec_ids")
    remaining: list[str] = []
    if isinstance(raw_ids, (list, tuple, set)):
        remaining = [
            str(x).strip()
            for x in raw_ids
            if str(x).strip() and str(x).strip() != SPEC_ID
        ]

    if remaining:
        out["source_spec_ids"] = remaining
    else:
        out.pop("source_spec_ids", None)

    if str(out.get("source_spec_id") or "").strip() == SPEC_ID:
        if remaining:
            out["source_spec_id"] = remaining[0]
        else:
            out.pop("source_spec_id", None)

    if str(out.get("source_name") or "").strip() == STRATEGY_NAME:
        out.pop("source_name", None)
    return out


def _event_for_watch_ids(event: dict, watch_ids: list[str], *, special4_source: bool) -> dict:
    ids = [str(x) for x in watch_ids if str(x)]
    out = dict(event)
    out["watch_ids"] = ids
    if ids:
        out["watch_id"] = ids[0]
    else:
        out.pop("watch_id", None)

    if special4_source:
        out["source_spec_id"] = SPEC_ID
        out["source_spec_ids"] = [SPEC_ID]
        out["source_name"] = STRATEGY_NAME
    else:
        out = _strip_special4_source(out)
    return out


class Special4Runtime:
    """SPECIAL4 전용 상태기계."""

    def __init__(self, api, symbol: str) -> None:
        self.api = api
        self.symbol = str(symbol)
        self.state_path = (
            _project_root() / "logs" / "special4_state.json"
            if self.symbol == "XAUUSD+"
            else _project_root() / "logs" / f"special4_state_{_symbol_key(self.symbol)}.json"
        )
        self._lock = threading.RLock()
        self._last_poll_mono = 0.0
        self.last_30m_open: Optional[float] = None
        self.active: dict[str, dict] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if not domain_memory.exists(self.state_path):
            return
        try:
            raw = read_json(self.state_path)
            if not isinstance(raw, dict):
                return

            # 기존 버전의 last_30m_open은 cycle 중복 방지용으로만 재사용합니다.
            last_open = raw.get("last_30m_open")
            if _finite(last_open):
                self.last_30m_open = float(last_open)

            # 대대적 전략 변경이므로 V2 active state만 복원합니다.
            if int(raw.get("version") or 0) != STRATEGY_VERSION:
                return

            restored = raw.get("active")
            if isinstance(restored, dict):
                for direction, item in restored.items():
                    if direction not in {"LONG", "SHORT"} or not isinstance(item, dict):
                        continue
                    wid = str(item.get("watch_id") or "")
                    if not wid.startswith("OZARM:SP4:"):
                        continue
                    if int(item.get("special4_version") or 0) != STRATEGY_VERSION:
                        continue
                    if not all(
                        _finite(item.get(k))
                        for k in ("anchor_ts", "anchor_price", "atr_1m", "expires_at")
                    ):
                        continue
                    self.active[direction] = dict(item)

            if self.active:
                logging.info("♻️ [SPECIAL4] %s 진행중 setup %d건 복원", self.symbol, len(self.active))
        except Exception:
            logging.exception("[SPECIAL4] 상태 복원 실패 | %s", self.state_path)

    def _save_state_locked(self) -> None:
        try:
            _atomic_write_json(
                self.state_path,
                {
                    "version": STRATEGY_VERSION,
                    "last_30m_open": self.last_30m_open,
                    "active": {k: dict(v) for k, v in self.active.items()},
                },
            )
        except Exception:
            logging.exception("[SPECIAL4] 상태 저장 실패 | %s", self.state_path)

    # ------------------------------------------------------------------
    # manager/OZ 공식 Plugin API
    # ------------------------------------------------------------------
    def _manager_child_alive(self, watch_id: str) -> bool:
        return bool(self.api.has_oz_watch(watch_id))

    def _watch_payload(self, item: dict) -> dict:
        return {
            "action": "MANUAL_WATCH",
            "watch_id": item["watch_id"],
            "timeframes": list(OZ_TFS),
            "symbol": self.symbol,
            "direction": item["direction"],
            "persistent": True,
            "request_chat_id": self.api.official_chat_id,
            "validation_mode": OZ_VALIDATION_MODE,
            "trigger_mode": FINAL_TRIGGER_MODE,
            "issued_at": float(item["anchor_ts"]),
            "source_spec_id": SPEC_ID,
            "source_spec_ids": [SPEC_ID],
            "source_name": STRATEGY_NAME,
            "special4_version": STRATEGY_VERSION,
            "special4_stage": "M30_CLOSE_6M_1M_OZ",
        }

    def _arm_locked(
        self,
        direction: str,
        boundary_epoch: float,
        anchor_price: float,
        atr_1m: float,
    ) -> None:
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return

        # 같은 방향의 현재 cycle state가 있다면 새 30m boundary 기준으로 교체합니다.
        old = self.active.pop(direction, None)
        if old:
            old_wid = str(old.get("watch_id") or "")
            if old_wid:
                self.api.cancel_oz_watches([old_wid])

        item = {
            "watch_id": _watch_id(self.symbol, direction, boundary_epoch),
            "direction": direction,
            "anchor_ts": float(boundary_epoch),
            "anchor_price": float(anchor_price),
            "atr_1m": float(atr_1m),
            "atr_mult": ATR_MULT,
            "expires_at": float(boundary_epoch) + float(ACTIVE_WINDOW_SEC),
            "special4_version": STRATEGY_VERSION,
        }
        self.active[direction] = item
        self.api.register_oz_watch(self._watch_payload(item), push=True)
        self._save_state_locked()
        logging.info(
            "🎯 [SPECIAL4] 30분 찢들 %s setup | anchor=%.6f | 1m ATR14=%.6f x %.1f | "
            "15m WONBI touch + 30m same-side WONBI non-touch | final=1m %s/%s | 유효=%d분",
            direction,
            anchor_price,
            atr_1m,
            ATR_MULT,
            OZ_VALIDATION_MODE,
            FINAL_TRIGGER_MODE,
            ACTIVE_WINDOW_SEC // 60,
        )

    def _cancel_watch_locked(self, direction: str, reason: str) -> None:
        item = self.active.pop(direction, None)
        if not item:
            return
        wid = str(item.get("watch_id") or "")
        if wid:
            self.api.cancel_oz_watches([wid])
        self._save_state_locked()
        logging.info("🛑 [SPECIAL4] 30분 찢들 setup 종료 | %s | %s | %s", direction, reason, wid)

    def _cancel_all_locked(self, reason: str) -> None:
        for direction in tuple(self.active):
            self._cancel_watch_locked(direction, reason)

    def _sync_completed_children_locked(self) -> None:
        changed = False
        for direction, item in list(self.active.items()):
            wid = str(item.get("watch_id") or "")
            if wid and not self._manager_child_alive(wid):
                self.active.pop(direction, None)
                changed = True
                logging.info("✅ [SPECIAL4] 30분 찢들 OZ 알림 완료 확인 | %s | %s", direction, wid)
        if changed:
            self._save_state_locked()

    # ------------------------------------------------------------------
    # 30분 마감 setup: 15m WONBI 필수 / 30m 같은 방향 WONBI 터치 시 탈락
    # ------------------------------------------------------------------
    def _setup_from_boundary(self, boundary_epoch: float) -> None:
        data = self.api.staff_request(
            self.symbol,
            ["1m", "15m", "30m"],
            [],
            lane="maintenance",
        )
        if not isinstance(data, dict):
            return

        closed1 = _latest_row_before(data.get("1m"), boundary_epoch)
        closed15 = _latest_row_before(data.get("15m"), boundary_epoch)
        closed30 = _latest_row_before(data.get("30m"), boundary_epoch)
        if closed1 is None or closed15 is None or closed30 is None:
            logging.warning(
                "[SPECIAL4] %s 30m 마감 setup 데이터 부족 | boundary=%s",
                self.symbol,
                boundary_epoch,
            )
            return

        # 30분 마감 직전 15분봉은 :15 / :45 시작봉만 인정합니다.
        stamp15 = pd.to_datetime(closed15.get("time"), errors="coerce", utc=True)
        if pd.isna(stamp15) or int(stamp15.minute) not in {15, 45}:
            logging.warning(
                "[SPECIAL4] %s 직전 유효 15m(:15/:45) 봉 없음 | boundary=%s",
                self.symbol,
                boundary_epoch,
            )
            return

        if not all(_finite(x) for x in (closed30.get("close"), closed1.get("atr_14"))):
            logging.warning(
                "[SPECIAL4] %s 30m 종가/1m ATR14 값 부족 | boundary=%s",
                self.symbol,
                boundary_epoch,
            )
            return

        anchor_price = float(closed30.get("close"))
        atr_1m = float(closed1.get("atr_14"))
        if atr_1m <= 0.0:
            return

        if not self.api.time_allowed(TIME_FILTERS):
            logging.info(
                "⏸️ [SPECIAL4] %s 거래시간 필터 밖 | 30분 찢들 setup 미등록",
                self.symbol,
            )
            return

        touch15_lower = _touches(closed15, "LOWER")
        touch15_upper = _touches(closed15, "UPPER")
        touch30_lower = _touches(closed30, "LOWER")
        touch30_upper = _touches(closed30, "UPPER")

        # LONG: 15m 하단 WONBI 터치 필수 + 30m 하단 WONBI 터치하면 탈락.
        if touch15_lower and not touch30_lower:
            self._arm_locked("LONG", boundary_epoch, anchor_price, atr_1m)
        elif touch15_lower and touch30_lower:
            logging.info(
                "❌ [SPECIAL4] %s LONG 나가리 | 15m 하단 WONBI 터치했으나 30m 하단 WONBI도 터치",
                self.symbol,
            )

        # SHORT: 15m 상단 WONBI 터치 필수 + 30m 상단 WONBI 터치하면 탈락.
        if touch15_upper and not touch30_upper:
            self._arm_locked("SHORT", boundary_epoch, anchor_price, atr_1m)
        elif touch15_upper and touch30_upper:
            logging.info(
                "❌ [SPECIAL4] %s SHORT 나가리 | 15m 상단 WONBI 터치했으나 30m 상단 WONBI도 터치",
                self.symbol,
            )

        if not (touch15_lower or touch15_upper):
            logging.info(
                "ℹ️ [SPECIAL4] %s setup 없음 | 직전 15m WONBI 미터치",
                self.symbol,
            )

    # ------------------------------------------------------------------
    # 6분 만료
    # ------------------------------------------------------------------
    def _expire_active_locked(self, now_epoch: Optional[float] = None) -> None:
        now = float(time.time() if now_epoch is None else now_epoch)
        for direction, item in list(self.active.items()):
            expires_at = item.get("expires_at")
            if not _finite(expires_at):
                expires_at = float(item["anchor_ts"]) + float(ACTIVE_WINDOW_SEC)
                item["expires_at"] = expires_at
            if now >= float(expires_at):
                self._cancel_watch_locked(
                    direction,
                    f"30m 마감 후 {ACTIVE_WINDOW_SEC // 60}분 유효시간 종료",
                )

    # ------------------------------------------------------------------
    # 기존 1m ATR14 선행이동 gate 유지
    # ------------------------------------------------------------------
    @staticmethod
    def _excursion_exceeded(df1: pd.DataFrame, item: dict) -> Optional[bool]:
        if df1 is None or df1.empty or "time" not in df1.columns:
            return None

        anchor_ts = float(item["anchor_ts"])
        anchor = float(item["anchor_price"])
        limit = float(item["atr_1m"]) * float(item.get("atr_mult") or ATR_MULT)
        if not math.isfinite(limit) or limit <= 0.0:
            return None

        times = pd.to_datetime(df1["time"], errors="coerce", utc=True)
        seconds = times.map(lambda x: float(x.timestamp()) if not pd.isna(x) else float("nan"))
        work = df1.loc[seconds >= anchor_ts - 1e-6]
        if work.empty:
            return False

        direction = str(item.get("direction") or "").upper()
        if direction == "LONG":
            highs = pd.to_numeric(work.get("high"), errors="coerce").dropna()
            if highs.empty:
                return None
            return float(highs.max()) - anchor >= limit

        if direction == "SHORT":
            lows = pd.to_numeric(work.get("low"), errors="coerce").dropna()
            if lows.empty:
                return None
            return anchor - float(lows.min()) >= limit

        return None

    def _track_active(self) -> None:
        with self._lock:
            self._sync_completed_children_locked()
            self._expire_active_locked()
            if not self.active:
                return

        data = self.api.staff_request(self.symbol, ["1m"], [], lane="maintenance")
        if not isinstance(data, dict):
            return
        df1 = data.get("1m")

        with self._lock:
            for direction, item in list(self.active.items()):
                exceeded = self._excursion_exceeded(df1, item)
                if exceeded is True:
                    move = float(item["atr_1m"]) * float(item.get("atr_mult") or ATR_MULT)
                    self._cancel_watch_locked(
                        direction,
                        f"1m ATR 선행이동 {move:.6f} 이상",
                    )

    # ------------------------------------------------------------------
    # 최종 1m OZ 직전 추세 정렬 gate
    # ------------------------------------------------------------------
    @staticmethod
    def _current_trend_allowed(data: dict, direction: str) -> tuple[Optional[bool], str]:
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False, "direction_invalid"

        df1 = data.get("1m") if isinstance(data, dict) else None
        df3 = data.get("3m") if isinstance(data, dict) else None
        df15 = data.get("15m") if isinstance(data, dict) else None
        if any(df is None or getattr(df, "empty", True) for df in (df1, df3, df15)):
            return None, "trend_data_missing"
        if len(df3) < 3 or len(df15) < 3:
            return None, "trend_history_short"

        row1 = df1.iloc[-1]
        row3 = df3.iloc[-1]
        two_bars_ago3 = df3.iloc[-3]
        row15 = df15.iloc[-1]
        two_bars_ago15 = df15.iloc[-3]

        # 현재가는 가장 빠른 1m live close를 사용합니다.
        current_price = row1.get("close")

        hma50_15 = row15.get("hma_50")
        hma50_15_two_bars_ago = two_bars_ago15.get("hma_50")

        ema50_3 = row3.get("ema_50")
        ema200_3 = row3.get("ema_200")
        hma168_3 = row3.get("hma_168")
        hma168_3_two_bars_ago = two_bars_ago3.get("hma_168")

        ema50_1 = row1.get("ema_50")
        ema200_1 = row1.get("ema_200")

        values = (
            current_price,
            hma50_15,
            hma50_15_two_bars_ago,
            ema50_3,
            ema200_3,
            hma168_3,
            hma168_3_two_bars_ago,
            ema50_1,
            ema200_1,
        )
        if not all(_finite(x) for x in values):
            return None, "trend_value_missing"

        current_price = float(current_price)
        hma50_15 = float(hma50_15)
        hma50_15_two_bars_ago = float(hma50_15_two_bars_ago)
        ema50_3 = float(ema50_3)
        ema200_3 = float(ema200_3)
        hma168_3 = float(hma168_3)
        hma168_3_two_bars_ago = float(hma168_3_two_bars_ago)
        ema50_1 = float(ema50_1)
        ema200_1 = float(ema200_1)

        if direction == "LONG":
            checks = {
                "15m_hma50_up": hma50_15 > hma50_15_two_bars_ago,
                "price_above_15m_hma50": current_price > hma50_15,
                "3m_ema50_above_ema200": ema50_3 > ema200_3,
                "3m_hma168_up": hma168_3 > hma168_3_two_bars_ago,
                "1m_ema50_above_ema200": ema50_1 > ema200_1,
            }
        else:
            checks = {
                "15m_hma50_down": hma50_15 < hma50_15_two_bars_ago,
                "price_below_15m_hma50": current_price < hma50_15,
                "3m_ema50_below_ema200": ema50_3 < ema200_3,
                "3m_hma168_down": hma168_3 < hma168_3_two_bars_ago,
                "1m_ema50_below_ema200": ema50_1 < ema200_1,
            }

        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return False, ",".join(failed)
        return True, "ok"

    def handle_final_event(self, event: dict) -> dict:
        watch_ids = _event_watch_ids(event)
        event_direction = str(event.get("direction") or "").strip().upper()

        with self._lock:
            matched = [
                (direction, dict(item))
                for direction, item in self.active.items()
                if str(item.get("watch_id") or "") in set(watch_ids)
                and (event_direction not in {"LONG", "SHORT"} or direction == event_direction)
            ]

        if not matched:
            # 이미 완료/취소된 늦은 이벤트는 재발송하지 않습니다.
            if not any(self.api.has_oz_watch(wid) for wid in watch_ids):
                return {
                    "ok": True,
                    "delivered": True,
                    "suppressed": True,
                    "special4_gate": "stale_event",
                }
            logging.error(
                "[SPECIAL4] FINAL_ALERT gate 상태 불일치 | %s | ids=%s",
                self.symbol,
                ",".join(watch_ids),
            )
            return {
                "ok": False,
                "delivered": False,
                "error": "special4_gate_state_missing",
            }

        now = time.time()
        expired: list[str] = []
        for direction, item in matched:
            expires_at = item.get("expires_at")
            if not _finite(expires_at):
                expires_at = float(item["anchor_ts"]) + float(ACTIVE_WINDOW_SEC)
            if now >= float(expires_at):
                expired.append(direction)

        if expired:
            with self._lock:
                for direction in expired:
                    self._cancel_watch_locked(
                        direction,
                        "FINAL_ALERT 직전 6분 유효시간 종료",
                    )
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special4_gate": "expired",
            }

        # race 방지를 위해 event lane에서 현재 1m/3m/15m + EMA/HMA를 즉시 재검증합니다.
        data = self.api.staff_request(
            self.symbol,
            ["1m", "3m", "15m"],
            ["EMA", "HMA"],
            lane="event",
        )
        if not isinstance(data, dict):
            return {
                "ok": False,
                "delivered": False,
                "error": "special4_gate_staff_unavailable",
            }

        df1 = data.get("1m")
        if df1 is None or getattr(df1, "empty", True):
            return {
                "ok": False,
                "delivered": False,
                "error": "special4_gate_1m_unavailable",
            }

        # ATR 선행이동 gate를 FINAL_ALERT 직전에도 다시 검사합니다.
        exceeded_dirs: list[tuple[str, float]] = []
        for direction, item in matched:
            exceeded = self._excursion_exceeded(df1, item)
            if exceeded is None:
                return {
                    "ok": False,
                    "delivered": False,
                    "error": "special4_gate_atr_indeterminate",
                }
            if exceeded:
                move = float(item["atr_1m"]) * float(item.get("atr_mult") or ATR_MULT)
                exceeded_dirs.append((direction, move))

        if exceeded_dirs:
            with self._lock:
                for direction, move in exceeded_dirs:
                    self._cancel_watch_locked(
                        direction,
                        f"FINAL_ALERT 직전 1m ATR 선행이동 {move:.6f} 이상",
                    )
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special4_gate": "atr_exceeded",
            }

        # 최종 1m OZ 순간에 추세 필터가 모두 동시에 맞아야 합니다.
        rejected: list[tuple[str, str]] = []
        for direction, _item in matched:
            allowed, reason = self._current_trend_allowed(data, direction)
            if allowed is None:
                return {
                    "ok": False,
                    "delivered": False,
                    "error": f"special4_gate_trend_indeterminate:{reason}",
                }
            if not allowed:
                rejected.append((direction, reason))

        if rejected:
            # 이 1m OZ는 전략 조건을 충족하지 못했으므로 해당 방향 cycle을 종료합니다.
            with self._lock:
                for direction, reason in rejected:
                    self._cancel_watch_locked(
                        direction,
                        f"1m OZ 시점 추세필터 불일치: {reason}",
                    )
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special4_gate": "trend_filter",
            }

        # 기존 전략 조건과 별개로 Telegram 최종 송출 직전에만 적용합니다.
        if not _final_alert_time_allowed(self.api):
            logging.info(
                "⏸️ [SPECIAL4] 최종 알림 송출 거래시간 필터 밖 | %s | ids=%s",
                self.symbol,
                ",".join(watch_ids),
            )
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special4_gate": "final_alert_time_filter",
            }

        alert_spec = SimpleNamespace(
            spec_id=SPEC_ID,
            name=STRATEGY_NAME,
            symbol=self.symbol,
            validation_mode=OZ_VALIDATION_MODE,
            trigger_mode=FINAL_TRIGGER_MODE,
            alert_template=MAIN_4_ALERT_TEMPLATE,
        )
        forwarded = dict(event)
        fallback = str(forwarded.get("message") or "").strip()
        forwarded["message"] = self.api.render_oz_alert(alert_spec, forwarded, fallback)
        result = self.api.deliver_oz_event_core(forwarded)

        if bool(result.get("delivered")):
            matched_ids = {str(item.get("watch_id") or "") for _direction, item in matched}
            with self._lock:
                changed = False
                for direction, item in list(self.active.items()):
                    if str(item.get("watch_id") or "") in matched_ids:
                        self.active.pop(direction, None)
                        changed = True
                if changed:
                    self._save_state_locked()
        return result

    # ------------------------------------------------------------------
    # SPECIAL poll hook
    # ------------------------------------------------------------------
    def poll(self, _targets=()) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_poll_mono < POLL_SEC:
            return
        self._last_poll_mono = now_mono

        with self._lock:
            self._sync_completed_children_locked()
            self._expire_active_locked()

        data30 = self.api.staff_request(self.symbol, ["30m"], [], lane="maintenance")
        if not isinstance(data30, dict):
            return
        live_open = _live_open_epoch(data30.get("30m"))
        if live_open is None:
            return

        with self._lock:
            previous = self.last_30m_open
            if previous is None:
                # 기동 순간 과거 setup을 소급 실행하지 않습니다.
                self.last_30m_open = live_open
                self._save_state_locked()
                previous = live_open

        if live_open > float(previous) + 1e-6:
            with self._lock:
                self._cancel_all_locked("새 30m 마감 cycle")
                self.last_30m_open = live_open
                self._save_state_locked()
            self._setup_from_boundary(live_open)

        self._track_active()


# ----------------------------------------------------------------------
# OZ FINAL_ALERT plugin hook
# ----------------------------------------------------------------------
def _is_special4_final_alert(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    if str(event.get("kind") or "FINAL_ALERT").upper() != "FINAL_ALERT":
        return False

    ids = _event_watch_ids(event)
    return bool(ids) and any(wid.startswith("OZARM:SP4:") for wid in ids)


class Special4EventHandler:
    """PIPELINE_4 FINAL_ALERT만 받아 동기 gate 후 core로 전달합니다."""

    def __init__(self, api, runtimes: dict[str, Special4Runtime]) -> None:
        self.api = api
        self.runtimes = dict(runtimes)

    def handle_oz_event(self, event: dict) -> Optional[dict]:
        if not _is_special4_final_alert(event):
            return None

        ids = _event_watch_ids(event)
        special_ids = [wid for wid in ids if wid.startswith("OZARM:SP4:")]
        remaining = [wid for wid in ids if wid not in set(special_ids)]

        symbol = str(event.get("symbol") or "").strip()
        runtime = self.runtimes.get(symbol)
        if runtime is None:
            return {
                "ok": False,
                "delivered": False,
                "error": f"special4_runtime_missing:{symbol or '-'}",
            }

        special_event = _event_for_watch_ids(event, special_ids, special4_source=True)
        special_result = runtime.handle_final_event(special_event)
        if not remaining:
            return special_result

        other_event = _event_for_watch_ids(event, remaining, special4_source=False)
        other_result = self.api.deliver_oz_event_core(other_event)
        return {
            "ok": bool(special_result.get("ok")) and bool(other_result.get("ok")),
            "delivered": bool(special_result.get("delivered")) or bool(other_result.get("delivered")),
            "special4_delivered": bool(special_result.get("delivered")),
            "other_delivered": bool(other_result.get("delivered")),
            "error": special_result.get("error") or other_result.get("error"),
        }


def _cleanup_incompatible_children(api) -> int:
    """구 SPECIAL4 또는 현재 V2 정의와 다른 persistent watch를 제거합니다."""
    victims: list[str] = []
    for wid, item in api.snapshot_oz_watches(
        source_spec_id=SPEC_ID,
        watch_id_prefix="OZARM:SP4:",
    ):
        if not isinstance(item, dict):
            victims.append(wid)
            continue

        symbol = str(item.get("symbol") or "")
        if symbol == LEGACY_COMBINED_SYMBOL:
            victims.append(wid)
            continue

        tfs = tuple(str(x) for x in (item.get("timeframes") or ()))
        version_ok = int(item.get("special4_version") or 0) == STRATEGY_VERSION
        mode_ok = (
            str(item.get("validation_mode") or "").upper() == OZ_VALIDATION_MODE
            and str(item.get("trigger_mode") or "").upper() == FINAL_TRIGGER_MODE
        )
        tf_ok = tfs == OZ_TFS
        if not (version_ok and mode_ok and tf_ok):
            victims.append(wid)

    count = api.cancel_oz_watches(victims)
    if count:
        logging.info("🧹 [SPECIAL4] 구/비호환 Watch %d건 정리", count)
    return count


def register(manager) -> None:
    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    _cleanup_incompatible_children(api)

    runtimes = {symbol: Special4Runtime(api, symbol) for symbol in SYMBOLS}
    for symbol, runtime in runtimes.items():
        api.register_watch_handler(f"SPECIAL4_RUNTIME_{_symbol_key(symbol)}", runtime)
    api.register_oz_event_handler(SPEC_ID, Special4EventHandler(api, runtimes))

    logging.info(
        "🟢 [SPECIAL4] 등록 | 30분 찢들 V%d | symbols=%s | "
        "15m WONBI touch + 30m same-side WONBI non-touch | "
        "15m HMA50 slope(현재/2봉전)/price + 3m EMA50/200/HMA168 slope(현재/2봉전) + 1m EMA50/200 | "
        "final=1m %s/%s | ATR14 x %.1f gate | 30m close 후 %d분",
        STRATEGY_VERSION,
        ",".join(SYMBOLS),
        OZ_VALIDATION_MODE,
        FINAL_TRIGGER_MODE,
        ATR_MULT,
        ACTIVE_WINDOW_SEC // 60,
    )
