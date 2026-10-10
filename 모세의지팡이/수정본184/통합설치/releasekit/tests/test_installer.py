"""Regression checks for the new installer only; no existing program is started.

Run with ``python -m unittest discover -s 통합설치/releasekit/tests -p test_installer.py``.
Fixtures remain inside this new tool directory and are removed after the run.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
import zipfile


TOOL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = TOOL_ROOT / "installer.cs"


class InstallerFixture(unittest.TestCase):
    """Compiled fixture installer and throwaway keys only; it holds no tests.

    Other installer test files inherit this class, so the basic checks below run once.
    """

    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("Windows installer checks require Windows.")
        compiler = Path(os.environ.get("WINDIR", "")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        if not compiler.is_file():
            raise unittest.SkipTest("The Windows C# compiler is unavailable.")
        cls.work_parent = TOOL_ROOT / ".test-work"
        cls.work_parent.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="installer-", dir=cls.work_parent)
        cls.work = Path(cls.temporary.name)
        # Throwaway fixture keys are generated in memory. No developer key is read.
        script = """
$rsa = New-Object Security.Cryptography.RSACryptoServiceProvider 2048
$rsa.PersistKeyInCsp = $false
$p = $rsa.ExportParameters($true)
[ordered]@{modulus=[Convert]::ToBase64String($p.Modulus); exponent=[Convert]::ToBase64String($p.Exponent); d=[Convert]::ToBase64String($p.D)} | ConvertTo-Json -Compress
$rsa.Dispose()
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=True, timeout=30,
        )
        key = json.loads(result.stdout)
        cls.n = int.from_bytes(base64.b64decode(key["modulus"]), "big")
        cls.d = int.from_bytes(base64.b64decode(key["d"]), "big")
        cls.key_size = len(base64.b64decode(key["modulus"]))
        source = TEMPLATE.read_text(encoding="utf-8").replace("@@MODULUS@@", key["modulus"]).replace("@@EXPONENT@@", key["exponent"])
        (cls.work / "installer.cs").write_text(source, encoding="utf-8")
        with zipfile.ZipFile(cls.work / "payload.zip", "w") as archive:
            archive.writestr("MOSES.exe", b"fixture only")
            archive.writestr("python.exe", b"fixture only")
        references = [
            "System.Windows.Forms.dll", "System.Drawing.dll", "System.Web.Extensions.dll",
            "System.Security.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll",
        ]
        command = [str(compiler), "/nologo", "/codepage:65001", "/platform:x64", "/resource:payload.zip,MosesPayload"]
        command.extend("/reference:" + reference for reference in references)
        cls._compile(command + ["/target:winexe", "/out:installer.exe", "installer.cs"])
        harness = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Windows.Forms;
