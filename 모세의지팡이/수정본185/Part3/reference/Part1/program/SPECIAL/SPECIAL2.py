# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL2 / 메인전략 2] 외부유동성 SWEEP -> 동일 TF 브레이커 OZ 진입 로직 메모
# ============================================================================
# 이 블록은 메모장으로 파일을 열었을 때 전략 구조를 즉시 확인하기 위한 설명용 주석입니다.
# 아래 실제 실행 코드의 조건/상수/순서에는 손대지 않았습니다.
#
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 감시 시간봉
#   - 1m / 2m / 3m / 4m / 5m / 6m / 10m / 12m / 15m / 20m / 30m / 1h
#   - 각 시간봉은 서로 독립된 spec입니다.
#
# ■ 1차 Setup: 외부유동성 SWEEP
#   - 각 TF에서 SWEEP@해당TF가 발생해야 합니다.
#   - 대상 외부유동성 레벨:
#       PDH / PDL / 4H / 8H / PWH / PWL / SESSION
#   - SWEEP 자격에는 ATR14와 1.5 배수가 설정되어 있습니다.
#       sweep_atr_period = 14
#       sweep_atr_mult   = 1.5
#
# ■ 가장 중요한 TF 고정 규칙
#   - "SWEEP가 발생한 TF"와 "최종 브레이커 OZ를 찾는 TF"를 반드시 1:1로 고정합니다.
#   - 1m SWEEP  -> 1m OZ만 감시
#   - 2m SWEEP  -> 2m OZ만 감시
#   - 5m SWEEP  -> 5m OZ만 감시
#   - 30m SWEEP -> 30m OZ만 감시
#   - 1h SWEEP  -> 1h OZ만 감시
#   - 서로 다른 TF를 교차 연결하지 않습니다.
#
# ■ 2차/최종 신호
#   - validation_mode = NORMAL
#   - trigger_mode    = BREAKER
#   - 즉, 외부유동성 SWEEP/ATR Setup이 만들어진 바로 그 TF에서 NORMAL 브레이커 OZ를 기다립니다.
#
# ■ 거래시간 필터
#   - MAIN_ASIA
#   - MAIN_LONDON
#   - MAIN_NEWYORK
#
# ■ 최종 알림의 "위치" 표시
#   - Setup을 만든 external_level_id를 읽어 사용자용 한글 위치명으로 변환합니다.
#   - 예: 전일 고가/저가, 주봉 고가/저가, 4시간 고가/저가, 8시간 고가/저가,
#         아시아/런던/뉴욕/이전 세션 고가·저가 등으로 표시합니다.
#   - external_level_price가 있으면 위치명 뒤에 실제 레벨 가격도 함께 표시합니다.
#
# ■ ATR 허용 범위 표시
#   - monitor_OZ가 이미 저장한 외부유동성 상태파일에서 해당 Watch의 max_distance를 읽습니다.
#   - 여기서 ATR 범위를 새로 계산하는 것이 아니라 "기존 저장 상태의 허용 범위"를 읽어 알림에 붙입니다.
#   - 값이 정상적으로 확인될 때만 "ATR: 허용 범위 ..."를 위치 항목에 추가합니다.
#
# ■ 이벤트 처리 범위
#   - SPECIAL2 EventHandler는 자기 spec_id에서 만들어진 FINAL_ALERT의 위치 표시를 담당합니다.
#   - 위치 metadata를 찾지 못한 경우에도 알림 자체를 막지 않고 기존 core 경로로 넘깁니다.
#
# ■ 최종 알림
#   - 알림 제목: [외부유동성 스윕 발생!]
#   - 종목 / 주기+최종트리거 / 외부유동성 위치(+가능하면 ATR 허용 범위) / 지표 / 현재 가격을 표시합니다.
#
# ■ 이 파일에서 하지 않는 것
#   - 다른 TF의 SWEEP와 다른 TF의 OZ를 섞지 않습니다.
#   - SPECIAL1의 TREND+WONBI 상위 Setup을 사용하지 않습니다.
#   - SPECIAL3의 EMA cross/FVG 체인을 사용하지 않습니다.
#   - SPECIAL4의 30분 마감 cycle을 사용하지 않습니다.
#   - SPECIAL5의 상위 OZ -> 하위 1/2/3m 부모/자식 구조를 사용하지 않습니다.
# ============================================================================
"""공식 메인전략 2 정의.

각 시간봉의 외부유동성 SWEEP/ATR 자격과 최종 브레이커 OZ를 같은 시간봉으로 고정합니다.
최종 알림에는 해당 setup을 만든 외부유동성 위치를 한글로 표시합니다.
"""
from __future__ import annotations
from durable_protocol import atomic_json, read_json
import oz_profiles

# ============================================================================
# [전략 2 사용자 설정]
# ============================================================================
# 최종 OZ 트리거: "BREAKER" 또는 "OZ"
FINAL_TRIGGER_MODE = "BREAKER"

# 최종 OZ 검증: "NORMAL"(일반) 또는 "BLIND"(무지성)
FINAL_VALIDATION_MODE = "NORMAL"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL2", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 최종 Telegram 알림 송출 거래시간
# 기본: 아시아 + 런던 + 뉴욕 메인 세션
# 24시간 송출하려면 0으로 설정
FINAL_ALERT_TIME_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
# ============================================================================

