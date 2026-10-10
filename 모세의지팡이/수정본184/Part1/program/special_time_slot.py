# -*- coding: utf-8 -*-
"""거래시간 판정 — 모든 전략·WATCH가 쓰는 하나의 규칙.

거래시간은 최종 알림이 나가는 순간에만 판정한다. 준비 단계(조건·감시)는 시간과 상관없이
진행하고, 알림 순간이 그 전략이 정한 거래시간 안이면 보낸다. 시간 밖이면 그 알림만 버린다.

거래시간 값의 모양(레시피·화면 설정·KIM 명령이 쓰는 그대로):
- 없음(None, 0, 빈 목록): 시간 제한 없음(24시간).
- 목록: 세션 이름(config의 MAIN_ASIA 등), "HHMM-HHMM" 시간 구간, ALL/ANYTIME/NONE(24시간).
  하나라도 지금 시각을 포함하면 허용.
- 세션별 설정(화면 설정 형식):
  {"MAIN_ASIA": {"enabled": true, "start": "09:00", "end": "15:30"}, "MAIN_LONDON": {"enabled": false}}
  사용 중인 세션이 하나도 없으면 24시간. start/end가 없으면 config 값("HHMM-HHMM")을 쓴다.
  세션 값이 "HHMM-HHMM" 문자열이면 그 구간을 쓴다.
- 시각 없이 세션 이름만 있으면 지금의 주요 거래시간(config MAIN_*)을 쓴다. "아시아장만 알려줘" 같은
  명령의 말이 이 경로다. 기본 스페셜과 화면의 전략별 설정은 자기 시각을 직접 가진다.
- 시각 판정: KST, 시작·종료 포함, 시작 > 종료면 자정 통과.
  시각은 domain_clock을 쓰므로 재생 중에는 재생 시각으로 판정한다.

화면의 전략별 거래시간 설정은 환경변수 OZ_SPECIAL_TIME_FILTERS(JSON 객체)로 전달된다.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping, Optional

from domain_clock import datetime as _dt

SPECIAL_TIME_ENV = "OZ_SPECIAL_TIME_FILTERS"
_KST = _dt.timezone(_dt.timedelta(hours=9))
_ANYTIME = {"ALL", "ANYTIME", "NONE"}


def _hhmm(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().replace(":", "")
    if not text:
        return None
    if len(text) != 4 or not text.isdigit():
        raise ValueError("시각은 00:00~24:00 범위로 입력해 주세요.")
    hh, mm = int(text[:2]), int(text[2:])
    if text == "2400":
        return text
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("시각은 00:00~24:00 범위로 입력해 주세요.")
    return text


def parse_special_time_json(raw: object) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{SPECIAL_TIME_ENV}는 JSON 객체여야 합니다")
    return data


def special_time_errors(mapping: Mapping[str, object]) -> dict[str, str]:
    """화면의 전략별 거래시간 설정을 검사한다. 형식이 틀린 전략과 이유를 돌려준다."""
    errors: dict[str, str] = {}
    for name, sessions in dict(mapping or {}).items():
        key = str(name or "").strip().upper()
        if not key:
            continue
        try:
            if not isinstance(sessions, dict):
                raise ValueError("전략 값은 세션 객체여야 합니다")
            for session, conf in sessions.items():
                sname = str(session or "").strip().upper()
                if not sname:
                    continue
                if isinstance(conf, bool):
                    continue
                if not isinstance(conf, dict):
                    raise ValueError(f"{sname} 값은 객체여야 합니다")
                _hhmm(conf.get("start")), _hhmm(conf.get("end"))
        except (ValueError, TypeError) as exc:
            errors[key] = str(exc)
    return errors


def _inside(now_hm: str, start: str, end: str) -> bool:
    if start <= end:
        return start <= now_hm <= end
    return now_hm >= start or now_hm <= end


def _range_text(raw: object) -> Optional[tuple[str, str]]:
    value = str(raw or "").strip().replace(":", "")
    if "-" not in value:
        return None
    start, end = [x.strip() for x in value.split("-", 1)]
    if len(start) != 4 or len(end) != 4 or not start.isdigit() or not end.isdigit():
        return None
    return start, end


def _session_selected(value) -> bool:
    if isinstance(value, str):return True
    if isinstance(value, bool):return value
    return bool(value.get('enabled', True))


def trading_time_allowed(filters, config_get: Callable[..., object]) -> bool:
    """지금 시각이 거래시간 안인가. 모든 전략·WATCH가 같은 이 함수로 판정한다."""
    if filters is None or (type(filters) is int and filters == 0):
        return True
    ranges: list[Optional[tuple[str, str]]] = []
    if isinstance(filters, dict):
        if not any(_session_selected(value) for value in filters.values()):
            return True
        for session, value in filters.items():
            if isinstance(value, str):
                ranges.append(_range_text(value))
                continue
            if isinstance(value, bool):
                value = {"enabled": value}
            if not value.get("enabled", True):
                continue
            start, end = _hhmm(value.get("start")), _hhmm(value.get("end"))
            if start is None or end is None:
                base = _range_text(config_get(session, ""))
                if base is None:
                    continue
                start, end = start or base[0], end or base[1]
            ranges.append((start, end))
    else:
        items = [str(x).strip() for x in filters if str(x).strip()]
        if not items:
            return True
        for item in items:
            key = item.upper()
            if key in _ANYTIME:
                return True
            literal = _range_text(item)
            ranges.append(literal if literal is not None else _range_text(config_get(key, "")))
    now_hm = _dt.datetime.now(_KST).strftime("%H%M")
    return any(r is not None and _inside(now_hm, *r) for r in ranges)


def session_filters_allowed(api, filters) -> bool:
    """레시피 전략용: 전략의 config 접근(api.config_get)으로 같은 규칙을 적용한다."""
    return trading_time_allowed(filters, api.config_get)


def strategy_trading_time(meaning: Mapping[str, object]):
    """레시피 전략의 거래시간: 최종 알림 시간이 있으면 그것, 없으면(0) 감시 단계에 적힌 시간."""
    final = meaning.get("final_time_filters")
    if final is None or (type(final) is int and final == 0):
        return meaning.get("time_filters")
    return final