using MosesInstaller;
public static class InstallerProbe {
    public static int Main(string[] args) {
        List<string> files = new List<string>();
        List<string> dirs = new List<string>();
        try {
            if (args[0] == "validate") {
                InstallCore.ValidateEnvelope(File.ReadAllBytes(args[1]), Int64.Parse(args[2]));
            } else if (args[0] == "read") {
                File.WriteAllBytes(args[2], InstallCore.ReadEnvelope(args[1]));
            } else if (args[0] == "extract") {
                using (FileStream input = File.OpenRead(args[1]))
                    InstallCore.ExtractArchive(input, args[2], files, dirs, null);
            } else if (args[0] == "dpapi") {
                byte[] original = File.ReadAllBytes(args[1]);
                byte[] locked = ProtectedData.Protect(original, null, DataProtectionScope.LocalMachine);
                byte[] restored = ProtectedData.Unprotect(locked, null, DataProtectionScope.LocalMachine);
                if (Convert.ToBase64String(original) != Convert.ToBase64String(restored)) return 8;
                InstallCore.ValidateEnvelope(restored, Int64.Parse(args[2]));
            } else if (args[0] == "expired_button") {
                // An empty destination guarantees even an old/regressed button
                // handler cannot extract files or create a desktop shortcut.
                using (InstallWindow form = new InstallWindow(File.ReadAllBytes(args[1]), "fixture")) {
                    FieldInfo field = typeof(InstallWindow).GetField("folder", BindingFlags.Instance | BindingFlags.NonPublic);
                    ((TextBox)field.GetValue(form)).Text = " ";
                    MethodInfo handler = typeof(InstallWindow).GetMethod("BeginInstall", BindingFlags.Instance | BindingFlags.NonPublic);
                    handler.Invoke(form, new object[] { null, EventArgs.Empty });
                    if (!form.IsDisposed || form.Visible) return 8;
                }
            } else return 9;
            return 0;
        } catch {
            InstallCore.RollBack(files, dirs);
            return 7;
        }
    }
}
'''
        (cls.work / "probe.cs").write_text(harness, encoding="utf-8")
        cls._compile(command + ["/target:exe", "/main:InstallerProbe", "/out:probe.exe", "installer.cs", "probe.cs"])

    @classmethod
    def _compile(cls, command):
        result = subprocess.run(command, cwd=cls.work, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout.replace(str(cls.work), "fixture") + result.stderr.replace(str(cls.work), "fixture"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        try:
            cls.work_parent.rmdir()
        except OSError:
            pass

    @classmethod
    def envelope(cls, **overrides):
        policy = {"schema": 1, "version": "v1.0", "expires_at": 2000000000, "install_expires_at": 1900000000}
        policy.update(overrides)
        payload = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
        encoded = b"\x00\x01" + b"\xff" * (cls.key_size - len(digest_info) - 3) + b"\x00" + digest_info
        signature = pow(int.from_bytes(encoded, "big"), cls.d, cls.n).to_bytes(cls.key_size, "big")
        return json.dumps({"payload": base64.b64encode(payload).decode("ascii"), "signature": base64.b64encode(signature).decode("ascii")}, separators=(",", ":")).encode("utf-8")


class InstallerTests(InstallerFixture):
    def probe(self, *args):
        return subprocess.run([str(self.work / "probe.exe"), *map(str, args)], cwd=self.work, capture_output=True, timeout=15)

    def validate(self, record, now):
        path = self.work / "policy.json"
        path.write_bytes(record)
        return self.probe("validate", path, now).returncode

    def test_signature_and_both_deadlines(self):
        record = self.envelope()
        self.assertEqual(0, self.validate(record, 1899999999))
        self.assertEqual(7, self.validate(record, 1900000000))
        self.assertEqual(7, self.validate(record, 1900000001))
        record = self.envelope(expires_at=1800000000)
        self.assertEqual(0, self.validate(record, 1799999999))
        self.assertEqual(7, self.validate(record, 1800000000))
        self.assertEqual(7, self.validate(record, 1800000001))

    def test_altered_or_malformed_policy_is_rejected(self):
        record = json.loads(self.envelope())
        payload = json.loads(base64.b64decode(record["payload"]))
        payload["expires_at"] += 86400
        record["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
        self.assertEqual(7, self.validate(json.dumps(record).encode(), 1700000000))
        for changes in ({"schema": 2}, {"schema": "1"}, {"expires_at": "2000000000"}, {"install_expires_at": 1900000000.5}, {"version": ""}):
            with self.subTest(changes=changes):
                self.assertEqual(7, self.validate(self.envelope(**changes), 1700000000))

    def test_local_calendar_expiry_at_pc_midnight(self):
        # The same installed-PC calendar rule is implemented by C# and Python.
        # Do not alter the machine clock or time zone to test the boundary.
        midnight_utc = int(time.mktime((2030, 1, 1, 0, 0, 0, 0, 0, -1)))
        midnight_wall = int(__import__("calendar").timegm((2030, 1, 1, 0, 0, 0)))
        record = self.envelope(expires_at=1, expires_local=midnight_wall,
                               install_expires_at=midnight_utc + 86400)
        self.assertEqual(0, self.validate(record, midnight_utc - 1))
        self.assertEqual(7, self.validate(record, midnight_utc))
        self.assertEqual(7, self.validate(record, midnight_utc + 1))
        self.assertEqual(7, self.validate(self.envelope(expires_local="1893456000"), 1700000000))

    def test_install_button_rechecks_deadline_without_starting_install(self):
        record = self.envelope(expires_at=32500000000, install_expires_at=1)
        path = self.work / "expired_button.json"
        path.write_bytes(record)
        result = self.probe("expired_button", path)
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertEqual(b"", result.stderr)

    def test_dpapi_preserves_signed_policy_on_installing_machine(self):
        path = self.work / "policy.json"
        path.write_bytes(self.envelope())
        self.assertEqual(0, self.probe("dpapi", path, 1700000000).returncode)

    def test_signed_trailer_is_read_after_executable_build(self):
        record = self.envelope()
        path = self.work / "stamped.exe"
        path.write_bytes((self.work / "installer.exe").read_bytes() + record + struct.pack("<I", len(record)) + b"MOSES96!")
        output = self.work / "read.json"
        self.assertEqual(0, self.probe("read", path, output).returncode)
        self.assertEqual(record, output.read_bytes())
        path.write_bytes(path.read_bytes()[:-1] + b"?")
        self.assertEqual(7, self.probe("read", path, output).returncode)

    def test_expired_installer_exits_without_window_or_output(self):
        record = self.envelope(expires_at=1, install_expires_at=1)
        path = self.work / "expired.exe"
        path.write_bytes((self.work / "installer.exe").read_bytes() + record + struct.pack("<I", len(record)) + b"MOSES96!")
        process = subprocess.Popen([str(path)], cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        windows = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def collect(window, _):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(window), ctypes.byref(pid))
            if pid.value == process.pid and user32.IsWindowVisible(ctypes.c_void_p(window)):
                windows.append(window)
            return True

        deadline = time.monotonic() + 10
        try:
            while process.poll() is None and time.monotonic() < deadline:
                user32.EnumWindows(collect, None)
                time.sleep(0.01)
            stdout, stderr = process.communicate(timeout=1)
            self.assertEqual(0, process.returncode)
            self.assertEqual(b"", stdout)
            self.assertEqual(b"", stderr)
            self.assertEqual([], windows)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def make_archive(self, name, entries):
        path = self.work / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, content in entries:
                archive.writestr(entry, content)
        return path

    def test_archive_extracts_in_moved_unicode_folder(self):
        archive = self.make_archive("normal.zip", [("MOSES.exe", b"launcher"), ("Part1/program/MT5/test.ex5", b"indicator")])
        for folder in ("location-a", "다른위치 폴더"):
            with self.subTest(folder=folder):
                target = self.work / folder / "MOSES"
                self.assertEqual(0, self.probe("extract", archive, target).returncode)
                self.assertEqual(b"launcher", (target / "MOSES.exe").read_bytes())
                self.assertEqual(b"indicator", (target / "Part1/program/MT5/test.ex5").read_bytes())

    def test_nonempty_folder_is_never_overwritten(self):
        archive = self.make_archive("existing.zip", [("settings.json", b"replacement")])
        target = self.work / "existing"
        target.mkdir()
        (target / "settings.json").write_bytes(b"original user data")
        self.assertEqual(7, self.probe("extract", archive, target).returncode)
        self.assertEqual(b"original user data", (target / "settings.json").read_bytes())

    def test_empty_generated_folder_is_created_on_install(self):
        archive = self.make_archive('empty-directory.zip', [('Part3/generated/', b''), ('MOSES.exe', b'fixture')])
        target = self.work / 'empty-directory-install'
        self.assertEqual(0, self.probe('extract', archive, target).returncode)
        self.assertTrue((target / 'Part3/generated').is_dir())
        self.assertEqual([], list((target / 'Part3/generated').iterdir()))

    def test_unsafe_archive_entries_are_rejected_before_creation(self):
        symlink = zipfile.ZipInfo("link")
        symlink.create_system = 3
        symlink.external_attr = 0o120777 << 16
        entries = ("../outside.txt", "folder/../../outside.txt", "/outside.txt", "drive:outside.txt", "file.txt:stream", "folder/./x", "trailing./x", "CON.txt", symlink)
        for index, entry in enumerate(entries):
            with self.subTest(entry=str(entry)):
                archive = self.make_archive("bad-%d.zip" % index, [("first.txt", b"good"), (entry, b"bad")])
                target = self.work / ("rejected-%d" % index)
                self.assertEqual(7, self.probe("extract", archive, target).returncode)
                self.assertFalse(target.exists())
        self.assertFalse((self.work / "outside.txt").exists())

    def test_windows_case_collisions_are_rejected(self):
        archive = self.make_archive("duplicate.zip", [("Folder/a.txt", b"a"), ("folder/A.txt", b"b")])
        target = self.work / "duplicates"
        self.assertEqual(7, self.probe("extract", archive, target).returncode)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
