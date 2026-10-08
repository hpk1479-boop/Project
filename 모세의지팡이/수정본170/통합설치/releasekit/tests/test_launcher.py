"""Exercise the double-click chain through cmd and Windows PowerShell 5.1.

CheckOnly never opens a window, downloads files, or builds a release. The
relocation fixture has no caches and never copies original application files.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launch entry")
TOOLS = Path(__file__).resolve().parents[2]
PROJECT = TOOLS.parent


def run_cmd(project, page, check_only=True):
    launcher = project / "모세의지팡이 배포.cmd"
    arguments = ' -CheckOnly' if check_only else ''
    command = f'chcp {page}>nul & call "{launcher}"{arguments}'
    # cmd.exe uses its own quoting rules, unlike normal Windows executables.
    # list2cmdline would insert backslashes before the embedded path quotes.
    native = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{command}"'
    # Match Explorer's double-click working directory. In particular, a test
    # run from the production tools directory must not import its checker
    # instead of the relocated fixture's checker.
    # A launched GUI can inherit an output handle; use a file so reading the
    # launcher's output does not wait for that GUI to close.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            native, cwd=project,
            stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output.seek(0)
        return subprocess.CompletedProcess(result.args, result.returncode, output.read())


@pytest.mark.parametrize("page", [949, 65001])
def test_double_click_chain_checks_base_python_without_ui(page):
    result = run_cmd(PROJECT, page)
    assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
    assert b"CHECK_ONLY_OK" in result.stdout


@pytest.fixture(scope="module")
def relocated_project(tmp_path_factory):
    root = tmp_path_factory.mktemp("launcher_relocation") / "이동 테스트 수정본 100"
    tools = root / "통합설치"
    tools.mkdir(parents=True)
    shutil.copy2(PROJECT / "모세의지팡이 배포.cmd", root / "모세의지팡이 배포.cmd")
    # Copy only developer launcher files; no original application or secrets.
    for source in TOOLS.iterdir():
        if source.is_file() and source.suffix in {".ps1", ".py"}:
            shutil.copy2(source, tools / source.name)
    package = tools / "releasekit"
    package.mkdir()
    for source in (TOOLS / "releasekit").iterdir():
        if source.is_file() and source.suffix == ".py":
            shutil.copy2(source, package / source.name)
    yield root


@pytest.mark.parametrize("page", [949, 65001])
def test_double_click_chain_survives_korean_and_space_relocation(relocated_project, page):
    result = run_cmd(relocated_project, page)
    assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
    assert b"CHECK_ONLY_OK" in result.stdout
    for name in ('.work', '.buildenv', 'prerequisites'):
        assert not (relocated_project / '통합설치' / name).exists()


def test_check_only_failure_does_not_install_dependencies(relocated_project):
    tools = relocated_project / "통합설치"
    checker = tools / "releasekit/check_launcher.py"
    original_check = checker.read_bytes()
    try:
        checker.write_text("print('FIXTURE_ENV_FAILURE')\nraise SystemExit(7)\n", encoding="utf-8")
        result = run_cmd(relocated_project, 65001)
        assert result.returncode == 1
        assert b"CHECK_ONLY_OK" not in result.stdout
        for name in ('.work', '.buildenv', 'prerequisites'):
            assert not (tools / name).exists()
    finally:
        checker.write_bytes(original_check)


def test_powershell_file_is_parseable_by_windows_powershell_51():
    script = TOOLS / "모세의지팡이 배포.ps1"
    quoted = str(script).replace("'", "''")
    code = (
        "$tokens=$null;$errors=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{quoted}',"
        "[ref]$tokens,[ref]$errors);"
        "$errors | ForEach-Object {$_.Message};"
        "if($errors.Count){exit 1}; Write-Output 'PARSE_OK'"
    )
    encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-EncodedCommand", encoded],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
    assert b"PARSE_OK" in result.stdout


def test_renamed_launcher_displays_real_settings_window(relocated_project):
    """Open through the renamed CMD/PS1 chain, verify the native window, close it."""
    receipts = relocated_project / '통합설치/.buildtmp'
    before = set(receipts.glob('launcher_*.ready.json'))
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    handles = []
    try:
        result = run_cmd(relocated_project, 65001, check_only=False)
        new_receipts = set(receipts.glob('launcher_*.ready.json')) - before
        for receipt in new_receipts:
            state = json.loads(receipt.read_text(encoding='utf-8'))
            expected_pid = state['pid']

            @enum_proc
            def collect_window(handle, unused):
                process_id = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
                if process_id.value == expected_pid:
                    text = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(handle, text, len(text))
                    if text.value == '모세의지팡이 배포':
                        handles.append(handle)
                return True

            user32.EnumWindows(collect_window, 0)
            assert state['ready'] is True
            assert state['window_title'] == '모세의지팡이 배포'
        assert result.returncode == 0, result.stdout.decode('utf-8', errors='replace')
        assert len(new_receipts) == 1
        assert handles, 'The launcher reported ready without its settings window.'
        assert all(user32.IsWindowVisible(handle) for handle in handles)
    finally:
        for handle in handles:
            user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE; no build is started.
    for name in ('.work', '.buildenv', 'prerequisites'):
        assert not (relocated_project / '통합설치' / name).exists()
