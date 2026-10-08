"""Live start passes the chosen strategies to the engine; stop goes through the saved shutdown.

(수정본162 deleted the two checks that the web paths do not import the removed Tk GUIs.)
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import nullcontext


PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))

from lab import unified_live


class WebHeadlessTests(unittest.TestCase):
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
