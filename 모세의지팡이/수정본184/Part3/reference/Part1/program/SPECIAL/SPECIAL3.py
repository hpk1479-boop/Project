# -*- coding: utf-8 -*-
# ============================================================================
# [SPECIAL3 / 메인전략 3] 장초반 추세 눌림 진입 로직 메모
# ============================================================================
# 이 블록은 메모장으로 파일을 열었을 때 전략 구조를 즉시 확인하기 위한 설명용 주석입니다.
# 아래 실제 실행 코드의 조건/상수/순서에는 손대지 않았습니다.
#
# ■ 대상 종목
#   - XAUUSD+
#   - NAS100
#
# ■ 핵심: 1m branch와 2m branch는 완전히 독립된 OR 관계
#   - Branch A: 1m EMA50/200 cross
#   - Branch B: 2m EMA50/200 cross
#   - 두 cross가 동시에 있어야 하는 AND 조건이 아닙니다.
#   - 1m branch 하나만 성립해도 자기 체인을 진행할 수 있고,
#     2m branch 하나만 성립해도 자기 체인을 진행할 수 있습니다.
#
# ■ 1차 후보: EMA50/200 cross
#   - ma_family   = EMA
#   - fast_period = 50
#   - slow_period = 200
#   - 각 branch는 자기 cross TF(1m 또는 2m)의 EMA50/200 cross를 사용합니다.
#
# ■ 2차 결합: 신규 FVG
#   - Setup 결합용 FVG TF = 5m / 6m
#   - EMA cross와 5m/6m 신규 FVG는 순서무관(UNORDERED)입니다.
#     즉, cross가 먼저여도 되고 FVG가 먼저여도 됩니다.
#   - 두 사건의 최대 간격 = 1800초 = 30분
#   - 30분을 넘겨 떨어진 cross와 FVG는 같은 Setup으로 묶지 않습니다.
#
# ■ Setup 성립 후: 동방향 FVG touch 대기
#   - 최종 진입 전 touch 감시 TF:
#       5m / 6m / 10m / 12m / 15m
#   - 성립한 방향과 같은 방향의 유효 FVG touch를 기다립니다.
#
# ■ 최종 OZ
#   - 최종 OZ TF = 1m 또는 2m
#   - validation_mode = NORMAL
#   - trigger_mode    = OZ
#   - 유효한 FVG touch 이후 1m/2m 중 먼저 발생한 최종 OZ 신호를 사용합니다.
#   - Setup 이후 최종 체인 유효창 = 7200초 = 2시간
#
# ■ 시간 필터
#   - 초기 cross/FVG Setup 단계: time_filters = () → 별도 세션 제한 없음
#   - 최종 FVG touch 단계: final_time_filters = () → 별도 세션 제한 없음
#   - 최종 알림 시간은 아래 FINAL_ALERT_TIME_FILTERS(또는 거래시간 슬롯)로만 정합니다.
#
# ■ 취소 조건
#   - cancel_on_opposite_cross = True
#     → 진행 중 반대 방향 cross가 나오면 해당 체인을 취소 대상으로 봅니다.
#   - cancel_on_opposite_fvg_tfs = 5m / 6m
#     → Setup용 5m/6m에서 반대 방향 FVG 조건이 발생하는 경우 취소 로직에 사용합니다.
#
# ■ 종목/branch 분리
#   - XAUUSD+와 NAS100은 별도 체인입니다.
#   - 1m cross branch와 2m cross branch 역시 별도 spec_id입니다.
#   - 서로의 cross/FVG 상태를 합쳐 하나의 branch처럼 계산하지 않습니다.
#
# ■ 최종 알림
#   - 알림 제목: [장초반 추세 눌림 발생!]
#   - 종목 / 최종 FVG touch 위치 / 최종 주기+트리거 / 지표 / 현재 가격을 표시합니다.
#   - 위치에는 최종 OZ 직전에 유효하게 터치한 FVG의 시간봉을 표시합니다.
#     예: 10m FVG touch -> "10분봉 FVG"
#
# ■ 최종 알림 송출 거래시간 필터
#   - Telegram FINAL_ALERT 송출 직전에만 적용합니다(KST, 시작·종료 포함).
#   - 기본값: 아시아 09:00~11:00 / 런던 16:00~18:00 / 뉴욕 21:00~24:00
#   - FINAL_ALERT_TIME_FILTERS = 0 으로 두면 이 송출 필터만 24시간 허용합니다.
#   - 거래시간 슬롯(전략 설정 화면)에 SPECIAL3 설정이 있으면 그 설정이 우선합니다.
#
# ■ 이 파일에서 하지 않는 것
#   - SPECIAL1의 TREND+WONBI Setup을 사용하지 않습니다.
#   - SPECIAL2의 외부유동성 SWEEP/ATR Setup을 사용하지 않습니다.
#   - SPECIAL4의 30분 마감 6분 cycle을 사용하지 않습니다.
#   - SPECIAL5의 상위 브레이커 OZ -> 하위 1/2/3m 구조를 사용하지 않습니다.
# ============================================================================
"""공식 메인전략 3 정의.

manager_KIM의 공용 StrategySpec/Composer 런타임을 사용하되,
전략 값 자체는 이 SPECIAL 모듈이 직접 소유합니다.

전략 3
------
1) 1m EMA50/200 cross 또는 2m EMA50/200 cross를 서로 독립적인 setup 후보로 감시합니다.
2) 각 cross 후보는 5m/6m 신규 FVG와 30분 이내에 순서무관으로 결합됩니다.
3) setup 성립 후 5m/6m/10m/12m/15m 동방향 FVG touch를 기다립니다.
4) 유효한 FVG touch 이후 1m 또는 2m OZ 중 먼저 발생한 최종 신호를 사용합니다.

1m branch와 2m branch는 AND가 아니라 OR입니다.
"""
from __future__ import annotations

