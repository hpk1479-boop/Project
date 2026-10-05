# -*- coding: utf-8 -*-
"""저장 데이터의 OZ 프로필 호환 경계. 현재 실행·명령 검증에는 사용하지 않습니다.

모든 과거 문자열 해석은 load_saved_oz()를 통해서만 수행합니다.
지원할 수 없는 설정은 원본과 경로를 보고하며 현재 프로필로 대체하지 않습니다.
"""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import re
from collections.abc import Mapping

import oz_profiles


class LegacyUnsupportedProfile(oz_profiles.ProfileError):
    """실행 대상에서 제외해야 하는 구버전 설정입니다."""
    def __init__(self, path: str, original: object):
        self.path = path
        self.original = deepcopy(original)
        super().__init__(f"구버전 미지원 OZ 설정: {path}={original!r}. 해당 설정을 불러오지 않습니다.")


def _check(value, path):
    if re.search(r"REGIME|SUPER|레짐|슈퍼|수퍼", str(value or ""), re.I):
        raise LegacyUnsupportedProfile(path, value)


def _profile(vm=None, tm=None, oz_mode=None, *, path="profile"):
    original = {"validation_mode": vm, "trigger_mode": tm, "oz_mode": oz_mode}
    for key, value in original.items():
        _check(value, path + "." + key)
    try:
        if oz_mode:
            raw = str(oz_mode).strip().upper()
            if raw in oz_profiles.VALIDATION_MODES:
                lvm, ltm = raw, "OZ"
            elif raw in {"BLIND_BREAKER", "BREAKER_BLIND"}:
                lvm, ltm = "BLIND", "BREAKER"
            else:
                lvm, ltm = "NORMAL", raw
            lvm, ltm = oz_profiles.normalize_profile(lvm, ltm)
            vm, tm = vm or lvm, tm or ltm
        return oz_profiles.normalize_profile(vm, tm)
    except oz_profiles.ProfileError as exc:
        raise LegacyUnsupportedProfile(path, original) from exc


def _text(value, path):
    _check(value, path)
    raw = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z_+\-]+", raw):
        return oz_profiles.profile_label(*_profile(oz_mode=raw, path=path))
    try:
        return oz_profiles.canonical_profile_text(raw)
    except oz_profiles.ProfileError as exc:
        raise LegacyUnsupportedProfile(path, value) from exc


def _watch(value, path):
    item = deepcopy(dict(value))
    watch_id = str(item.get("watch_id") or "")
    for token in watch_id.split(":"):
        profile_token = re.fullmatch(
            r"(?:(?:HIGH|BREAKER|REGIME|SUPER)_)*(?:REGIME|SUPER)(?:_(?:BREAKER|REGIME|SUPER))*", token, re.I)
        if profile_token:
            _check(token, path + ".watch_id")
    vm, tm = item.get("validation_mode"), item.get("trigger_mode")
    kind = str(item.pop("trigger_type", "") or "").strip().upper()
    if kind:
        _check(kind, path + ".trigger_type")
        if kind in oz_profiles.VALIDATION_MODES:
            vm = vm or kind
        else:
            canonical = _profile(tm=kind, path=path + ".trigger_type")[1]
            tm = tm or canonical
    vm, tm = _profile(vm, tm, item.pop("oz_mode", None), path=path)
    item["validation_mode"], item["trigger_mode"] = vm, tm
    for key in ("triggers", "invalidation_triggers"):
        if key in item:
            item[key] = [_watch(row, f"{path}.{key}[{i}]") for i, row in enumerate(item[key])]
    return item


def _recipe(value, path):
    result = deepcopy(dict(value))
    config = result.setdefault("config", {})
    for key in config:
        if key == "SOURCE_BAND_ENABLED":
            raise LegacyUnsupportedProfile(path + ".config." + key, config[key])
        if key.endswith("_SOURCE_TRIGGER_MODE"):
            _check(key, path + ".config." + key)
    for prefix in ("FINAL", "SOURCE"):
        vk, tk = prefix + "_VALIDATION_MODE", prefix + "_TRIGGER_MODE"
        if prefix == "FINAL" or vk in config or tk in config:
            config[vk], config[tk] = _profile(config.get(vk), config.get(tk), path=path + ".config." + prefix)
    return result