import json
import logging
from pathlib import Path
from types import SimpleNamespace


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


MAIN_ALERT_TEMPLATE = (
    "[2. 외부유동성 스윕 발생!] {side_icon} {side_text} 신호 발생\n"
    "──────────────────\n"
    "• 종목: {sym}\n"
    "• 위치: {trigger_name}\n"
    f"• 주기: {{tf_label}} {_final_trigger_alert_text()}\n"
    "• 지표: {indicators_text}\n"
    "• 현재 가격: {current_price:,.2f}\n"
    "──────────────────"
)


def _append_atr_to_alert_message(message: str, atr_range: float | None) -> str:
    """렌더 완료된 Telegram 메시지의 마지막 구분선 직전에 ATR 한 줄을 붙입니다."""
    text = str(message or "")
    if atr_range is None:
        return text
    atr_line = f"• ATR: 허용 범위 {atr_range:,.2f}"
    separator = "\n──────────────────"
    pos = text.rfind(separator)
    if pos < 0:
        return f"{text}\n{atr_line}" if text else atr_line
    return f"{text[:pos]}\n{atr_line}{text[pos:]}"

SYMBOLS = ("XAUUSD+", "NAS100")
MAIN_SESSION_FILTERS = ("MAIN_ASIA", "MAIN_LONDON", "MAIN_NEWYORK")
OZ_TO_1H = ("1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m", "1h")
SWEEP_LEVELS = ("PDH", "PDL", "4H", "8H", "PWH", "PWL", "SESSION")


def _final_alert_time_allowed(api) -> bool:
    """전략 2 최종 Telegram 송출시간만 검사합니다. 0이면 24시간 허용합니다."""
    if FINAL_ALERT_TIME_FILTERS == 0:
        return True
    if isinstance(FINAL_ALERT_TIME_FILTERS, str):
        filters = (FINAL_ALERT_TIME_FILTERS.strip(),) if FINAL_ALERT_TIME_FILTERS.strip() else ()
    else:
        filters = tuple(str(x).strip() for x in FINAL_ALERT_TIME_FILTERS if str(x).strip())
    return True if not filters else bool(api.time_allowed(filters))


def _main_2_specs() -> list[dict]:
    """각 TF 외부유동성 Setup -> 동일 TF 일반 브레이커 올존."""
    out: list[dict] = []
    for symbol in SYMBOLS:
        for tf in OZ_TO_1H:
            spec_id = f"PIPELINE_2@{tf}" if symbol == "XAUUSD+" else f"PIPELINE_2@{symbol}@{tf}"
            out.append({
                "spec_id": spec_id,
                "name": "MAIN_2_EXTERNAL_LIQUIDITY_TO_1H_BELOW_DIV_OZ",
                "symbol": symbol,
                "conditions": (f"SWEEP@{tf}",),
                # 핵심: SWEEP/ATR source TF와 최종 OZ TF를 1:1로 고정합니다.
                "oz_tfs": (tf,),
                "combination": "ALL",
                "validation_mode": FINAL_VALIDATION_MODE,
                "trigger_mode": FINAL_TRIGGER_MODE,
                "time_filters": MAIN_SESSION_FILTERS,
                "sweep_levels": SWEEP_LEVELS,
                "sweep_atr_period": 14,
                "sweep_atr_mult": 1.5,
                "alert_template": MAIN_ALERT_TEMPLATE,
            })
    return out


def _event_watch_ids(event: dict) -> list[str]:
    ids = [str(x).strip() for x in (event.get("watch_ids") or ()) if str(x).strip()]
    one = str(event.get("watch_id") or "").strip()
    if one and one not in ids:
        ids.append(one)
    return ids


def _location_name(level_id: object, direction: object) -> str:
    """manager child에 보존된 external_level_id를 사용자용 한글 위치명으로 바꿉니다."""
    raw = str(level_id or "").strip()
    upper = raw.upper().replace("-", "_").replace(" ", "_")
    side = str(direction or "").strip().upper()
    is_low = side == "LONG"

    if "PDH" in upper:
        return "전일 고가"
    if "PDL" in upper:
        return "전일 저가"
    if "PWH" in upper:
        return "주봉고가"
    if "PWL" in upper:
        return "주봉저가"

    if "4H" in upper:
        return "4시간 저가" if is_low else "4시간 고가"
    if "8H" in upper:
        return "8시간 저가" if is_low else "8시간 고가"

    # 세션 level_id에 세션명이 포함되는 경우 그대로 한글화합니다.
    if "ASIA" in upper:
        return "아시아 세션 저가" if is_low else "아시아 세션 고가"
    if "LONDON" in upper:
        return "런던 세션 저가" if is_low else "런던 세션 고가"
    if "NEWYORK" in upper or "NEW_YORK" in upper or "NY_" in upper or upper.startswith("NY"):
        return "뉴욕 세션 저가" if is_low else "뉴욕 세션 고가"
    if "SESSION" in upper:
        return "이전 세션 저가" if is_low else "이전 세션 고가"

    # 알려진 코드가 없는 예외 payload도 영문 내부 ID를 Telegram에 노출하지 않습니다.
    return "외부유동성 저가" if is_low else "외부유동성 고가"