import oz_profiles
try:
    import special_time_slot as _time_slot  # 거래시간 슬롯(없으면 코드 기본값만 사용)
except ImportError:
    _time_slot = None

import logging
import re
from types import SimpleNamespace


# ============================================================================
# [전략 3 사용자 설정]
# ============================================================================
# 최종 OZ 트리거: "BREAKER" 또는 "OZ"
FINAL_TRIGGER_MODE = "OZ"

# 최종 OZ 검증: "NORMAL"(일반) 또는 "BLIND"(무지성)
FINAL_VALIDATION_MODE = "NORMAL"

# [전략 설정 트리거 슬롯]
# OZ_SYSTEM CONTROL [전략 설정]에서 입력한 최종 OZ 트리거(예: "무지성 브레이커 올존")가 있으면
# 그 값을, 없거나 위 코드 기본값과 같으면 위 코드 기본값을 그대로 사용합니다.
FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE, FINAL_TRIGGER_OVERRIDDEN = oz_profiles.special_final_profile(
    "SPECIAL3", FINAL_VALIDATION_MODE, FINAL_TRIGGER_MODE
)

# 최종 Telegram 알림 송출 거래시간 (KST, 세션 이름: "HHMM-HHMM")
# 전략 3 전용 시간이며 config의 MAIN_* 시간을 쓰지 않습니다.
# 24시간 송출하려면 아래 값을 0으로 변경:
# FINAL_ALERT_TIME_FILTERS = 0
FINAL_ALERT_TIME_FILTERS = {
    "MAIN_ASIA": "0900-1100",
    "MAIN_LONDON": "1600-1800",
    "MAIN_NEWYORK": "2100-2400",
}
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


MAIN_3_ALERT_TEMPLATE = (
    "[3. 장초반 추세 눌림 발생!] {side_icon} {side_text} 신호 발생\n"
    "──────────────────\n"
    "• 종목: {sym}\n"
    "• 위치: {trigger_name}\n"
    f"• 주기: {{b_tf}} {_final_trigger_alert_text()}\n"
    "• 지표: {indicators_text}\n"
    "• 현재 가격: {current_price:,.2f}\n"
    "──────────────────"
)

STRATEGY_NAME = "장초반 추세 눌림 발생!"
SYMBOLS = ("XAUUSD+", "NAS100")
CROSS_TFS = ("1m", "2m")
SETUP_FVG_TFS = ("5m", "6m")
FINAL_FVG_TOUCH_TFS = ("5m", "6m", "10m", "12m", "15m")
FINAL_OZ_TFS = ("1m", "2m")



def _final_alert_time_allowed(api) -> bool:
    """전략 3 최종 Telegram 송출시간만 검사합니다. 0이면 24시간 허용합니다."""
    if _time_slot is not None:
        _slot_allowed = _time_slot.final_alert_allowed(api, "SPECIAL3")
        if _slot_allowed is not None:
            return _slot_allowed
    if FINAL_ALERT_TIME_FILTERS == 0:
        return True
    if isinstance(FINAL_ALERT_TIME_FILTERS, dict):
        return _time_slot is not None and _time_slot.ranges_allowed(FINAL_ALERT_TIME_FILTERS)
    if isinstance(FINAL_ALERT_TIME_FILTERS, str):
        value = FINAL_ALERT_TIME_FILTERS.strip()
        filters = (value,) if value else ()
    else:
        filters = tuple(str(x).strip() for x in FINAL_ALERT_TIME_FILTERS if str(x).strip())
    return True if not filters else bool(api.time_allowed(filters))


