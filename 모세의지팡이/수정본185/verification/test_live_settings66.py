"""Live startup never treats an unreadable saved configuration as first use."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_control(folder=ROOT):
    spec = importlib.util.spec_from_file_location("live_settings66", folder / "Part1/live_control.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    return owner


@pytest.fixture
def owner(tmp_path, monkeypatch):
    module = load_control()
    monkeypatch.setattr(module, "SPECIAL_SETTINGS_PATH", tmp_path / "special_settings.json")
    monkeypatch.setattr(module, "discover_live_specials", lambda: ["SPECIAL1", "SPECIAL2"])
    monkeypatch.setattr(module, "start_program", Mock(return_value=(True, "started")))
    monkeypatch.setattr(module.subprocess, "Popen", Mock(side_effect=AssertionError("real engine start forbidden")))
    return module


@pytest.mark.parametrize("content", [
    "", "{", "[]", "null", "{}", '{"specials": null}', '{"specials": []}',
    '{"specials": {"SPECIAL1": null}}', '{"specials": {"SPECIAL1": []}}',
    '{"specials": {"SPECIAL1": {"enabled": "false"}}}',
    '{"specials": {"SPECIAL1": {"enabled": 0}}}',
    '{"specials": {"SPECIAL1": {"enabled": null}}}',
    '{"specials": {"SPECIAL1": {"trigger": 1}}}',
    '{"specials": {"SPECIAL1": {"time_filters": []}}}',
    '{"specials": {"SPECIAL1": {"time_filters": {"MAIN_ASIA": "false"}}}}',
    '{"specials": {"SPECIAL1": {"time_filters": {"MAIN_ASIA": {"enabled": "false"}}}}}',
    '{"specials": {"SPECIAL1": {"time_filters": {"MAIN_ASIA": {"start": "25:00"}}}}}',
    '{"specials": {"SPECIAL1": {"time_filters": {"MAIN_ASIA": {"end": "bad"}}}}}',
    '{"specials": {"SPEICAL1": {"enabled": false}}}',
    '{"specials": {"SPECIAL1": {"enabled": false}, "special1": {"enabled": true}}}',
    '{"specials": {"SPECIAL1": {"enabled": false}, "SPECIAL1": {"enabled": true}}}',
])
def test_existing_invalid_file_blocks_start_without_rewriting(owner, content):
    owner.SPECIAL_SETTINGS_PATH.write_text(content, encoding="utf-8")
    before = owner.SPECIAL_SETTINGS_PATH.read_bytes()
    with pytest.raises(owner.SpecialSettingsError, match="전략 설정 파일"):
        owner.start_live()
    owner.start_program.assert_not_called()
    owner.subprocess.Popen.assert_not_called()
    assert owner.SPECIAL_SETTINGS_PATH.read_bytes() == before


def test_invalid_utf8_blocks_start(owner):
    owner.SPECIAL_SETTINGS_PATH.write_bytes(b"\xff\xfe")
    with pytest.raises(owner.SpecialSettingsError) as error:
        owner.start_live()
    assert error.value.kind == "encoding"
    assert "문자 인코딩" in str(error.value)
    owner.start_program.assert_not_called()
    assert owner.SPECIAL_SETTINGS_PATH.read_bytes() == b"\xff\xfe"


@pytest.mark.parametrize("failure", [PermissionError("denied"), OSError("unreadable")])
def test_read_failure_blocks_start_and_does_not_expose_machine_path(owner, monkeypatch, failure):
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == owner.SPECIAL_SETTINGS_PATH:
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(owner.SpecialSettingsError) as error:
        owner.start_live()
    assert error.value.kind == "read"
    assert str(owner.SPECIAL_SETTINGS_PATH) not in str(error.value)
    owner.start_program.assert_not_called()


def test_first_use_without_file_preserves_code_defaults(owner):
    assert owner.load_special_settings() is None
    assert owner.start_live() == (True, "started")
    owner.start_program.assert_called_once_with("event_host.py", enabled_specials=None,
                                               special_triggers="", special_times="{}")
    assert not owner.SPECIAL_SETTINGS_PATH.exists()


@pytest.mark.parametrize("items,enabled", [
    ({}, {"SPECIAL1", "SPECIAL2"}),
    ({"SPECIAL1": {}}, {"SPECIAL1", "SPECIAL2"}),
    ({"special1": {"enabled": False}}, {"SPECIAL2"}),
    ({"SPECIAL1": {"enabled": False}, "SPECIAL2": {"enabled": False}}, set()),
])
def test_valid_partial_empty_or_disabled_settings_keep_selection(owner, items, enabled):
    owner.SPECIAL_SETTINGS_PATH.write_text(json.dumps({"specials": items}), encoding="utf-8")
    owner.start_live()
    assert owner.start_program.call_args.kwargs["enabled_specials"] == enabled


def test_current_trigger_and_time_settings_are_passed_unchanged(owner):
    times = {"MAIN_ASIA": {"enabled": True, "start": "09:00", "end": "2400"},
             "MAIN_LONDON": False, "MAIN_NEWYORK": {}}
    rows = {"SPECIAL1": {"enabled": True, "trigger": "무지성 브레이커 올존", "time_filters": times},
            "SPECIAL2": {"enabled": False, "trigger": None, "time_filters": None}}
    owner.SPECIAL_SETTINGS_PATH.write_text(json.dumps({"version": 1, "specials": rows}), encoding="utf-8")
    before = owner.SPECIAL_SETTINGS_PATH.read_bytes()
    assert owner.load_special_settings() == rows
    owner.start_live()
    arguments = owner.start_program.call_args.kwargs
    assert arguments["enabled_specials"] == {"SPECIAL1"}
    assert json.loads(arguments["special_triggers"]) == {"SPECIAL1": "무지성 브레이커 올존"}
    assert json.loads(arguments["special_times"]) == {"SPECIAL1": times}
    assert owner.SPECIAL_SETTINGS_PATH.read_bytes() == before


@pytest.mark.parametrize("retired", ["REGIME", "SUPER", "DIVERGENCE"])
def test_retired_oz_profile_keeps_individual_quarantine_contract(owner, retired):
    rows = {"SPECIAL1": {"enabled": True, "trigger": retired},
            "SPECIAL2": {"enabled": True, "trigger": "브레이커 올존"}}
    owner.SPECIAL_SETTINGS_PATH.write_text(json.dumps({"specials": rows}), encoding="utf-8")
    before = owner.SPECIAL_SETTINGS_PATH.read_bytes()
    loaded = owner.load_special_settings()
    assert loaded["SPECIAL1"]["enabled"] is False
    assert retired in loaded["SPECIAL1"]["load_error"]
    assert loaded["SPECIAL1"]["original"] == rows["SPECIAL1"]
    owner.start_live()
    arguments = owner.start_program.call_args.kwargs
    assert arguments["enabled_specials"] == {"SPECIAL2"}
    assert json.loads(arguments["special_triggers"]) == {"SPECIAL2": "브레이커 올존"}
    assert owner.SPECIAL_SETTINGS_PATH.read_bytes() == before


def test_independent_cli_reports_invalid_file_with_nonzero_exit(owner, capsys):
    owner.SPECIAL_SETTINGS_PATH.write_text("{", encoding="utf-8")
    assert owner.main(["start"]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert "JSON 형식" in response["message"]
    owner.start_program.assert_not_called()


def test_guard_still_works_after_project_is_moved(tmp_path, monkeypatch):
    relocated = tmp_path / "이동한 프로젝트"
    part1 = relocated / "Part1"
    part1.mkdir(parents=True)
    shutil.copyfile(ROOT / "Part1/live_control.py", part1 / "live_control.py")
    path = part1 / "special_settings.json"
    path.write_text('{"specials": []}', encoding="utf-8")
    module = load_control(relocated)
    start = Mock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(module, "start_program", start)
    with pytest.raises(module.SpecialSettingsError):
        module.start_live()
    start.assert_not_called()
    assert module.SPECIAL_SETTINGS_PATH == path