def _location_text(payload: dict, direction: object) -> str:
    name = _location_name(payload.get("external_level_id"), direction)
    price = payload.get("external_level_price")
    try:
        return f"{name} · {float(price):,.2f}"
    except (TypeError, ValueError):
        return name


def _external_liquidity_state_path() -> Path:
    """monitor_OZ가 이미 저장한 외부유동성 상태파일 위치를 찾습니다."""
    from domain_memory import module_directory
    here = module_directory(__file__)
    root = here.parent if here.name.upper() == "SPECIAL" else here
    return root / "logs" / "oz_external_liquidity_state.json"


def _atr_allowed_range(payload: dict, direction: object) -> float | None:
    """OZ의 저장 상태에서 해당 외부유동성의 기존 ATR 허용 범위(max_distance)만 읽습니다."""
    watch_id = str(payload.get("external_watch_id") or "").strip()
    level_id = str(payload.get("external_level_id") or "").strip()
    side = str(direction or "").strip().upper()
    if not watch_id or not level_id or side not in {"LONG", "SHORT"}:
        return None

    try:
        raw = read_json(_external_liquidity_state_path())
    except (OSError, ValueError, TypeError):
        return None

    states = raw.get("states", {}) if isinstance(raw, dict) else {}
    if not isinstance(states, dict):
        return None

    state = states.get(f"{watch_id}|{side}|{level_id}")
    if not isinstance(state, dict):
        state = next((
            item for item in states.values()
            if isinstance(item, dict)
            and str(item.get("watch_id") or "").strip() == watch_id
            and str(item.get("direction") or "").strip().upper() == side
            and str(item.get("level_id") or "").strip() == level_id
        ), None)
    if not isinstance(state, dict):
        return None

    try:
        value = float(state.get("max_distance"))
    except (TypeError, ValueError):
        return None
    return value if value > 0.0 else None


class Special2EventHandler:
    """SPECIAL2 FINAL_ALERT의 위치 표시만 담당하는 Plugin hook."""

    def __init__(self, api, spec_id: str) -> None:
        self.api = api
        self.spec_id = str(spec_id)

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
                "special2_gate": "final_alert_time_filter",
            }

        direction = str(event.get("direction") or "").strip().upper()
        payload = None
        for watch_id in _event_watch_ids(event):
            item = self.api.get_oz_watch(watch_id)
            if not isinstance(item, dict):
                continue
            source_ids = []
            raw_ids = item.get("source_spec_ids")
            if isinstance(raw_ids, (list, tuple, set)):
                source_ids.extend(str(x).strip() for x in raw_ids if str(x).strip())
            single = str(item.get("source_spec_id") or "").strip()
            if single:
                source_ids.append(single)
            if self.spec_id in set(source_ids):
                payload = item
                break

        if payload is None:
            # 위치 metadata를 찾지 못하면 기존 core 경로로 넘겨 알림 자체를 막지 않습니다.
            return None

        forwarded = dict(event)
        # manager_KIM의 기존 공식 템플릿 renderer가 지원하는 trigger_name 슬롯을
        # SPECIAL2에서 '위치' 표시용으로 사용합니다. manager_KIM 수정은 없습니다.
        location_text = _location_text(payload, direction)
        atr_range = _atr_allowed_range(payload, direction)
        forwarded["trigger_name"] = location_text

        # grade 필드는 건드리지 않습니다.
        # SPECIAL2 템플릿을 먼저 공식 renderer로 렌더한 뒤 ATR만 마지막 구분선 직전에 추가합니다.
        alert_spec = SimpleNamespace(
            spec_id=self.spec_id,
            name="MAIN_2_EXTERNAL_LIQUIDITY_TO_1H_BELOW_DIV_OZ",
            symbol=str(event.get("symbol") or payload.get("symbol") or ""),
            validation_mode=FINAL_VALIDATION_MODE,
            trigger_mode=FINAL_TRIGGER_MODE,
            alert_template=MAIN_ALERT_TEMPLATE,
        )
        fallback = str(forwarded.get("message") or "").strip()
        rendered = self.api.render_oz_alert(alert_spec, forwarded, fallback)
        forwarded["message"] = _append_atr_to_alert_message(rendered, atr_range)
        return self.api.deliver_oz_event_core(forwarded)


def register(manager) -> None:
    """manager_KIM의 공식 전략 registry + OZ final hook에 메인전략 2를 등록합니다."""
    specs = tuple(_main_2_specs())
    manager.register_special_bundle(
        strategies=specs,
        timed_chains=(),
    )

    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    for spec in specs:
        spec_id = str(spec["spec_id"])
        api.register_oz_event_handler(spec_id, Special2EventHandler(api, spec_id))

    logging.info(
        "🟢 [SPECIAL2] 등록 | 동일 TF SWEEP/ATR→BREAKER OZ | specs=%d | 위치 한글 알림 활성",
        len(specs),
    )
