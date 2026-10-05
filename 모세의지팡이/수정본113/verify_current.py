"""Run only the reviewed current checks. No product modules are changed here."""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "verify_current_manifest.json"
GROUPS = ("Part1", "Part2", "Part3", "Python", "JS", "Verifier")


def relative(root, name):
    """Accept only portable, project-owned manifest and report paths."""
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise ValueError(f"프로젝트 상대 경로가 아닙니다: {name!r}")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"프로젝트 상대 경로가 아닙니다: {name!r}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"프로젝트 밖으로 나가는 경로: {name!r}")
    return resolved


def portable_text(text, root=ROOT):
    for base in (str(root), root.as_posix()):
        text = text.replace(base.replace("\\", "\\\\"), ".").replace(base, ".")
    # Dependency tracebacks may contain a Python installation path. Do not store it.
    text = re.sub(r"(?:\.\.[\\/])+", "<external>/", text)
    return re.sub(r"\b[A-Za-z]:[\\/][^\r\n\"']*", "<external path>", text)


def portable_value(value, root=ROOT):
    if isinstance(value, str):
        return portable_text(value, root)
    if isinstance(value, list):
        return [portable_value(item, root) for item in value]
    if isinstance(value, dict):
        return {key: portable_value(item, root) for key, item in value.items()}
    return value


def declarations(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            found.append(node.name)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"):
                    found.append(node.name + "::" + method.name)
    return found


def inventory(root, manifest):
    selected = {path for group in ("Part1", "Part2", "Part3", "Verifier")
                for path in manifest["suites"][group]}
    excluded_files = manifest["excluded_files"]
    excluded_cases = manifest["excluded_cases"]
    problems, skipped = [], []
    actual = set()
    for directory in manifest["test_directories"]:
        folder = relative(root, directory)
        if not folder.is_dir():
            problems.append("검사 디렉터리 없음: " + directory)
            continue
        for file in sorted(folder.rglob("test_*.py")):
            if any(part.startswith(".") or part == "__pycache__" for part in file.relative_to(root).parts):
                continue
            name = file.relative_to(root).as_posix()
            actual.add(name)
            try:
                tests = declarations(file)
            except (SyntaxError, UnicodeError) as exc:
                problems.append(name + ": 테스트 목록 분석 오류: " + str(exc))
                continue
            if name in excluded_files:
                if name in selected:
                    problems.append("선택/제외 중복: " + name)
                for test in tests:
                    skipped.append({"target": name + "::" + test, "reason": excluded_files[name]})
            elif name not in selected:
                problems.append("공식 목록에 미분류된 새 테스트: " + name)
            for test in tests:
                target = name + "::" + test
                if target in excluded_cases:
                    if name not in selected:
                        problems.append("선택되지 않은 파일의 개별 제외: " + target)
                    skipped.append({"target": target, "reason": excluded_cases[target]})
    for name in selected | set(excluded_files):
        if name not in actual:
            problems.append("공식 목록의 테스트 파일 없음: " + name)
    for target in excluded_cases:
        name, case = target.split("::", 1)
        if name in actual and case not in declarations(relative(root, name)):
            problems.append("개별 제외 대상이 사라졌습니다: " + target)
    for name in manifest["excluded_browser_tests"]:
        if not relative(root, name).is_file():
            problems.append("선택적 브라우저 시험 없음: " + name)
        skipped.append({"target": name, "reason": manifest["excluded_browser_tests"][name]})
    browser_actual = {file.relative_to(root).as_posix() for file in (root / "Part3/tests").glob("test_*.cjs")}
    for name in browser_actual - set(manifest["excluded_browser_tests"]):
        problems.append("미분류된 브라우저 시험: " + name)
    return problems, skipped


def child(command, root, env, timeout, log):
    start = time.monotonic()
    try:
        result = subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        output, code = result.stdout, result.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        output = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw) + "\n검사 시간 제한 초과"
        code = 1
    except OSError as exc:
        output, code = str(exc), 1
    log.write_text(portable_text(output, root), encoding="utf-8")
    return code, round(time.monotonic() - start, 3)


def read_child_result(path, code, label):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result.get("passed"), int) or not isinstance(result.get("failed"), int):
            raise ValueError("검사 개수가 없습니다")
        if result["passed"] < 0 or result["failed"] < 0:
            raise ValueError("검사 개수가 음수입니다")
        if code != 0 and result["failed"] == 0:
            result["failed"] = 1
            result.setdefault("failures", []).append(label + ": 비정상 종료")
        if result["passed"] == 0 and result["failed"] == 0:
            result["failed"] = 1
            result.setdefault("failures", []).append(label + ": 실행한 검사 없음")
        return result
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"passed": 0, "failed": 1, "skipped": [], "failures": [label + ": 결과 없음/손상: " + str(exc)]}


