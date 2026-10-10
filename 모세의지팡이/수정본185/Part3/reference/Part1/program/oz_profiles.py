# -*- coding: utf-8 -*-
"""OZ(올리브오일 존, 올존) 프로필 공용 어휘.

이 모듈은 계산을 하지 않고 OZ 프로필 이름만 정의합니다.
monitor_OZ / manager_KIM / watch_orchestrator / command_interpreter / SPECIAL /
OZ_SYSTEM CONTROL이 같은 이름 규칙을 쓰도록 하는 단일 정의입니다.

프로필 = validation_mode + trigger_mode

validation_mode
  NORMAL : 일반 올존 (중위 OUT + 중위 HMA6 시가 조건 + 상위 IN 검증)
  BLIND  : 무지성 (상위/중위 검증 없이 기본조건만)

trigger_mode = 아래 수식어를 자유롭게 조합한 공식 문자열
  (수식어 없음)  -> "OZ"
  BREAKER     : 최종 트리거를 B0 엄격 돌파만 허용
  REGIME         : 상위 프레임 레짐밴드 중심선 기울기·위치 필터
  SUPER          : 기준 프레임 레짐밴드 중심선 교차 필터
  공식 문자열은 항상 BREAKER -> REGIME -> SUPER 순서로 "_" 연결합니다.
  예) BREAKER_REGIME, REGIME_SUPER, BREAKER_REGIME_SUPER

사용자 입력 순서는 자유입니다. "레짐 무지성 브레이커 올존"과
"무지성 브레이커 레짐 올존"은 같은 프로필입니다.
"""
from __future__ import annotations

import itertools
import json
from typing import Iterable, Mapping, Optional

VALIDATION_MODES = frozenset({"NORMAL", "BLIND"})
TRIGGER_FLAG_ORDER = ("BREAKER", "REGIME", "SUPER")
TRIGGER_FLAGS = frozenset(TRIGGER_FLAG_ORDER)


def canonical_trigger_mode(flags: Iterable[str]) -> str:
    """수식어 집합을 공식 trigger_mode 문자열로 바꿉니다."""
    chosen = {str(x).strip().upper() for x in flags if str(x).strip()}
    unknown = chosen - TRIGGER_FLAGS
    if unknown:
        raise ValueError(f"알 수 없는 OZ 트리거 수식어: {','.join(sorted(unknown))}")
    ordered = [flag for flag in TRIGGER_FLAG_ORDER if flag in chosen]
    return "_".join(ordered) if ordered else "OZ"


def trigger_flags(trigger_mode: object) -> Optional[frozenset[str]]:
    """공식/비공식 trigger_mode 문자열의 수식어 집합. 해석 불가면 None."""
    raw = str(trigger_mode or "").strip().upper()
    if not raw:
        return None
    if raw == "OZ":
        return frozenset()
    parts = [p for p in raw.replace("-", "_").replace("+", "_").split("_") if p]
    if not parts:
        return None
    flags = set()
    for part in parts:
        if part == "OZ":
            continue
        if part not in TRIGGER_FLAGS or part in flags:
            return None
        flags.add(part)
    return frozenset(flags)


TRIGGER_MODES = frozenset(
    canonical_trigger_mode(combo)
    for n in range(len(TRIGGER_FLAG_ORDER) + 1)
    for combo in itertools.combinations(TRIGGER_FLAG_ORDER, n)
)


# 기존 6개 프로필 순서를 먼저 유지하고, 새 조합을 뒤에 붙입니다.
_LEGACY_PROFILE_KEYS = (
    ("NORMAL", "OZ"),
    ("BLIND", "OZ"),
    ("NORMAL", "BREAKER"),
    ("BLIND", "BREAKER"),
    ("NORMAL", "REGIME"),
    ("NORMAL", "BREAKER_REGIME"),
)
PROFILE_KEYS: tuple[tuple[str, str], ...] = _LEGACY_PROFILE_KEYS + tuple(
    (vm, canonical_trigger_mode(combo))
    for n in range(len(TRIGGER_FLAG_ORDER) + 1)
    for combo in itertools.combinations(TRIGGER_FLAG_ORDER, n)
    for vm in ("NORMAL", "BLIND")
    if (vm, canonical_trigger_mode(combo)) not in _LEGACY_PROFILE_KEYS
)


def has_flag(trigger_mode: object, flag: str) -> bool:
    flags = trigger_flags(trigger_mode)
    return bool(flags) and str(flag).upper() in flags


def normalize_profile(
    validation_mode: object = None,
    trigger_mode: object = None,
    legacy_oz_mode: object = None,
) -> tuple[str, str]:
    """프로필을 공식 (validation_mode, trigger_mode)로 정규화합니다.

    v1 oz_mode 호환:
      NORMAL -> NORMAL+OZ, BLIND -> BLIND+OZ, BREAKER -> NORMAL+BREAKER,
      BLIND_BREAKER -> BLIND+BREAKER, REGIME -> NORMAL+REGIME,
      BREAKER_REGIME -> NORMAL+BREAKER_REGIME
    알 수 없는 값은 기존과 같이 NORMAL / OZ로 둡니다.
    """
    vm = str(validation_mode or "").strip().upper()
    tm = str(trigger_mode or "").strip().upper()
    legacy = str(legacy_oz_mode or "").strip().upper()

    if legacy:
        if legacy == "NORMAL":
            vm, tm = vm or "NORMAL", tm or "OZ"
        elif legacy == "BLIND":
            vm, tm = vm or "BLIND", tm or "OZ"
        elif legacy in {"BLIND_BREAKER", "BREAKER_BLIND"}:
            vm, tm = vm or "BLIND", tm or "BREAKER"
        elif trigger_flags(legacy) is not None:
            vm, tm = vm or "NORMAL", tm or legacy

    if vm not in VALIDATION_MODES:
        vm = "NORMAL"
    flags = trigger_flags(tm)
    tm = canonical_trigger_mode(flags) if flags is not None else "OZ"
    return vm, tm


