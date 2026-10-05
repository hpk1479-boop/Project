"""The integrated web paths must work without importing legacy Tk GUIs."""

from __future__ import annotations

import builtins
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import nullcontext


PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))

from lab import unified_backtest, unified_live


class WebHeadlessTests(unittest.TestCase):
    def test_live_controls_and_status_do_not_import_tkinter(self):
        original_import = builtins.__import__

        def no_tk(name, *args, **kwargs):
            if name == "tkinter" or name.startswith("tkinter."):
                raise AssertionError("Web path imported the legacy Tk GUI")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=no_tk):
            with patch.object(unified_live, "_control_cache", None):
                owner = unified_live.control()
                self.assertIn("start_program", owner)
                self.assertIn("stop_program", owner)
                self.assertIn("load_special_settings", owner)
                self.assertIsInstance(unified_live.specials()["items"], dict)

            diagnostics, labels = unified_live._modules()
            sample = {
                "modules": {"STAFF": {"status": "정상", "last": 1, "errors": 0}},
                "pipe_connected": True,
            }
            with patch.object(diagnostics, "read_snapshot", return_value=sample):
                current = unified_live.status("STAFF")
            self.assertEqual(current["modules"]["STAFF"]["state"], "연결중")
            self.assertEqual(labels.display_name("STAFF"), "STAFF")

    def test_backtest_contract_imports_without_tkinter(self):
        original_import = builtins.__import__

        def no_tk(name, *args, **kwargs):
            if name == "tkinter" or name.startswith("tkinter."):
                raise AssertionError("Backtest web path imported the legacy Tk GUI")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=no_tk):
            settings, ui_model, progress = unified_backtest._part2()
        self.assertTrue(callable(settings.scenario))
        self.assertTrue(callable(ui_model.make_scenario))
        self.assertTrue(callable(progress))

    def test_headless_live_start_and_stop_keep_process_contract(self):
        owner = unified_live.control()
        namespace = owner["start_program"].__globals__
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            script = base / "event_host.py"
            script.write_text("", encoding="ascii")
            processes = Mock()
            processes.lifecycle_lock.side_effect = lambda: nullcontext()
            processes.find_engine_instances.return_value = []
            processes.wait_until_ready.return_value = (True, '감시 엔진 준비 완료',
                                                       {'pid': 123, 'created': '100', 'root': base})
            processes.stop_engine_instances.return_value = {'ok': True, 'message': '상태 저장 후 종료 완료'}
            replacements = {
                "BASE_DIR": base,
                "LOG_DIR": base / "logs",
                "PYCACHE_DIR": base / "logs" / "pycache",
                "resolve_script_path": lambda _: script,
                "resolve_manager_modules": lambda: ({}, []),
                "process_control": lambda: processes,
            }
            with patch.dict(namespace, replacements), patch.object(
                namespace["subprocess"], "Popen"
            ) as spawn:
                ok, _message = owner["start_program"](
                    "event_host.py", enabled_specials={"SPECIAL1"},
                    special_triggers='{"SPECIAL1":"브레이커 올존"}',
                    special_times='{"SPECIAL1":{}}',
                )
            self.assertTrue(ok)
            self.assertEqual(spawn.call_args.args[0][3], str(script))
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["OZ_ENABLED_SPECIALS"], "SPECIAL1")
            self.assertEqual(env["OZ_SPECIAL_TRIGGERS"], '{"SPECIAL1":"브레이커 올존"}')
            self.assertEqual(env["OZ_SPECIAL_TIME_FILTERS"], '{"SPECIAL1":{}}')
            self.assertTrue((base / "logs" / "pycache").is_dir())
            processes.wait_until_ready.assert_called_once()

            instance = {'pid': 321, 'created': '200', 'root': base, 'script': script}
            processes.find_engine_instances.return_value = [instance]
            with patch.dict(namespace, replacements), patch.object(
                namespace["subprocess"], "run", return_value=SimpleNamespace(returncode=0)
            ) as kill:
                ok, _message = owner["stop_program"]("event_host.py")
            self.assertTrue(ok)
            processes.stop_engine_instances.assert_called_once_with([instance])
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