def _event_watch_ids(event: dict) -> list[str]:
    ids = [str(x).strip() for x in (event.get("watch_ids") or ()) if str(x).strip()]
    one = str(event.get("watch_id") or "").strip()
    if one and one not in ids:
        ids.append(one)
    return ids


def _source_spec_ids(payload: dict) -> set[str]:
    ids: set[str] = set()
    raw_ids = payload.get("source_spec_ids")
    if isinstance(raw_ids, (list, tuple, set)):
        ids.update(str(x).strip() for x in raw_ids if str(x).strip())
    one = str(payload.get("source_spec_id") or "").strip()
    if one:
        ids.add(one)
    return ids


def _normalize_fvg_tf(value: object) -> str | None:
    """FVG 시간봉 문자열을 FINAL_FVG_TOUCH_TFS 중 하나로 정규화합니다."""
    raw = str(value or "").strip().lower().replace(" ", "")
    if not raw:
        return None

    # 정확한 TF 값은 그대로 허용합니다.
    for tf in FINAL_FVG_TOUCH_TFS:
        token = str(tf).lower()
        if raw == token:
            return token

    # "15m FVG"에서 "5m"을 잘못 잡지 않도록 숫자 경계를 확인합니다.
    match = re.search(r"(?<!\d)(5|6|10|12|15)(?:m|분봉)(?!\d)", raw)
    if not match:
        return None

    # 단독 문자열에서 임의의 OZ TF 등을 집지 않도록 FVG 문맥이 있는 값만 허용합니다.
    if "fvg" not in raw and "에프브이지" not in raw:
        return None
    return f"{match.group(1)}m"


def _fvg_touch_tf_from_payload(payload: object) -> str | None:
    """중첩 metadata까지 탐색하여 실제 최종 FVG touch TF를 읽습니다."""
    if not isinstance(payload, dict):
        return None

    direct_keys = (
        "final_fvg_touch_tf",
        "fvg_touch_tf",
        "final_touch_fvg_tf",
        "final_fvg_tf",
        "last_fvg_touch_tf",
        "last_touch_fvg_tf",
        "touched_fvg_tf",
    )
    nested_keys = (
        "final_fvg_touch",
        "fvg_touch",
        "final_touch_fvg",
        "last_fvg_touch",
        "last_touch_fvg",
        "touched_fvg",
    )
    tf_keys = ("tf", "timeframe", "source_tf", "fvg_tf", "touch_tf")

    seen: set[int] = set()

    def walk(obj: object, *, touch_context: bool = False, depth: int = 0) -> str | None:
        if depth > 8:
            return None
        if isinstance(obj, (dict, list, tuple, set)):
            marker = id(obj)
            if marker in seen:
                return None
            seen.add(marker)

        if isinstance(obj, dict):
            # 가장 신뢰도가 높은 전용 key를 먼저 확인합니다.
            for key in direct_keys:
                if key in obj:
                    tf = _normalize_fvg_tf(obj.get(key))
                    if tf:
                        return tf

            # FVG touch 객체 안에서는 tf/timeframe 같은 일반 key도 허용합니다.
            for key in nested_keys:
                if key not in obj:
                    continue
                item = obj.get(key)
                if isinstance(item, dict):
                    for tf_key in tf_keys:
                        if tf_key in item:
                            raw = item.get(tf_key)
                            tf = _normalize_fvg_tf(raw)
                            if tf:
                                return tf
                            # touch 문맥에서는 "5m" 같은 순수 TF도 허용합니다.
                            pure = str(raw or "").strip().lower().replace(" ", "")
                            if pure in FINAL_FVG_TOUCH_TFS:
                                return pure
                else:
                    tf = _normalize_fvg_tf(item)
                    if tf:
                        return tf
                    pure = str(item or "").strip().lower().replace(" ", "")
                    if pure in FINAL_FVG_TOUCH_TFS:
                        return pure

                tf = walk(item, touch_context=True, depth=depth + 1)
                if tf:
                    return tf

            # key 이름 자체에 FVG touch 의미가 있는 경우도 처리합니다.
            for key, value in obj.items():
                name = str(key).lower().replace(" ", "")
                is_touch_fvg_key = "fvg" in name and ("touch" in name or "touched" in name)
                is_fvg_tf_key = "fvg" in name and ("tf" in name or "timeframe" in name)
                if not (is_touch_fvg_key or is_fvg_tf_key):
                    continue

                tf = _normalize_fvg_tf(value)
                if tf:
                    return tf
                pure = str(value or "").strip().lower().replace(" ", "")
                if is_touch_fvg_key and pure in FINAL_FVG_TOUCH_TFS:
                    return pure
                tf = walk(value, touch_context=is_touch_fvg_key or touch_context, depth=depth + 1)
                if tf:
                    return tf

            # final/meta/context/payload 등 안쪽에 들어간 metadata를 끝까지 탐색합니다.
            for key, value in obj.items():
                name = str(key).lower().replace(" ", "")
                child_touch_context = touch_context or ("fvg" in name and "touch" in name)
                tf = walk(value, touch_context=child_touch_context, depth=depth + 1)
                if tf:
                    return tf
            return None

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                tf = walk(item, touch_context=touch_context, depth=depth + 1)
                if tf:
                    return tf
            return None

        if touch_context:
            pure = str(obj or "").strip().lower().replace(" ", "")
            if pure in FINAL_FVG_TOUCH_TFS:
                return pure
            return _normalize_fvg_tf(obj)

        # 일반 문자열은 "10m FVG"처럼 FVG가 명시된 경우에만 읽습니다.
        return _normalize_fvg_tf(obj)

    return walk(payload)


