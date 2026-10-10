"""Unified setup contracts, with no real install, download, or engine launch.

PowerShell is exercised through temporary UTF-8 BOM scripts. Native child
processes are restricted to harmless Python probes and argv echoes; installer
and launch flows replace their external boundaries with fail-closed mocks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "통합설치"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is required")
MARKER = "__INSTALLER_TEST_JSON__"


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps_object(value):
    return "(" + ps_literal(json.dumps(value, ensure_ascii=False)) + " | ConvertFrom-Json)"


def run_ps(tmp_path, body, *, cwd=None, expected_code=0):
    script = tmp_path / "무해한 검사 스크립트.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "$OutputEncoding = [Console]::OutputEncoding\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        cwd=cwd or tmp_path, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, env=os.environ.copy(),
    )
    assert result.returncode == expected_code, (
        f"PowerShell exit {result.returncode}, expected {expected_code}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def run_json(tmp_path, body, **kwargs):
    result = run_ps(
        tmp_path, body + "\nWrite-Output ('" + MARKER + "' + "
        "($result | ConvertTo-Json -Depth 20 -Compress))\n", **kwargs,
    )
    payloads = [line[len(MARKER):] for line in result.stdout.splitlines()
                if line.startswith(MARKER)]
    assert len(payloads) == 1, result.stdout
    return json.loads(payloads[0])


def common_prelude(root=ROOT):
    return ". " + ps_literal(root / "통합설치" / "common.ps1") + "\n"


def base_info(executable=sys.executable, prefix=None):
    return {"version": [3, 12, 10], "bits": 64, "executable": str(executable),
            "base_executable": str(executable), "prefix": str(prefix or Path(executable).parent),
            "base_prefix": str(prefix or Path(executable).parent)}


def copy_tools(tmp_path):
    root = tmp_path / "옮긴 프로젝트 & 작은'따옴표"
    target = root / "통합설치"
    target.mkdir(parents=True)
    for source in TOOLS.iterdir():
        if source.is_file() and source.suffix.lower() in {".ps1", ".bat", ".txt", ".md"}:
            shutil.copyfile(source, target / source.name)
    for name in ("MOSES_실행.bat", "requirements-main.txt"):
        source = ROOT / name
        if source.is_file():
            shutil.copyfile(source, root / name)
    return root


def append_mocks(root, text):
    """Replace boundaries in a private tool copy, never in the product files."""
    common = root / "통합설치" / "common.ps1"
    common.write_text(common.read_text(encoding="utf-8-sig") + "\n" + text,
                      encoding="utf-8-sig")


NO_EXTERNAL_ACTIONS = """
function Start-Process { throw 'unexpected external process' }
function Invoke-WebRequest { throw 'unexpected network request' }
function Invoke-RestMethod { throw 'unexpected network request' }
"""


def entry_command(root, filename, arguments):
    # A called .ps1 can return to the harness after `exit`; propagate its code.
    return "& " + ps_literal(root / "통합설치" / filename) + " " + arguments + "\nexit $LASTEXITCODE\n"


@pytest.mark.parametrize("version,bits,accepted", [
    ([3, 12, 10], 64, True), ([3, 13, 5], 64, True),
    ([3, 10, 11], 64, False), ([3, 11, 9], 64, False),
    ([3, 14, 0], 64, False), ([3, 12, 10], 32, False),
])
def test_python_selection_checks_supported_version_and_architecture(tmp_path, version, bits, accepted):
    info = base_info()
    info.update(version=version, bits=bits)
    actual = run_json(tmp_path, common_prelude() +
                      "$result = [bool](Test-CompatiblePython " + ps_object(info) + ")")
    assert actual is accepted


def test_native_arguments_round_trip_without_shell_interpretation(tmp_path):
    arguments = ["", "한글 공백", "a&b", "작은'따옴표", '큰"따옴표',
                 "끝\\", "공백 경로\\", "\\\\서버\\공유 폴더\\"]
    command = ["-X", "utf8", "-c",
               "import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=False))", *arguments]
    body = common_prelude() + "$r = Invoke-Process -Executable " + ps_literal(sys.executable)
    body += " -Arguments @(" + ",".join(ps_literal(arg) for arg in command) + ")"
    body += " -WorkingDirectory " + ps_literal(tmp_path) + "\n$result = $r"
    result = run_json(tmp_path, body)
    assert result["ExitCode"] == 0
    assert json.loads(result["StdOut"].strip()) == arguments


def test_native_failure_retains_child_exit_code_and_stderr(tmp_path):
    body = common_prelude() + "$result = Invoke-Process -Executable " + ps_literal(sys.executable)
    body += " -Arguments @('-c', 'import sys;sys.stderr.write(\"expected failure\");sys.exit(7)')"
    body += " -WorkingDirectory " + ps_literal(tmp_path)
    result = run_json(tmp_path, body)
    assert result["ExitCode"] == 7
    assert "expected failure" in result["StdErr"]


def test_child_environment_disables_automatic_install_without_changing_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("PYLAUNCHER_ALLOW_INSTALL", "inherited install permission")
    monkeypatch.setenv("PYTHON_MANAGER_AUTOMATIC_INSTALL", "true")
    keys = ["PYLAUNCHER_ALLOW_INSTALL", "PYTHON_MANAGER_AUTOMATIC_INSTALL", "PYTHONUTF8",
            "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE"]
    code = "import json,os;print(json.dumps({k:os.environ.get(k) for k in " + repr(keys) + "}))"
    body = common_prelude() + "$result = Invoke-Process -Executable " + ps_literal(sys.executable)
    body += " -Arguments @('-c'," + ps_literal(code) + ") -WorkingDirectory " + ps_literal(tmp_path)
    result = run_json(tmp_path, body)
    assert result["ExitCode"] == 0
    assert json.loads(result["StdOut"]) == {
        "PYLAUNCHER_ALLOW_INSTALL": None, "PYTHON_MANAGER_AUTOMATIC_INSTALL": "false",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert os.environ["PYLAUNCHER_ALLOW_INSTALL"] == "inherited install permission"
    assert os.environ["PYTHON_MANAGER_AUTOMATIC_INSTALL"] == "true"


def test_python_probe_reads_actual_executable_and_prefix(tmp_path):
    result = run_json(tmp_path, common_prelude() + "$result = Get-PythonInfo -Executable " +
                      ps_literal(sys.executable))
    assert result["version"] == list(sys.version_info[:3])
    assert result["bits"] == (64 if sys.maxsize > 2**32 else 32)
    assert Path(result["executable"]).resolve() == Path(sys.executable).resolve()
    assert Path(result["prefix"]).resolve() == Path(sys.prefix).resolve()
    assert Path(result["base_prefix"]).resolve() == Path(sys.base_prefix).resolve()


def test_dot_source_has_no_install_or_filesystem_side_effects(tmp_path):
    root = copy_tools(tmp_path)
    before = {str(path.relative_to(root)) for path in root.rglob("*")}
    body = "function global:Start-Process { throw 'unexpected process launch' }\n"
    body += "function global:Invoke-WebRequest { throw 'unexpected network request' }\n"
    body += common_prelude(root) + "$result = $true"
    assert run_json(tmp_path, body) is True
    after = {str(path.relative_to(root)) for path in root.rglob("*")}
    assert after == before


def test_portable_log_removes_project_and_host_user_paths(tmp_path):
    root = tmp_path / "사용자 프로젝트"
    outside = Path.home() / "호스트 개인 경로" / "python.exe"
    text = f"project={root / 'Part2' / '.venv-generic'}; host={outside}"
    body = common_prelude() + "$result = ConvertTo-PortableText -Text " + ps_literal(text)
    body += " -ProjectRoot " + ps_literal(root)
    result = run_json(tmp_path, body)
    assert str(root).casefold() not in result.casefold()
    assert str(Path.home()).casefold() not in result.casefold()
    assert Path.home().name.casefold() not in result.casefold()
    assert "Part2" in result and ".venv-generic" in result


# The install.ps1 installer was deleted at the user's request (통합설치/README.md); its five tests went with it.


def test_backtest_failure_restores_both_independent_environments(tmp_path):
    root = copy_tools(tmp_path)
    for kind in ("generic", "history"):
        env = root / "Part2" / (".venv-" + kind)
        env.mkdir(parents=True)
        (env / "original.txt").write_text(kind, encoding="utf-8")
    body = common_prelude(root) + NO_EXTERNAL_ACTIONS + """
