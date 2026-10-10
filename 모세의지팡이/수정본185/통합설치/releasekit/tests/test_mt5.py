from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


_MODULE = Path(__file__).resolve().parents[1] / "mt5.py"
_SPEC = importlib.util.spec_from_file_location("moses_release_mt5", _MODULE)
mt5 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mt5)


def _project(tmp_path: Path, marker: str = "intended") -> Path:
    root = tmp_path / "project"
    source = root / "Part1" / "program" / "MT5"
    source.mkdir(parents=True)
    for name in mt5.MT5_PROGRAMS:
        text = (
            f'// {marker}\r\n#property strict\r\n'
            'int OnInit()\r\n{\r\n   return INIT_SUCCEEDED;\r\n}\r\n'
            'void OnTick() { double price=1.0; }\r\n'
        )
        (source / f"{name}.mq5").write_bytes(text.encode("utf-8"))
        (source / f"{name}.ex5").write_bytes(b"stale-unlicensed-binary")
    (source / "STAFF_Wire_Schema.mqh").write_bytes(b"// unchanged include\r\n")
    return root


def _fake_compiler(monkeypatch, *, succeeds: bool = True):
    def compile_generated(command, **kwargs):
        source = Path(kwargs["cwd"]) / command[1].removeprefix("/compile:")
        log = source.with_suffix(".log")
        log_text = f"{source} : information: compiling {source}\n"
        if succeeds:
            source.with_suffix(".ex5").write_bytes(b"fresh-ex5:" + source.read_bytes())
            log_text += "Result: 0 errors, 0 warnings, 5 ms elapsed\n"
        else:
            log_text += "Result: 1 errors, 0 warnings\n"
        log.write_bytes(log_text.encode("utf-16"))
        return subprocess.CompletedProcess(command, 1, b"", b"")
    monkeypatch.setattr(mt5.subprocess, "run", compile_generated)
    monkeypatch.setattr(mt5, "find_metaeditor", lambda selected=None: Path("MetaEditor64.exe"))


@pytest.mark.parametrize("now,expected", [(0, False), (-1, False), (33399, True), (33400, False), (33401, False)])
def test_exclusive_expiry_boundary(now, expected):
    assert mt5.expiry_allows(now, 1000) is expected


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5, 32535216000])
def test_invalid_expiry_is_rejected(bad):
    with pytest.raises(ValueError):
        mt5.expiry_helper(bad, mt5.MT5_PROGRAMS[0])


def test_generated_gate_preserves_application_calculation_code_exactly():
    source = b"// source\r\nint OnInit()\r\n{\r\n return INIT_SUCCEEDED;\r\n}\r\nint OnCalculate() { return 19; }\r\n"
    generated = mt5.licensed_source(source, 1234567890, "RSI_of_Moses")
    helper = mt5.expiry_helper(1234567890, "RSI_of_Moses", "\r\n").encode("utf-8")
    gate = b"\r\n   if(!MosesDeployment96Allowed()) return INIT_FAILED;"
    assert generated.startswith(helper)
    assert generated[len(helper):].replace(gate, b"", 1) == source
    assert b"FileFlush(handle)" in helper
    assert b"FILE_MODIFY_DATE" in helper
    assert b"TimeGMTOffset()" not in helper
    assert b"TimeLocal()" not in helper and b"TimeGMT()" not in helper
    assert b"OnTick" not in helper and b"OnCalculate" not in helper
    assert b"#import" not in helper and b"Print(" not in helper and b"Alert(" not in helper


def test_no_oninit_or_already_injected_source_fails_closed():
    with pytest.raises(mt5.MT5BuildError):
        mt5.licensed_source(b"void OnTick() {}", 1000, mt5.MT5_PROGRAMS[0])
    generated = mt5.licensed_source(b"int OnInit(){return 0;}", 1000, mt5.MT5_PROGRAMS[0])
    with pytest.raises(mt5.MT5BuildError):
        mt5.licensed_source(generated, 2000, mt5.MT5_PROGRAMS[0])