def _pure_fvg_tf(value: object) -> str | None:
    """5번 전략처럼 Watch에 보존된 원본 TF 값을 직접 읽기 위한 정규화 함수입니다."""
    raw = str(value or "").strip().lower().replace(" ", "")
    return raw if raw in FINAL_FVG_TOUCH_TFS else None


def _source_fvg_location(api, watch_ids: object, event: dict | None = None) -> str:
    """완료한 OZ Watch에 저장된 실제 FVG touch TF를 알림용 위치명으로 반환합니다.

    SPECIAL5의 _source_oz_location()과 같은 방식으로, 최종 알림 직전에
    active Watch를 직접 읽어 원본 TF를 먼저 확보합니다.
    """
    ids = [str(x).strip() for x in (watch_ids or ()) if str(x).strip()]

    # 공용 timed-chain 런타임에서 사용할 수 있는 원본/touch TF key를 우선 확인합니다.
    # source_tf/tf는 최종 OZ가 1m/2m이면 FINAL_FVG_TOUCH_TFS에 들어오지 않으므로
    # 5m/6m/10m/12m/15m인 경우에만 FVG 원본 TF로 인정합니다.
    candidate_keys = (
        "source_fvg_tf",
        "final_fvg_touch_tf",
        "fvg_touch_tf",
        "final_touch_fvg_tf",
        "last_fvg_touch_tf",
        "last_touch_fvg_tf",
        "touched_fvg_tf",
        "source_touch_tf",
        "trigger_tf",
        "touch_tf",
        "source_tf",
        "tf",
    )

    for watch_id in ids:
        item = api.get_oz_watch(watch_id)
        if not isinstance(item, dict):
            continue

        for key in candidate_keys:
            tf = _pure_fvg_tf(item.get(key))
            if tf:
                return f"{int(tf[:-1])}분봉 FVG"

        tf = _fvg_touch_tf_from_payload(item)
        if tf:
            return f"{int(tf[:-1])}분봉 FVG"

    if isinstance(event, dict):
        for key in candidate_keys:
            tf = _pure_fvg_tf(event.get(key))
            if tf:
                return f"{int(tf[:-1])}분봉 FVG"

        tf = _fvg_touch_tf_from_payload(event)
        if tf:
            return f"{int(tf[:-1])}분봉 FVG"

    return "FVG"


