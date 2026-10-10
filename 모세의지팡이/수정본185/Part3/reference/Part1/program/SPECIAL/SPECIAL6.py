# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL6 / 메인전략 6] 15m/30m 추세 정렬 FVG 눌림 -> 6m 이하 브레이커 OZ
# ============================================================================
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 상위 Setup TF
#   - 15m branch
#   - 30m branch
#   - 두 branch는 서로 독립입니다.
#
# ■ LONG Setup (각 branch 자기 TF 안에서 전부 동시에 충족)
#   1) EMA50 > EMA200 정배열
#   2) HMA168 우상향: 현재 STAFF HMA168 > 2봉 전 STAFF HMA168
#   3) 같은 TF의 상승(BULL) FVG를 현재 가격이 터치
#
# ■ SHORT Setup = LONG의 정확한 반대
#   1) EMA50 < EMA200 역배열
#   2) HMA168 우하향: 현재 STAFF HMA168 < 2봉 전 STAFF HMA168
#   3) 같은 TF의 하락(BEAR) FVG를 현재 가격이 터치
#
# ■ Setup 이후 최종 신호
#   - 1m / 2m / 3m / 4m / 5m / 6m
#   - validation_mode = NORMAL
#   - trigger_mode    = BREAKER
#   - 즉, 15m 또는 30m의 유효 Setup에서 6분봉 이하 브레이커 올존을 감시합니다.
#
# ■ 거래시간 필터
#   - MAIN_ASIA
#   - MAIN_LONDON
#   - MAIN_NEWYORK
#
# ■ 중요 TF 고정 규칙
#   - 15m EMA 배열/HMA168 방향은 15m FVG touch와만 결합합니다.
#   - 30m EMA 배열/HMA168 방향은 30m FVG touch와만 결합합니다.
#   - 15m 조건과 30m FVG, 또는 30m 조건과 15m FVG를 교차 결합하지 않습니다.
# ============================================================================
"""공식 메인전략 6 정의.

15m/30m 각각에서 EMA50/200 배열 + HMA168 방향 + 동방향 FVG touch가
동시에 성립하면, 6분봉 이하 NORMAL 브레이커 OZ를 같은 방향으로 감시합니다.
"""
from __future__ import annotations

import oz_profiles


# ============================================================================
# [전략 6 사용자 설정]
# ============================================================================
# 최종 OZ 트리거: "BREAKER" 또는 "OZ"
FINAL_TRIGGER_MODE = "BREAKER"

# 최종 OZ 검증: "NORMAL"(일반) 또는 "BLIND"(무지성)
FINAL_VALIDATION_MODE = "NORMAL"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL6", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 최종 Telegram 알림 송출 거래시간
# 기본: 아시아 + 런던 + 뉴욕 메인 세션
# 24시간 송출하려면 0으로 설정
FINAL_ALERT_TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
# ============================================================================


def _setup_tf_label(setup_tf: str) -> str:
    tf = str(setup_tf).strip().lower()
    if tf.endswith("m") and tf[:-1].isdigit():
        return f"{int(tf[:-1])}분봉"
    if tf.endswith("h") and tf[:-1].isdigit():
        return f"{int(tf[:-1])}시간봉"
    return tf


def _final_trigger_alert_text() -> str:
    """최종 Telegram 알림에 표시할 트리거명을 반환합니다."""
    if FINAL_TRIGGER_OVERRIDDEN:
        return oz_profiles.profile_label(FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE)
    mode = str(FINAL_TRIGGER_MODE or "").strip().upper()
    if mode == "BREAKER":
        return "브레이커 올존"
    if mode == "OZ":
        return "올존"
    return mode or "올존"


def _main_6_alert_template(setup_tf: str) -> str:
    location = f"{_setup_tf_label(setup_tf)} FVG"
    trigger_text = _final_trigger_alert_text()
    return (
        "[6. 추세 FVG 눌림 발생!] {side_icon} {side_text} 신호 발생\n"
        "──────────────────\n"
        "• 종목: {sym}\n"
        f"• 위치: {location}\n"
        f"• 주기: {{b_tf}} {trigger_text}\n"
        "• 지표: {indicators_text}\n"
        "• 현재 가격: {current_price:,.2f}\n"
        "──────────────────"
    )


