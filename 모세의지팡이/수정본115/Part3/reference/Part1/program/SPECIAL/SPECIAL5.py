# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL5] 프렉탈 MS 셋업 - 상위 브레이커 OZ -> 1/2/3분 무지성 OZ 상세 메모
# ============================================================================
# 이 블록은 메모장으로 파일을 열었을 때 전략 구조를 즉시 확인하기 위한 설명용 주석입니다.
# 아래 실제 실행 코드의 조건/상수/순서에는 손대지 않았습니다.
#
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 전체 구조: "상위 부모 OZ"를 내부 Setup으로 소비한 뒤 "하위 자식 OZ"를 새로 감시
#   1차 부모 감시 → 상위 브레이커 OZ 또는 레짐 브레이커 OZ
#   2차 필터     → 브레이커 부모만 TF 마지막 확정봉 EMA50/200 방향 확인
#   3차 자식 감시 → 같은 방향 1m / 2m / 3m BLIND OZ
#   4차 완료     → 1m/2m/3m 중 최초 최종 OZ만 실제 전략 알림으로 사용
#
# ■ 1차: 상위 부모 Watch
#   - source TF:
#       5m / 6m / 10m / 12m / 15m / 20m / 30m / 1h
#   - validation_mode = NORMAL
#   - trigger_mode    = BREAKER + BREAKER_REGIME (각각 별도 Watch)
#   - 위 TF 각각에 두 종류의 persistent 상위 Watch를 유지합니다.
#   - 이 상위 FINAL_ALERT 자체는 Telegram 전략 알림으로 내보내지 않고 내부 Setup 이벤트로 소비합니다.
#
# ■ 상위 부모 OZ를 실제 Setup으로 인정하는 조건
#   1) 방향이 LONG 또는 SHORT여야 합니다.
#   2) source_tf가 위 SOURCE_TFS 중 하나여야 합니다.
#   3) MAIN_ASIA / MAIN_LONDON / MAIN_NEWYORK 거래시간 안이어야 합니다.
#   4) BREAKER 부모는 source TF의 "마지막 확정봉" EMA50/EMA200 방향이 신호와 일치해야 합니다.
#      - STAFF snapshot 마지막 행은 live 봉이므로 -2 행(마지막 확정봉)을 사용합니다.
#      - LONG : EMA50 > EMA200
#      - SHORT: EMA50 < EMA200
#   5) BREAKER_REGIME 부모는 EMA50/200 추가 필터 없이 레짐 브레이커 올존 발생 자체를 인정합니다.
#   - 둘 중 하나가 인정되면 같은 하위 1m/2m/3m Setup을 생성합니다.
#
# ■ 2차: 하위 자식 Watch
#   - 부모 방향을 그대로 이어받습니다. 방향을 새로 계산하지 않습니다.
#   - 하위 TF = 1m / 2m / 3m
#   - 각 TF는 서로 독립된 자식 Watch입니다.
#   - validation_mode = BLIND
#   - trigger_mode    = OZ
#   - 세 자식은 같은 special5_setup_id로 한 그룹에 묶입니다.
#
# ■ 같은 방향의 새 부모 Setup이 다시 오면
#   - 아직 완료되지 않은 "같은 방향"의 기존 하위 Watch 묶음을 새 Setup으로 교체합니다.
#   - 즉, 같은 방향에서는 가장 최근 상위 부모 OZ에서 만들어진 하위 Setup을 유지합니다.
#   - LONG 새 Setup 교체와 SHORT 새 Setup 교체는 방향별로 따로 처리됩니다.
#
# ■ 부모 무효화: source TF 반대 HMA6/17 cross
#   - Setup 생성 이후 부모 source TF에서 신호 방향의 반대 HMA6/17 cross가 확인되면
#     그 setup_id에 연결된 1m/2m/3m 자식 전체를 한꺼번에 취소합니다.
#   - LONG의 반대 cross : HMA6/17 dead cross
#     (직전 HMA6 >= HMA17 이고 현재 HMA6 < HMA17)
#   - SHORT의 반대 cross: HMA6/17 golden cross
#     (직전 HMA6 <= HMA17 이고 현재 HMA6 > HMA17)
#
# ■ 자식별 독립 취소 ①: 자기 TF 반대 HMA6/17 cross
#   - 1m 자식은 1m의 반대 cross만 자기 취소에 사용합니다.
#   - 2m 자식은 2m의 반대 cross만 자기 취소에 사용합니다.
#   - 3m 자식은 3m의 반대 cross만 자기 취소에 사용합니다.
#   - 한 자식이 이 조건으로 취소되어도 다른 TF 자식까지 자동 취소하지 않습니다.
#
# ■ 자식별 독립 취소 ②: 자기 TF 확정봉 수 만료
#   - MAX_BARS_AFTER_B0 설정값을 사용합니다. 값이 없거나 잘못되면 기본 10을 사용합니다.
#   - setup 이후 "새로 시작해 확정된 봉"만 세며 마지막 live 봉은 세지 않습니다.
#   - MAX_BARS_AFTER_B0=N이면 각 자식 TF에서 N개 확정봉까지 허용합니다.
#   - N+1번째 확정봉부터 해당 TF 자식 Watch만 취소합니다.
#   - 1m/2m/3m은 각자 자기 봉 수로 따로 만료됩니다.
#
# ■ 최종 진입/알림
#   - 1m / 2m / 3m 중 어느 하나에서 BLIND + OZ FINAL_ALERT가 먼저 완료되면
#     [프렉탈 MS 셋업 발생!] 템플릿으로 실제 전략 알림을 보냅니다.
#   - 성공적으로 전달되면 같은 setup_id의 나머지 형제 자식 Watch도 전부 취소합니다.
#   - 따라서 한 Setup에서 최종 알림은 최초 완료 자식을 기준으로 마감됩니다.
#
# ■ 상위 부모 Watch 유지 방식
#   - 상위 Watch는 persistent이며 maintenance poll에서 계속 존재하도록 보정합니다.
#   - 상위 FINAL_ALERT는 내부적으로 정상 ACK/suppress 처리하여 부모 감시를 계속 유지합니다.
#
# ■ 이 파일에서 하지 않는 것
#   - SPECIAL1의 TREND+WONBI 상위 Setup을 사용하지 않습니다.
#   - SPECIAL2의 외부유동성 SWEEP/ATR Setup을 사용하지 않습니다.
#   - SPECIAL3의 EMA cross + FVG 체인을 사용하지 않습니다.
#   - SPECIAL4의 30분 마감/6분/ATR 선행이동 cycle을 사용하지 않습니다.
#   - manager_KIM에는 범용 SPECIAL Plugin API/OZ Hook만 사용하며,
#     SPECIAL5의 전략 계산과 부모/자식 상태 판단은 이 모듈이 소유합니다.
# ============================================================================
"""SPECIAL5 - 상위 브레이커 OZ -> 1/2/3분 무지성 OZ.

전략 정의
---------
1) XAUUSD+ / NAS100의 5m/6m/10m/12m/15m/20m/30m/1h에서
   NORMAL + BREAKER OZ 또는 NORMAL + BREAKER_REGIME OZ가 발생하면 선행 setup 후보로 사용합니다.
2) 선행 setup 자체는 Telegram에 노출하지 않습니다.
3) BREAKER 선행 OZ는 source TF의 마지막 확정봉 EMA50/200 방향을 확인합니다.
   LONG은 EMA50 > EMA200, SHORT는 EMA50 < EMA200일 때만 인정합니다.
   BREAKER_REGIME 선행 OZ는 EMA50/200 추가 필터 없이 인정합니다.
4) 인정된 선행 setup의 방향(LONG/SHORT)을 그대로 이어받아
   1m/2m/3m BLIND + OZ를 감시합니다.
5) 1m/2m/3m 중 최초 최종 OZ가 발생하면
   '[프렉탈 MS 셋업 발생!]' 상세 알림을 보냅니다.
6) 같은 방향의 새 상위 setup이 오면 아직 미완료인 기존 하위 setup을 교체합니다.
7) 선행 setup은 MAIN_ASIA / MAIN_LONDON / MAIN_NEWYORK 거래시간 안에서만 받습니다.
8) 상위 source TF에서 반대 HMA6/17 cross가 발생하면 연결된 하위 setup 전체를 취소합니다.
9) 하위 1m/2m/3m은 각각 자기 TF의 확정봉 수로 독립 만료합니다.
   MAX_BARS_AFTER_B0=N이면 각 하위 TF에서 setup 이후 N개 확정봉까지 허용하고 N+1번째부터 취소합니다.
10) 하위 TF 자체에서 반대 HMA6/17 cross가 발생하면 그 TF의 자식 Watch만 취소합니다.
11) 1m/2m/3m 중 하나가 최종 OZ를 완료하면 같은 setup의 나머지 자식 Watch도 함께 취소합니다.

전략 계산은 이 모듈이 소유합니다.
manager_KIM에는 범용 SPECIAL Plugin API/OZ Hook만 사용하고, monitor_OZ의 탐지/프로토콜은 수정하지 않습니다.
"""
from __future__ import annotations