def _source(value, path):
    """저장된 Python의 프로필 리터럴만 읽습니다. 실행/파일 재작성은 하지 않습니다."""
    import ast
    source = str(value)
    tree = ast.parse(source)
    changes = []
    lines = source.splitlines(keepends=True)
    def literal(node, key):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return
        old = node.value
        if key in {'validation_mode', 'FINAL_VALIDATION_MODE', 'SOURCE_VALIDATION_MODE'}:
            _check(old, f"{path}:{node.lineno}.{key}")
            new = oz_profiles.normalize_profile(old)[0]
        else:
            new = _profile(tm=old, path=f"{path}:{node.lineno}.{key}")[1]
        if new != old:
            changes.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, repr(new)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id == 'SOURCE_BAND_ENABLED':
                raise LegacyUnsupportedProfile(f"{path}:{node.lineno}", node.id)
            if node.id.endswith('_SOURCE_TRIGGER_MODE'):
                _check(node.id, f"{path}:{node.lineno}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and (target.id.endswith('_TRIGGER_MODE') or target.id in {'FINAL_VALIDATION_MODE','SOURCE_VALIDATION_MODE'}):
                    literal(node.value, target.id)
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and (key.value.endswith('_TRIGGER_MODE') or key.value in {'trigger_mode','validation_mode','FINAL_VALIDATION_MODE','SOURCE_VALIDATION_MODE'}):
                    literal(val, key.value)
        if isinstance(node, ast.keyword) and node.arg in {'trigger_mode', 'validation_mode'}:
            literal(node.value, node.arg)
    # AST offsets count UTF-8 bytes. Apply in reverse to preserve all other source text.
    for line, start, endline, end, replacement in sorted(set(changes), reverse=True):
        before = lines[line-1].encode('utf-8')[:start].decode('utf-8')
        after = lines[endline-1].encode('utf-8')[end:].decode('utf-8')
        lines[line-1:endline] = [before + replacement + after]
    return ''.join(lines)


def _checkpoints(value):
    from durable_protocol import identity
    files = dict(value)
    rejected = json.loads(files.get("oz_rejected_profile_checkpoints.json", "{}"))
    migrations = json.loads(files.get("oz_profile_checkpoint_migrations.json", "{}"))
    for name, raw in tuple(files.items()):
        if not name.startswith("oz_observed_"):
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            _check(payload.get("strategy_condition"), name + ".strategy_condition")
            stored = payload.get("profile")
            if stored is None:
                continue
            canonical = _profile(*stored, path=name)
        except oz_profiles.ProfileError as exc:
            rejected[name] = {"reason": str(exc), "original": raw}
            files.pop(name, None)
            logging.warning("[OZ 후보 상태 격리] %s | %s", name, exc)
            continue
        except (ValueError, TypeError):
            # 형식이 손상된 상태는 원래의 복원 오류 처리 경로에 맡깁니다.
            continue
        if tuple(stored) == canonical:
            continue
        symbol = payload.get("symbol")
        # symbol이 있는 상태는 현재 프로필의 식별자를 사용합니다.
        # symbol이 없는 상태는 기존 식별자를 유지하며 별도 이름 추정은 하지 않습니다.
        payload["profile"] = list(canonical)
        if symbol:
            target = "oz_observed_" + identity(symbol, *canonical)[:24] + ".json"
            if target not in files:
                files[target] = json.dumps(payload, ensure_ascii=False)
            files.pop(name, None)
            migrations[name] = {"target": target, "reason": "OZ profile canonicalized; observed state retained"}
        else:
            files[name] = json.dumps(payload, ensure_ascii=False)
    if rejected:
        files["oz_rejected_profile_checkpoints.json"] = json.dumps(rejected, ensure_ascii=False)
    if migrations:
        files["oz_profile_checkpoint_migrations.json"] = json.dumps(migrations, ensure_ascii=False)
    return files


def load_saved_oz(value, *, kind="watch", path="saved"):
    """디스크/복원 입력 전용 단일 진입점. 입력 객체는 수정하지 않습니다.

    watch/recipe/text/scenario: 성공한 값만 반환하고 미지원 설정은 예외로 보고합니다.
    specials: 개별 미지원 항목은 실행 불가 표식으로 보고합니다.
    checkpoints: 미지원 상태는 원본을 격리하고 지원 상태만 반환합니다.
    """
    if kind == "watch":
        return _watch(value, path)
    if kind == "text":
        return _text(value, path)
    if kind == "recipe":
        result = _recipe(value, path)
        if result.get("source_text"):
            result["source_text"] = _source(result["source_text"], path + ".source_text")
        return result
    if kind == "source":
        return _source(value, path)
    if kind == "scenario":
        result = deepcopy(dict(value))
        result["triggers"] = {name: _text(text, f"{path}.triggers.{name}")
                              for name, text in result.get("triggers", {}).items()}
        return result
    if kind == "specials":
        result = {}
        for name, row in dict(value).items():
            if not isinstance(row, Mapping):
                continue
            item = deepcopy(dict(row))
            try:
                if item.get("trigger") is not None:
                    item["trigger"] = _text(item["trigger"], f"{path}.{name}.trigger")
                result[str(name).upper()] = item
            except oz_profiles.ProfileError as exc:
                result[str(name).upper()] = {"enabled": False, "load_error": str(exc), "original": deepcopy(row)}
                logging.error("[OZ 저장 설정 불러오기 제외] %s | %s", name, exc)
        return result
    if kind == "checkpoints":
        return _checkpoints(value)
    raise ValueError(f"알 수 없는 저장 데이터 종류: {kind}")
