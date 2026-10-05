"""Independent engine control survives removal of the two legacy GUI applications."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location("launcher65_" + Path(relative).stem,
                                                 ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("settings,enabled", [
    (None, None),
    ({"SPECIAL1": {"enabled": False}, "SPECIAL2": {"enabled": True}}, {"SPECIAL2"}),
    ({"SPECIAL1": {"enabled": False}, "SPECIAL2": {"enabled": False}}, set()),
])
def test_public_start_keeps_saved_selection_contract(monkeypatch, settings, enabled):
    owner = load("Part1/live_control.py")
    monkeypatch.setattr(owner, "load_special_settings", lambda: settings)
    monkeypatch.setattr(owner, "discover_live_specials", lambda: ["SPECIAL1", "SPECIAL2"])
    triggers = Mock(return_value="saved triggers")
    start = Mock(return_value=(True, "started"))
    monkeypatch.setattr(owner, "special_trigger_env", triggers)
    monkeypatch.setattr(owner, "start_program", start)
    assert owner.start_live() == (True, "started")
    triggers.assert_called_once_with(settings)
    start.assert_called_once_with("event_host.py", enabled_specials=enabled,
                                 special_triggers="saved triggers", special_times="{}")


def test_public_start_keeps_time_filter_json(monkeypatch):
    owner = load("Part1/live_control.py")
    times = {"LONDON": {"enabled": True, "start": "0900", "end": "1200"}}
    settings = {"SPECIAL1": {"enabled": True, "time_filters": times},
                "SPECIAL2": {"enabled": False, "time_filters": None}}
    monkeypatch.setattr(owner, "load_special_settings", lambda: settings)
    monkeypatch.setattr(owner, "discover_live_specials", lambda: list(settings))
    monkeypatch.setattr(owner, "special_trigger_env", lambda value: "")
    start = Mock(return_value=(True, "started"))
    monkeypatch.setattr(owner, "start_program", start)
    owner.start_live()
    assert json.loads(start.call_args.kwargs["special_times"]) == {"SPECIAL1": times}


@pytest.mark.parametrize("action,success", [("start", True), ("start", False),
                                           ("stop", True), ("stop", False)])
def test_independent_part1_cli_returns_operation_exit_code(monkeypatch, capsys, action, success):
    owner = load("Part1/live_control.py")
    operation = Mock(return_value=(success, "operation result"))
    monkeypatch.setattr(owner, action + "_live", operation)
    assert owner.main([action]) == (0 if success else 1)
    assert json.loads(capsys.readouterr().out) == {"ok": success, "message": "operation result"}
    operation.assert_called_once_with()


def test_independent_status_does_not_start_engine(monkeypatch, capsys):
    owner = load("Part1/live_control.py")
    monkeypatch.setattr(owner, "find_program_process_ids", lambda name: [101])
    monkeypatch.setattr(owner, "load_special_settings", lambda: {"SPECIAL1": {"enabled": False}})
    monkeypatch.setattr(owner, "discover_live_specials", lambda: ["SPECIAL1", "SPECIAL2"])
    monkeypatch.setattr(owner.subprocess, "Popen", Mock(side_effect=AssertionError("must not start")))
    assert owner.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True, "running": True, "pids": [101], "enabled_specials": ["SPECIAL2"]}


def test_independent_cli_reports_settings_error(monkeypatch, capsys):
    owner = load("Part1/live_control.py")
    monkeypatch.setattr(owner, "start_live", Mock(side_effect=ValueError("invalid settings")))
    assert owner.main(["start"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "message": "invalid settings"}


def test_part1_help_works_in_relocated_folder_without_part3(tmp_path):
    relocated = tmp_path / "이동한 프로젝트"
    part1 = relocated / "Part1"
    part1.mkdir(parents=True)
    for name in ("__main__.py", "live_control.py"):
        shutil.copyfile(ROOT / "Part1" / name, part1 / name)
    result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "Part1", "--help"],
                            cwd=relocated, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "{start,status,stop}" in result.stdout
    assert not list(relocated.rglob("__pycache__"))
    assert not (part1 / "logs").exists()


@pytest.mark.parametrize("entry,cwd,module", [
    ("Part1", "", True),
    ("event_backtest", "Part2", True),
    ("bootstrap.py", "Part2", False),
])
def test_independent_help_forbids_ui_and_part3_imports(entry, cwd, module):
    code = """import runpy, sys
class EngineOnly:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'tkinter', 'webview', 'lab', 'Part3'}:
            raise AssertionError('Independent engine imported UI or Part3: ' + name)
sys.meta_path.insert(0, EngineOnly())
sys.argv = [sys.argv[1], '--help']
""" + ("runpy.run_module(sys.argv[0], run_name='__main__')" if module else
        "runpy.run_path(sys.argv[0], run_name='__main__')")
    result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code, entry],
                            cwd=ROOT / cwd, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("arguments,expected", [
    (["--backtest"], ["--help"]),
    (["--backtest", "--help"], ["--help"]),
    (["--backtest", "--", "plan", "--scenario", "scenarios/xau_selected.json"],
     ["plan", "--scenario", "scenarios/xau_selected.json"]),
])
def test_bootstrap_forwards_existing_headless_backtest_cli(monkeypatch, arguments, expected):
    owner = load("Part2/bootstrap.py")
    prepared = Mock()
    command = Mock(return_value=3)
    monkeypatch.setattr(owner, "ensure_environment", prepared)
    monkeypatch.setattr(owner, "log", lambda text: None)
    monkeypatch.setattr(owner.subprocess, "call", command)
    assert owner.main(arguments) == 3
    prepared.assert_called_once_with()
    assert command.call_args.args[0] == [str(owner.executable(owner.ROOT / ".venv-generic")),
                                       "-m", "event_backtest", *expected]
    assert command.call_args.kwargs["cwd"] == owner.ROOT


def test_bootstrap_environment_preparation_does_not_launch_ui(monkeypatch):
    owner = load("Part2/bootstrap.py")
    prepared = Mock()
    monkeypatch.setattr(owner, "ensure_environment", prepared)
    monkeypatch.setattr(owner, "log", lambda text: None)
    monkeypatch.setattr(owner.subprocess, "call", Mock(side_effect=AssertionError("must not launch")))
    assert owner.main([]) == 0
    prepared.assert_called_once_with()


def test_environment_probe_does_not_require_tk(monkeypatch, tmp_path):
    owner = load("Part2/bootstrap.py")
    env = tmp_path / "runtime"
    executable = owner.executable(env)
    executable.parent.mkdir(parents=True)
    executable.touch()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"prefix": str(env), "modules": {}}))

    monkeypatch.setattr(owner.subprocess, "run", run)
    assert owner.probe(env)[0]
    assert "tkinter" not in commands[0][3]
    assert "duckdb" in commands[0][3]


def test_removed_gui_entrypoints_are_not_launcher_targets():
    for relative in ("Part1/OZ_SYSTEM CONTROL.pyw", "Part2/BACKTEST CONTROL.pyw", "Part2/LIVE_REPLAY.pyw"):
        assert not (ROOT / relative).exists()
    launcher = (ROOT / "Part2/START_BACKTEST.cmd").read_text(encoding="utf-8-sig")
    assert "--launch" not in launcher
    assert 'bootstrap.py" %*' in launcher