import oz_profiles

import hashlib
import logging
import math
from domain_clock import time

import pandas as pd
from types import SimpleNamespace
from typing import Iterable


SPEC_ID = "PIPELINE_5"
STRATEGY_NAME = "프렉탈 MS 셋업 발생!"
SYMBOLS = ("XAUUSD+", "NAS100")
LEGACY_COMBINED_SYMBOL = ",".join(SYMBOLS)

# ============================================================================
# [전략 5 사용자 설정]
# ============================================================================
# 최종 하위 OZ 트리거: "BREAKER" 또는 "OZ"
# FINAL_VALIDATION_MODE = "BLIND"이므로 기본 OZ는 알림에 "무지성 올존"으로 표시됩니다.
FINAL_TRIGGER_MODE = "OZ"

# 최종 Telegram 알림 송출 거래시간 필터.
# 24시간 송출하려면 아래 값을 0으로 변경합니다.
FINAL_ALERT_TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
# ============================================================================

# monitor_OZ가 지원하는 5분 이상 1시간 이하 base TF 전체.
SOURCE_TFS = ("5m", "6m", "10m", "12m", "15m", "20m", "30m", "1h")
FINAL_TFS = ("1m", "2m", "3m")

# 상위 부모 Watch는 기존 전략 정의 그대로 NORMAL + BREAKER를 유지합니다.
SOURCE_VALIDATION_MODE = "NORMAL"
SOURCE_TRIGGER_MODE = "BREAKER"
BREAKER_REGIME_SOURCE_TRIGGER_MODE = "BREAKER_REGIME"
SOURCE_EMA_FAST = 50
SOURCE_EMA_SLOW = 200

