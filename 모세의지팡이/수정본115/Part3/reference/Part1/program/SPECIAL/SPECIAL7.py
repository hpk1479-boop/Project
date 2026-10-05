# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL7 / 메인전략 7] 15분 추세 방향 -> 1분 브레이커 레짐 올존
# ============================================================================
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ Setup
#   - 15분봉 TREND 방향을 그대로 사용합니다.
#   - 15m TREND LONG  -> 최종 1m LONG만 감시
#   - 15m TREND SHORT -> 최종 1m SHORT만 감시
#
# ■ 최종 신호
#   - TF              = 1m
#   - validation_mode = NORMAL
#   - trigger_mode    = BREAKER_REGIME
#   - 즉, 15분 추세 방향과 같은 방향의 1분 브레이커 레짐 올존입니다.
#
# ■ 추가 조건
#   - 없음
#   - WONBI / FVG / SWEEP / EMA / HMA 조건을 추가하지 않습니다.
#   - 별도 거래시간 필터를 추가하지 않습니다.
# ============================================================================
"""공식 메인전략 7 정의.

15분 TREND 방향을 Setup 방향으로 사용하고,
같은 방향의 1분 NORMAL + BREAKER_REGIME OZ를 최종 신호로 사용합니다.
"""
from __future__ import annotations

import oz_profiles


SPEC_ID = "PIPELINE_7"
STRATEGY_NAME = "15분 추세중 1분 레짐 올존 발생!"
SYMBOLS = ("XAUUSD+", "NAS100")

SETUP_TF = "15m"
FINAL_OZ_TFS = ("1m",)
FINAL_VALIDATION_MODE = "NORMAL"
FINAL_TRIGGER_MODE = "BREAKER_REGIME"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL7", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)
MAIN_SESSION_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
FINAL_ALERT_TIME_FILTERS = MAIN_SESSION_FILTERS


def _final_trigger_alert_text() -> str:
    """최종 Telegram 알림에 표시할 트리거명을 반환합니다."""
    if FINAL_TRIGGER_OVERRIDDEN:
        return oz_profiles.profile_label(FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE)
    return "브레이커 레짐 올존"


def _main_7_alert_template() -> str:
    return (
        "[7. 15분 추세중 1분 레짐 올존 발생!] {side_icon} {side_text} 신호 발생\n"
        "──────────────────\n"
        "• 종목: {sym}\n"
        "• 위치: 15분봉 추세\n"
        f"• 주기: {{b_tf}} {_final_trigger_alert_text()}\n"
        "• 지표: {indicators_text}\n"
        "• 현재 가격: {current_price:,.2f}\n"
        "──────────────────"
    )


def _spec_id(symbol: str) -> str:
    return SPEC_ID if symbol == "XAUUSD+" else f"{SPEC_ID}@{symbol}"


def _main_7_specs() -> list[dict]:
    """15m TREND 방향 -> 같은 방향 1m 브레이커 레짐 올존."""
    return [
        {
            "spec_id": _spec_id(symbol),
            "name": "MAIN_7_15M_TREND_TO_1M_BREAKER_REGIME_OZ",
            "symbol": symbol,
            "conditions": ("TREND@15m",),
            "oz_tfs": FINAL_OZ_TFS,
            "combination": "ALL",
            "validation_mode": FINAL_VALIDATION_MODE,
            "trigger_mode": FINAL_TRIGGER_MODE,
            "time_filters": MAIN_SESSION_FILTERS,
            "alert_template": _main_7_alert_template(),
        }
        for symbol in SYMBOLS
    ]



def _final_alert_time_allowed(api) -> bool:
    return bool(api.time_allowed(FINAL_ALERT_TIME_FILTERS))


class Special7EventHandler:
    """전략 7 FINAL_ALERT를 MAIN 세션 안에서만 Telegram으로 송출합니다."""

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
                "special7_gate": "final_alert_time_filter",
            }

        return self.api.deliver_oz_event_core(event)


def register(manager) -> None:
    """manager_KIM 공식 registry에 메인전략 7을 등록합니다."""
    specs = tuple(_main_7_specs())
    manager.register_special_bundle(
        strategies=specs,
        timed_chains=(),
    )

    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    handler = Special7EventHandler(api)
    for spec in specs:
        api.register_oz_event_handler(str(spec["spec_id"]), handler)
