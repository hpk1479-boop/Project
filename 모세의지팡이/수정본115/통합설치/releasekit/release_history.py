"""Developer-only issuance history for identical original MQL source builds.

Preparing changes only a generated payload. A successfully issued installer
can then commit the pending history; failed builds never approve a new hash.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid


HISTORY_FILE = "통합설치/.keys/license_build_history.json"
COMPATIBILITY_FILE = "Part2/ea_build_compatibility.json"
PROVENANCE_FILE = "runtime/mql_build_provenance.json"
MQL_FOLDER = "Part1/program/MT5"
REQUIRED_MQ5 = frozenset((
    "THE_STAFF_OF_MOSES.mq5", "PRICE_of_Moses.mq5", "RSI_of_Moses.mq5",
    "STO_of_Moses.mq5", "DI_of_Moses.mq5",
))
EA_FILE = MQL_FOLDER + "/THE_STAFF_OF_MOSES.ex5"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("배포 이력 해시 형식 오류")
    return value


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("배포 이력 상대 경로 형식 오류")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(p in (".", "..") for p in path.parts):
        raise ValueError("배포 이력 상대 경로 형식 오류")
    return path


def _safe_path(root, relative):
    """Refuse a junction or symlink along any existing output/input component."""
    path = root
    for part in _relative(relative).parts:
        path = path / part
        if path.exists() or path.is_symlink():
            attributes = path.lstat()
            if path.is_symlink() or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("배포 이력 경로에 연결 폴더를 사용할 수 없습니다.")
    if not path.resolve().is_relative_to(root):
        raise ValueError("배포 이력 경로가 수정본 밖을 가리킵니다.")
    return path


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("배포 이력 JSON 중복 항목")
        result[key] = value
    return result


def _decode(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("배포 이력 JSON 형식 오류") from error


def _source_map(value):
    if not isinstance(value, dict):
        raise ValueError("원본 MQL 해시 목록 형식 오류")
    result = {}
    for relative, digest in value.items():
        path = _relative(relative)
        if len(path.parts) != 4 or path.parent.as_posix() != MQL_FOLDER:
            raise ValueError("원본 MQL 상대 경로 형식 오류")
        if path.name not in REQUIRED_MQ5 and path.suffix.lower() != ".mqh":
            raise ValueError("원본 MQL 해시 목록에 허용되지 않은 파일이 있습니다.")
        result[relative] = _hash(digest)
    if not {MQL_FOLDER + "/" + name for name in REQUIRED_MQ5}.issubset(result):
        raise ValueError("원본 MT5 프로그램 5개의 해시가 필요합니다.")
    return dict(sorted(result.items()))


def _current_sources(root, original_hashes):
    if not isinstance(original_hashes, dict):
        raise ValueError("원본 MQL 해시 목록 형식 오류")
    # The caller's complete original inventory also includes test MQ5 files.
    # Only the five shipping programs and every original include form a family.
    supplied = {}
    for name, digest in original_hashes.items():
        path = _relative(name)
        if path.parent.as_posix() == MQL_FOLDER and (path.name in REQUIRED_MQ5 or path.suffix.lower() == ".mqh"):
            supplied[name] = digest
    supplied = _source_map(supplied)
    folder = _safe_path(root, MQL_FOLDER)
    expected = {MQL_FOLDER + "/" + name for name in REQUIRED_MQ5}
    expected.update(path.relative_to(root).as_posix() for path in folder.iterdir()
                    if path.suffix.lower() == ".mqh" and path.is_file())
    if set(supplied) != expected:
        raise ValueError("원본 MQL 포함 파일 목록이 일치하지 않습니다.")
    for relative, digest in supplied.items():
        path = _safe_path(root, relative)
        if not path.is_file() or _sha(path.read_bytes()) != digest:
            raise ValueError("원본 MQL 변경이 감지되어 배포 이력을 중단했습니다.")
    return supplied


def _validate_history(value):
    if not isinstance(value, dict) or set(value) != {"schema", "families"} or type(value.get("schema")) is not int or value["schema"] != 1 or not isinstance(value["families"], list):
        raise ValueError("배포 이력 형식 오류")
    seen_fingerprints = set()
    seen_hashes = set()
    for family in value["families"]:
        if not isinstance(family, dict) or set(family) != {"fingerprint", "original_hashes", "ea_build_hashes"}:
            raise ValueError("배포 이력 그룹 형식 오류")
        fingerprint = _hash(family["fingerprint"])
        sources = _source_map(family["original_hashes"])
        if _sha(_canonical(sources)) != fingerprint or fingerprint in seen_fingerprints:
            raise ValueError("배포 이력 원본 식별값 충돌")
        hashes = family["ea_build_hashes"]
        if not isinstance(hashes, list) or not hashes:
            raise ValueError("배포 이력 EA 해시 목록 형식 오류")
        for digest in hashes:
            _hash(digest)
        if len(set(hashes)) != len(hashes) or seen_hashes.intersection(hashes):
            raise ValueError("서로 다른 원본의 EA 배포 해시가 충돌했습니다.")
        seen_fingerprints.add(fingerprint)
        seen_hashes.update(hashes)
    return value


def _read_history(root):
    path = _safe_path(root, HISTORY_FILE)
    if not path.exists():
        return {"schema": 1, "families": []}, None
    if not path.is_file():
        raise ValueError("배포 이력 파일 형식 오류")
    raw = path.read_bytes()
    return _validate_history(_decode(raw)), _sha(raw)


def _read_compatibility(root):
    path = _safe_path(root, COMPATIBILITY_FILE)
    if path.exists() and not path.is_file():
        raise ValueError("원본 EA 녹화 호환 목록 파일 형식 오류")
    data = _decode(path.read_bytes()) if path.is_file() else {"version": 1, "groups": []}
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("groups"), list):
        raise ValueError("원본 EA 녹화 호환 목록 형식 오류")
    seen = set()
    for group in data["groups"]:
        if not isinstance(group, dict):
            raise ValueError("원본 EA 녹화 호환 그룹 형식 오류")
        hashes = group.get("ea_build_hashes")
        if not isinstance(hashes, list) or len(hashes) < 2:
            raise ValueError("원본 EA 녹화 호환 해시 형식 오류")
        for digest in hashes:
            _hash(digest)
        if len(set(hashes)) != len(hashes) or seen.intersection(hashes):
            raise ValueError("원본 EA 녹화 호환 해시 중복")
        anchor = group.get("source_anchor")
        if anchor is not None:
            if not isinstance(anchor, dict) or set(anchor) != {"ea_build_hash", "mql_source_fingerprint", "evidence"}:
                raise ValueError("원본 EA 소스 검증 항목 형식 오류")
            if _hash(anchor["ea_build_hash"]) not in hashes:
                raise ValueError("원본 EA 소스 검증 해시가 호환 그룹에 없습니다.")
            _hash(anchor["mql_source_fingerprint"])
            _relative(anchor["evidence"])
        seen.update(hashes)
    return data, seen


def prepare_compatibility(root, payload, original_hashes, ea_hash):
    """Prepare a generated compatibility file without approving an issuance."""
    root = Path(root).resolve()
    payload = Path(payload)
    try:
        relative = payload.absolute().relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("생성된 배포 폴더만 준비할 수 있습니다.") from error
    if not relative.startswith("통합설치/.work/") or ".." in PurePosixPath(relative).parts:
        raise ValueError("생성된 배포 폴더만 준비할 수 있습니다.")
    payload = _safe_path(root, relative)
    if not payload.is_dir():
        raise ValueError("생성된 배포 폴더가 없습니다.")
    sources = _current_sources(root, original_hashes)
    fingerprint = _sha(_canonical(sources))
    ea_hash = _hash(ea_hash)
    binary = _safe_path(payload, EA_FILE)
    if not binary.is_file() or _sha(binary.read_bytes()) != ea_hash:
        raise ValueError("생성된 EA 배포 파일 해시가 일치하지 않습니다.")
    history, expected = _read_history(root)
    candidate = copy.deepcopy(history)
    family = next((item for item in candidate["families"] if item["fingerprint"] == fingerprint), None)
    if family is None:
        family = {"fingerprint": fingerprint, "original_hashes": sources, "ea_build_hashes": []}
        candidate["families"].append(family)
    if ea_hash not in family["ea_build_hashes"]:
        family["ea_build_hashes"].append(ea_hash)
    family["ea_build_hashes"].sort()
    _validate_history(candidate)
    compatibility, legacy_hashes = _read_compatibility(root)
    hashes = list(family["ea_build_hashes"])
    if legacy_hashes.intersection(hashes):
        raise ValueError("기존 승인 그룹과 배포 이력 해시가 충돌했습니다.")
    # Bridge only an explicitly reviewed source/binary pair. Reading a stale
    # developer EX5 beside edited MQ5 files must never approve old captures.
    original_binary = _safe_path(root, EA_FILE)
    original_hash = _sha(original_binary.read_bytes()) if original_binary.is_file() else None
    anchored = next((group for group in compatibility["groups"]
                     if (group.get("source_anchor") or {}).get("mql_source_fingerprint") == fingerprint
                     and group["source_anchor"]["ea_build_hash"] == original_hash), None)
    recording_hashes = hashes
    if anchored is not None:
        recording_hashes = sorted(set(anchored["ea_build_hashes"]) | set(hashes))
        anchored["ea_build_hashes"] = recording_hashes
        anchored["licensed_build_evidence"] = PROVENANCE_FILE
    elif len(hashes) >= 2:
        compatibility["groups"].append({
            "ea_build_hashes": hashes,
            "evidence": PROVENANCE_FILE,
            "scope": "licensed builds from identical original MQL sources",
            "mql_source_fingerprint": fingerprint,
        })
    info = {"history_file": HISTORY_FILE, "mql_source_fingerprint": fingerprint,
            "ea_build_hash": ea_hash, "compatible_ea_build_hashes": hashes}
    provenance = {"schema": 1, "mql_source_fingerprint": fingerprint,
                  "original_hashes": sources, "ea_build_hash": ea_hash,
                  "compatible_ea_build_hashes": hashes,
                  "recording_ea_build_hashes": recording_hashes}
    # All validation precedes output mutation, and every output is stage-local.
    for name, value in ((COMPATIBILITY_FILE, compatibility), (PROVENANCE_FILE, provenance)):
        target = _safe_path(payload, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_canonical(value))
    return {"expected_history_sha256": expected, "history": candidate, "info": info}


def commit_history(root, pending):
    """Atomically record a successfully issued build; reject stale previews."""
    root = Path(root).resolve()
    if not isinstance(pending, dict) or set(pending) != {"expected_history_sha256", "history", "info"}:
        raise ValueError("배포 이력 대기 항목 형식 오류")
    candidate = _validate_history(copy.deepcopy(pending["history"]))
    info = pending["info"]
    if not isinstance(info, dict) or set(info) != {"history_file", "mql_source_fingerprint", "ea_build_hash", "compatible_ea_build_hashes"} or info["history_file"] != HISTORY_FILE:
        raise ValueError("배포 이력 대기 정보 형식 오류")
    family = next((item for item in candidate["families"] if item["fingerprint"] == info["mql_source_fingerprint"]), None)
    if family is None or info["ea_build_hash"] not in family["ea_build_hashes"] or info["compatible_ea_build_hashes"] != family["ea_build_hashes"]:
        raise ValueError("배포 이력 대기 그룹이 일치하지 않습니다.")
    _current_sources(root, family["original_hashes"])
    expected = pending["expected_history_sha256"]
    if expected is not None:
        _hash(expected)
    target = _safe_path(root, HISTORY_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _safe_path(root, HISTORY_FILE + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("다른 배포 이력 저장이 진행 중입니다.") from error
    temporary = None
    try:
        os.close(descriptor)
        previous, actual = _read_history(root)
        if actual != expected:
            raise RuntimeError("배포 이력이 변경되어 새로 발급해야 합니다.")
        # A pending object must retain every previously approved family/hash.
        for old in previous["families"]:
            new = next((item for item in candidate["families"] if item["fingerprint"] == old["fingerprint"]), None)
            if new is None or old["original_hashes"] != new["original_hashes"] or not set(old["ea_build_hashes"]).issubset(new["ea_build_hashes"]):
                raise ValueError("기존 배포 이력을 지울 수 없습니다.")
        temporary = _safe_path(root, HISTORY_FILE + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(_canonical(candidate))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        lock.unlink()
    return copy.deepcopy(info)
