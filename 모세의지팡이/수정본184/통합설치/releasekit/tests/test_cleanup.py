import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from releasekit import cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moses_cleanup_test_")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "수정본97"
        self.install = self.root / "통합설치"
        self.install.mkdir(parents=True)
        self.messages = []

    def run_cleanup(self):
        return cleanup.cleanup_build_files(self.root, emit=self.messages.append)

    def make_cache(self, name):
        cache = self.install / name
        (cache / "old_build/nested").mkdir(parents=True)
        (cache / "old_build/nested/file.bin").write_bytes(b"cache")
        return cache

    def symlink(self, link, destination, directory=False):
        try:
            link.symlink_to(destination, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

    def test_only_exact_three_folders_are_removed(self):
        for name in cleanup._TARGET_NAMES:
            self.make_cache(name)
        retained = [
            self.install / ".keys/private_key.json",
            self.install / "releasekit/original.py",
            self.install / "prerequisites_backup/file.bin",
            self.root / "배포/v1.0/Setup.exe",
            self.root / "Part1/program/MT5/original.mq5",
            self.root / "검증결과/check.json",
            self.root / ".work/user-file.txt",
        ]
        for item in retained:
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_bytes(b"preserve")
        result = self.run_cleanup()
        self.assertTrue(result["success"])
        self.assertEqual(
            [row["path"] for row in result["targets"]],
            ["통합설치/.work", "통합설치/prerequisites", "통합설치/.buildenv"],
        )
        self.assertTrue(all(row["status"] == "removed" for row in result["targets"]))
        self.assertTrue(all(not (self.install / name).exists() for name in cleanup._TARGET_NAMES))
        self.assertTrue(all(item.read_bytes() == b"preserve" for item in retained))

    def test_missing_folders_are_successful_and_result_is_bounded(self):
        for _ in range(3):
            result = self.run_cleanup()
            self.assertTrue(result["success"])
            self.assertTrue(all(row["status"] == "missing" for row in result["targets"]))
        report = self.install / ".buildtmp/cleanup_result.json"
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), result)
        self.assertLess(report.stat().st_size, 4096)
        self.assertEqual(list(report.parent.iterdir()), [report])

    def test_current_buildenv_interpreter_is_preserved(self):
        cache = self.make_cache(".buildenv")
        self.make_cache(".work")
        with patch.object(cleanup.sys, "executable", str(cache / "Scripts/python.exe")):
            result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertTrue(cache.exists())
        self.assertEqual(result["targets"][2]["status"], "blocked")
        self.assertFalse((self.install / ".work").exists())

    def test_interpreter_in_similarly_named_folder_does_not_block(self):
        self.make_cache(".buildenv")
        with patch.object(cleanup.sys, "executable", str(self.install / ".buildenv2/python.exe")):
            result = self.run_cleanup()
        self.assertTrue(result["success"])
        self.assertFalse((self.install / ".buildenv").exists())

    def test_file_in_place_of_cache_is_preserved(self):
        target = self.install / ".work"
        target.write_bytes(b"original")
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertEqual(result["targets"][0]["status"], "blocked")
        self.assertEqual(target.read_bytes(), b"original")

    def test_retry_locked_folder_then_succeed(self):
        self.make_cache(".work")
        native_remove = cleanup.shutil.rmtree
        calls = []

        def remove(path, **kwargs):
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError(errno.EACCES, "locked fixture")
            return native_remove(path, **kwargs)

        with patch.object(cleanup.shutil, "rmtree", remove), patch.object(cleanup.time, "sleep") as sleep:
            result = self.run_cleanup()
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result["targets"][0]["attempts"], 3)

    def test_persistent_lock_is_reported_and_other_cache_is_removed(self):
        locked = self.make_cache(".work")
        other = self.make_cache("prerequisites")
        native_remove = cleanup.shutil.rmtree
        calls = []

        def remove(path, **kwargs):
            if path == locked:
                calls.append(path)
                raise PermissionError(errno.EACCES, "locked fixture")
            return native_remove(path, **kwargs)

        with patch.object(cleanup.shutil, "rmtree", remove), patch.object(cleanup.time, "sleep"):
            result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertTrue(locked.exists())
        self.assertFalse(other.exists())
        self.assertEqual(len(calls), cleanup._MAX_ATTEMPTS)
        self.assertEqual(result["targets"][0]["status"], "failed")
        self.assertTrue(any("정리 실패" in line for line in self.messages))
        self.assertEqual(json.loads((self.install / ".buildtmp/cleanup_result.json").read_text(encoding="utf-8")), result)

    def test_non_retryable_failure_stops_without_retrying(self):
        self.make_cache(".work")
        with patch.object(cleanup.shutil, "rmtree", side_effect=OSError(errno.EIO, "disk fixture")) as remove:
            with patch.object(cleanup.time, "sleep") as sleep:
                result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertEqual(remove.call_count, 1)
        self.assertEqual(sleep.call_count, 0)

    def test_readonly_cache_can_be_removed(self):
        cache = self.make_cache(".work")
        (cache / "old_build/nested/file.bin").chmod(stat.S_IREAD)
        result = self.run_cleanup()
        self.assertTrue(result["success"])
        self.assertFalse(cache.exists())

    def test_target_symlink_to_outside_is_blocked(self):
        outside = self.base / "outside"
        outside.mkdir()
        original = outside / "original.py"
        original.write_text("preserve")
        self.symlink(self.install / ".work", outside, directory=True)
        self.make_cache("prerequisites")
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertEqual(result["targets"][0]["status"], "blocked")
        self.assertEqual(original.read_text(), "preserve")
        self.assertTrue((self.install / ".work").is_symlink())
        self.assertFalse((self.install / "prerequisites").exists())

    def test_broken_target_symlink_is_blocked(self):
        link = self.install / ".work"
        self.symlink(link, self.base / "missing", directory=True)
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertEqual(result["targets"][0]["status"], "blocked")
        self.assertTrue(link.is_symlink())

    def test_nested_symlink_preserves_entire_target_and_outside(self):
        cache = self.make_cache(".work")
        outside = self.base / "original.py"
        outside.write_text("preserve")
        self.symlink(cache / "old_build/nested/linked.py", outside)
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertTrue((cache / "old_build/nested/file.bin").exists())
        self.assertEqual(outside.read_text(), "preserve")

    def test_linked_installation_folder_is_blocked(self):
        self.install.rmdir()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / ".work").mkdir()
        self.symlink(self.install, outside, directory=True)
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertTrue((outside / ".work").exists())
        self.assertTrue(all(row["status"] == "blocked" for row in result["targets"]))

    def test_linked_revision_root_is_blocked(self):
        linked = self.base / "linked-revision"
        self.make_cache(".work")
        self.symlink(linked, self.root, directory=True)
        result = cleanup.cleanup_build_files(linked, emit=self.messages.append)
        self.assertFalse(result["success"])
        self.assertTrue((self.install / ".work").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows junction test")
    def test_real_windows_nested_junction_is_blocked(self):
        cache = self.make_cache(".work")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "original.py").write_text("preserve")
        junction = cache / "old_build/nested/junction"
        creation = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
        )
        self.assertEqual(creation.returncode, 0, creation.stderr)
        result = self.run_cleanup()
        self.assertFalse(result["success"])
        self.assertEqual(result["targets"][0]["status"], "blocked")
        self.assertEqual((outside / "original.py").read_text(), "preserve")
        self.assertTrue((cache / "old_build/nested/file.bin").exists())

    def test_validator_rejects_outside_or_unlisted_targets(self):
        outside = self.base / ".work"
        outside.mkdir()
        with self.assertRaises(cleanup.UnsafeCleanupPath):
            cleanup._validate_target(self.install, outside)
        unknown = self.install / ".keys"
        unknown.mkdir()
        with self.assertRaises(cleanup.UnsafeCleanupPath):
            cleanup._validate_target(self.install, unknown)

    def test_linked_result_folder_does_not_write_outside(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.symlink(self.install / ".buildtmp", outside, directory=True)
        self.make_cache(".work")
        result = self.run_cleanup()
        self.assertTrue(result["success"])
        self.assertFalse((outside / "cleanup_result.json").exists())
        self.assertTrue(any("기록 저장 실패" in line for line in self.messages))


if __name__ == "__main__":
    unittest.main()