def main():
    print("MOSES CURRENT VERIFY", flush=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = relative(ROOT, "검증결과/current_verify/" + stamp)
    destination.mkdir(parents=True)
    # Keep test/capture paths short enough for Windows' 260-character APIs.
    temp = relative(ROOT, ".v/" + uuid.uuid4().hex[:8])
    temp.mkdir(parents=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="", TMP=str(temp), TEMP=str(temp))
    # Opt-in system process tests are excluded even if the caller enabled them elsewhere.
    env.pop("MOSES_PROCESS_SMOKE", None)
    problems, skipped = inventory(ROOT, manifest)
    outcomes = {group: {"passed": 0, "failed": 0, "failures": []} for group in GROUPS}
    outcomes["Verifier"]["failed"] += len(problems)
    outcomes["Verifier"]["failures"].extend(problems)
    jobs = []
    for group in ("Part1", "Part2", "Part3", "Verifier"):
        for index, name in enumerate(manifest["suites"][group]):
            jobs.append((group, index, name))

    def run_suite(job):
        group, index, name = job
        label = group + "_" + str(index).zfill(2)
        result_file = destination / (label + ".json")
        log_file = destination / (label + ".log")
        command = [sys.executable, "-B", "verification/current_worker.py", "pytest", name,
                   result_file.relative_to(ROOT).as_posix(), temp.relative_to(ROOT).as_posix()]
        code, seconds = child(command, ROOT, env, manifest["timeout_sec"], log_file)
        result = read_child_result(result_file, code, name)
        result.update(target=name, seconds=seconds, log=log_file.relative_to(ROOT).as_posix())
        return group, result

    details = []
    # Serial execution avoids shared config/file interference between integration tests.
    for job in jobs:
        group, result = run_suite(job)
        details.append(result)
        for field in ("passed", "failed"):
            outcomes[group][field] += result[field]
        outcomes[group]["failures"].extend(result.get("failures", []))
        skipped.extend(result.get("skipped", []))
        state = "FAIL" if result["failed"] else "PASS"
        print(f"  {state} {result['target']} ({result['passed']} pass)", flush=True)

    # compileall is a separate process and writes bytecode only to this run's temp folder.
    py_result = destination / "Python.json"
    command = [sys.executable, "-B", "verification/current_worker.py", "compile", "all",
               py_result.relative_to(ROOT).as_posix(), temp.relative_to(ROOT).as_posix()]
    code, seconds = child(command, ROOT, env, manifest["timeout_sec"], destination / "Python.log")
    result = read_child_result(py_result, code, "Python compileall")
    outcomes["Python"] = {key: result.get(key, [] if key == "failures" else 0) for key in ("passed", "failed", "failures")}
    details.append(dict(result, target="Python compileall", seconds=seconds))

    node = shutil.which("node")
    js_files = sorted((ROOT / "Part3/web").rglob("*.js"))
    if not node or not js_files:
        outcomes["JS"]["failed"] += 1
        outcomes["JS"]["failures"].append("Node.js 없음" if not node else "웹 JS 파일 없음")
    else:
        for index, file in enumerate(js_files):
            name = file.relative_to(ROOT).as_posix()
            code, seconds = child([node, "--check", name], ROOT, env, manifest["timeout_sec"], destination / f"JS_{index}.log")
            outcomes["JS"]["failed" if code else "passed"] += 1
            if code:
                outcomes["JS"]["failures"].append(name + ": JS 문법 오류")
            details.append({"target": name, "passed": int(code == 0), "failed": int(code != 0), "seconds": seconds})
    passed = sum(row["passed"] for row in outcomes.values())
    failed = sum(row["failed"] for row in outcomes.values())
    report = dict(groups=outcomes, passed=passed, failed=failed, skipped=skipped,
                  details=details, manifest="verify_current_manifest.json", exit_code=int(failed != 0))
    payload = json.dumps(portable_value(report), ensure_ascii=False, indent=2)
    (destination / "summary.json").write_text(payload, encoding="utf-8")
    latest = ROOT / "검증결과/current_verify/latest.json"
    latest.write_text(json.dumps({"summary": (destination / "summary.json").relative_to(ROOT).as_posix(),
                                  "exit_code": int(failed != 0)}, ensure_ascii=False, indent=2), encoding="utf-8")
    # This run created this checked project-owned path; evidence is already saved.
    if temp.is_relative_to(ROOT / ".v"):
        shutil.rmtree(temp)
    print()
    for group, row in outcomes.items():
        print(f"{group} {'FAIL' if row['failed'] else 'PASS'} ({row['passed']} pass, {row['failed']} fail)")
    print(f"\nTOTAL PASS: {passed}\nFAIL: {failed}\nSKIP: {len(skipped)}")
    reasons = {}
    for row in skipped:
        reasons.setdefault(row["reason"], []).append(row["target"])
    for reason, targets in reasons.items():
        print(f"  제외 {len(targets)}항목: {reason}")
        shown = {}
        for target in targets:
            label = target.split("::", 1)[0] if target.split("::", 1)[0] in manifest["excluded_files"] else target
            shown[label] = shown.get(label, 0) + 1
        for label, count in shown.items():
            print(f"    {label} ({count}항목)")
    for group, row in outcomes.items():
        for failure in row["failures"]:
            print(f"  FAIL [{group}] {portable_text(failure)}")
    print("\n공식 목록: CURRENT_VERIFY.md / verify_current_manifest.json")
    print("결과: " + (destination / "summary.json").relative_to(ROOT).as_posix())
    return int(failed != 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("\nVerifier FAIL\nFAIL: 1\n" + portable_text(str(exc)), flush=True)
        sys.exit(1)