# 하위 최종 Watch의 검증 방식은 기존 전략 정의 그대로 BLIND를 유지합니다.
FINAL_VALIDATION_MODE = "BLIND"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL5", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 메인전략 1/2/4와 같은 거래시간 필터.
TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")

HIGH_PREFIX = "OZARM:SP5:HIGH:"
BREAKER_REGIME_HIGH_PREFIX = "OZARM:SP5:HIGH_BREAKER_REGIME:"
FINAL_PREFIX = "OZARM:SP5:FINAL:"
POLL_SEC = 1.0


def _final_trigger_alert_text() -> str:
    """최종 Telegram 알림에 표시할 하위 트리거명을 반환합니다."""
    if FINAL_TRIGGER_OVERRIDDEN:
        return oz_profiles.profile_label(FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE)
    mode = str(FINAL_TRIGGER_MODE or "").strip().upper()
    validation = str(FINAL_VALIDATION_MODE or "").strip().upper()
    if mode == "BREAKER":
        return "브레이커 올존"
    if mode == "OZ" and validation == "BLIND":
        return "올존"
    if mode == "OZ":
        return "올존"
    return mode or "올존"

MAIN_5_ALERT_TEMPLATE = (
    "[5. 프렉탈 MS 셋업 발생!] {side_icon} {side_text} 신호 발생\n"
    "──────────────────\n"
    "• 종목: {sym}\n"
    "• 위치: {trigger_name}\n"
    f"• 주기: {{b_tf}} {_final_trigger_alert_text()}\n"
    "• 지표: {indicators_text}\n"
    "• 현재 가격: {current_price:,.2f}\n"
    "──────────────────"
)


def _final_alert_filters() -> tuple[str, ...]:
    """0이면 24시간, 그 외에는 지정된 SPECIAL5 최종 송출 세션을 반환합니다."""
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


def _source_oz_location(api, watch_ids: Iterable[str], event: dict | None = None) -> str:
    """완료한 하위 Watch의 부모 OZ 종류/TF를 알림용 위치명으로 반환합니다."""
    source_tf = ""
    source_mode = "BREAKER"

    # FINAL_ALERT가 부모 metadata를 보존한 경우 실시간 event 값을 우선 사용합니다.
    if isinstance(event, dict):
        candidate = str(event.get("source_high_tf") or "").strip().lower()
        if candidate in SOURCE_TFS:
            source_tf = candidate
            mode = str(event.get("special5_source_trigger_mode") or "BREAKER").strip().upper()
            source_mode = "BREAKER_REGIME" if mode == "BREAKER_REGIME" else "BREAKER"

    for watch_id in watch_ids:
        if source_tf:
            break
        wid = str(watch_id)
        item = api.get_oz_watch(wid)
        if isinstance(item, dict):
            candidate = str(item.get("source_high_tf") or "").strip().lower()
            if candidate in SOURCE_TFS:
                source_tf = candidate
                mode = str(item.get("special5_source_trigger_mode") or "BREAKER").strip().upper()
                source_mode = "BREAKER_REGIME" if mode == "BREAKER_REGIME" else "BREAKER"
                break

        # 새 FINAL watch ID에는 부모 TF/모드를 함께 넣어 Watch 조회가 끝난 뒤에도 복원 가능하게 합니다.
        if wid.startswith(FINAL_PREFIX):
            parts = wid[len(FINAL_PREFIX):].split(":", 2)
            if len(parts) >= 2 and parts[0] in SOURCE_TFS:
                source_tf = parts[0]
                source_mode = "BREAKER_REGIME" if parts[1] == "BREAKER_REGIME" else "BREAKER"
                break

    source_name = "레짐 브레이커 올존" if source_mode == "BREAKER_REGIME" else "브레이커 올존"
    if source_tf.endswith("m") and source_tf[:-1].isdigit():
        return f"{int(source_tf[:-1])}분봉 {source_name}"
    if source_tf.endswith("h") and source_tf[:-1].isdigit():
        return f"{int(source_tf[:-1])}시간봉 {source_name}"
    return f"상위 {source_name}"