def test_source_folder_cannot_be_staging(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _fake_compiler(monkeypatch)
    source = root / "Part1" / "program" / "MT5"
    with pytest.raises(mt5.MT5BuildError):
        mt5.build_mt5(root, source, 1234567890)
    with pytest.raises(mt5.MT5BuildError):
        mt5.build_mt5(root, source / "copies", 1234567890)
    with pytest.raises(mt5.MT5BuildError):
        mt5.build_mt5(root, root, 1234567890)


def test_build_uses_latest_sources_and_preserves_originals(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _fake_compiler(monkeypatch)
    source = root / "Part1" / "program" / "MT5"
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    outputs = mt5.build_mt5(root, tmp_path / "stage", 1234567890)
    assert len(outputs) == 5
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert all(p.suffix == ".ex5" for p in outputs)
    assert all(p.read_bytes().startswith(b"fresh-ex5:") for p in outputs)
    assert outputs[0].parent.name == "EA"
    assert all(p.parent.name == "Indicators" for p in outputs[1:])
    receipt_path = next((tmp_path / "stage" / "_mt5_build").rglob("compile_receipt.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["original_sources_unchanged"] is True
    assert all(not Path(p).is_absolute() for p in receipt["sources"])
    assert str(tmp_path) not in receipt_path.read_text(encoding="utf-8")
    for log in receipt_path.parent.glob("*.log"):
        assert str(tmp_path) not in log.read_text(encoding="utf-8")
    latest = source / "RSI_of_Moses.mq5"
    latest.write_bytes(latest.read_bytes().replace(b"intended", b"latest-modification"))
    rebuilt = mt5.build_mt5(root, tmp_path / "stage2", 1234567900)
    assert b"latest-modification" in rebuilt[2].read_bytes()
    assert b"1234600300" in rebuilt[2].read_bytes()


def test_calendar_midnight_is_identical_in_every_pc_timezone():
    import calendar
    import datetime
    selected_midnight = datetime.datetime(2027, 1, 1)
    local_midnight = calendar.timegm(selected_midnight.timetuple())
    builder_input = local_midnight - 9 * 3600
    # PC-local calendar inputs have the same meaning regardless of UTC offset.
    for offset_hours in (-8, 0, 9):
        utc = datetime.datetime(2026, 12, 31, 23, 59, 59) - datetime.timedelta(hours=offset_hours)
        local = utc + datetime.timedelta(hours=offset_hours)
        assert mt5.expiry_allows(calendar.timegm(local.timetuple()), builder_input)
        local += datetime.timedelta(seconds=1)
        assert not mt5.expiry_allows(calendar.timegm(local.timetuple()), builder_input)


def test_failed_compilation_never_returns_or_reuses_old_ex5(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _fake_compiler(monkeypatch, succeeds=False)
    with pytest.raises(mt5.MT5BuildError, match="compilation failed"):
        mt5.build_mt5(root, tmp_path / "stage", 1234567890)
    assert not list((tmp_path / "stage" / "MT5").rglob("*.ex5"))


def test_build_survives_project_and_staging_relocation(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _fake_compiler(monkeypatch)
    moved = tmp_path / "다른 위치" / "수정본98"
    moved.parent.mkdir()
    root.rename(moved)
    outputs = mt5.build_mt5(moved, tmp_path / "다른 위치" / "새 배포", 1234567890)
    assert len(outputs) == 5
    assert all(path.is_file() for path in outputs)


def test_original_modification_during_compile_is_detected(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _fake_compiler(monkeypatch)
    fake_compile = mt5.subprocess.run
    original = root / "Part1" / "program" / "MT5" / "RSI_of_Moses.mq5"
    def changed(command, **kwargs):
        result = fake_compile(command, **kwargs)
        original.write_bytes(original.read_bytes() + b"// concurrent change\r\n")
        return result
    monkeypatch.setattr(mt5.subprocess, "run", changed)
    with pytest.raises(mt5.MT5BuildError, match="sources changed"):
        mt5.build_mt5(root, tmp_path / "stage", 1234567890)

