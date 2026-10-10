"""Isolated workers for the official runner; product code remains unmodified."""
from __future__ import annotations

import compileall
import ipaddress
import json
import os
import platform
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_current import relative, portable_text, portable_value


def block_external_network():
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def allow(address):
        if isinstance(address, tuple):
            host = address[0]
            if host == "localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise AssertionError("기본 검증의 외부 네트워크 접속 차단: " + str(host))
        # Local IPC such as AF_UNIX is allowed.
        if isinstance(address, (str, bytes)):
            return
        raise AssertionError("알 수 없는 네트워크 주소")

    def connect(self, address):
        allow(address)
        return original_connect(self, address)

    def connect_ex(self, address):
        allow(address)
        return original_connect_ex(self, address)

    def sendto(self, data, *args):
        allow(args[-1])
        return original_sendto(self, data, *args)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto


def block_workspace_ai(root=ROOT, client_class=None):
    """Never reuse or launch this working project's real AI service in a test.

    Loopback remains available for isolated fake servers. Network monkeypatches
    do not cross a subprocess boundary, so guard the client before it can read
    the real endpoint, start a service, or send a request with a cached endpoint.
    """
    protected = Path(root).resolve()
    if client_class is None:
        if not (protected / 'common_ai/client.py').is_file():
            return  # The verifier's own tiny fixture has no product AI module.
        from common_ai.client import Client
        client_class = Client
    def wrap(method):
        def checked(self, *args, **kwargs):
            selected = getattr(self, 'root', None)
            if selected is not None and Path(selected).resolve() == protected:
                raise AssertionError('오프라인 검사는 작업본 AI 서비스에 접근할 수 없습니다. 임시 프로젝트와 가짜 전송을 사용하세요.')
            return method(self, *args, **kwargs)
        return checked
    for name in ('_read_record', '_start', '_request'):
        setattr(client_class, name, wrap(getattr(client_class, name)))


def pytest_worker(target, temporary, manifest):
    import pytest

    class Counts:
        def __init__(self):
            self.passed = set()
            self.failed = set()
            self.skipped = {}
            self.failures = []
            self.deselected = set()

        def pytest_collection_modifyitems(self, config, items):
            keep, omit = [], []
            for item in items:
                if item.nodeid.split("[", 1)[0] in manifest["excluded_cases"]:
                    omit.append(item)
                else:
                    keep.append(item)
            items[:] = keep
            if omit:
                config.hook.pytest_deselected(items=omit)

        def pytest_deselected(self, items):
            self.deselected.update(item.nodeid for item in items)

        def pytest_collectreport(self, report):
            if report.failed:
                self.failed.add(report.nodeid or target)
                self.failures.append(portable_text(str(report.longrepr)))

        def pytest_runtest_logreport(self, report):
            if getattr(report, "wasxfail", None):
                self.failed.add(report.nodeid)
                self.passed.discard(report.nodeid)
                self.failures.append(report.nodeid + ": 공식 검사에서 xfail/xpass는 정상 통과로 취급하지 않습니다")
            elif report.failed:
                self.failed.add(report.nodeid)
                self.passed.discard(report.nodeid)
                self.failures.append(portable_text(str(report.longrepr)))
            elif report.skipped:
                self.passed.discard(report.nodeid)
                self.skipped[report.nodeid] = dict(target=report.nodeid, reason=str(report.longrepr))
            elif report.when == "call" and report.nodeid not in self.failed:
                self.passed.add(report.nodeid)

    plugin = Counts()
    # Explicit target + neutral config: inherited pytest.ini and environment options
    # cannot silently deselect or xfail a current official check.
    config = temporary / "pytest_current.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    exit_code = pytest.main(["-c", str(config), "--rootdir", str(ROOT), "-q", "-ra", "--tb=short",
                             "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest"), target], plugins=[plugin])
    if int(exit_code) != 0 and not plugin.failed:
        plugin.failed.add(target + "::runner")
        plugin.failures.append("pytest 비정상 종료: " + str(exit_code))
    for node in plugin.deselected:
        if node.split("[", 1)[0] not in manifest["excluded_cases"]:
            plugin.failed.add(node)
            plugin.failures.append(node + ": 공식 목록에 없는 자동 deselect")
    # Expected failures cannot make an official current check appear successful.
    result = dict(passed=len(plugin.passed - plugin.failed - set(plugin.skipped)), failed=len(plugin.failed),
                  skipped=list(plugin.skipped.values()), failures=plugin.failures,
                  deselected=sorted(plugin.deselected))
    return int(bool(result["failed"])), result


def compile_worker(temporary, manifest):
    passed, failures, files = 0, [], []
    excluded = manifest["compile_excluded_directories"]
    def compile_file(file, name):
        # Exact source bytes are staged so compileall never writes into a product
        # folder. Adjacent legacy bytecode avoids doubling an absolute Windows path
        # under pycache_prefix. .pyw is renamed only in this temporary staging area.
        staged = temporary / "sources" / str(len(files)) / Path(name).with_suffix(".py").name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(file.read_bytes())
        return compileall.compile_file(str(staged), force=True, quiet=1, legacy=True,
                                       ddir=Path(name).parent.as_posix())

    for directory in manifest["compile_directories"]:
        source = relative(ROOT, directory)
        if not source.is_dir():
            failures.append(directory + ": Python 소스 디렉터리 없음")
            continue
        candidates = []
        for folder, directories, names in os.walk(source):
            directories[:] = sorted(d for d in directories if not d.startswith(".") and d not in ("__pycache__", "검증결과")
                                    and (Path(folder) / d).relative_to(ROOT).as_posix() not in excluded)
            candidates.extend(Path(folder) / name for name in sorted(names))
        for file in candidates:
            name = file.relative_to(ROOT).as_posix()
            if file.suffix not in (".py", ".pyw") or not file.is_file():
                continue
            if any(part.startswith(".") or part in ("__pycache__", "검증결과") for part in file.relative_to(ROOT).parts):
                continue
            if any(name == prefix or name.startswith(prefix + "/") for prefix in excluded):
                continue
            files.append(name)
            ok = compile_file(file, name)
            if ok:
                passed += 1
            else:
                failures.append(name + ": Python 문법 오류")
    for name in manifest["compile_root_files"]:
        file = relative(ROOT, name)
        files.append(name)
        if file.is_file() and compile_file(file, name):
            passed += 1
        else:
            failures.append(name + ": Python 파일 없음/문법 오류")
    return int(bool(failures)), dict(passed=passed, failed=len(failures), skipped=[], failures=failures, files=files)


def main():
    kind, target, result_name, temporary_name = sys.argv[1:]
    result_file = relative(ROOT, result_name)
    temporary = relative(ROOT, temporary_name) / result_file.stem
    temporary.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "verify_current_manifest.json").read_text(encoding="utf-8"))
    # A current source import must not depend on a previous test's sys.path edits.
    sys.path[:0] = [str(ROOT / name) for name in ("Part1/program", "Part2", "Part3", "Part3/tests", "tests")]
    # Cache the real local OS identity before fixtures forbid process creation.
    platform.uname()
    block_external_network()
    try:
        if kind == "pytest":
            block_workspace_ai()
            code, result = pytest_worker(target, temporary, manifest)
        elif kind == "compile":
            code, result = compile_worker(temporary, manifest)
        else:
            raise ValueError("알 수 없는 검사 종류: " + kind)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        code, result = 1, dict(passed=0, failed=1, skipped=[], failures=[str(exc)])
    result_file.write_text(json.dumps(portable_value(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