function Get-EnvironmentReport { return [pscustomobject]@{ OK = $false; Reason = 'fixture missing module' } }
function Invoke-Process {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    foreach ($kind in @('generic', 'history')) {
        $envPath = Join-Path $WorkingDirectory ('.venv-' + $kind)
        New-Item -ItemType Directory -Path $envPath -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $envPath 'partial.txt'), 'failed ' + $kind)
    }
    return [pscustomobject]@{ ExitCode = 9; StdOut = ''; StdErr = 'fixture bootstrap failure' }
}
$failed = $false
"""
    body += "try { Install-BacktestEnv " + ps_object(base_info()) + " " + ps_literal(root)
    body += " | Out-Null } catch { $failed = $true; $reason = $_.Exception.Message }\n"
    body += "$result = @{ failed = $failed; reason = $reason }"
    result = run_json(tmp_path, body)
    assert result["failed"] is True and "종료 코드 9" in result["reason"]
    for kind in ("generic", "history"):
        assert (root / "Part2" / (".venv-" + kind) / "original.txt").read_text() == kind
        failed = list((root / "Part2").glob(".venv-" + kind + ".failed-*"))
        assert len(failed) == 1
        assert (failed[0] / "partial.txt").read_text() == "failed " + kind


def prepare_launcher(root, *, old_prefix=False):
    env = root / "통합설치" / ".venv-moses"
    scripts = env / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_bytes(b"not an executable")
    (scripts / "python.exe").write_bytes(b"not an executable")
    (root / "START_MOSES.pyw").write_text("raise AssertionError('must not execute')", encoding="utf-8")
    mocks = NO_EXTERNAL_ACTIONS + "\n$script:FixtureBase = " + ps_object(base_info()) + "\n"
    mocks += """
