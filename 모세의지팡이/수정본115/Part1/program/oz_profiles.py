# -*- coding: utf-8 -*-
"""OZ 공용 어휘: NORMAL/BLIND × OZ/BREAKER의 네 프로필.

BREAKER는 TRUE B0의 엄격한 돌파 조건입니다.
프로필의 실행·입력·저장은 이 모듈의 현재 어휘만 사용합니다.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Mapping, Optional

VALIDATION_MODES = frozenset({"NORMAL", "BLIND"})
TRIGGER_FLAG_ORDER = ("BREAKER",)
TRIGGER_FLAGS = frozenset(TRIGGER_FLAG_ORDER)
TRIGGER_MODES = frozenset({"OZ", "BREAKER"})
PROFILE_KEYS = (("NORMAL", "OZ"), ("BLIND", "OZ"),
                ("NORMAL", "BREAKER"), ("BLIND", "BREAKER"))
# Runtime arbitration order remains NORMAL before BLIND, as in revision47.
EVALUATION_PROFILE_KEYS = (("NORMAL", "OZ"), ("NORMAL", "BREAKER"),
                           ("BLIND", "OZ"), ("BLIND", "BREAKER"))


class ProfileError(ValueError):
    """현재 프로필 문법에 맞지 않는 설정입니다."""


def canonical_trigger_mode(flags: Iterable[str]) -> str:
    chosen = {str(x).strip().upper() for x in flags if str(x).strip()}
    if chosen - TRIGGER_FLAGS:
        raise ProfileError(f"알 수 없는 OZ 트리거 수식어: {','.join(sorted(chosen - TRIGGER_FLAGS))}")
    return "BREAKER" if chosen else "OZ"


def trigger_flags(trigger_mode: object) -> Optional[frozenset[str]]:
    raw = str(trigger_mode or "").strip().upper()
    if not raw:
        return None
    if raw == "OZ":
        return frozenset()
    parts = [p for p in re.split(r"[_+\-]", raw) if p and p != "OZ"]
    if parts == ["BREAKER"]:
        return frozenset({"BREAKER"})
    return None


def has_flag(trigger_mode: object, flag: str) -> bool:
    flags = trigger_flags(trigger_mode)
    return bool(flags) and str(flag).upper() in flags


def normalize_profile(validation_mode: object = None, trigger_mode: object = None,
                      oz_mode: object = None) -> tuple[str, str]:
    """현재 네 프로필만 검증합니다. 생략한 필드에는 기본값을 사용합니다."""
    vm = str(validation_mode or "").strip().upper()
    tm = str(trigger_mode or "").strip().upper()
    if oz_mode is not None:
        raise ProfileError("OZ 프로필은 validation_mode와 trigger_mode로 지정하세요")
    vm, tm = vm or "NORMAL", tm or "OZ"
    if vm not in VALIDATION_MODES:
        raise ProfileError(f"알 수 없는 OZ 검증 모드: {vm!r}")
    if tm not in TRIGGER_MODES:
        raise ProfileError(f"알 수 없는 OZ 트리거: {tm!r}")
    return vm, tm


def profile_label(validation_mode: object, trigger_mode: object) -> str:
    vm, tm = normalize_profile(validation_mode, trigger_mode)
    return ("무지성 " if vm == "BLIND" else "") + ("브레이커 " if tm == "BREAKER" else "") + "올존"


PROFILE_WORDS = {"무지성": "BLIND", "브레이커": "BREAKER", "일반": "NORMAL"}
PROFILE_WORD_PATTERN = "|".join(sorted(PROFILE_WORDS, key=len, reverse=True))


def text_validation_mode(text: object) -> str:
    return "BLIND" if "무지성" in str(text or "").lower() else "NORMAL"


def text_trigger_mode(text: object) -> str:
    """OZ 명사구를 현재 어휘로 파싱합니다. 미등록 수식어는 일반 문법 오류입니다."""
    raw = str(text or "")
    modes = []
    for match in re.finditer(r"올존|\bOZ\b", raw, re.I):
        prefix = raw[:match.start()]
        # 시간봉/조건 연결어는 명사구의 경계이며 프로필 수식어가 아닙니다.
        boundary = list(re.finditer(
            r"\d+\s*(?:분|시간|일|[mhd])(?:봉)?(?:\s*(?:이하|이상|미만|초과))?"
            r"|(?:조건에서|조건이면|생기면|성립하면|발생하면|터치하면|이후|동안|그러면|다음|중에|중|후)\s*|[→>,;:]", prefix, re.I))
        phrase = prefix[boundary[-1].end():] if boundary else prefix
        phrase = re.sub(r"^(?:\s*(?:매수|매도|하단|상단|같은방향|동방향|모든프레임|모든프레임에서|에서|의))+", "", phrase)
        # 종목만 지정한 명령도 공용 명령 정규화의 표준 종목 표기를 받습니다.
        if not boundary:
            phrase = re.sub(r"^\s*(?:골드|금|나스닥|비트코인|비트|XAUUSD\+?|NAS100|BTCUSD)\s*", "", phrase, flags=re.I)
        modes.append(parse_profile_text(phrase + "올존")[1])
    if len(set(modes)) > 1:
        raise ProfileError("한 명령의 OZ 트리거를 하나로 지정하세요")
    return modes[-1] if modes else "OZ"


def parse_profile_text(text: object) -> tuple[str, str]:
    """현재 네 UI 프로필만 허용합니다."""
    raw = str(text or "").strip()
    if not raw:
        raise ProfileError("트리거가 비어 있습니다")
    if raw.upper() in TRIGGER_MODES:
        return normalize_profile(trigger_mode=raw)
    rest = re.sub(r"\s+", "", raw).lower()
    if rest.endswith("올존"):
        rest = rest[:-2]
    elif rest.endswith("oz"):
        rest = rest[:-2]
    else:
        raise ProfileError(f"트리거는 '올존' 또는 'OZ'로 끝나야 합니다: {raw}")
    vocabulary = {**PROFILE_WORDS, "breaker": "BREAKER"}
    words = []
    while rest:
        for word in sorted(vocabulary, key=len, reverse=True):
            if rest.startswith(word):
                words.append(vocabulary[word]); rest = rest[len(word):]; break
        else:
            raise ProfileError(f"알 수 없는 트리거 단어: {rest!r}. 올존/무지성 올존/브레이커 올존/무지성 브레이커 올존만 지원합니다.")
    if len(words) != len(set(words)):
        raise ProfileError(f"같은 OZ 수식어가 두 번 들어 있습니다: {raw}")
    if "NORMAL" in words and "BLIND" in words:
        raise ProfileError("'일반'과 '무지성'은 함께 쓸 수 없습니다")
    return ("BLIND" if "BLIND" in words else "NORMAL", "BREAKER" if "BREAKER" in words else "OZ")


# -----------------------------------------------------------------------------
# SPECIAL 최종 OZ 트리거 슬롯
# -----------------------------------------------------------------------------
# OZ_SYSTEM CONTROL [전략 설정] -> 환경변수 OZ_SPECIAL_TRIGGERS(JSON) -> manager_KIM
# -> set_special_trigger_overrides() -> 각 SPECIAL이 special_final_profile()로 조회.
SPECIAL_TRIGGERS_ENV = "OZ_SPECIAL_TRIGGERS"
_SPECIAL_TRIGGER_OVERRIDES: dict[str, tuple[str, str, str]] = {}
_SPECIAL_TRIGGER_ERRORS: dict[str, str] = {}


def parse_special_trigger_json(raw: object) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{SPECIAL_TRIGGERS_ENV}는 JSON 객체여야 합니다")
    return {str(k).strip().upper(): str(v).strip() for k, v in data.items() if str(k).strip()}


def set_special_trigger_overrides(mapping: Mapping[str, str]) -> dict[str, str]:
    """SPECIAL별 트리거 문구를 등록합니다. 해석 실패 항목은 등록하지 않고 오류를 돌려줍니다."""
    errors: dict[str, str] = {}
    parsed: dict[str, tuple[str, str, str]] = {}
    for name, text in dict(mapping or {}).items():
        key = str(name or "").strip().upper()
        if not key:
            continue
        try:
            vm, tm = parse_profile_text(text)
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        parsed[key] = (vm, tm, profile_label(vm, tm))
    _SPECIAL_TRIGGER_ERRORS.clear()
    _SPECIAL_TRIGGER_ERRORS.update(errors)
    _SPECIAL_TRIGGER_OVERRIDES.clear()
    _SPECIAL_TRIGGER_OVERRIDES.update(parsed)
    return errors


def special_trigger_overrides() -> dict[str, tuple[str, str, str]]:
    return dict(_SPECIAL_TRIGGER_OVERRIDES)


def special_final_profile(name: str, default_validation_mode: str, default_trigger_mode: str) -> tuple[str, str, bool]:
    """SPECIAL 최종 OZ 프로필. 슬롯 값이 없거나 코드 기본값과 같으면 코드 기본값을 그대로 씁니다."""
    key = str(name or "").strip().upper()
    if key in _SPECIAL_TRIGGER_ERRORS:
        raise ProfileError(f"{key}: {_SPECIAL_TRIGGER_ERRORS[key]}")
    default = normalize_profile(default_validation_mode, default_trigger_mode)
    item = _SPECIAL_TRIGGER_OVERRIDES.get(str(name or "").strip().upper())
    if item is None:
        return default[0], default[1], False
    vm, tm, _text = item
    if (vm, tm) == default:
        return default[0], default[1], False
    return vm, tm, True


def normalize_watch_payload(payload: Mapping) -> dict:
    """실행용 Watch의 프로필을 검증하고 나머지 메타데이터는 유지합니다."""
    item = dict(payload)
    if any(key in item for key in ("oz_mode", "trigger_type")):
        raise ProfileError("OZ 프로필은 validation_mode와 trigger_mode로 지정하세요")
    vm, tm = normalize_profile(item.get("validation_mode"), item.get("trigger_mode"))
    item["validation_mode"], item["trigger_mode"] = vm, tm
    return item


def canonical_profile_text(text: object) -> str:
    return profile_label(*parse_profile_text(text))


def normalize_special_settings(settings: Mapping) -> dict:
    """Atomic save/start validation. None alone means the code default."""
    result = {}
    errors = []
    for name, raw in dict(settings or {}).items():
        item = dict(raw)
        if item.get("load_error"):
            raise ProfileError(f"{name}: 불러오지 못한 설정입니다. 현재 프로필을 선택한 후 저장하세요")
        unknown = set(item) - {'enabled', 'trigger', 'time_filters'}
        if unknown:
            raise ProfileError(f"{name}: 알 수 없는 전략 설정 필드: {', '.join(sorted(unknown))}")
        text = item.get('trigger')
        if text is not None:
            try:
                item['trigger'] = canonical_profile_text(text)
            except (ValueError, TypeError) as exc:
                errors.append(f'{name}.trigger={text!r}: {exc}')
        result[str(name)] = item
    if errors:
        raise ProfileError('지원하지 않는 저장 OZ 설정 (자동 대체/저장하지 않음):\n' + '\n'.join(errors))
    return result
