"""The quick guide ships beside the user manual and gets the same checks."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from releasekit import builder, manuals, validation
from test_builder import fake_build, project, write
from test_manuals100 import archive_for, assert_no_issue, build_fixture

QUICK_PDF = b"%PDF-1.7\n% quick guide part 1 2 3\n%%EOF\n"
REAL_ROOT = Path(__file__).resolve().parents[3]


def test_quick_guide_is_in_installer_archive_and_report(fake_build):
    root, unused, events, clock = fake_build
    write(root, manuals.QUICK_RELATIVE, QUICK_PDF)
    final, report = build_fixture(root)
    expected = {"path": manuals.QUICK_RELATIVE, "bytes": len(QUICK_PDF),
                "sha256": hashlib.sha256(QUICK_PDF).hexdigest()}
    assert report["quick_guide"] == expected
    assert report["manual"]["path"] == manuals.MANUAL_RELATIVE
    assert manuals.QUICK_RELATIVE in report["payload_files"]
    with zipfile.ZipFile(archive_for(root, report)) as stream:
        assert stream.read(manuals.QUICK_RELATIVE) == QUICK_PDF


def test_missing_quick_guide_stops_before_build_work(fake_build):
    root, unused, events, clock = fake_build
    (root / manuals.QUICK_RELATIVE).unlink()
    with pytest.raises(ValueError, match="매뉴얼이 없습니다: " + manuals.QUICK_RELATIVE):
        build_fixture(root)
    assert not events
    assert_no_issue(root)


@pytest.mark.parametrize("data", [b"", b"{}", b"%PDF-1.7\ntruncated"])
def test_invalid_quick_guide_pdf_is_rejected(project, data):
    write(project, manuals.QUICK_RELATIVE, data)
    with pytest.raises(ValueError, match="매뉴얼 PDF"):
        list(builder.source_paths(project))
    assert not (project / "통합설치/.work").exists()


def test_quick_guide_changed_in_archive_cannot_issue_installer(fake_build, monkeypatch):
    root, unused, events, clock = fake_build
    original = validation.write_payload_archive

    def alter_archive(payload, archive):
        original(payload, archive)
        archive = Path(archive)
        replacement = archive.with_suffix(".modified.zip")
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(replacement, "w") as target:
            for item in source.infolist():
                value = QUICK_PDF if item.filename == manuals.QUICK_RELATIVE else source.read(item.filename)
                target.writestr(item, value)
        replacement.replace(archive)

    monkeypatch.setattr(validation, "write_payload_archive", alter_archive)
    with pytest.raises(RuntimeError, match="포함된 매뉴얼"):
        build_fixture(root)
    assert_no_issue(root)


def test_current_revision_has_both_published_manuals():
    records = manuals.manual_records(REAL_ROOT)
    assert [record["path"] for record in records] == list(manuals.MANUALS)
    assert all(record["bytes"] > 0 for record in records)
