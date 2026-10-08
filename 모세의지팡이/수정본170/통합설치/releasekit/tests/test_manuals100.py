"""Current manual inclusion and issuance checks with disposable release fixtures.

These tests exercise the normal builder with synthetic compiler outputs. They
never compile the real application or access the developer's private keys.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from releasekit import builder, manuals, release_history, validation
from test_builder import fake_build, project, write


FIRST_PDF = b"%PDF-1.7\n% first published edition\n%%EOF\n"
SECOND_PDF = b"%PDF-1.7\n% revised published edition with current instructions\n%%EOF\n"


def build_fixture(root):
    return builder.build(root, builder.BuildSettings("v4.2", "2026-12-31", 10),
                         emit=lambda unused: None)


def assert_no_issue(root):
    assert not list((root / "배포").rglob("*.exe"))
    assert not (root / release_history.HISTORY_FILE).exists()


def archive_for(root, report):
    evidence = root / report["release_validation"]["report"]
    return root / "통합설치/.work" / evidence.parent.name / "payload.zip"


def replace_archive_manual(archive, data):
    replacement = archive.with_suffix(".modified.zip")
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(replacement, "w") as target:
        for item in source.infolist():
            value = data if item.filename == manuals.MANUAL_RELATIVE else source.read(item.filename)
            target.writestr(item, value)
    replacement.replace(archive)


def test_required_published_pdf_excludes_editing_sources_and_extra_drafts(project, tmp_path):
    excluded = ("매뉴얼/편집원본/모세_사용자_매뉴얼.md", "매뉴얼/편집원본/build_manual.py",
                "매뉴얼/편집원본/모세_사용자_매뉴얼.docx", "매뉴얼/이전판.pdf",
                "문서/모세_사용자_매뉴얼_수정본100.pdf")
    for relative in excluded:
        write(project, relative, b"editing source or old edition")
    selected = list(builder.source_paths(project))
    names = {relative.as_posix() for relative, unused in selected}
    assert manuals.MANUAL_RELATIVE in names
    assert not names.intersection(excluded)
    target = tmp_path / "payload"
    builder.prepare_data(project, target, selected)
    assert (target / manuals.MANUAL_RELATIVE).read_bytes() == (project / manuals.MANUAL_RELATIVE).read_bytes()
    assert not any((target / relative).exists() for relative in excluded)


@pytest.mark.parametrize("data", [b"", b"{}", b"not a PDF\n%%EOF", b"%PDF-1.7\ntruncated",
                                 b"%PDF-1.7\n%%EOF\ntrailing non-whitespace"])
def test_invalid_published_pdf_is_rejected_before_build_work(project, data):
    write(project, manuals.MANUAL_RELATIVE, data)
    with pytest.raises(ValueError, match="매뉴얼 PDF"):
        list(builder.source_paths(project))
    assert not (project / "통합설치/.work").exists()


def test_missing_published_pdf_cannot_issue_installer(fake_build):
    root, unused, events, clock = fake_build
    (root / manuals.MANUAL_RELATIVE).unlink()
    with pytest.raises(ValueError, match="매뉴얼이 없습니다"):
        build_fixture(root)
    assert not events
    assert_no_issue(root)


def test_current_pdf_bytes_hash_and_korean_installer_name_are_recorded(fake_build):
    root, unused, events, clock = fake_build
    write(root, manuals.MANUAL_RELATIVE, FIRST_PDF)
    final, report = build_fixture(root)
    assert final.name == "모세의지팡이 통합설치.exe"
    assert report["manual"] == {"path": manuals.MANUAL_RELATIVE, "bytes": len(FIRST_PDF),
                                "sha256": hashlib.sha256(FIRST_PDF).hexdigest()}
    summary = root / report["release_validation"]["report"]
    saved = json.loads((summary.parent / "build_summary.json").read_bytes())
    assert saved["manual"] == report["manual"]
    assert manuals.MANUAL_RELATIVE in report["payload_files"]
    with zipfile.ZipFile(archive_for(root, report)) as stream:
        assert stream.read(manuals.MANUAL_RELATIVE) == FIRST_PDF


def test_next_release_uses_new_manual_without_reusing_previous_pdf(fake_build):
    root, unused, events, clock = fake_build
    write(root, manuals.MANUAL_RELATIVE, FIRST_PDF)
    first_final, first_report = build_fixture(root)
    write(root, manuals.MANUAL_RELATIVE, SECOND_PDF)
    second_final, second_report = build_fixture(root)
    assert first_final.parent != second_final.parent
    assert first_report["manual"]["sha256"] != second_report["manual"]["sha256"]
    for report, expected in ((first_report, FIRST_PDF), (second_report, SECOND_PDF)):
        with zipfile.ZipFile(archive_for(root, report)) as stream:
            assert stream.read(manuals.MANUAL_RELATIVE) == expected
        assert report["manual"]["sha256"] == hashlib.sha256(expected).hexdigest()
    assert (root / manuals.MANUAL_RELATIVE).read_bytes() == SECOND_PDF


@pytest.mark.parametrize("action", ["modify", "delete"])
def test_manual_changed_or_deleted_during_compile_withholds_copied_installer(fake_build, monkeypatch, action):
    root, unused, events, clock = fake_build
    original = builder._run

    def concurrent_change(args, current_root, log, **kwargs):
        original(args, current_root, log, **kwargs)
        if action == "modify":
            write(root, manuals.MANUAL_RELATIVE, SECOND_PDF)
        else:
            (root / manuals.MANUAL_RELATIVE).unlink()

    monkeypatch.setattr(builder, "_run", concurrent_change)
    with pytest.raises((RuntimeError, OSError)):
        build_fixture(root)
    assert any(item[0] == "final_copy" for item in events)
    assert_no_issue(root)


def test_copy_payload_tampering_is_rejected_before_compile(fake_build, monkeypatch):
    root, unused, events, clock = fake_build
    original = builder.prepare_data

    def alter_copy(current_root, destination, sources):
        paths = original(current_root, destination, sources)
        write(Path(destination), manuals.MANUAL_RELATIVE, SECOND_PDF)
        return paths

    monkeypatch.setattr(builder, "prepare_data", alter_copy)
    with pytest.raises(RuntimeError, match="매뉴얼 변경"):
        build_fixture(root)
    assert not any(item[0] == "compile" for item in events)
    assert_no_issue(root)


@pytest.mark.parametrize("action", ["modify", "delete"])
def test_payload_change_after_runtime_validation_is_rejected(fake_build, monkeypatch, action):
    root, unused, events, clock = fake_build
    original = validation.validate_release

    def alter_payload(current_root, payload, *args, **kwargs):
        result = original(current_root, payload, *args, **kwargs)
        if action == "modify":
            write(Path(payload), manuals.MANUAL_RELATIVE, SECOND_PDF)
        else:
            (Path(payload) / manuals.MANUAL_RELATIVE).unlink()
        return result

    monkeypatch.setattr(validation, "validate_release", alter_payload)
    with pytest.raises((ValueError, RuntimeError), match="매뉴얼"):
        build_fixture(root)
    assert not any(item[0] == "compile" for item in events)
    assert_no_issue(root)


@pytest.mark.parametrize("stage", ["archive_creation", "installer_compile"])
def test_archive_manual_tampering_cannot_issue_installer(fake_build, monkeypatch, stage):
    root, unused, events, clock = fake_build
    if stage == "archive_creation":
        original = validation.write_payload_archive

        def alter_archive(payload, archive):
            original(payload, archive)
            replace_archive_manual(Path(archive), SECOND_PDF)

        monkeypatch.setattr(validation, "write_payload_archive", alter_archive)
    else:
        original = builder._run

        def alter_compiled_archive(args, current_root, log, **kwargs):
            original(args, current_root, log, **kwargs)
            replace_archive_manual(Path(log).parent / "payload.zip", SECOND_PDF)

        monkeypatch.setattr(builder, "_run", alter_compiled_archive)
    with pytest.raises(RuntimeError, match="포함된 매뉴얼"):
        build_fixture(root)
    assert_no_issue(root)
    assert any(item[0] == "final_copy" for item in events) == (stage == "installer_compile")
