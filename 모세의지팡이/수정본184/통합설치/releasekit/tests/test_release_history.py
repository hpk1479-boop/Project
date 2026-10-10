"""Issuance compatibility checks using fixture binaries and original reader."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from releasekit import release_history as history


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def original_hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / history.MQL_FOLDER).iterdir() if path.suffix in {".mq5", ".mqh"}}


def payload(root, name, value):
    target = root / "통합설치/.work" / name / "payload"
    write(target, history.EA_FILE, value)
    return target, hashlib.sha256(value).hexdigest()


def preview(root, name, value):
    target, digest = payload(root, name, value)
    return target, history.prepare_compatibility(root, target, original_hashes(root), digest)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "source with spaces"
    for name in history.REQUIRED_MQ5:
        write(root, history.MQL_FOLDER + "/" + name, ("// " + name + "\n").encode())
    write(root, history.MQL_FOLDER + "/STAFF_Wire.mqh", b"// original shared include\n")
    write(root, history.MQL_FOLDER + "/STAFF_Symbol_Map_Test.mq5", b"// unshipped test\n")
    legacy = {"version": 1, "owner_note": "preserve this field",
              "groups": [{"ea_build_hashes": ["a" * 64, "b" * 64],
                          "evidence": "검증결과/old.json", "scope": "original approved pair"}]}
    write(root, history.COMPATIBILITY_FILE, json.dumps(legacy, ensure_ascii=False).encode())
    return root


def original_reader(payload_root, monkeypatch):
    # Execute only the existing compatibility reader from working 96, with its
    # settings dependency replaced. No original main program is launched.
    package = types.ModuleType("fixture_original_build_compat")
    package.__path__ = []
    settings = types.ModuleType(package.__name__ + ".settings")
    settings.ROOT = payload_root
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, settings.__name__, settings)
    path = Path(__file__).resolve().parents[3] / "Part2/event_backtest/build_compat.py"
    spec = importlib.util.spec_from_file_location(package.__name__ + ".build_compat", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_does_not_commit_and_preserves_every_original_field(project):
    original = (project / history.COMPATIBILITY_FILE).read_bytes()
    target, pending = preview(project, "first", b"compiled expiration A")
    assert not (project / history.HISTORY_FILE).exists()
    assert json.loads((target / history.COMPATIBILITY_FILE).read_bytes()) == json.loads(original)
    assert (project / history.COMPATIBILITY_FILE).read_bytes() == original
    metadata = json.loads((target / history.PROVENANCE_FILE).read_bytes())
    assert len(metadata["original_hashes"]) == 6
    assert "STAFF_Symbol_Map_Test.mq5" not in json.dumps(metadata)
    assert str(project) not in json.dumps(metadata)
    assert pending["info"]["compatible_ea_build_hashes"] == [metadata["ea_build_hash"]]
    assert not any(path.suffix in {".mq5", ".mqh", ".py"} for path in target.rglob("*") if path.is_file())


def test_same_source_renewal_is_approved_by_existing_reader(project, monkeypatch):
    first_payload, first = preview(project, "first", b"compiled expiration A")
    history.commit_history(project, first)
    second_payload, second = preview(project, "second", b"compiled expiration B")
    reader = original_reader(second_payload, monkeypatch)
    one, two = first["info"]["ea_build_hash"], second["info"]["ea_build_hash"]
    assert reader.compatible_hashes(two) == frozenset((one, two))
    assert reader.compatible_pair(two, one)
    assert reader.compatible_hashes("a" * 64) == frozenset(("a" * 64, "b" * 64))
    original = json.loads((project / history.COMPATIBILITY_FILE).read_bytes())
    generated = json.loads((second_payload / history.COMPATIBILITY_FILE).read_bytes())
    assert generated["groups"][:-1] == original["groups"]
    assert generated["owner_note"] == original["owner_note"]
    # The new EA is not added to developer history before successful issuance.
    assert two not in (project / history.HISTORY_FILE).read_text()
    assert history.commit_history(project, second) == second["info"]


def test_changed_mqh_source_is_excluded_by_existing_reader(project, monkeypatch):
    unused, first = preview(project, "first", b"compiled expiration A")
    history.commit_history(project, first)
    unused, second = preview(project, "second", b"compiled expiration B")
    history.commit_history(project, second)
    write(project, history.MQL_FOLDER + "/STAFF_Wire.mqh", b"// changed calculation implementation\n")
    target, changed = preview(project, "changed", b"compiled changed MQL")
    reader = original_reader(target, monkeypatch)
    current = changed["info"]["ea_build_hash"]
    assert reader.compatible_hashes(current) == frozenset((current,))
    assert not reader.compatible_pair(current, first["info"]["ea_build_hash"])
    assert not reader.compatible_pair(current, second["info"]["ea_build_hash"])
    history.commit_history(project, changed)
    data = json.loads((project / history.HISTORY_FILE).read_bytes())
    assert len(data["families"]) == 2
    assert str(project) not in json.dumps(data)


def test_hash_collision_across_changed_sources_is_rejected(project):
    unused, first = preview(project, "first", b"one identical compiled binary")
    history.commit_history(project, first)
    write(project, history.MQL_FOLDER + "/DI_of_Moses.mq5", b"// changed original source\n")
    with pytest.raises(ValueError, match="충돌"):
        preview(project, "collision", b"one identical compiled binary")


def test_stale_concurrent_preview_does_not_overwrite_successful_history(project):
    unused, first = preview(project, "first", b"compiled expiration A")
    unused, second = preview(project, "second", b"compiled expiration B")
    history.commit_history(project, first)
    original = (project / history.HISTORY_FILE).read_bytes()
    with pytest.raises(RuntimeError, match="변경"):
        history.commit_history(project, second)
    assert (project / history.HISTORY_FILE).read_bytes() == original
    assert not (project / (history.HISTORY_FILE + ".lock")).exists()


def test_retry_of_identical_build_is_idempotent(project):
    unused, first = preview(project, "first", b"identical compiled binary")
    history.commit_history(project, first)
    unused, repeated = preview(project, "repeated", b"identical compiled binary")
    history.commit_history(project, repeated)
    data = json.loads((project / history.HISTORY_FILE).read_bytes())
    assert len(data["families"]) == 1
    assert len(data["families"][0]["ea_build_hashes"]) == 1


@pytest.mark.parametrize("invalid", [b"{not json}", b'{"schema":1,"families":{},"absolute":"C:/secret"}',
                                     b'{"schema":1,"schema":1,"families":[]}'])
def test_invalid_history_stops_before_payload_changes(project, invalid):
    target, digest = payload(project, "invalid", b"compiled EA")
    write(target, history.COMPATIBILITY_FILE, b"preserve fixture payload")
    write(project, history.HISTORY_FILE, invalid)
    with pytest.raises(ValueError):
        history.prepare_compatibility(project, target, original_hashes(project), digest)
    assert (target / history.COMPATIBILITY_FILE).read_bytes() == b"preserve fixture payload"
    assert not (target / history.PROVENANCE_FILE).exists()
    assert (project / history.HISTORY_FILE).read_bytes() == invalid


def test_missing_header_or_changed_original_hash_is_rejected(project):
    target, digest = payload(project, "invalid", b"compiled EA")
    hashes = original_hashes(project)
    headers = [name for name in hashes if name.endswith(".mqh")]
    missing = {name: value for name, value in hashes.items() if name not in headers}
    with pytest.raises(ValueError, match="목록"):
        history.prepare_compatibility(project, target, missing, digest)
    hashes[headers[0]] = "f" * 64
    with pytest.raises(ValueError, match="변경"):
        history.prepare_compatibility(project, target, hashes, digest)


def test_original_folder_or_wrong_binary_cannot_be_prepared(project):
    hashes = original_hashes(project)
    with pytest.raises(ValueError, match="배포 폴더"):
        history.prepare_compatibility(project, project, hashes, "c" * 64)
    target, digest = payload(project, "wrong binary", b"compiled EA")
    with pytest.raises(ValueError, match="배포 파일"):
        history.prepare_compatibility(project, target, hashes, "c" * 64)


def test_legacy_group_overlap_is_a_conflict(project):
    target, digest = payload(project, "overlap", b"compiled EA")
    original = {"version": 1, "groups": [{"ea_build_hashes": [digest, "a" * 64]}]}
    write(project, history.COMPATIBILITY_FILE, json.dumps(original).encode())
    with pytest.raises(ValueError, match="충돌"):
        history.prepare_compatibility(project, target, original_hashes(project), digest)
    assert not (project / history.HISTORY_FILE).exists()


def test_invalid_original_compatibility_is_rejected(project):
    original = project / history.COMPATIBILITY_FILE
    original.unlink()
    original.mkdir()
    with pytest.raises(ValueError, match="파일 형식"):
        preview(project, "invalid compat", b"compiled EA")
    assert not (project / history.HISTORY_FILE).exists()


def test_busy_history_commit_does_not_modify_history(project):
    unused, pending = preview(project, "busy", b"compiled EA")
    lock = write(project, history.HISTORY_FILE + ".lock", b"fixture other writer")
    with pytest.raises(RuntimeError, match="진행 중"):
        history.commit_history(project, pending)
    assert lock.read_bytes() == b"fixture other writer"
    assert not (project / history.HISTORY_FILE).exists()


def test_commit_cannot_remove_previous_family_and_cannot_accept_changed_source(project):
    unused, first = preview(project, "first", b"compiled expiration A")
    history.commit_history(project, first)
    unused, second = preview(project, "second", b"compiled expiration B")
    altered = copy.deepcopy(second)
    altered["history"]["families"][0]["ea_build_hashes"].remove(first["info"]["ea_build_hash"])
    altered["info"]["compatible_ea_build_hashes"] = altered["history"]["families"][0]["ea_build_hashes"]
    with pytest.raises(ValueError, match="지울"):
        history.commit_history(project, altered)
    write(project, history.MQL_FOLDER + "/PRICE_of_Moses.mq5", b"// changed during final copy\n")
    with pytest.raises(ValueError, match="변경"):
        history.commit_history(project, second)


def test_source_and_history_are_portable_after_root_move(project, tmp_path):
    unused, first = preview(project, "first", b"compiled expiration A")
    history.commit_history(project, first)
    moved = tmp_path / "different external disk" / "moved project"
    moved.parent.mkdir()
    project.rename(moved)
    unused, second = preview(moved, "second", b"compiled expiration B")
    history.commit_history(moved, second)
    assert len(second["info"]["compatible_ea_build_hashes"]) == 2
    assert str(project) not in (moved / history.HISTORY_FILE).read_text()
    assert str(moved) not in (moved / history.HISTORY_FILE).read_text()


def reviewed_original(project):
    binary = write(project, history.EA_FILE, b"reviewed original recording EA")
    original_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    fingerprint = history._sha(history._canonical(history._current_sources(project, original_hashes(project))))
    compatibility = json.loads((project / history.COMPATIBILITY_FILE).read_bytes())
    compatibility["groups"][0]["ea_build_hashes"].append(original_hash)
    compatibility["groups"][0]["source_anchor"] = {
        "ea_build_hash": original_hash, "mql_source_fingerprint": fingerprint,
        "evidence": "verification/reviewed_recording_source.json"}
    write(project, history.COMPATIBILITY_FILE, history._canonical(compatibility))
    return original_hash


def test_first_license_and_renewal_reuse_reviewed_developer_data(project, monkeypatch):
    original_hash = reviewed_original(project)
    original = (project / history.COMPATIBILITY_FILE).read_bytes()
    first_payload, first = preview(project, "first", b"licensed expiration A")
    reader = original_reader(first_payload, monkeypatch)
    first_hash = first["info"]["ea_build_hash"]
    assert reader.compatible_pair(first_hash, original_hash)
    assert reader.compatible_pair(first_hash, "a" * 64)
    assert not reader.compatible_pair(first_hash, "f" * 64)
    history.commit_history(project, first)
    second_payload, second = preview(project, "renewal", b"licensed expiration B")
    reader = original_reader(second_payload, monkeypatch)
    assert reader.compatible_pair(second["info"]["ea_build_hash"], first_hash)
    assert reader.compatible_pair(second["info"]["ea_build_hash"], original_hash)
    metadata = json.loads((second_payload / history.PROVENANCE_FILE).read_bytes())
    assert original_hash in metadata["recording_ea_build_hashes"]
    # Issuance history still records issued binaries, never manufactured origins.
    assert original_hash not in second["info"]["compatible_ea_build_hashes"]
    assert (project / history.COMPATIBILITY_FILE).read_bytes() == original
    generated = json.loads((second_payload / history.COMPATIBILITY_FILE).read_bytes())
    assert len(generated["groups"]) == 1
    assert generated["groups"][0]["evidence"] == "검증결과/old.json"
    assert generated["owner_note"] == "preserve this field"


@pytest.mark.parametrize("change", ["source", "binary"])
def test_source_or_binary_change_cannot_reuse_old_developer_data(project, monkeypatch, change):
    original_hash = reviewed_original(project)
    if change == "source":
        write(project, history.MQL_FOLDER + "/STAFF_Wire.mqh", b"changed recording calculation")
    else:
        write(project, history.EA_FILE, b"unreviewed original compiler output")
    target, pending = preview(project, "changed", b"new licensed EA")
    reader = original_reader(target, monkeypatch)
    assert not reader.compatible_pair(pending["info"]["ea_build_hash"], original_hash)


def test_anchor_must_reference_approved_hash_and_portable_evidence(project):
    reviewed_original(project)
    path = project / history.COMPATIBILITY_FILE
    data = json.loads(path.read_bytes())
    data["groups"][0]["source_anchor"]["ea_build_hash"] = "f" * 64
    path.write_bytes(history._canonical(data))
    with pytest.raises(ValueError, match="호환 그룹"):
        preview(project, "invalid anchor", b"licensed EA")


def test_null_anchor_is_unreviewed_and_does_not_bridge(project, monkeypatch):
    data = json.loads((project / history.COMPATIBILITY_FILE).read_bytes())
    data["groups"][0]["source_anchor"] = None
    write(project, history.COMPATIBILITY_FILE, history._canonical(data))
    target, pending = preview(project, "unreviewed", b"licensed EA")
    assert not original_reader(target, monkeypatch).compatible_pair(pending["info"]["ea_build_hash"], "a" * 64)
