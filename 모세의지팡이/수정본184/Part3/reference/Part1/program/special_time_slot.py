# -*- coding: utf-8 -*-
"""SPECIAL 최종 알림 거래시간 슬롯.

트리거 슬롯(oz_profiles.special_final_profile)과 같은 방식이다.
- 설정이 없으면 None을 돌려주고, 각 SPECIAL은 코드에 적힌 기존 거래시간 판정을 그대로 쓴다.
- 설정이 있으면 그 전략의 최종 알림 송출 시간만 이 설정으로 판정한다.
  setup 생성·감시·연쇄 조건의 시간 필터는 바꾸지 않는다.

설정 형식(JSON 객체, 환경변수 OZ_SPECIAL_TIME_FILTERS 또는 set_special_time_overrides):
{
  "SPECIAL1": {
    "MAIN_ASIA":    {"enabled": true,  "start": "09:00", "end": "15:30"},
    "MAIN_LONDON":  {"enabled": false, "start": "16:00", "end": "20:00"},
    "MAIN_NEWYORK": {"enabled": true}
  },
  "SPECIAL9": {}
}
- 세션 이름은 기존 config 키(MAIN_ASIA 등)를 쓴다.
- start/end가 없으면 기존 config 값(api.config_get(세션 이름), "HHMM-HHMM")을 쓴다.
- enabled가 false인 세션의 시간값은 보존만 하고 판정에는 쓰지 않는다.
- 전략 항목이 있는데 사용 중인 세션이 하나도 없으면 그 전략의 최종 알림은 없다.
  (설정 없음 = 코드 기본값, 전부 해제 = 알림 없음. 두 의미를 구분한다.)
- 시각 판정은 기존 TimePolicy와 같다: KST, 시작·종료 포함, 시작 > 종료면 자정 통과.
  시각은 domain_clock을 쓰므로 재생 중에는 재생 시각으로 판정한다.
"""
from __future__ import annotations

import json
import os
from typing import Mapping, Optional

from domain_clock import datetime as _dt

SPECIAL_TIME_ENV = "OZ_SPECIAL_TIME_FILTERS"
_KST = _dt.timezone(_dt.timedelta(hours=9))

# 전략 이름 -> {세션 이름: (사용 여부, "HHMM" 또는 None, "HHMM" 또는 None)}
_OVERRIDES: dict[str, dict[str, tuple[bool, Optional[str], Optional[str]]]] = {}
_EXPLICIT = False


def _hhmm(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().replace(":", "")
    if not text:
        return None
    if len(text) != 4 or not text.isdigit():
        raise ValueError(f"시각은 HH:MM 또는 HHMM 형식이어야 합니다: {value!r}")
    hh, mm = int(text[:2]), int(text[2:])
    if text == "2400":
        return text
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"시각 범위 오류: {value!r}")
    return text


def parse_special_time_json(raw: object) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{SPECIAL_TIME_ENV}는 JSON 객체여야 합니다")
    return data


def set_special_time_overrides(mapping: Mapping[str, object]) -> dict[str, str]:
    """전략별 거래시간 설정을 등록한다. 형식이 틀린 전략은 등록하지 않고 오류를 돌려준다."""
    global _EXPLICIT
    errors: dict[str, str] = {}
    parsed: dict[str, dict[str, tuple[bool, Optional[str], Optional[str]]]] = {}
    for name, sessions in dict(mapping or {}).items():
        key = str(name or "").strip().upper()
        if not key:
            continue
        try:
            if not isinstance(sessions, dict):
                raise ValueError("전략 값은 세션 객체여야 합니다")
            item: dict[str, tuple[bool, Optional[str], Optional[str]]] = {}
            for session, conf in sessions.items():
                sname = str(session or "").strip().upper()
                if not sname:
                    continue
                if isinstance(conf, bool):
                    conf = {"enabled": conf}
                if not isinstance(conf, dict):
                    raise ValueError(f"{sname} 값은 객체여야 합니다")
                item[sname] = (bool(conf.get("enabled", True)), _hhmm(conf.get("start")), _hhmm(conf.get("end")))
            parsed[key] = item
        except (ValueError, TypeError) as exc:
            errors[key] = str(exc)
    _OVERRIDES.clear()
    _OVERRIDES.update(parsed)
    _EXPLICIT = True
    return errors


def _ensure_loaded() -> None:
    global _EXPLICIT
    if _EXPLICIT:
        return
    raw = os.environ.get(SPECIAL_TIME_ENV, "")
    if raw.strip():
        set_special_time_overrides(parse_special_time_json(raw))
    _EXPLICIT = True


def special_time_overrides() -> dict:
    _ensure_loaded()
    return {k: dict(v) for k, v in _OVERRIDES.items()}


def _inside(now_hm: str, start: str, end: str) -> bool:
    # 기존 TimePolicy._inside와 같은 판정
    if start <= end:
        return start <= now_hm <= end
    return now_hm >= start or now_hm <= end


def _range_from_config(api, session: str) -> Optional[tuple[str, str]]:
    getter = getattr(api, "config_get", None)
    raw = getter(session, "") if callable(getter) else ""
    value = str(raw or "").strip().replace(":", "")
    if "-" not in value:
        return None
    start, end = [x.strip() for x in value.split("-", 1)]
    if len(start) != 4 or len(end) != 4 or not start.isdigit() or not end.isdigit():
        return None
    return start, end


def ranges_allowed(ranges: Mapping[str, object]) -> bool:
    """코드 기본값이 {세션 이름: "HHMM-HHMM"}인 전략의 최종 알림 허용 여부."""
    now_hm = _dt.datetime.now(_KST).strftime("%H%M")
    for raw in dict(ranges or {}).values():
        value = str(raw or "").strip().replace(":", "")
        if "-" not in value:
            continue
        start, end = (_hhmm(x) for x in value.split("-", 1))
        if start and end and _inside(now_hm, start, end):
            return True
    return False


def final_alert_allowed(api, name: str) -> Optional[bool]:
    """설정이 없으면 None(코드 기본값 사용), 있으면 허용 여부를 돌려준다."""
    _ensure_loaded()
    sessions = _OVERRIDES.get(str(name or "").strip().upper())
    if sessions is None:
        return None
    now_hm = _dt.datetime.now(_KST).strftime("%H%M")
    for session, (enabled, start, end) in sessions.items():
        if not enabled:
            continue
        if start is None or end is None:
            base = _range_from_config(api, session)
            if base is None:
                continue
            start = start or base[0]
            end = end or base[1]
        if _inside(now_hm, start, end):
            return True
    return False