class Special3EventHandler:
    """SPECIAL3 FINAL_ALERT의 송출시간 필터와 FVG 위치 표시만 담당합니다."""

    def __init__(self, api, spec_id: str) -> None:
        self.api = api
        self.spec_id = str(spec_id)

    def handle_oz_event(self, event: dict) -> dict | None:
        if not isinstance(event, dict):
            return None
        if str(event.get("kind") or "FINAL_ALERT").upper() != "FINAL_ALERT":
            return None

        matched_payloads: list[dict] = []
        related_payloads: list[dict] = []
        for watch_id in _event_watch_ids(event):
            item = self.api.get_oz_watch(watch_id)
            if not isinstance(item, dict):
                continue
            related_payloads.append(item)
            if self.spec_id in _source_spec_ids(item):
                matched_payloads.append(item)

        # source spec이 확인되는 watch가 있으면 그것들을 우선 사용합니다.
        # 여러 watch_id가 있을 수 있으므로 첫 번째에서 멈추지 않습니다.
        payloads: list[dict] = matched_payloads or related_payloads

        # Watch metadata가 사라진 예외 상황에서는 이벤트 자체의 source spec을 확인합니다.
        if not payloads and self.spec_id in _source_spec_ids(event):
            payloads = [event]
        if not payloads:
            return None

        if not _final_alert_time_allowed(self.api):
            logging.info(
                "⏸️ [SPECIAL3] 최종 알림 거래시간 필터 밖 | symbol=%s | spec=%s",
                str(event.get("symbol") or "-") or "-",
                self.spec_id,
            )
            return {
                "ok": True,
                "delivered": True,
                "suppressed": True,
                "special3_gate": "final_alert_time_filter",
            }

        # SPECIAL5와 같은 방식으로 core 전달 전에 위치 TF를 확보하고
        # 이 전략의 템플릿을 여기서 다시 렌더링합니다.
        # event에 기존 message가 이미 "FVG"로 렌더링되어 있어도 새 위치명이 반영됩니다.
        watch_ids = _event_watch_ids(event)
        source_location = _source_fvg_location(self.api, watch_ids, event)
        forwarded = dict(event)
        forwarded["trigger_name"] = source_location

        alert_spec = SimpleNamespace(
            spec_id=self.spec_id,
            name=STRATEGY_NAME,
            symbol=str(event.get("symbol") or ""),
            validation_mode=FINAL_VALIDATION_MODE,
            trigger_mode=FINAL_TRIGGER_MODE,
            alert_template=MAIN_3_ALERT_TEMPLATE,
        )
        fallback = str(forwarded.get("message") or "").strip()
        forwarded["message"] = self.api.render_oz_alert(alert_spec, forwarded, fallback)
        return self.api.deliver_oz_event_core(forwarded)


def _spec_id(symbol: str, cross_tf: str) -> str:
    """기존 1m spec_id는 유지하고 신규 2m branch만 별도 ID를 부여합니다."""
    if cross_tf == "1m":
        return "PIPELINE_3" if symbol == "XAUUSD+" else f"PIPELINE_3@{symbol}"
    return f"PIPELINE_3@{cross_tf}" if symbol == "XAUUSD+" else f"PIPELINE_3@{symbol}@{cross_tf}"


def _main_3_timed_chain(symbol: str, cross_tf: str) -> dict:
    """cross_tf EMA50/200 cross <-> 5m/6m 신규 FVG -> FVG touch -> 1m OR 2m OZ."""
    return {
        "spec_id": _spec_id(symbol, cross_tf),
        "name": f"MAIN_3_{cross_tf.upper()}_EMA50_200_5M6M_PAIR_TO_FVG_TOUCH_1M_OR_2M_OZ",
        "symbol": symbol,
        "cross_tf": cross_tf,
        "ma_family": "EMA",
        "fast_period": 50,
        "slow_period": 200,
        "fvg_tfs": SETUP_FVG_TFS,
        "order_mode": "UNORDERED",
        "max_gap_sec": 1800,
        "max_gap_bars": 0,
        "final_window_sec": 7200,
        "oz_tfs": FINAL_OZ_TFS,
        "final_fvg_touch_tfs": FINAL_FVG_TOUCH_TFS,
        "validation_mode": FINAL_VALIDATION_MODE,
        "trigger_mode": FINAL_TRIGGER_MODE,
        "time_filters": (),
        "final_time_filters": (),
        "cancel_on_opposite_cross": True,
        "cancel_on_opposite_fvg_tfs": SETUP_FVG_TFS,
        "alert_template": MAIN_3_ALERT_TEMPLATE,
        "enabled": True,
    }


def register(manager) -> None:
    """manager_KIM의 공식 전략 registry에 메인전략 3의 1m/2m OR branch를 등록합니다."""
    manager.register_special_bundle(
        strategies=(),
        timed_chains=tuple(
            _main_3_timed_chain(symbol, cross_tf)
            for symbol in SYMBOLS
            for cross_tf in CROSS_TFS
        ),
    )

    api = getattr(manager, "special_api", None)
    if api is None:
        raise RuntimeError("manager_KIM의 SPECIAL Plugin API를 사용할 수 없습니다")

    for symbol in SYMBOLS:
        for cross_tf in CROSS_TFS:
            spec_id = _spec_id(symbol, cross_tf)
            api.register_oz_event_handler(spec_id, Special3EventHandler(api, spec_id))
