# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL1 / 메인전략 1] 내부유동성 계열 진입 로직 메모
# ============================================================================
# 이 블록은 메모장으로 파일을 열었을 때 전략 구조를 즉시 확인하기 위한 설명용 주석입니다.
# 아래 실제 실행 코드의 조건/상수/순서에는 손대지 않았습니다.
#
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 전략의 핵심 구조
#   - 1h / 2h / 3h / 4h를 각각 서로 독립된 상위 Setup branch로 사용합니다.
#   - 각 branch는 반드시 "같은 시간봉"의 두 조건을 동시에 만족해야 합니다.
#       ① TREND@해당TF
#       ② WONBI@해당TF
#   - combination="ALL" 이므로 TREND와 WONBI 중 하나만 맞는 상태는 Setup 성립이 아닙니다.
#
# ■ Setup branch 구성
#   - 1h branch : TREND@1h + WONBI@1h
#   - 2h branch : TREND@2h + WONBI@2h
#   - 3h branch : TREND@3h + WONBI@3h
#   - 4h branch : TREND@4h + WONBI@4h
#   - 위 4개 branch는 서로 섞어서 계산하지 않습니다.
#     예) TREND@1h + WONBI@2h 같은 교차 조합은 이 전략 정의가 아닙니다.
#
# ■ Setup 성립 후 최종 신호 감시
#   - 후속 OZ 감시 대상 TF:
#       1m / 2m / 3m / 4m / 5m / 6m / 10m / 12m / 15m / 20m / 30m / 1h
#   - validation_mode = NORMAL
#   - trigger_mode    = BREAKER
#   - 즉, 상위 1h~4h TREND+WONBI Setup을 기준으로 1h 이하 NORMAL 브레이커 OZ를 감시합니다.
#
# ■ 거래시간 필터
#   - MAIN_ASIA
#   - MAIN_LONDON
#   - MAIN_NEWYORK
#   - 위 공식 메인 세션 필터 안에서만 이 전략의 최종 흐름이 유효합니다.
#
# ■ 종목/TF별 Spec 분리
#   - XAUUSD+와 NAS100은 각각 별도 spec으로 등록됩니다.
#   - 1h/2h/3h/4h branch 역시 각각 별도 spec_id를 가집니다.
#   - 따라서 다른 상위 TF의 조건을 한 Setup으로 합치지 않습니다.
#
# ■ 최종 알림
#   - 알림 제목: [내부유동성 스윕 발생!]
#   - 종목 / 최종 주기 / 등급 / 지표 / 현재 가격을 표시합니다.
#
# ■ 이 파일에서 하지 않는 것
#   - SPECIAL2의 외부유동성 SWEEP/ATR 로직을 사용하지 않습니다.
#   - SPECIAL3의 EMA50/200 cross + FVG 연결 로직을 사용하지 않습니다.
#   - SPECIAL4의 30분 마감 6분 cycle 로직을 사용하지 않습니다.
#   - SPECIAL5의 상위 OZ -> 1/2/3분 하위 OZ 부모/자식 로직을 사용하지 않습니다.
# ============================================================================
"""공식 메인전략 1 정의.

manager_KIM의 공용 StrategySpec/Composer 런타임을 사용하되,
전략 값 자체는 이 SPECIAL 모듈이 직접 소유합니다.
"""
from __future__ import annotations

import oz_profiles


# ============================================================================
# [전략 1 사용자 설정]
# ============================================================================
# 최종 OZ 트리거: "BREAKER" 또는 "OZ"
FINAL_TRIGGER_MODE = "BREAKER"

# 최종 OZ 검증: "NORMAL"(일반) 또는 "BLIND"(무지성)
FINAL_VALIDATION_MODE = "NORMAL"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL1", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 최종 Telegram 알림 송출 거래시간
# 기본: 아시아 + 런던 + 뉴욕 메인 세션
# 24시간 송출하려면 0으로 설정
FINAL_ALERT_TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
# ============================================================================


def _final_trigger_alert_text() -> str:
    """최종 Telegram 알림에 표시할 트리거명을 반환합니다."""
    if FINAL_TRIGGER_OVERRIDDEN:
        return oz_profiles.profile_label(FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE)
    mode = str(FINAL_TRIGGER_MODE).strip()
    mode_upper = mode.upper()
    if mode_upper == "BREAKER":
        return "브레이커 올존"
    if mode_upper == "OZ":
        return "올존"
    return f"{mode} 올존"


def _main_alert_template(tf: str) -> str:
    """상위 WONBI branch TF와 최종 OZ 주기/트리거를 함께 표시합니다."""
    upper_wonbi = f"{str(tf).removesuffix('h')}시간 원비"
    trigger_text = _final_trigger_alert_text()
    return (
        "[1. 내부유동성 스윕 발생!] {side_icon} {side_text} 신호 발생\n"
        "──────────────────\n"
        "• 종목: {sym}\n"
        f"• 위치: {upper_wonbi}\n"
        f"• 주기: {{b_tf}} {trigger_text}\n"
        "• 지표: {indicators_text}\n"
        "• 현재 가격: {current_price:,.2f}\n"
        "──────────────────"
    )


def _final_alert_time_allowed(api) -> bool:
    """전략 1 최종 Telegram 송출시간만 검사합니다. 0이면 24시간 허용합니다."""
    if FINAL_ALERT_TIME_FILTERS == 0:
        return True
    if isinstance(FINAL_ALERT_TIME_FILTERS, str):
        filters = (FINAL_ALERT_TIME_FILTERS.strip(),) if FINAL_ALERT_TIME_FILTERS.strip() else ()
    else:
        filters = tuple(str(x).strip() for x in FINAL_ALERT_TIME_FILTERS if str(x).strip())
    return True if not filters else bool(api.time_allowed(filters))

SYMBOLS = ("XAUUSD+", "NAS100")
MAIN_SESSION_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
OZ_TO_1H = ("1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m", "1h")


def _main_1_specs() -> list[dict]:
    """1h/2h/3h/4h 기본더블비 각각 -> 1h 이하 브레이커 올존."""
    out: list[dict] = []
    for symbol in SYMBOLS:
        for tf in ("1h", "2h", "3h", "4h"):
            spec_id = f"PIPELINE_1@{tf}" if symbol == "XAUUSD+" else f"PIPELINE_1@{symbol}@{tf}"
            out.append({
                "spec_id": spec_id,
                "name": "MAIN_1_BASIC_DOUBLEB_1H_4H_TO_1H_BELOW_DIV_OZ",
                "symbol": symbol,
                "conditions": (f"TREND@{tf}", f"WONBI@{tf}"),
                "oz_tfs": OZ_TO_1H,
                "combination": "ALL",
                "validation_mode": FINAL_VALIDATION_MODE,
                "trigger_mode": FINAL_TRIGGER_MODE,
                "time_filters": MAIN_SESSION_FILTERS,
                "alert_template": _main_alert_template(tf),
            })
    return out


class Special1EventHandler:
    """전략 1 FINAL_ALERT의 Telegram 송출시간만 추가 검사합니다."""

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
                "special1_gate": "final_alert_time_filter",
            }

        return self.api.deliver_oz_event_core(event)


def register(manager) -> None:
    """manager_KIM의 공식 전략 registry에 메인전략 1을 등록합니다."""
    specs = tuple(_main_1_specs())
    manager.register_special_bundle(
        strategies=specs,
        timed_chains=(),
    )

    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    handler = Special1EventHandler(api)
    for spec in specs:
        api.register_oz_event_handler(str(spec["spec_id"]), handler)
