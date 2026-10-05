"""Successful native installs close themselves and remove only that setup.

Fixtures use temporary native executables and opt out of registry/prerequisite
integration. No real MOSES, MT5, developer keys or user installation is used.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile

from test_installer import InstallerTests as _InstallerTests


PROGRAMS = ("THE_STAFF_OF_MOSES", "PRICE_of_Moses", "RSI_of_Moses", "STO_of_Moses", "DI_of_Moses")


class Installer111Tests(_InstallerTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with zipfile.ZipFile(cls.work / "payload111.zip", "w") as archive:
            for name in ("MOSES.exe", "python.exe", "runtime/code.bundle"):
                archive.writestr(name, b"fixture, never executed")
            archive.writestr("settings/strategy_registry.json", b'{"presets":[]}')
            for name in PROGRAMS:
                for folder in ("MT5", "Part1/program/MT5"):
                    archive.writestr(folder + "/" + name + ".ex5", ("licensed fixture " + name).encode())
        harness = r'''
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using MosesInstaller;
public static class Installer111Probe {
    [STAThread]
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        Dictionary<string, object> row = new Dictionary<string, object>();
        byte[] policy = File.ReadAllBytes(args[1]);
        using (InstallWindow form = new InstallWindow(policy, "fixture")) {
            FieldInfo busy = typeof(InstallWindow).GetField("busy", flags);
            MethodInfo finish = typeof(InstallWindow).GetMethod("FinishInstallation", flags);
            form.Shown += delegate {
                form.BeginInvoke((Action)delegate {
                    busy.SetValue(form, true);
                    Exception error = null;
                    bool cancelled = args[0] == "cancel";
                    if (!cancelled) {
                        try {
                            using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("MosesPayload"))
                                InstallCore.InstallArchive(payload, args[2], policy, args[3], null, false, args[0] == "update");
                        } catch (Exception problem) { error = problem; }
                    }
                    finish.Invoke(form, new object[] { new RunWorkerCompletedEventArgs(null, error, cancelled) });
                    row["completed"] = form.CompletedSuccessfully;
                    row["busy"] = busy.GetValue(form);
                    row["visible"] = form.Visible;
                    row["disposed"] = form.IsDisposed;
                    row["status"] = ((Label)typeof(InstallWindow).GetField("status", flags).GetValue(form)).Text;
                    row["launch_field_exists"] = typeof(InstallWindow).GetField("launch", flags) != null;
                    if (!form.CompletedSuccessfully) form.Close();
                });
            };
            Application.Run(form);
            row["window_loop_ended"] = true;
            if (form.CompletedSuccessfully) InstallCore.ScheduleInstallerRemoval(Assembly.GetExecutingAssembly().Location);
        }
        Console.Write(new JavaScriptSerializer().Serialize(row));
        return 0;
    }
}
'''
        (cls.work / "installer111_probe.cs").write_text(harness, encoding="utf-8")
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        command = [str(compiler), "/nologo", "/codepage:65001", "/platform:x64",
                   "/target:exe", "/main:Installer111Probe", "/out:installer111_probe.exe",
                   "/resource:payload111.zip,MosesPayload"]
        command += ["/reference:" + name + ".dll" for name in (
            "System.Windows.Forms", "System.Drawing", "System.Web.Extensions", "System.Security",
            "System.IO.Compression", "System.IO.Compression.FileSystem")]
        cls._compile(command + ["installer.cs", "installer111_probe.cs"])

    def run_window(self, mode, target, mt5=""):
        directory = self.work / (self._testMethodName + " 한글 ' 공백")
        directory.mkdir()
        executable = directory / "모세의지팡이 통합설치.exe"
        shutil.copyfile(self.work / "installer111_probe.exe", executable)
        sibling = directory / "다른 설치.exe"
        sibling.write_bytes(b"keep this unrelated setup")
        policy = directory / "fixture.json"
        policy.write_bytes(self.envelope())
        result = subprocess.run([str(executable), mode, str(policy), str(target), str(mt5)],
                                cwd=directory, capture_output=True, timeout=20)
        self.assertEqual(0, result.returncode, (result.stdout + result.stderr).decode("utf-8", errors="replace"))
        row = json.loads(result.stdout.decode("utf-8-sig"))
        self.assertTrue(row["window_loop_ended"])
        self.assertFalse(row["busy"])
        self.assertFalse(row["launch_field_exists"])
        if row["completed"]:
            deadline = time.monotonic() + 15
            while executable.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(executable.exists(), "Successful setup must close and delete itself without another click.")
            self.assertTrue(row["disposed"])
            self.assertFalse(row["visible"])
        else:
            self.assertTrue(row["visible"], "Failed/cancelled completion must leave its error visible.")
            self.assertFalse(row["disposed"])
            self.assertTrue(executable.is_file(), "Failed/cancelled setup must remain available.")
        self.assertEqual(b"keep this unrelated setup", sibling.read_bytes())
        self.assertTrue(policy.is_file())
        return row

    def test_successful_install_closes_removes_setup_and_delivers_five_manual_files(self):
        target = self.work / "설치 위치" / "모세"
        row = self.run_window("install", target)
        self.assertTrue(row["completed"])
        self.assertEqual({name + ".ex5" for name in PROGRAMS}, {p.name for p in (target / "MT5").iterdir()})
        for name in PROGRAMS:
            self.assertEqual((target / "MT5" / (name + ".ex5")).read_bytes(),
                             (target / "Part1/program/MT5" / (name + ".ex5")).read_bytes())

    def test_update_closes_removes_setup_and_replaces_manual_and_terminal_files(self):
        target = self.work / "업데이트 위치" / "모세"
        (target / "runtime").mkdir(parents=True)
        for relative in ("MOSES.exe", "python.exe", "runtime/code.bundle"):
            (target / relative).write_bytes(b"previous fixture")
        managed = []
        for name in PROGRAMS:
            for folder in ("MT5", "Part1/program/MT5"):
                relative = folder + "/" + name + ".ex5"
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"previous licensed fixture")
                managed.append(relative)
        (target / "runtime/install.json").write_text(json.dumps({"schema": 1, "version": "old", "managed_files": managed}), encoding="utf-8")
        mt5 = self.work / "터미널 데이터"
        (mt5 / "MQL5").mkdir(parents=True)
        row = self.run_window("update", target, mt5)
        self.assertTrue(row["completed"])
        for name in PROGRAMS:
            current = (target / "MT5" / (name + ".ex5")).read_bytes()
            destination = mt5 / "MQL5" / ("Experts" if name == PROGRAMS[0] else "Indicators") / (name + ".ex5")
            self.assertEqual(("licensed fixture " + name).encode(), current)
            self.assertEqual(current, destination.read_bytes())
        record = json.loads((target / "runtime/install.json").read_bytes())
        self.assertTrue(set(managed) <= set(record["managed_files"]))

    def test_failure_keeps_setup_and_displays_error(self):
        target = self.work / "실패 위치"
        target.mkdir()
        untouched = target / "사용자 파일.txt"
        untouched.write_bytes(b"preserve user data")
        row = self.run_window("install", target)
        self.assertFalse(row["completed"])
        self.assertIn("비어 있는 새 폴더", row["status"])
        self.assertEqual(b"preserve user data", untouched.read_bytes())

    def test_cancelled_worker_keeps_setup_and_can_retry(self):
        target = self.work / "취소 위치"
        row = self.run_window("cancel", target)
        self.assertFalse(row["completed"])
        self.assertEqual("설치가 취소되었습니다.", row["status"])
        self.assertFalse(target.exists())

    def test_removal_retries_a_read_lock_and_removes_only_matching_setup(self):
        template = (Path(__file__).resolve().parents[1] / "installer.cs").read_text(encoding="utf-8")
        script = re.search(r'const string script\s*=\s*@"(.*?)";', template, re.DOTALL).group(1).replace('""', '"')
        target = self.work / "일시 잠금 통합설치.exe"
        data = b"matching completed fixture"
        target.write_bytes(data)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 128, None)
        self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
        environment = dict(os.environ, MOSES_SETUP_REMOVE_PATH=str(target), MOSES_SETUP_REMOVE_PID="2147483647",
                           MOSES_SETUP_REMOVE_START="0", MOSES_SETUP_REMOVE_LENGTH=str(len(data)),
                           MOSES_SETUP_REMOVE_SHA256=hashlib.sha256(data).hexdigest().upper())
        powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        process = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                    base64.b64encode(script.encode("utf-16-le")).decode()],
                                   env=environment, cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(2)
            self.assertIsNone(process.poll(), "The helper must retry instead of abandoning a temporary read lock.")
            self.assertTrue(target.is_file())
        finally:
            kernel.CloseHandle(handle)
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(0, process.returncode, (stdout + stderr).decode("utf-8", errors="replace"))
        self.assertFalse(target.exists())