def _final_alert_time_allowed(api) -> bool:
    """전략 6 최종 Telegram 송출시간만 검사합니다. 0이면 24시간 허용합니다."""
    if FINAL_ALERT_TIME_FILTERS == 0:
        return True
    if isinstance(FINAL_ALERT_TIME_FILTERS, str):
        filters = (FINAL_ALERT_TIME_FILTERS.strip(),) if FINAL_ALERT_TIME_FILTERS.strip() else ()
    else:
        filters = tuple(str(x).strip() for x in FINAL_ALERT_TIME_FILTERS if str(x).strip())
    return True if not filters else bool(api.time_allowed(filters))


SYMBOLS = ("XAUUSD+", "NAS100")
SETUP_TFS = ("15m", "30m")
FINAL_OZ_TFS = ("1m", "2m", "3m", "4m", "5m", "6m")
MAIN_SESSION_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")


def _spec_id(symbol: str, setup_tf: str, direction: str) -> str:
    side = "LONG" if direction == "LONG" else "SHORT"
    if symbol == "XAUUSD+":
        return f"PIPELINE_6@{setup_tf}@{side}"
    return f"PIPELINE_6@{symbol}@{setup_tf}@{side}"


def _direction_conditions(setup_tf: str, direction: str) -> tuple[dict, ...]:
    """같은 setup TF 안에서 EMA 배열 + HMA168 방향 + FVG touch를 묶습니다."""
    is_long = direction == "LONG"
    return (
        {
            "kind": "MA_STATE",
            "tf": setup_tf,
            "direction": direction,
            "side": "ABOVE" if is_long else "BELOW",
            "ma_family": "EMA",
            "fast_period": 50,
            "slow_period": 200,
        },
        {
            # manager_KIM 공용 HMA 기울기 기준: 현재 STAFF 값 vs 2봉 전 STAFF 값.
            "kind": "MA_SLOPE_STATE",
            "tf": setup_tf,
            "direction": direction,
            "side": "UP" if is_long else "DOWN",
            "ma_family": "HMA",
            "slow_period": 168,
        },
        {
            "kind": "FVG",
            "tf": setup_tf,
            "direction": direction,
            "side": "BULL" if is_long else "BEAR",
        },
    )


def _main_6_specs() -> list[dict]:
    out: list[dict] = []
    for symbol in SYMBOLS:
        for setup_tf in SETUP_TFS:
            for direction in ("LONG", "SHORT"):
                out.append({
                    "spec_id": _spec_id(symbol, setup_tf, direction),
                    "name": f"MAIN_6_{setup_tf.upper()}_EMA50_200_HMA168_FVG_TO_6M_BELOW_DIV_OZ",
                    "symbol": symbol,
                    "conditions": _direction_conditions(setup_tf, direction),
                    "oz_tfs": FINAL_OZ_TFS,
                    "combination": "ALL",
                    "validation_mode": FINAL_VALIDATION_MODE,
                    "trigger_mode": FINAL_TRIGGER_MODE,
                    "final_direction": direction,
                    "time_filters": MAIN_SESSION_FILTERS,
                    "alert_template": _main_6_alert_template(setup_tf),
                })
    return out


class Special6EventHandler:
    """전략 6 FINAL_ALERT의 Telegram 송출시간만 추가 검사합니다."""

    def __init__(self, api) -> None:
        self.api = api

    def handle_oz_event(self, event: dict) -> dict | None:
        if not isinstance(event, dict):
            return None
        if str(event.get("kind") or "FINAL_ALERT").upper() != "FINAL_ALERT":
            return None

        if not _final_alert_time_allowed(self.api):
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special6_gate": "final_alert_time_filter",
            }

        return self.api.deliver_oz_event_core(event)


def register(manager) -> None:
    """manager_KIM 공식 registry에 SPECIAL6의 15m/30m 양방향 branch를 등록합니다."""
    specs = tuple(_main_6_specs())
    manager.register_special_bundle(
        strategies=specs,
        timed_chains=(),
    )

    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    handler = Special6EventHandler(api)
    for spec in specs:
        api.register_oz_event_handler(str(spec["spec_id"]), handler)

