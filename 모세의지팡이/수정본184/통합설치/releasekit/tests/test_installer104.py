"""Isolated native checks for installer actions and MT5 path resolution.

Only generated fixture executables and files are used. No actual MOSES or MT5
process is started. Archive probes disable system integration; the failing
window payload is rejected before prerequisites or installation registration.
"""
from __future__ import annotations

import ctypes
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time
import unittest
import zipfile

from test_installer import InstallerFixture


class Installer104Tests(InstallerFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        harness = r'''
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using MosesInstaller;
public static class Installer104Probe {
    private static object Property(object item, string name) {
        return item.GetType().GetProperty(name).GetValue(item, null);
    }
    private static void Json(object value) {
        Console.Write(new JavaScriptSerializer().Serialize(value));
    }
    [STAThread]
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        try {
            if (args[0] == "is") {
                Json(InstallCore.IsInstallation(args[1]));
            } else if (args[0] == "filter") {
                Json(InstallCore.FilterInstallations(args.Skip(1)));
            } else if (args[0] == "default") {
                Json(InstallCore.DefaultInstallationFolder(args[1]));
            } else if (args[0] == "locations") {
                List<object> rows = new List<object>();
                foreach (object item in InstallCore.FindMt5Locations(args[1])) {
                    rows.Add(new Dictionary<string, object> {
                        { "data", Property(item, "DataFolder") },
                        { "terminal", Property(item, "TerminalFolder") },
                        { "label", item.ToString() }
                    });
                }
                Json(rows);
            } else if (args[0] == "resolve") {
                List<string> roots = new List<string>();
                IEnumerable candidates = InstallCore.ResolveMt5Folder(args[2], InstallCore.FindMt5Locations(args[1]));
                foreach (object item in candidates) {
                    roots.Add(item is string ? (string)item : Convert.ToString(Property(item, "DataFolder")));
                }
                Json(roots);
            } else if (args[0] == "apply") {
                using (Stream payload = File.OpenRead(args[1])) {
                    InstallCore.InstallArchive(payload, args[2], File.ReadAllBytes(args[3]),
                        args[4], null, false, Boolean.Parse(args[5]));
                }
            } else if (args[0] == "buttons") {
                using (InstallWindow form = new InstallWindow(File.ReadAllBytes(args[1]), "fixture")) {
                    BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                    Button install = (Button)typeof(InstallWindow).GetField("install", flags).GetValue(form);
                    Button update = (Button)typeof(InstallWindow).GetField("update", flags).GetValue(form);
                    TextBox folder = (TextBox)typeof(InstallWindow).GetField("folder", flags).GetValue(form);
                    Json(new Dictionary<string, object> {
                        { "install", install.Text }, { "update", update.Text },
                        { "folder", folder.Text }, { "readonly", folder.ReadOnly },
                        { "install_handler", typeof(InstallWindow).GetMethod("BeginInstall", flags) != null },
                        { "update_handler", typeof(InstallWindow).GetMethod("BeginUpdate", flags) != null }
                    });
                }
            } else if (args[0] == "schedule") {
                InstallCore.ScheduleInstallerRemoval(Assembly.GetExecutingAssembly().Location);
                File.WriteAllText(args[1], "ready");
                DateTime deadline = DateTime.UtcNow.AddSeconds(20);
                while (!File.Exists(args[2]) && DateTime.UtcNow < deadline) Thread.Sleep(25);
                if (!File.Exists(args[2])) return 8;
            } else if (args[0] == "schedule-other") {
                InstallCore.ScheduleInstallerRemoval(args[1]);
            } else if (args[0] == "reject-update") {
                BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                using (InstallWindow form = new InstallWindow(File.ReadAllBytes(args[1]), "fixture")) {
                    TextBox folder = (TextBox)typeof(InstallWindow).GetField("folder", flags).GetValue(form);
                    Label status = (Label)typeof(InstallWindow).GetField("status", flags).GetValue(form);
                    folder.Text = args[2];
                    typeof(InstallWindow).GetMethod("BeginUpdate", flags).Invoke(form, new object[] { null, EventArgs.Empty });
                    Json(new Dictionary<string, object> {
                        { "completed", form.CompletedSuccessfully }, { "status", status.Text },
                        { "busy", typeof(InstallWindow).GetField("busy", flags).GetValue(form) }
                    });
                }
            } else if (args[0] == "failed-window") {
                BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                using (InstallWindow form = new InstallWindow(File.ReadAllBytes(args[1]), "fixture")) {
                    TextBox folder = (TextBox)typeof(InstallWindow).GetField("folder", flags).GetValue(form);
                    ComboBox mt5 = (ComboBox)typeof(InstallWindow).GetField("mt5Location", flags).GetValue(form);
                    FieldInfo busy = typeof(InstallWindow).GetField("busy", flags);
                    Label status = (Label)typeof(InstallWindow).GetField("status", flags).GetValue(form);
                    folder.Text = args[2];
                    typeof(InstallWindow).GetMethod("LoadMt5", flags).Invoke(form, new object[] { args[2] });
                    mt5.SelectedIndex = 0;
                    typeof(InstallWindow).GetMethod("BeginInstall", flags).Invoke(form, new object[] { null, EventArgs.Empty });
                    DateTime deadline = DateTime.UtcNow.AddSeconds(15);
                    while ((bool)busy.GetValue(form) && DateTime.UtcNow < deadline) {
                        Application.DoEvents(); Thread.Sleep(20);
                    }
                    Json(new Dictionary<string, object> {
                        { "busy", busy.GetValue(form) }, { "completed", form.CompletedSuccessfully },
                        { "status", status.Text }
                    });
                    form.Close();
                    if (form.CompletedSuccessfully) InstallCore.ScheduleInstallerRemoval(Assembly.GetExecutingAssembly().Location);
                }
            } else return 9;
            return 0;
        } catch (Exception error) {
            Console.Write(error.Message);
            return 7;
        }
    }
}
'''
        (cls.work / "installer104_probe.cs").write_text(harness, encoding="utf-8")
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        command = [str(compiler), "/nologo", "/codepage:65001", "/platform:x64",
                   "/target:exe", "/main:Installer104Probe", "/out:installer104_probe.exe",
                   "/resource:payload.zip,MosesPayload"]
        command += ["/reference:" + name + ".dll" for name in (
            "System.Windows.Forms", "System.Drawing", "System.Web.Extensions", "System.Security",
            "System.IO.Compression", "System.IO.Compression.FileSystem")]
        cls._compile(command + ["installer.cs", "installer104_probe.cs"])

    def native(self, *args, executable=None, timeout=30):
        return subprocess.run([str(executable or self.work / "installer104_probe.exe"),
                               *map(str, args)], cwd=self.work, capture_output=True, timeout=timeout)

    def native_json(self, *args):
        result = self.native(*args)
        self.assertEqual(0, result.returncode, result.stdout.decode("utf-8", errors="replace"))
        return json.loads(result.stdout.decode("utf-8-sig"))

    def fixture_install(self, folder, version="fixture"):
        for relative in ("MOSES.exe", "python.exe", "runtime/code.bundle"):
            file = folder / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(version.encode())
        (folder / "runtime/install.json").write_text(json.dumps({
            "schema": 1, "version": version, "managed_files": [], "mt5_data_folder": ""
        }), encoding="utf-8")
        return folder

    def mt5_fixture(self, name, terminal=None, encoding="utf-16"):
        data_root = self.work / self._testMethodName / "MetaQuotes" / "Terminal"
        folder = data_root / name
        (folder / "MQL5" / "Experts").mkdir(parents=True)
        (folder / "MQL5" / "Indicators").mkdir()
        if terminal is not None:
            terminal.mkdir(parents=True, exist_ok=True)
            (terminal / "terminal64.exe").write_bytes(b"fixture, never executed")
            (folder / "origin.txt").write_text(str(terminal), encoding=encoding)
        return data_root, folder

    def package104(self, name, marker=b"release"):
        package = self.work / (name + ".zip")
        files = {
            "MOSES.exe": marker, "python.exe": marker, "runtime/code.bundle": marker,
            "settings/strategy_registry.json": json.dumps({"presets": [{"id": "SPECIAL1"}]}).encode(),
            "settings/ai_settings.json": b"{}", "Part1/program/config.txt": b"defaults",
        }
        for base in ("THE_STAFF_OF_MOSES", "PRICE_of_Moses", "RSI_of_Moses", "STO_of_Moses", "DI_of_Moses"):
            files["Part1/program/MT5/" + base + ".ex5"] = marker
        with zipfile.ZipFile(package, "w") as archive:
            for relative, value in files.items():
                archive.writestr(relative, value)
            archive.writestr("Part3/TEST_SPECIAL/", b"")
        return package

    def apply104(self, target, *, update, mt5="", package=None):
        policy = self.work / "policy104.json"
        policy.write_bytes(self.envelope())
        return self.native("apply", package or self.package104("action"), target, policy,
                           mt5, str(update))

    @staticmethod
    def tree_bytes(root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()} if root.exists() else {}

    def test_installation_filter_requires_complete_install_and_deduplicates_paths(self):
        root = self.fixture_install(self.work / "설치 필터")
        partial = self.work / "partial"
        (partial / "runtime").mkdir(parents=True)
        for relative in ("MOSES.exe", "python.exe", "runtime/code.bundle"):
            (partial / relative).write_bytes(b"partial")
        self.assertTrue(self.native_json("is", root))
        self.assertFalse(self.native_json("is", partial))
        values = self.native_json("filter", root, str(root).upper(), str(root) + "\\", root / ".",
                                  partial, self.work / "missing", "", "bad\x01path")
        self.assertEqual([str(root)], values)
        (root / "python.exe").unlink()
        self.assertEqual([], self.native_json("filter", root))

    def test_default_is_moses_beside_setup_not_documents(self):
        setup = self.work / "다운로드 공백 폴더" / "모세의지팡이 통합설치.exe"
        self.assertEqual(str(setup.parent / "모세"), self.native_json("default", setup))

    def test_buttons_are_both_available_without_fixed_update_mode(self):
        policy = self.work / "button-policy.json"
        policy.write_bytes(self.envelope())
        row = self.native_json("buttons", policy)
        self.assertEqual("설치", row["install"])
        self.assertEqual("업데이트", row["update"])
        self.assertFalse(row["readonly"])
        self.assertTrue(row["install_handler"])
        self.assertTrue(row["update_handler"])
        self.assertEqual(str(self.work / "모세"), row["folder"])

    def test_origin_paths_produce_human_labels_and_canonical_resolution(self):
        terminal = self.work / "Broker MT5 설치"
        data_root, data = self.mt5_fixture("ABCDEF0123456789ABCDEF0123456789", terminal)
        rows = self.native_json("locations", data_root)
        row = next(item for item in rows if item["data"] == str(data))
        self.assertEqual(str(terminal), row["terminal"])
        self.assertIn(str(terminal), row["label"])
        self.assertIn(terminal.name, row["label"])
        self.assertNotEqual(str(data), row["label"])
        for selected in (data, data / "MQL5", data / "MQL5" / "Experts",
                         data / "MQL5" / "Indicators", terminal, data_root):
            with self.subTest(selected=selected):
                self.assertEqual([str(data)], self.native_json("resolve", data_root, selected))

    def test_utf8_origin_and_multiple_roots_never_guess_one(self):
        terminal = self.work / "Another Broker MT5"
        data_root, one = self.mt5_fixture("11111111111111111111111111111111", terminal, "utf-8")
        _, two = self.mt5_fixture("22222222222222222222222222222222", terminal)
        self.assertEqual({str(one), str(two)}, set(self.native_json("resolve", data_root, terminal)))
        self.assertEqual({str(one), str(two)}, set(self.native_json("resolve", data_root, data_root)))
        unknown = self.work / "unrelated-folder"
        unknown.mkdir()
        self.assertEqual([], self.native_json("resolve", data_root, unknown))
        self.assertEqual([], self.native_json("resolve", data_root, self.work / "absent-folder"))

    def test_portable_data_root_and_originless_data_root_remain_usable(self):
        data_root, data = self.mt5_fixture("Portable MT5")
        (data / "terminal64.exe").write_bytes(b"portable fixture, never executed")
        self.assertEqual([str(data)], self.native_json("resolve", data_root, data))
        self.assertEqual([str(data)], self.native_json("resolve", data_root, data / "MQL5"))
        self.assertTrue(any(row["data"] == str(data) for row in self.native_json("locations", data_root)))

    def test_update_requires_existing_install_and_never_falls_back_to_install(self):
        for name, create in (("absent-update", False), ("empty-update", True), ("partial-update", True)):
            target = self.work / name
            if create:
                target.mkdir()
            if name.startswith("partial"):
                (target / "MOSES.exe").write_bytes(b"not an installation")
            before = self.tree_bytes(target)
            result = self.apply104(target, update=True)
            self.assertEqual(7, result.returncode)
            message = result.stdout.decode("utf-8", errors="replace")
            self.assertIn("모세 설치 위치를 찾을 수 없습니다", message)
            self.assertIn("폴더를 지정해 주세요", message)
            self.assertIn("‘설치’를 클릭해 주세요", message)
            self.assertEqual(before, self.tree_bytes(target))
            if not create:
                self.assertFalse(target.exists())

    def test_install_rejects_existing_install_and_nonempty_user_folder(self):
        installed = self.fixture_install(self.work / "already installed")
        ordinary = self.work / "ordinary-user-folder"
        ordinary.mkdir()
        (ordinary / "important.txt").write_bytes(b"user data")
        for target in (installed, ordinary):
            with self.subTest(target=target):
                before = self.tree_bytes(target)
                result = self.apply104(target, update=False)
                self.assertEqual(7, result.returncode)
                self.assertEqual(before, self.tree_bytes(target))

    def test_update_button_explains_invalid_selected_folder_without_starting_worker(self):
        policy = self.work / "reject-update-policy.json"
        policy.write_bytes(self.envelope())
        target = self.work / "not-an-update-target"
        row = self.native_json("reject-update", policy, target)
        self.assertFalse(row["completed"])
        self.assertFalse(row["busy"])
        self.assertIn("모세 설치 위치를 찾을 수 없습니다", row["status"])
        self.assertIn("폴더를 지정해 주세요", row["status"])
        self.assertIn("‘설치’를 클릭해 주세요", row["status"])
        self.assertFalse(target.exists())

    def test_separate_installations_then_explicit_update_only_changes_selected_one(self):
        first, second = self.work / "모세 v1", self.work / "모세 v2"
        for target in (first, second):
            result = self.apply104(target, update=False)
            self.assertEqual(0, result.returncode, result.stdout.decode("utf-8", errors="replace"))
        untouched = self.tree_bytes(first)
        (second / "Part1/program/config.txt").write_bytes(b"user configuration")
        package = self.package104("new-release", b"updated")
        result = self.apply104(second, update=True, package=package)
        self.assertEqual(0, result.returncode, result.stdout.decode("utf-8", errors="replace"))
        self.assertEqual(untouched, self.tree_bytes(first))
        self.assertEqual(b"updated", (second / "MOSES.exe").read_bytes())
        self.assertEqual(b"user configuration", (second / "Part1/program/config.txt").read_bytes())
        self.assertEqual({str(first), str(second)}, set(self.native_json("filter", first, second)))

    def test_mt5_copy_and_manual_copy_payload_are_both_preserved(self):
        _, mt5 = self.mt5_fixture("Manual copy fixture")
        target = self.work / "mt5-copy-install"
        result = self.apply104(target, update=False, mt5=mt5)
        self.assertEqual(0, result.returncode, result.stdout.decode("utf-8", errors="replace"))
        for base in ("THE_STAFF_OF_MOSES", "PRICE_of_Moses", "RSI_of_Moses", "STO_of_Moses", "DI_of_Moses"):
            manual = target / "Part1/program/MT5" / (base + ".ex5")
            installed = mt5 / "MQL5" / ("Experts" if base == "THE_STAFF_OF_MOSES" else "Indicators") / (base + ".ex5")
            self.assertEqual(manual.read_bytes(), installed.read_bytes())

    def test_removal_rejects_an_executable_other_than_its_own_assembly(self):
        other = self.work / "other.exe"
        other.write_bytes(b"must survive")
        result = self.native("schedule-other", other)
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"must survive", other.read_bytes())
        self.assertTrue((self.work / "installer104_probe.exe").is_file())

    def test_self_removal_waits_for_exit_and_preserves_sibling_files(self):
        directory = self.work / "한글 공백 ' 삭제 확인"
        directory.mkdir()
        executable = directory / "모세의지팡이 통합설치.exe"
        shutil.copyfile(self.work / "installer104_probe.exe", executable)
        sibling = directory / "다른 설치.exe"
        sibling.write_bytes(b"must survive")
        ready, release = directory / "ready.txt", directory / "release.txt"
        process = subprocess.Popen([str(executable), "schedule", str(ready), str(release)],
                                   cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "Removal helper scheduling did not finish.")
            self.assertIsNone(process.poll())
            self.assertTrue(executable.is_file(), "The running installer must remain until exit.")
            self.assertEqual(b"must survive", sibling.read_bytes())
            release.write_text("exit", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(0, process.returncode, (stdout + stderr).decode("utf-8", errors="replace"))
            deadline = time.monotonic() + 15
            while executable.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(executable.exists(), "The completed fixture installer was not removed.")
            self.assertEqual(b"must survive", sibling.read_bytes())
            self.assertTrue(ready.is_file())
            self.assertTrue(release.is_file())
        finally:
            if process.poll() is None:
                release.write_text("exit", encoding="utf-8")
                process.kill()
                process.communicate(timeout=10)

    def test_actual_removal_script_deletes_original_and_preserves_same_length_replacement(self):
        template = (Path(__file__).resolve().parents[1] / "installer.cs").read_text(encoding="utf-8")
        match = re.search(r'const string script\s*=\s*@"(.*?)";', template, re.DOTALL)
        self.assertIsNotNone(match, "The one-time removal script was not found.")
        script = match.group(1).replace('""', '"')
        original, replacement = b"original fixture", b"replaced fixture"
        self.assertEqual(len(original), len(replacement))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        for name, current in (("원본 통합설치.exe", original), ("교체된 통합설치.exe", replacement),
                              ("길이 변경 통합설치.exe", replacement + b"changed length")):
            with self.subTest(name=name):
                target = self.work / name
                target.write_bytes(current)
                environment = dict(os.environ)
                environment.update({
                    "MOSES_SETUP_REMOVE_PATH": str(target),
                    "MOSES_SETUP_REMOVE_PID": "2147483647",
                    "MOSES_SETUP_REMOVE_START": "0",
                    "MOSES_SETUP_REMOVE_LENGTH": str(len(original)),
                    "MOSES_SETUP_REMOVE_SHA256": hashlib.sha256(original).hexdigest().upper(),
                })
                result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                        env=environment, cwd=self.work, capture_output=True, timeout=15,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
                if current == original:
                    self.assertFalse(target.exists(), "A matching completed setup must be removed.")
                else:
                    self.assertEqual(current, target.read_bytes(), "A replacement at the old path must not be deleted.")

    def test_failed_payload_does_not_mark_window_success_or_delete_fixture_setup(self):
        policy = self.work / "failed-window-policy.json"
        policy.write_bytes(self.envelope())
        target = self.work / "failed-window-target"
        executable = self.work / "installer104_probe.exe"
        before = executable.read_bytes()
        row = self.native_json("failed-window", policy, target)
        self.assertFalse(row["busy"])
        self.assertFalse(row["completed"])
        self.assertIn("필수 프로그램 파일", row["status"])
        self.assertFalse(target.exists())
        self.assertEqual(before, executable.read_bytes())

    def test_cancelling_fixture_installation_keeps_setup_file(self):
        record = self.envelope()
        executable = self.work / "cancelled104.exe"
        executable.write_bytes((self.work / "installer.exe").read_bytes() + record +
                               struct.pack("<I", len(record)) + b"MOSES96!")
        original = executable.read_bytes()
        process = subprocess.Popen([str(executable)], cwd=self.work,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        window = None
        try:
            deadline = time.monotonic() + 10
            while window is None and process.poll() is None and time.monotonic() < deadline:
                windows = []

                @callback_type
                def collect(handle, _):
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(ctypes.c_void_p(handle), ctypes.byref(pid))
                    if pid.value == process.pid and user32.IsWindowVisible(ctypes.c_void_p(handle)):
                        windows.append(handle)
                    return True

                user32.EnumWindows(collect, None)
                window = windows[0] if windows else None
                if window is None:
                    time.sleep(0.025)
            self.assertIsNotNone(window, "The isolated fixture installer window did not open.")
            user32.SendMessageW(ctypes.c_void_p(window), 0x0010, 0, 0)  # WM_CLOSE: cancel before installing.
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(0, process.returncode, (stdout + stderr).decode("utf-8", errors="replace"))
            self.assertEqual(original, executable.read_bytes())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