def _stable_id(prefix: str, *parts: object, length: int = 20) -> str:
    raw = "|".join(str(x) for x in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def _symbol_key(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(symbol)).strip("_") or "SYMBOL"


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _closed_bar_count_after(df: pd.DataFrame, after_epoch: float) -> int | None:
    """setup 이후 새로 시작해 확정된 봉 수. 마지막 행(live)은 세지 않습니다."""
    if df is None or df.empty or "time" not in df.columns:
        return None
    closed = df.iloc[:-1]
    if closed.empty:
        return 0
    times = pd.to_datetime(closed["time"], errors="coerce", utc=True)
    epochs = times.map(lambda x: float(x.timestamp()) if not pd.isna(x) else float("nan"))
    return int((epochs > float(after_epoch) + 1e-6).sum())


def _opposite_hma_cross_after(df: pd.DataFrame, direction: str, after_epoch: float) -> bool:
    """monitor_OZ와 같은 HMA6/17 cross 정의로 setup 이후 반대 cross 존재 여부를 봅니다."""
    if df is None or len(df) < 2 or "time" not in df.columns:
        return False
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False

    times = pd.to_datetime(df["time"], errors="coerce", utc=True)
    h6 = pd.to_numeric(df.get("hma_6"), errors="coerce")
    h17 = pd.to_numeric(df.get("hma_17"), errors="coerce")
    if h6 is None or h17 is None:
        return False

    for idx in range(1, len(df)):
        t = times.iloc[idx]
        if pd.isna(t) or float(t.timestamp()) <= float(after_epoch) + 1e-6:
            continue
        p6, p17 = h6.iloc[idx - 1], h17.iloc[idx - 1]
        c6, c17 = h6.iloc[idx], h17.iloc[idx]
        if not all(_finite(x) for x in (p6, p17, c6, c17)):
            continue
        if direction == "LONG":
            # LONG 부모/자식의 반대 cross = HMA6/17 dead cross.
            if float(p6) >= float(p17) and float(c6) < float(c17):
                return True
        else:
            # SHORT 부모/자식의 반대 cross = HMA6/17 golden cross.
            if float(p6) <= float(p17) and float(c6) > float(c17):
                return True
    return False


def _event_watch_ids(event: object) -> list[str]:
    if not isinstance(event, dict):
        return []
    ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
    single = str(event.get("watch_id") or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def _is_high_watch_id(watch_id: object) -> bool:
    wid = str(watch_id or "")
    return wid.startswith(HIGH_PREFIX) or wid.startswith(BREAKER_REGIME_HIGH_PREFIX)


def _high_watch_trigger_mode(watch_id: object) -> str:
    wid = str(watch_id or "")
    if wid.startswith(BREAKER_REGIME_HIGH_PREFIX):
        return "BREAKER_REGIME"
    if wid.startswith(HIGH_PREFIX):
        return "BREAKER"
    return ""


def _is_final_watch_id(watch_id: object) -> bool:
    return str(watch_id or "").startswith(FINAL_PREFIX)


def _strip_special5_source(event: dict) -> dict:
    """혼합 FINAL_ALERT에서 SPECIAL5 메타데이터만 제거합니다."""
    out = dict(event)
    raw_ids = out.get("source_spec_ids")
    remaining: list[str] = []
    if isinstance(raw_ids, (list, tuple, set)):
        remaining = [str(x).strip() for x in raw_ids if str(x).strip() and str(x).strip() != SPEC_ID]
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


def _event_for_watch_ids(event: dict, watch_ids: Iterable[str], *, special5_source: bool) -> dict:
    ids = [str(x) for x in watch_ids if str(x)]
    out = dict(event)
    out["watch_ids"] = ids
    if ids:
        out["watch_id"] = ids[0]
    else:
        out.pop("watch_id", None)

    if special5_source:
        out["source_spec_id"] = SPEC_ID
        out["source_spec_ids"] = [SPEC_ID]
        out["source_name"] = STRATEGY_NAME
    else:
        out = _strip_special5_source(out)
    return out


class Special5Runtime:
    """SPECIAL5가 소유하는 상위 setup -> 하위 OZ 연결 상태."""

    def __init__(self, api, symbol: str) -> None:
        self.api = api
        self.symbol = str(symbol)
        self._last_poll_mono = 0.0

    # ------------------------------------------------------------------
    # manager/OZ 공식 Plugin API
    # ------------------------------------------------------------------
    def _official_chat_id(self):
        return self.api.official_chat_id

    def _time_allowed(self) -> bool:
        return bool(self.api.time_allowed(TIME_FILTERS))

    def _source_ema_trend_allowed(self, direction: str, source_tf: str) -> bool:
        """선행 OZ source TF의 마지막 확정봉 EMA50/200이 신호 방향과 일치하는지 확인합니다."""
        direction = str(direction or "").upper()
        source_tf = str(source_tf or "").strip().lower()
        if direction not in {"LONG", "SHORT"} or source_tf not in SOURCE_TFS:
            return False

        data = self.api.staff_request(self.symbol, [source_tf], ["EMA"], lane="event")
        if not isinstance(data, dict):
            logging.warning(
                "[SPECIAL5] 선행 EMA50/200 데이터 요청 실패 | %s %s | source=%s",
                self.symbol, direction, source_tf,
            )
            return False

        df = data.get(source_tf)
        if df is None or getattr(df, "empty", True) or len(df) < 2:
            logging.warning(
                "[SPECIAL5] 선행 EMA50/200 확정봉 데이터 부족 | %s %s | source=%s",
                self.symbol, direction, source_tf,
            )
            return False

        # STAFF snapshot의 마지막 행은 live 봉이므로 직전 행(-2), 즉 마지막 확정봉만 사용합니다.
        closed = df.iloc[-2]
        ema_fast = closed.get(f"ema_{SOURCE_EMA_FAST}")
        ema_slow = closed.get(f"ema_{SOURCE_EMA_SLOW}")
        if not all(_finite(x) for x in (ema_fast, ema_slow)):
            logging.warning(
                "[SPECIAL5] 선행 EMA50/200 값 부족 | %s %s | source=%s | ema50=%s ema200=%s",
                self.symbol, direction, source_tf, ema_fast, ema_slow,
            )
            return False

        ema_fast = float(ema_fast)
        ema_slow = float(ema_slow)
        allowed = ema_fast > ema_slow if direction == "LONG" else ema_fast < ema_slow
        if not allowed:
            logging.info(
                "⏸️ [SPECIAL5] 선행 EMA50/200 추세 필터 불일치 | %s %s | source=%s | EMA50=%.6f EMA200=%.6f",
                self.symbol, direction, source_tf, ema_fast, ema_slow,
            )
        return allowed

    def _register_child(self, payload: dict, *, push: bool = True) -> None:
        self.api.register_oz_watch(payload, push=push)

    def _cancel_children(self, watch_ids: Iterable[str]) -> None:
        self.api.cancel_oz_watches(watch_ids)

    # ------------------------------------------------------------------
    # 1차: 5m~1h 상위 OZ 상시 Watch
    # - 기존 NORMAL + BREAKER는 그대로 유지하고 EMA50/200 필터를 적용합니다.
    # - NORMAL + BREAKER_REGIME를 별도 Watch로 추가해 같은 하위 1m/2m/3m 확장으로 연결합니다.
    # ------------------------------------------------------------------
    def _high_watch_payload(self, tf: str, trigger_mode: str = SOURCE_TRIGGER_MODE) -> dict:
        trigger_mode = str(trigger_mode or SOURCE_TRIGGER_MODE).strip().upper()
        is_breaker_regime = trigger_mode == BREAKER_REGIME_SOURCE_TRIGGER_MODE
        prefix = BREAKER_REGIME_HIGH_PREFIX if is_breaker_regime else HIGH_PREFIX
        stage = "HIGH_BREAKER_REGIME" if is_breaker_regime else "HIGH_BREAKER"
        return {
            "action": "MANUAL_WATCH",
            "watch_id": (
                f"{prefix}{tf}"
                if self.symbol == "XAUUSD+"
                else f"{prefix}{_symbol_key(self.symbol)}:{tf}"
            ),
            "timeframes": [tf],
            "symbol": self.symbol,
            "direction": None,
            "persistent": True,
            # 공식 채널 ID를 owner로 사용해 manager stale-official prune 대상에서 분리합니다.
            # 실제 선행 FINAL_ALERT는 SPECIAL5 bridge가 소비하므로 Telegram에는 노출되지 않습니다.
            "request_chat_id": self._official_chat_id(),
            "validation_mode": SOURCE_VALIDATION_MODE,
            "trigger_mode": trigger_mode,
            "source_spec_id": SPEC_ID,
            "source_spec_ids": [SPEC_ID],
            "source_name": STRATEGY_NAME,
            "special5_stage": stage,
        }

    def ensure_high_watches(self, *, push_new: bool = True) -> int:
        compare_keys = (
            "timeframes", "symbol", "direction", "persistent", "request_chat_id",
            "validation_mode", "trigger_mode", "source_spec_id", "source_name",
        )
        changed = 0
        for tf in SOURCE_TFS:
            for trigger_mode in (SOURCE_TRIGGER_MODE, BREAKER_REGIME_SOURCE_TRIGGER_MODE):
                payload = self._high_watch_payload(tf, trigger_mode)
                if self.api.ensure_oz_watch(
                    payload, compare_keys=compare_keys, push_if_changed=push_new
                ):
                    changed += 1
        return changed

    # ------------------------------------------------------------------
    # 2차: 선행 방향 그대로 1m/2m/3m BLIND + OZ
    # ------------------------------------------------------------------
    def _lower_max_bars(self) -> int:
        try:
            value = int(self.api.config_get("MAX_BARS_AFTER_B0", "10"))
        except (TypeError, ValueError):
            value = 10
        return max(1, value)

    def _final_children_snapshot(self) -> list[tuple[str, dict]]:
        return self.api.snapshot_oz_watches(
            source_spec_id=SPEC_ID,
            symbol=self.symbol,
            watch_id_prefix=FINAL_PREFIX,
        )

    def _setup_groups_for_watch_ids(self, watch_ids: Iterable[str]) -> set[str]:
        groups: set[str] = set()
        for wid in sorted({str(x) for x in watch_ids if str(x)}):
            item = self.api.get_oz_watch(wid)
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol") or "") != self.symbol:
                continue
            group = str(item.get("special5_setup_id") or "").strip()
            if group:
                groups.add(group)
        return groups

    def _cancel_setup_group(self, setup_id: str, reason: str) -> None:
        setup_id = str(setup_id or "").strip()
        if not setup_id:
            return
        victims = [
            wid for wid, item in self._final_children_snapshot()
            if str(item.get("special5_setup_id") or "").strip() == setup_id
        ]
        if victims:
            self._cancel_children(victims)
            logging.info("🛑 [SPECIAL5] 하위 setup 전체 취소 | setup=%s | reason=%s", setup_id, reason)

    def arm_final(self, direction: str, source_tf: str, source_trigger_mode: str = SOURCE_TRIGGER_MODE) -> list[str]:
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"SPECIAL5 선행 OZ 방향 오류: {direction}")
        source_tf = str(source_tf or "").strip().lower()
        if source_tf not in SOURCE_TFS:
            raise ValueError(f"SPECIAL5 선행 OZ TF 오류: {source_tf}")
        source_trigger_mode = str(source_trigger_mode or SOURCE_TRIGGER_MODE).strip().upper()
        if source_trigger_mode not in {SOURCE_TRIGGER_MODE, BREAKER_REGIME_SOURCE_TRIGGER_MODE}:
            raise ValueError(f"SPECIAL5 선행 OZ 모드 오류: {source_trigger_mode}")

        now_ns = time.time_ns()
        issued_at = time.time()
        setup_id = (
            _stable_id("SP5SETUP", direction, source_trigger_mode, source_tf, now_ns, length=20)
            if self.symbol == "XAUUSD+"
            else _stable_id("SP5SETUP", self.symbol, direction, source_trigger_mode, source_tf, now_ns, length=20)
        )
        payloads: list[dict] = []
        for final_tf in FINAL_TFS:
            digest = hashlib.sha1(
                "|".join(
                    str(x) for x in (
                        (direction, source_trigger_mode, source_tf, final_tf, now_ns)
                        if self.symbol == "XAUUSD+"
                        else (self.symbol, direction, source_trigger_mode, source_tf, final_tf, now_ns)
                    )
                ).encode("utf-8")
            ).hexdigest()[:20]
            watch_id = f"{FINAL_PREFIX}{source_tf}:{source_trigger_mode}:{digest}"
            payloads.append({
                "action": "MANUAL_WATCH",
                "watch_id": watch_id,
                "timeframes": [final_tf],
                "symbol": self.symbol,
                "direction": direction,
                "persistent": True,
                "request_chat_id": self._official_chat_id(),
                "validation_mode": FINAL_VALIDATION_MODE,
                "trigger_mode": FINAL_TRIGGER_MODE,
                "source_spec_id": SPEC_ID,
                "source_spec_ids": [SPEC_ID],
                "source_name": STRATEGY_NAME,
                "source_high_tf": source_tf,
                "special5_source_trigger_mode": source_trigger_mode,
                "special5_final_tf": final_tf,
                "special5_setup_id": setup_id,
                "special5_stage": "LOW_BLIND_OZ",
                "issued_at": issued_at,
            })

        # 같은 방향 setup은 브레이커/레짐 브레이커 구분 없이 가장 최근 상위 OZ 하나만 유효하게 유지합니다.
        self.api.replace_oz_watches(
            payloads,
            source_spec_id=SPEC_ID,
            symbol=self.symbol,
            direction=direction,
            watch_id_prefix=FINAL_PREFIX,
        )

        source_name = "레짐 브레이커 OZ" if source_trigger_mode == BREAKER_REGIME_SOURCE_TRIGGER_MODE else "브레이커 OZ"
        logging.info(
            "🎯 [SPECIAL5] 선행 %s → 하위 무지성 OZ | %s %s | source=%s | final=%s | setup=%s",
            source_name, self.symbol, direction, source_tf, ",".join(FINAL_TFS), setup_id,
        )
        return [str(x["watch_id"]) for x in payloads]

    def _maintain_final_children(self) -> None:
        snapshot = self._final_children_snapshot()
        if not snapshot:
            return

        required_tfs = set()
        for _wid, item in snapshot:
            source_tf = str(item.get("source_high_tf") or "").strip().lower()
            final_tf = str(item.get("special5_final_tf") or ((item.get("timeframes") or [""])[0])).strip().lower()
            if source_tf in SOURCE_TFS:
                required_tfs.add(source_tf)
            if final_tf in FINAL_TFS:
                required_tfs.add(final_tf)
        if not required_tfs:
            return

        data = self.api.staff_request(self.symbol, sorted(required_tfs), ["HMA"], lane="maintenance")
        if not isinstance(data, dict) or not data:
            return

        max_bars = self._lower_max_bars()

        # 같은 setup의 부모 invalidation은 한 번만 판정하고 전체 자식을 취소합니다.
        groups: dict[str, dict] = {}
        for _wid, item in snapshot:
            setup_id = str(item.get("special5_setup_id") or "").strip()
            if setup_id:
                groups.setdefault(setup_id, item)

        cancelled_groups: set[str] = set()
        for setup_id, item in groups.items():
            direction = str(item.get("direction") or "").upper()
            source_tf = str(item.get("source_high_tf") or "").strip().lower()
            try:
                issued_at = float(item.get("issued_at"))
            except (TypeError, ValueError):
                continue
            if source_tf not in SOURCE_TFS or direction not in {"LONG", "SHORT"}:
                continue
            if _opposite_hma_cross_after(data.get(source_tf), direction, issued_at):
                self._cancel_setup_group(setup_id, f"부모 {source_tf} 반대 HMA6/17 cross")
                cancelled_groups.add(setup_id)

        # 부모가 살아 있으면 1m/2m/3m 자식을 각 자기 TF 기준으로 독립 유지/취소합니다.
        for wid, item in snapshot:
            setup_id = str(item.get("special5_setup_id") or "").strip()
            if setup_id in cancelled_groups:
                continue
            direction = str(item.get("direction") or "").upper()
            final_tf = str(item.get("special5_final_tf") or ((item.get("timeframes") or [""])[0])).strip().lower()
            try:
                issued_at = float(item.get("issued_at"))
            except (TypeError, ValueError):
                continue
            if final_tf not in FINAL_TFS or direction not in {"LONG", "SHORT"}:
                continue
            df = data.get(final_tf)
            if df is None or getattr(df, "empty", True):
                continue

            if _opposite_hma_cross_after(df, direction, issued_at):
                self._cancel_children([wid])
                logging.info(
                    "🛑 [SPECIAL5] 하위 TF 반대 HMA6/17 cross 취소 | %s %s | tf=%s | child=%s",
                    self.symbol, direction, final_tf, wid,
                )
                continue

            bars = _closed_bar_count_after(df, issued_at)
            if bars is not None and bars > max_bars:
                self._cancel_children([wid])
                logging.info(
                    "⌛ [SPECIAL5] 하위 TF 봉수 만료 | %s %s | tf=%s | bars=%d>%d | child=%s",
                    self.symbol, direction, final_tf, bars, max_bars, wid,
                )

    # manager maintenance의 SPECIAL poll hook. 상위 Watch 복구 + 하위 setup 생존 관리.
    def poll(self, _targets=()) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_poll_mono < POLL_SEC:
            return
        self._last_poll_mono = now_mono
        self.ensure_high_watches(push_new=True)
        self._maintain_final_children()


# ----------------------------------------------------------------------
# OZ FINAL_ALERT plugin hook
# ----------------------------------------------------------------------
class Special5EventHandler:
    def __init__(self, api, runtimes: dict[str, Special5Runtime]) -> None:
        self.api = api
        self.runtimes = dict(runtimes)

    def _runtime_for_event(self, event: dict) -> Special5Runtime | None:
        return self.runtimes.get(str(event.get("symbol") or "").strip())

    def handle_oz_event(self, event: dict) -> dict | None:
        if not isinstance(event, dict):
            return None
        if str(event.get("kind") or "FINAL_ALERT").upper() != "FINAL_ALERT":
            return None

        ids = _event_watch_ids(event)
        if not ids:
            return None

        high_ids = [wid for wid in ids if _is_high_watch_id(wid)]
        final_ids = [wid for wid in ids if _is_final_watch_id(wid)]
        if not high_ids and not final_ids:
            return None
        runtime = self._runtime_for_event(event)

        # 1) 5m~1h 상위 OZ는 내부 setup으로 소비합니다.
        #    - 기존 BREAKER: EMA50/200 방향 일치 시에만 확장
        #    - 추가 BREAKER_REGIME: 레짐 브레이커 올존 발생 자체로 같은 하위 1m/2m/3m 확장
        if high_ids:
            direction = str(event.get("direction") or "").upper()
            source_tf = str(event.get("source_tf") or event.get("tf") or "").strip().lower()
            breaker_ids = [wid for wid in high_ids if _high_watch_trigger_mode(wid) == "BREAKER"]
            breaker_regime_ids = [wid for wid in high_ids if _high_watch_trigger_mode(wid) == "BREAKER_REGIME"]
            if runtime is None:
                logging.warning(
                    "[SPECIAL5] 미등록 심볼 선행 OZ 이벤트 무시 | symbol=%s ids=%s",
                    str(event.get("symbol") or "-") or "-", ",".join(high_ids),
                )
            elif direction not in {"LONG", "SHORT"} or source_tf not in SOURCE_TFS:
                logging.warning(
                    "[SPECIAL5] 선행 OZ 이벤트 무시 | symbol=%s direction=%s source_tf=%s ids=%s",
                    runtime.symbol, direction or "-", source_tf or "-", ",".join(high_ids),
                )
            else:
                try:
                    if runtime._time_allowed():
                        if breaker_ids and runtime._source_ema_trend_allowed(direction, source_tf):
                            runtime.arm_final(direction, source_tf, "BREAKER")
                        if breaker_regime_ids:
                            runtime.arm_final(direction, source_tf, "BREAKER_REGIME")
                    else:
                        source_types = []
                        if breaker_ids:
                            source_types.append("브레이커")
                        if breaker_regime_ids:
                            source_types.append("레짐 브레이커")
                        logging.info(
                            "⏸️ [SPECIAL5] 거래시간 필터 밖 | 선행 %s OZ setup 미등록 | %s %s %s",
                            "+".join(source_types) or "상위", runtime.symbol, direction, source_tf,
                        )
                except Exception as exc:
                    logging.exception("[SPECIAL5] 하위 OZ arm 실패 | %s", runtime.symbol)
                    if set(ids).issubset(set(high_ids)):
                        return {
                            "ok": False, "delivered": False,
                            "error": f"special5_arm_failed:{type(exc).__name__}:{exc}",
                        }

            remaining = [wid for wid in ids if wid not in set(high_ids)]
            if not remaining:
                # monitor_OZ에는 정상 처리로 ACK하여 persistent 상위 Watch를 유지합니다.
                return {
                    "ok": True,
                    "delivered": True,
                    "suppressed": True,
                    "special5_stage": True,
                    "internal_watch_ids": high_ids,
                }

            forwarded = _event_for_watch_ids(event, remaining, special5_source=False)
            return self.api.deliver_oz_event_core(forwarded)

        # 2) 1m/2m/3m BLIND+OZ 최종 신호는 SPECIAL5 템플릿으로 전달합니다.
        if final_ids:
            if runtime is None:
                return {
                    "ok": False, "delivered": False,
                    "error": f"special5_runtime_missing:{str(event.get('symbol') or '-')}",
                }

            alert_spec = SimpleNamespace(
                spec_id=SPEC_ID,
                name=STRATEGY_NAME,
                symbol=runtime.symbol,
                validation_mode=FINAL_VALIDATION_MODE,
                trigger_mode=FINAL_TRIGGER_MODE,
                alert_template=MAIN_5_ALERT_TEMPLATE,
            )

            # core가 완료 child를 active-child에서 제거하기 전에 형제 setup ID와 부모 OZ TF를 확보합니다.
            completed_groups = runtime._setup_groups_for_watch_ids(final_ids)
            source_location = _source_oz_location(self.api, final_ids, event)
            special_event = _event_for_watch_ids(event, final_ids, special5_source=True)
            special_event["trigger_name"] = source_location

            if _final_alert_time_allowed(self.api):
                fallback = str(special_event.get("message") or "").strip()
                special_event["message"] = self.api.render_oz_alert(alert_spec, special_event, fallback)
                special_result = self.api.deliver_oz_event_core(special_event)
            else:
                logging.info(
                    "⏸️ [SPECIAL5] 최종 알림 송출 거래시간 필터 밖 | symbol=%s | 위치=%s | watch=%s",
                    runtime.symbol, source_location, ",".join(final_ids),
                )
                # monitor_OZ에는 정상 처리/ACK하여 거래시간 밖 최종 신호가 재전송되지 않게 합니다.
                special_result = {
                    "ok": True,
                    "delivered": True,
                    "suppressed": True,
                    "special5_gate": "final_alert_time_filter",
                }

            if bool(special_result.get("delivered")):
                for setup_id in completed_groups:
                    runtime._cancel_setup_group(setup_id, "형제 TF 중 최초 최종 OZ 완료")

            remaining = [wid for wid in ids if wid not in set(final_ids)]
            if not remaining:
                return special_result

            other_event = _event_for_watch_ids(event, remaining, special5_source=False)
            other_result = self.api.deliver_oz_event_core(other_event)
            return {
                "ok": bool(special_result.get("ok")) and bool(other_result.get("ok")),
                "delivered": bool(special_result.get("delivered")) or bool(other_result.get("delivered")),
                "special5_delivered": bool(special_result.get("delivered")),
                "other_delivered": bool(other_result.get("delivered")),
                "error": special_result.get("error") or other_result.get("error"),
            }

        return None


def _cleanup_legacy_combined_symbol_children(api) -> int:
    """이전 "XAUUSD+,NAS100" 단일 문자열 Watch만 공식 Plugin API로 제거합니다."""
    victims = [
        wid for wid, item in api.snapshot_oz_watches(source_spec_id=SPEC_ID)
        if (_is_high_watch_id(wid) or _is_final_watch_id(wid))
        and str(item.get("symbol") or "") == LEGACY_COMBINED_SYMBOL
    ]
    count = api.cancel_oz_watches(victims)
    if count:
        logging.info("🧹 [SPECIAL5] 이전 결합 심볼 Watch %d건 정리", count)
    return count


def register(manager) -> None:
    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    _cleanup_legacy_combined_symbol_children(api)
    runtimes = {symbol: Special5Runtime(api, symbol) for symbol in SYMBOLS}
    api.register_oz_event_handler(SPEC_ID, Special5EventHandler(api, runtimes))

    # 상위 브레이커 OZ + 레짐 브레이커 OZ 감시를 manager active-child lifecycle에 등록해 재시작시 자동 복구합니다.
    armed = 0
    for symbol, runtime in runtimes.items():
        armed += runtime.ensure_high_watches(push_new=True)
        api.register_watch_handler(f"SPECIAL5_RUNTIME_{_symbol_key(symbol)}", runtime)

    logging.info(
        "🟢 [SPECIAL5] 등록 | %s | symbols=%s | source=%s NORMAL/(BREAKER+BREAKER_REGIME) → final=%s BLIND/OZ | time=%s | 신규 source watch=%d",
        SPEC_ID, ",".join(SYMBOLS), ",".join(SOURCE_TFS), ",".join(FINAL_TFS), ",".join(TIME_FILTERS), armed,
    )