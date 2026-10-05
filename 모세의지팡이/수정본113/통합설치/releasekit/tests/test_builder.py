"""Build-pipeline behavior with synthetic source trees and stubbed compilers.

No original program, broker terminal, real installer, or developer private key
is executed/read by these checks. Compiler output is deliberately a fixture.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import zipfile

import pytest

from releasekit import builder, inventory, license_runtime, manuals, mt5, release_history, validation


def write(root, relative, data):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "synthetic source"
    for tree in builder.TREES:
        (root / tree).mkdir(parents=True, exist_ok=True)
    for name in builder.FILES:
        if name == manuals.MANUAL_RELATIVE:
            data = b"%PDF-1.7\n% synthetic published manual\n%%EOF\n"
        else:
            data = b"VALUE = 1\n" if Path(name).suffix in {".py", ".pyw"} else b"{}"
        write(root, name, data)
    for name in mt5.MT5_PROGRAMS:
        write(root, "Part1/program/MT5/" + name + ".mq5", b"// synthetic original MQL\n")
    write(root, "Part1/program/MT5/STAFF_Wire_V2.mqh", b"// synthetic shared include\n")
    write(root, "Part2/ea_build_compatibility.json", b'{"version":1,"groups":[]}')
    write(root, "common_ai/provider.py", b"def interpret(text):\n    return text.upper()\n")
    write(root, "Part3/web/index.html", "<p>기존 화면</p>".encode())
    write(root, "Part1/program/config.txt", (
        "TELEGRAM_TOKEN=fixture-telegram-secret\r\n"
        "TELEGRAM_CHAT_ID=fixture-recipient\r\n"
        "TELEGRAM_COMMAND_CHAT_IDS=fixture-commands\r\n"
        "GEMINI_API_KEY=fixture-ai-secret\r\n"
        "SYMBOLS=XAUUSD+\r\n"
        "MAIN_ASIA=09:00-12:00\r\n"
    ).encode())
    write(root, "settings/ai_settings.json", b'{"gemini_api_key":"fixture-private-key"}')
    write(root, "Part2/event_backtest.json", b'{"warehouse":"fixture-private-path"}')
    return root


def test_expiry_means_next_korea_midnight_and_custom_version(monkeypatch):
    monkeypatch.setattr(builder.time, "time", lambda: 1700000000)
    expected = int(datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc).timestamp())
    assert builder.expiry_epoch("2026-12-31") == expected
    settings = builder.BuildSettings("v2.3.4-beta", "2026-12-31", 30)
    assert settings.validate() == expected
    assert settings.version == "v2.3.4-beta"


@pytest.mark.parametrize("version", ["", "v1", "../v1.0", "v1.0/other", "v1.0\n", "v" + "1" * 65 + ".0"])
def test_invalid_version_is_rejected_before_build(version, monkeypatch):
    monkeypatch.setattr(builder.time, "time", lambda: 1700000000)
    with pytest.raises(ValueError):
        builder.BuildSettings(version, "2026-12-31", 10).validate()


@pytest.mark.parametrize("value", ["2026-13-01", "2026-02-30", "26-12-31", "2026-12-31T12:00:00"])
def test_invalid_calendar_date_is_rejected(value):
    with pytest.raises(ValueError):
        builder.expiry_epoch(value)


def test_install_window_boundaries_are_checked(monkeypatch):
    monkeypatch.setattr(builder.time, "time", lambda: 1700000000)
    for minutes in (1, 10, 10080):
        assert builder.BuildSettings("v1.0", "2026-12-31", minutes).validate() > 1700000000
    for minutes in (0, -1, 10081):
        with pytest.raises(ValueError):
            builder.BuildSettings("v1.0", "2026-12-31", minutes).validate()
    monkeypatch.setattr(builder.time, "time", lambda: builder.expiry_epoch("2026-12-31"))
    with pytest.raises(ValueError):
        builder.BuildSettings("v1.0", "2026-12-31", 10).validate()


def test_source_selection_excludes_models_private_tool_and_user_state(project):
    excluded = (
        "models/model.gguf", "models/adapter.safetensors", "runtime/llama.cpp/llama-server.exe",
        "통합설치/.keys/release_rsa.json", "통합설치/releasekit/ui.py",
        "Part1/event_state/candidate.json", "Part1/program/logs/live.json",
        "Part1/program/tests/test_fixture.py", "Part1/program/.venv/site.py",
        "Part3/projects/connections.json", "Part3/generated/Test_SPECIAL001.py",
        "배포/old/START_MOSES.pyw", "검증결과/old_result.json",
        "Part2/generic_backtest/reference_sources/indicator.mq5",
        "Part1/program/MT5/PRICE_of_Moses.mq5", "Part1/program/MT5/PRICE_of_Moses.ex5",
    )
    for name in excluded:
        write(project, name, b"fixture that must not be selected")
    names = {rel.as_posix() for rel, _ in builder.source_paths(project)}
    assert not names.intersection(excluded)
    assert "common_ai/provider.py" in names
    assert "Part3/web/index.html" in names
    assert "settings/strategy_registry.json" in names
    assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)


def test_prepare_data_sanitizes_only_distribution_and_never_copies_code(project, tmp_path):
    selected = list(builder.source_paths(project))
    before = {str(rel): path.read_bytes() for rel, path in selected}
    target = tmp_path / "payload"
    code = builder.prepare_data(project, target, selected)
    assert "common_ai/provider.py" in code
    assert not (target / "common_ai/provider.py").exists()
    assert not any(path.suffix in {".py", ".pyw", ".mq5"} for path in target.rglob("*") if path.is_file())
    config = (target / "Part1/program/config.txt").read_text()
    for secret in ("fixture-telegram-secret", "fixture-recipient", "fixture-commands", "fixture-ai-secret"):
        assert secret not in config
    assert "SYMBOLS=XAUUSD+" in config
    assert "MAIN_ASIA=09:00-12:00" in config
    assert json.loads((target / "settings/ai_settings.json").read_bytes()) == {}
    assert json.loads((target / "Part2/event_backtest.json").read_bytes())["warehouse"] is None
    assert {str(rel): path.read_bytes() for rel, path in selected} == before
    assert b"fixture-private-key" in (project / "settings/ai_settings.json").read_bytes()


def test_required_source_missing_stops_build(project):
    (project / "START_MOSES.pyw").unlink()
    with pytest.raises(ValueError, match="START_MOSES"):
        list(builder.source_paths(project))


def test_notice_files_with_same_basename_and_python_license_are_preserved(tmp_path, monkeypatch):
    packages = tmp_path / "packages"
    first = write(packages, "example/license.txt", b"package notice")
    second = write(packages, "example/native/license.txt", b"native notice")
    pyroot = tmp_path / "python"
    write(pyroot, "LICENSE.txt", b"Python notice")

    class FixtureDistribution:
        metadata = {"Name": "example"}
        files = [PurePosixPath("example/license.txt"), PurePosixPath("example/native/license.txt")]

        def locate_file(self, rel):
            return packages / Path(rel)

    monkeypatch.setattr(builder.importlib.metadata, "distributions", lambda: [FixtureDistribution()])
    monkeypatch.setattr(builder.sys, "base_prefix", str(pyroot))
    target = tmp_path / "payload"
    target.mkdir()
    builder.collect_notices(target)
    notices = list((target / "THIRD_PARTY_LICENSES/example").rglob("license.txt"))
    assert {path.read_bytes() for path in notices} == {first.read_bytes(), second.read_bytes()}
    assert (target / "THIRD_PARTY_LICENSES/Python/LICENSE.txt").read_bytes() == b"Python notice"


def test_original_bytes_remain_identical_when_old_entries_are_archived(tmp_path):
    source = tmp_path / "source95"
    target = tmp_path / "working96"
    for index, old_name in enumerate(inventory.RELOCATIONS):
        value = ("preserved installer entry %d\r\n" % index).encode()
        write(source, old_name, value)
        write(target, inventory.RELOCATIONS[old_name], value)
    write(source, "Part1/program/strategy.py", b"original strategy\r\n")
    write(target, "Part1/program/strategy.py", b"original strategy\r\n")
    expected = inventory.inventory(source)
    result = inventory.compare(source, target, expected)
    assert result["source_changed"] == []
    assert result["copy_changed"] == []
    assert all(not (target / old_name).exists() for old_name in inventory.RELOCATIONS)
    write(target, "Part1/program/strategy.py", b"changed strategy")
    assert inventory.compare(source, target, expected)["copy_changed"] == ["Part1/program/strategy.py"]


@pytest.fixture
def fake_build(project, monkeypatch):
    # Generate a disposable key under the fixture, never the actual tool .keys.
    if os.name != "nt":
        pytest.skip("The fixture key generator uses Windows .NET.")
    key = builder.signing_key(project)
    public = {name: key[name] for name in ("modulus", "exponent")}
    real_copy = shutil.copy2
    events = []
    clock = {"now": 1700000000}
    monkeypatch.setattr(builder.time, "time", lambda: clock["now"])

    def fake_python(root, work, public_key, code_paths, version):
        output = Path(work) / "fixture-frozen"
        write(output, "MOSES.exe", b"fixture desktop executable")
        write(output, "python.exe", b"fixture console worker")
        write(output, "_internal/python313.dll", b"fixture runtime")
        write(Path(work), "python_compile.log", b"fixture compiler completed")
        events.append(("python", version))
        return output

    def fake_mt5(root, stage, expires, **unused):
        outputs = []
        for name in mt5.MT5_PROGRAMS:
            outputs.append(write(Path(stage), "MT5/" + name + ".ex5", b"fixture licensed ex5"))
        return outputs

    def fake_run(args, root, log, **unused):
        output = next(str(arg)[5:] for arg in args if str(arg).startswith("/out:"))
        write(Path(output).parent, Path(output).name, b"MZ synthetic installer")
        Path(log).write_bytes(b"fixture installer compilation completed")
        events.append(("compile", clock["now"]))

    def copy_with_elapsed(source, destination, *args, **kwargs):
        result = real_copy(source, destination, *args, **kwargs)
        if Path(destination).parent.parent.name == "배포":
            assert Path(destination).read_bytes() == b"MZ synthetic installer"
            events.append(("final_copy", clock["now"]))
            clock["now"] += 37
        return result

    def no_notices(destination):
        write(Path(destination), "THIRD_PARTY_LICENSES/fixture/LICENSE.txt", b"fixture notice")

    write(project, "통합설치/prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe", b"fixture, never executed")
    monkeypatch.setattr(builder, "build_python", fake_python)
    monkeypatch.setattr(mt5, "build_mt5", fake_mt5)
    monkeypatch.setattr(builder, "_run", fake_run)
    monkeypatch.setattr(builder, "collect_notices", no_notices)
    monkeypatch.setattr(builder, "compiler_path", lambda: Path("synthetic-csc.exe"))
    monkeypatch.setattr(builder.shutil, "copy2", copy_with_elapsed)
    def fake_validation(root, payload, key, dependencies, resources, paths, evidence, **unused):
        events.append(("validation", clock["now"]))
        return {'passed': True, 'checks': [{'mode': mode} for mode in ('imports', 'http', 'gui')]}
    monkeypatch.setattr(validation, "validate_release", fake_validation)
    return project, public, events, clock


def test_final_file_copy_precedes_issuance_and_payload_has_no_original_sources(fake_build):
    project, public, events, clock = fake_build
    before = {rel.as_posix(): path.read_bytes() for rel, path in builder.source_paths(project)}
    settings = builder.BuildSettings("v4.2", "2026-12-31", 10)
    final, report = builder.build(project, settings, emit=lambda unused: None)
    policy = license_runtime.verify_policy(license_runtime.read_policy(final), public)
    assert policy["issued_at"] == clock["now"] == 1700000037
    assert policy["install_expires_at"] == 1700000637
    assert policy["version"] == report["version"] == "v4.2"
    assert report["source_unchanged"] is True
    assert report["modules"] > 0
    assert report["release_validation"]["passed"] is True
    assert events.index(("validation", 1700000000)) < events.index(("compile", 1700000000))
    assert events.index(("final_copy", 1700000000)) > events.index(("compile", 1700000000))
    assert report["installer"] == final.relative_to(project).as_posix()
    assert str(project) not in json.dumps(report)
    assert {rel.as_posix(): path.read_bytes() for rel, path in builder.source_paths(project)} == before
    archive = next((project / "통합설치/.work").glob("*/payload.zip"))
    with zipfile.ZipFile(archive) as payload:
        names = payload.namelist()
        assert "MOSES.exe" in names and "python.exe" in names
        assert "runtime/code.bundle" in names
        assert "Part3/TEST_SPECIAL/" in names
        assert len([name for name in names if name.endswith(".ex5")]) == 10
        for name in mt5.MT5_PROGRAMS:
            assert payload.read('MT5/' + name + '.ex5') == payload.read('Part1/program/MT5/' + name + '.ex5')
        assert not any(Path(name).suffix.lower() in {".py", ".pyw", ".mq5", ".mqh"} for name in names)
        assert not any(".keys" in name or "release_rsa" in name or "releasekit/ui" in name for name in names)
        assert b"fixture-telegram-secret" not in payload.read("Part1/program/config.txt")


def test_failed_runtime_validation_withholds_installer_and_history(fake_build, monkeypatch):
    project, public, events, clock = fake_build
    def failed_validation(*args, **kwargs):
        raise RuntimeError('실제 배포 EXE 검사 실패: missing runtime module')
    monkeypatch.setattr(validation, 'validate_release', failed_validation)
    with pytest.raises(RuntimeError, match='실제 배포 EXE 검사 실패'):
        builder.build(project, builder.BuildSettings('v4.2', '2026-12-31', 10), emit=lambda value: None)
    assert not any(event[0] == 'compile' for event in events)
    assert not list((project/'배포').rglob('*.exe'))
    assert not (project/release_history.HISTORY_FILE).exists()


def test_current_docs_and_new_runtime_package_are_automatically_selected(project):
    write(project,'Part3/lab/server.py',b'DOCS=["README.md","NEW_GUIDE.md"]\n')
    write(project,'Part3/README.md',b'current readme')
    write(project,'Part3/NEW_GUIDE.md',b'new version guide')
    write(project,'Part3/lab/view.py',b'from added_runtime import helper\n')
    write(project,'Part2/added_runtime/__init__.py',b'VALUE=1\n')
    write(project,'Part2/added_runtime/helper.py',b'VALUE=2\n')
    write(project,'Part3/web/new_asset.bin',b'new web asset')
    selected={rel.as_posix() for rel,source in builder.source_paths(project)}
    assert {'Part3/README.md','Part3/NEW_GUIDE.md','Part2/added_runtime/__init__.py',
            'Part2/added_runtime/helper.py','Part3/web/new_asset.bin'} <= selected


def test_unsuccessful_validation_result_withholds_installer(fake_build, monkeypatch):
    project, public, events, clock = fake_build
    monkeypatch.setattr(validation,'validate_release',lambda *args,**kwargs: {'passed':False,'checks':[]})
    with pytest.raises(RuntimeError,match='설치파일 생성을 중단'):
        builder.build(project,builder.BuildSettings('v4.2','2026-12-31',10),emit=lambda value:None)
    assert not any(event[0]=='compile' for event in events)
    assert not list((project/'배포').rglob('*.exe'))
    assert not (project/release_history.HISTORY_FILE).exists()


@pytest.mark.parametrize("changed_path", ["START_MOSES.pyw", "Part1/program/MT5/DI_of_Moses.mq5",
                                         "Part1/program/MT5/STAFF_Wire_V2.mqh"])
def test_source_changed_during_build_withholds_installer(fake_build, monkeypatch, changed_path):
    project, public, events, clock = fake_build
    real_run = builder._run

    def concurrent_change(args, root, log, **kwargs):
        real_run(args, root, log, **kwargs)
        write(project, changed_path, b"// concurrent change\n")

    monkeypatch.setattr(builder, "_run", concurrent_change)
    with pytest.raises(RuntimeError, match="원본 변경"):
        builder.build(project, builder.BuildSettings("v4.2", "2026-12-31", 10), emit=lambda unused: None)
    assert not list((project / "배포").rglob("*.exe"))
    assert not (project / release_history.HISTORY_FILE).exists()


def test_failed_history_commit_withholds_signed_installer(fake_build, monkeypatch):
    project, public, events, clock = fake_build

    def fail_commit(root, pending):
        raise RuntimeError("fixture history conflict")

    monkeypatch.setattr(release_history, "commit_history", fail_commit)
    with pytest.raises(RuntimeError, match="history conflict"):
        builder.build(project, builder.BuildSettings("v4.2", "2026-12-31", 10), emit=lambda unused: None)
    assert ("final_copy", 1700000000) in events
    assert not list((project / "배포").rglob("*.exe"))
    assert not (project / release_history.HISTORY_FILE).exists()