def profile_label(validation_mode: object, trigger_mode: object) -> str:
    """사용자 표시명. 기존 6개 프로필의 표시명은 그대로입니다."""
    vm, tm = normalize_profile(validation_mode, trigger_mode)
    flags = trigger_flags(tm) or frozenset()
    words = []
    if vm == "BLIND":
        words.append("무지성")
    if "BREAKER" in flags:
        words.append("브레이커")
    if "REGIME" in flags:
        words.append("레짐")
    if "SUPER" in flags:
        words.append("슈퍼")
    words.append("올존")
    return " ".join(words)


# 자연어 키워드. 순서 무관.
PROFILE_WORDS = {
    "무지성": "BLIND",
    "브레이커": "BREAKER",
    "레짐": "REGIME",
    "슈퍼": "SUPER",
    "일반": "NORMAL",
}
PROFILE_WORD_PATTERN = "|".join(sorted(PROFILE_WORDS, key=len, reverse=True))


def text_validation_mode(text: object) -> str:
    return "BLIND" if "무지성" in str(text or "").lower() else "NORMAL"


def text_trigger_mode(text: object) -> str:
    """Watch 자연어 안의 수식어로 trigger_mode를 만듭니다."""
    low = str(text or "").lower()
    flags = [flag for word, flag in PROFILE_WORDS.items() if flag in TRIGGER_FLAGS and word in low]
    return canonical_trigger_mode(flags)


def parse_profile_text(text: object) -> tuple[str, str]:
    """트리거 입력칸 문구를 엄격하게 해석합니다.

    허용: 일반 / 무지성 / 브레이커 / 레짐 / 슈퍼 + 올존(또는 OZ), 순서·띄어쓰기 자유.
    예) "무지성 브레이커 올존", "레짐무지성올존", "슈퍼 올존", "일반 올존"
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("트리거가 비어 있습니다")
    rest = raw.replace(" ", "").replace("\t", "").lower()
    if rest.endswith("올존"):
        rest = rest[:-2]
    elif rest.endswith("oz"):
        rest = rest[:-2]
    else:
        raise ValueError(f"트리거는 '올존' 또는 'OZ'로 끝나야 합니다: {raw}")

    words: list[str] = []
    ordered_words = sorted(PROFILE_WORDS, key=len, reverse=True)
    while rest:
        for word in ordered_words:
            if rest.startswith(word):
                words.append(word)
                rest = rest[len(word):]
                break
        else:
            raise ValueError(
                f"알 수 없는 트리거 단어: '{rest}' (사용 가능: 일반, 무지성, 브레이커, 레짐, 슈퍼)"
            )
    if len(words) != len(set(words)):
        raise ValueError(f"같은 단어가 두 번 들어 있습니다: {raw}")
    if "일반" in words and "무지성" in words:
        raise ValueError("'일반'과 '무지성'은 함께 쓸 수 없습니다")

    vm = "BLIND" if "무지성" in words else "NORMAL"
    tm = canonical_trigger_mode(PROFILE_WORDS[w] for w in words if PROFILE_WORDS[w] in TRIGGER_FLAGS)
    return vm, tm


# -----------------------------------------------------------------------------
# SPECIAL 최종 OZ 트리거 슬롯
# -----------------------------------------------------------------------------
# OZ_SYSTEM CONTROL [전략 설정] -> 환경변수 OZ_SPECIAL_TRIGGERS(JSON) -> manager_KIM
# -> set_special_trigger_overrides() -> 각 SPECIAL이 special_final_profile()로 조회.
SPECIAL_TRIGGERS_ENV = "OZ_SPECIAL_TRIGGERS"
_SPECIAL_TRIGGER_OVERRIDES: dict[str, tuple[str, str, str]] = {}


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
        parsed[key] = (vm, tm, str(text).strip())
    _SPECIAL_TRIGGER_OVERRIDES.clear()
    _SPECIAL_TRIGGER_OVERRIDES.update(parsed)
    return errors


def special_trigger_overrides() -> dict[str, tuple[str, str, str]]:
    return dict(_SPECIAL_TRIGGER_OVERRIDES)


def special_final_profile(name: str, default_validation_mode: str, default_trigger_mode: str) -> tuple[str, str, bool]:
    """SPECIAL 최종 OZ 프로필. 슬롯 값이 없거나 코드 기본값과 같으면 코드 기본값을 그대로 씁니다."""
    default = normalize_profile(default_validation_mode, default_trigger_mode)
    item = _SPECIAL_TRIGGER_OVERRIDES.get(str(name or "").strip().upper())
    if item is None:
        return default[0], default[1], False
    vm, tm, _text = item
    if (vm, tm) == default:
        return default[0], default[1], False
    return vm, tm, True