function Get-PythonInfo {
    param([string]$Executable, [string[]]$Arguments = @())
    if ($Executable -eq $script:FixtureBase.executable) { return $script:FixtureBase }
    $prefix = Split-Path -Parent (Split-Path -Parent $Executable)
"""
    if old_prefix:
        mocks += "$prefix = Join-Path (Split-Path -Parent $script:SetupRoot) 'old copied project\\.venv-moses'\n"
    mocks += """
    return [pscustomobject]@{
        version = $script:FixtureBase.version; bits = 64; executable = $Executable
        base_executable = $script:FixtureBase.executable; prefix = $prefix
        base_prefix = $script:FixtureBase.prefix
    }
}
function Get-WebViewVersion { return '120.0.0.0' }
function Invoke-Process {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    return [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
}
function Download-Installer { throw 'unexpected download' }
"""
    append_mocks(root, mocks)
    return env


def test_relocated_launcher_works_from_other_cwd_with_relative_environment(tmp_path):
    initial = copy_tools(tmp_path)
    root = tmp_path / "다시 이동한 도구 & 한글 공백'"
    initial.rename(root)
    env = prepare_launcher(root)
    other_cwd = tmp_path / "작업 위치 & 별도 폴더"
    other_cwd.mkdir()
    run_ps(tmp_path, entry_command(root, "launch.ps1", "-CheckOnly"),
           cwd=other_cwd)
    assert env.relative_to(root).as_posix() == "통합설치/.venv-moses"
    log = (root / "통합설치" / "logs" / "launch.log").read_text(encoding="utf-8-sig")
    assert "실행 전 검사 통과" in log
    assert str(root).casefold() not in log.casefold()
    assert "전용 창 시작" not in log


def test_launcher_rejects_copied_venv_prefix_and_explains_repair(tmp_path):
    root = copy_tools(tmp_path)
    prepare_launcher(root, old_prefix=True)
    result = run_ps(tmp_path, entry_command(root, "launch.ps1", "-CheckOnly"), expected_code=1)
    assert "복사된 환경" in result.stdout
    assert "검사 통과" not in result.stdout
