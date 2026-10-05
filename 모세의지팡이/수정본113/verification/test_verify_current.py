"""Regression checks for honest counts, exclusions, paths and actual CLI exit codes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_current import ROOT, inventory, read_child_result, relative


class VerifyCurrentTests(unittest.TestCase):
    def test_manifest_contains_only_portable_owned_paths(self):
        manifest = json.loads((ROOT / "verify_current_manifest.json").read_text(encoding="utf-8"))
        for group in manifest["suites"].values():
            for name in group:
                self.assertTrue(relative(ROOT, name).is_file(), name)
        for bad in ("../escape.py", "/absolute.py", "Z:/project/test.py", "folder\\test.py"):
            with self.assertRaises(ValueError):
                relative(ROOT, bad)

    def test_nonzero_child_exit_cannot_be_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.write_text(json.dumps(dict(passed=8, failed=0, skipped=[])))
            self.assertEqual(read_child_result(path, 1, "test")["failed"], 1)

    def test_empty_or_missing_result_cannot_be_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            self.assertEqual(read_child_result(path, 0, "test")["failed"], 1)
            path.write_text(json.dumps(dict(passed=0, failed=0)))
            self.assertEqual(read_child_result(path, 0, "test")["failed"], 1)

    def test_current_inventory_requires_review_of_new_and_missing_files(self):
        manifest = dict(suites={"Part1": ["tests/test_current.py"], "Part2": [], "Part3": [], "Verifier": []},
                        test_directories=["tests"], excluded_files={}, excluded_cases={}, excluded_browser_tests={})
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "tests").mkdir()
            (base / "tests/test_new.py").write_text("def test_new(): pass\n")
            errors, _ = inventory(base, manifest)
            self.assertTrue(any("미분류" in error for error in errors))
            self.assertTrue(any("파일 없음" in error for error in errors))

    def test_excluded_file_is_inspected_without_importing_it(self):
        manifest = dict(suites={group: [] for group in ("Part1", "Part2", "Part3", "Verifier")},
                        test_directories=["tests"], excluded_files={"tests/test_old.py": "missing retired module"},
                        excluded_cases={}, excluded_browser_tests={})
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "tests").mkdir()
            (base / "tests/test_old.py").write_text("raise RuntimeError('must not import')\ndef test_old(): pass\n")
            errors, skips = inventory(base, manifest)
            self.assertEqual(errors, [])
            self.assertEqual(skips, [dict(target="tests/test_old.py::test_old", reason="missing retired module")])

    def fixture(self, base):
        for folder in ("verification", "tests", "Part1", "Part2", "Part3/web"):
            (base / folder).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "verify_current.py", base / "verify_current.py")
        shutil.copyfile(ROOT / "verification/current_worker.py", base / "verification/current_worker.py")
        (base / "tests/test_current.py").write_text("def test_real_assertion():\n    assert 2 + 2 == 4\n", encoding="utf-8")
        (base / "tests/test_retired.py").write_text("raise RuntimeError('retired must never import')\ndef test_old(): assert False\n", encoding="utf-8")
        (base / "Part1/current.py").write_text("CURRENT = True\n", encoding="utf-8")
        (base / "Part2/current.py").write_text("CURRENT = True\n", encoding="utf-8")
        (base / "Part3/current.py").write_text("CURRENT = True\n", encoding="utf-8")
        (base / "Part3/web/current.js").write_text("const current = true;\n", encoding="utf-8")
        manifest = dict(suites={"Part1": ["tests/test_current.py"], "Part2": [], "Part3": [], "Verifier": []},
            test_directories=["tests"], excluded_files={"tests/test_retired.py": "retired module"}, excluded_cases={},
            excluded_browser_tests={}, timeout_sec=30, compile_directories=["Part1", "Part2", "Part3", "verification"],
            compile_root_files=["verify_current.py"], compile_excluded_directories=[])
        (base / "verify_current_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def run_cli(self, base, cwd):
        proc = subprocess.run([sys.executable, "-B", str(base / "verify_current.py")], cwd=cwd,
                              capture_output=True, text=True, encoding="utf-8", timeout=60,
                              env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        latest = json.loads((base / "검증결과/current_verify/latest.json").read_text(encoding="utf-8"))
        report = json.loads((base / latest["summary"]).read_text(encoding="utf-8"))
        return proc, report

    def test_cli_relocated_root_is_independent_of_cwd_and_legacy_pytest_ini(self):
        with tempfile.TemporaryDirectory(prefix="verify relocation ") as folder:
            base = Path(folder) / "새 위치 한글"
            self.fixture(base)
            (base / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests\n")
            proc, report = self.run_cli(base, Path(folder))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(report["failed"], 0)
            self.assertEqual(report["groups"]["Part1"]["passed"], 1)
            self.assertEqual(len(report["skipped"]), 1)
            self.assertNotIn(str(base), json.dumps(report))

    def test_real_assertion_and_missing_module_fail_exit_one(self):
        for source in ("def test_failure(): assert False\n", "import module_that_does_not_exist\ndef test_missing(): pass\n"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                self.fixture(base)
                (base / "tests/test_current.py").write_text(source)
                proc, report = self.run_cli(base, base)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertGreater(report["failed"], 0)
                self.assertEqual(report["groups"]["Part1"]["passed"], 0)

    def test_real_python_pyw_and_js_syntax_errors_fail_exit_one(self):
        for name, source, group in (("Part1/current.py", "def broken(\n", "Python"),
                                     ("START_MOSES.pyw", "def broken(\n", "Python"),
                                     ("Part3/web/current.js", "const broken = ;\n", "JS")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                self.fixture(base)
                (base / name).write_text(source)
                if name.endswith(".pyw"):
                    manifest = json.loads((base / "verify_current_manifest.json").read_text())
                    manifest["compile_root_files"].append(name)
                    (base / "verify_current_manifest.json").write_text(json.dumps(manifest))
                proc, report = self.run_cli(base, base)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertGreater(report["groups"][group]["failed"], 0)

    def test_missing_node_is_failure_not_skip(self):
        from unittest.mock import patch
        import verify_current
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            self.fixture(base)
            with patch.object(verify_current, "ROOT", base), patch.object(verify_current, "MANIFEST", base / "verify_current_manifest.json"), \
                 patch.object(verify_current.shutil, "which", return_value=None):
                self.assertEqual(verify_current.main(), 1)
            latest = json.loads((base / "검증결과/current_verify/latest.json").read_text(encoding="utf-8"))
            report = json.loads((base / latest["summary"]).read_text(encoding="utf-8"))
            self.assertEqual(report["groups"]["JS"]["failed"], 1)

    def test_xfail_or_xpass_cannot_turn_current_checks_into_pass(self):
        for assertion in ("False", "True"):
            with self.subTest(assertion=assertion), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                self.fixture(base)
                (base / "tests/test_current.py").write_text(
                    "import pytest\n@pytest.mark.xfail(reason='old expectation')\n"
                    + "def test_current(): assert " + assertion + "\n")
                proc, report = self.run_cli(base, base)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertGreater(report["groups"]["Part1"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
