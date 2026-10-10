"""Normal closing starts immediately; only a repeated close asks about force."""
from __future__ import annotations

import ctypes
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import backtest_jobs, desktop_window, runtime_lifecycle
from lab import catalog, live_processes

_CONFIRM_FORCE_CLOSE = desktop_window._confirm_force_close

class CloseTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.window = Mock()
        self.closer = desktop_window._EngineCloser(self.window)
        self.closer._notify = Mock()
        self.prepare = self.stack.enter_context(patch.object(runtime_lifecycle, 'prepare_shutdown'))
        self.cancel = self.stack.enter_context(patch.object(runtime_lifecycle, 'cancel_shutdown'))
        self.save = self.stack.enter_context(patch.object(runtime_lifecycle, 'shutdown', return_value={'ok': True}))
        self.force = self.stack.enter_context(patch.object(runtime_lifecycle, 'force_shutdown', return_value={'ok': True}))
        self.choice = self.stack.enter_context(patch.object(desktop_window, '_confirm_force_close', return_value=False))
        self.exit = self.stack.enter_context(patch.object(desktop_window, '_exit_application'))
        self.error = self.stack.enter_context(patch.object(desktop_window, '_show_close_error'))
        self.warning = self.stack.enter_context(patch.object(desktop_window, '_show_close_warning'))

    def join(self):
        for thread in (self.closer.choice_thread, self.closer.force_thread, self.closer.thread):
            if thread is not None:
                thread.join(5)
                self.assertFalse(thread.is_alive())

    def test_first_x_starts_saving_without_any_dialog(self):
        self.assertFalse(self.closer.closing())
        self.join()
        self.prepare.assert_called_once()
        self.save.assert_called_once()
        self.choice.assert_not_called()
        self.force.assert_not_called()
        self.cancel.assert_not_called()
        self.window.destroy.assert_called_once()
        self.assertTrue(self.closer.ready)
        self.assertIn('종료 중', self.closer._notify.call_args.args[0])

    def test_no_answer_keeps_saving_and_closes_when_finished(self):
        entered, release = threading.Event(), threading.Event()
        def save(**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'ok': True}
        self.save.side_effect = save
        try:
            self.closer.closing()
            self.assertTrue(entered.wait(5))
            self.closer.closing()
            self.closer.choice_thread.join(5)
            self.force.assert_not_called()
            self.cancel.assert_not_called()
            self.window.destroy.assert_not_called()
            self.assertFalse(self.closer.interrupt.is_set())
        finally:
            release.set()
            self.join()
        self.window.destroy.assert_called_once()

    def test_yes_exits_while_normal_live_stop_remains_blocked(self):
        entered, release = threading.Event(), threading.Event()
        def save(**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'ok': True}
        self.save.side_effect = save
        self.choice.return_value = True
        try:
            self.closer.closing()
            self.assertTrue(entered.wait(5))
            self.assertFalse(self.closer.closing())
            self.closer.choice_thread.join(5)
            self.closer.force_thread.join(5)
            self.force.assert_called_once_with()
            self.exit.assert_called_once_with()
            self.assertTrue(self.closer.thread.is_alive())
            self.assertTrue(self.closer.ready)
            self.assertTrue(self.closer.thread.daemon)
            self.closer.finish()  # Must not join the blocked normal worker.
            self.assertTrue(self.closer.thread.is_alive())
            self.assertTrue(self.closer.interrupt.is_set())
        finally:
            release.set()
            self.join()
        self.window.destroy.assert_not_called()
        self.warning.assert_not_called()
        self.cancel.assert_not_called()

    def test_late_normal_stop_error_cannot_reopen_after_force(self):
        entered, release = threading.Event(), threading.Event()
        def save(**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            raise RuntimeError('terminated by force')
        self.save.side_effect = save
        self.choice.return_value = True
        try:
            self.closer.closing()
            self.assertTrue(entered.wait(5))
            self.closer.closing()
            self.closer.choice_thread.join(5)
            self.closer.force_thread.join(5)
        finally:
            release.set()
            self.join()
        self.error.assert_not_called()
        self.cancel.assert_not_called()
        self.assertTrue(self.closer.ready)

    def test_one_open_question_and_no_still_closes_after_saving(self):
        saving, finish, dialog, answer = [threading.Event() for _ in range(4)]
        def save(**kwargs):
            saving.set()
            self.assertTrue(finish.wait(5))
            return {'ok': True}
        def choice(window):
            dialog.set()
            self.assertTrue(answer.wait(5))
            return False
        self.save.side_effect, self.choice.side_effect = save, choice
        try:
            self.closer.closing()
            self.assertTrue(saving.wait(5))
            self.closer.closing()
            self.assertTrue(dialog.wait(5))
            self.closer.closing()
            self.assertEqual(self.choice.call_count, 1)
            finish.set()
            self.window.destroy.assert_not_called()
        finally:
            finish.set()
            answer.set()
            self.join()
        self.window.destroy.assert_called_once()
        self.cancel.assert_not_called()

    def test_force_failure_keeps_application_open(self):
        self.force.side_effect = RuntimeError('identity could not be verified')
        self.closer._force_and_close()
        self.exit.assert_not_called()
        self.assertFalse(self.closer.ready)
        self.error.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows confirmation')
    def test_windows_question_has_yes_no_and_defaults_to_no(self):
        for answer, expected in ((6, True), (7, False), (0, False)):
            with self.subTest(answer=answer), patch.object(ctypes.windll.user32, 'MessageBoxW', return_value=answer) as show:
                self.assertIs(_CONFIRM_FORCE_CLOSE(self.window), expected)
                self.assertIn('강제 종료하시겠습니까?', show.call_args.args[1])
                self.assertEqual(show.call_args.args[3] & 0xF, 4)
                self.assertEqual(show.call_args.args[3] & 0x300, 0x100)


class LifecycleTests(unittest.TestCase):
    def test_prepare_never_reads_jobs_to_choose_an_initial_dialog(self):
        with patch.object(backtest_jobs, 'begin_shutdown') as begin, \
             patch.object(backtest_jobs, 'active_jobs') as jobs:
            runtime_lifecycle.prepare_shutdown()
        begin.assert_called_once()
        jobs.assert_not_called()

    def test_force_does_not_wait_for_the_normal_shutdown_lock(self):
        with patch.object(backtest_jobs, 'force_shutdown', return_value={'warnings': ['backtest']}) as force, \
             patch.object(runtime_lifecycle.unified_live, 'force_shutdown', return_value={'warnings': ['live']}) as live, \
             patch.object(runtime_lifecycle, '_force_ai_service', return_value={'ok': True}) as ai:
            runtime_lifecycle._LOCK.acquire()
            try:
                thread = threading.Thread(target=lambda: runtime_lifecycle.force_shutdown(), daemon=True)
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
            finally:
                runtime_lifecycle._LOCK.release()
        force.assert_called_once_with(timeout=5)
        live.assert_called_once_with()
        ai.assert_called_once_with(5)

    def test_failed_component_does_not_skip_other_force_operations(self):
        with patch.object(backtest_jobs, 'force_shutdown', side_effect=RuntimeError('backtest failed')), \
             patch.object(runtime_lifecycle.unified_live, 'force_shutdown', return_value={}) as live, \
             patch.object(runtime_lifecycle, '_force_ai_service', return_value={}) as ai:
            with self.assertRaisesRegex(RuntimeError, 'backtest failed'):
                runtime_lifecycle.force_shutdown()
        live.assert_called_once()
        ai.assert_called_once()

    def test_force_after_backtest_save_prevents_normal_live_cleanup(self):
        interrupted = threading.Event()
        def saved(**kwargs):
            interrupted.set()
            return {'ok': True}
        with patch.object(backtest_jobs, 'shutdown', side_effect=saved), \
             patch.object(runtime_lifecycle.unified_live, 'shutdown') as live, \
             patch.object(runtime_lifecycle, 'cancel_shutdown') as cancel:
            with self.assertRaises(backtest_jobs.ForceShutdownRequested):
                runtime_lifecycle.shutdown(force_requested=interrupted)
        live.assert_not_called()
        cancel.assert_not_called()

    def test_force_caused_normal_live_failure_does_not_reset_start_gate(self):
        interrupted = threading.Event()
        def killed():
            interrupted.set()
            raise RuntimeError('force killed live')
        with patch.object(backtest_jobs, 'shutdown', return_value={}), \
             patch.object(runtime_lifecycle.unified_live, 'shutdown', side_effect=killed), \
             patch.object(runtime_lifecycle, 'cancel_shutdown') as cancel:
            with self.assertRaises(backtest_jobs.ForceShutdownRequested):
                runtime_lifecycle.shutdown(force_requested=interrupted)
        cancel.assert_not_called()


class AIForceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.client = Mock()
        self.client._read_record.return_value = {'pid': 314159, 'created': '1234'}
        self.stack.enter_context(patch('common_ai.client.Client', return_value=self.client))
        self.identity = self.stack.enter_context(patch('common_ai.process_identity.identity', return_value=None))
        self.api = Mock()
        self.api.hold_process_identity.side_effect = lambda *args: nullcontext(True)
        self.stack.enter_context(patch('lab.live_processes.process_api', return_value=self.api))
        self.kill = self.stack.enter_context(patch.object(runtime_lifecycle.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)))

    def test_only_verified_project_service_tree_is_terminated(self):
        self.assertTrue(runtime_lifecycle._force_ai_service(5)['ok'])
        self.api.hold_process_identity.assert_called_once_with(314159, '1234')
        self.assertEqual(self.kill.call_args.args[0], ['taskkill', '/PID', '314159', '/T', '/F'])
        self.client.close.assert_called_once()

    def test_missing_or_reused_owner_does_not_kill_any_process(self):
        for record, active in ((None, True), ({'pid': 314159, 'created': '1234'}, False)):
            with self.subTest(record=record):
                self.client._read_record.return_value = record
                self.api.hold_process_identity.side_effect = lambda *args: nullcontext(active)
                runtime_lifecycle._force_ai_service(5)
        self.kill.assert_not_called()

    def test_remaining_exact_owner_reports_failure(self):
        self.identity.return_value = '1234'
        with self.assertRaisesRegex(RuntimeError, '공통 AI'):
            runtime_lifecycle._force_ai_service(5)

    def test_record_pointing_to_application_is_rejected(self):
        self.client._read_record.return_value = {'pid': os.getpid(), 'created': '1234'}
        with self.assertRaisesRegex(RuntimeError, '소유 정보'):
            runtime_lifecycle._force_ai_service(5)
        self.kill.assert_not_called()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('MOSES_PROCESS_SMOKE') == '1',
                     'Explicitly enabled isolated Windows process smoke test')
class ForceProcessSmokeTests(unittest.TestCase):
    def test_force_close_exits_actual_application_with_a_blocked_save_thread(self):
        script = '''
import sys, threading, time
sys.path.insert(0, sys.argv[1])
from lab import desktop_window, runtime_lifecycle
from unittest.mock import Mock
entered = threading.Event()
def saving(**kwargs):
    entered.set()
    threading.Event().wait(60)
runtime_lifecycle.prepare_shutdown = lambda: None
runtime_lifecycle.shutdown = saving
runtime_lifecycle.force_shutdown = lambda: {'ok': True}
desktop_window._confirm_force_close = lambda window: True
closer = desktop_window._EngineCloser(Mock())
closer._notify = Mock()
closer.closing()
assert entered.wait(5)
print('SAVING', flush=True)
closer.closing()
threading.Event().wait(60)
'''
        process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-c', script,
            str(Path(__file__).resolve().parents[1])], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, creationflags=live_processes.CREATE_NO_WINDOW)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'SAVING')
            started = time.monotonic()
            process.wait(timeout=5)
            elapsed = time.monotonic() - started
            self.assertEqual(process.returncode, 0, process.stderr.read())
            self.assertLess(elapsed, 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_force_ai_terminates_fixture_tree_and_preserves_unrelated_python(self):
        from common_ai.client import project_id
        from common_ai.process_identity import identity
        script = ('import subprocess,sys,time; '
                  'child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(60)"]); '
                  'print(child.pid,flush=True);time.sleep(60)')
        service = subprocess.Popen([sys.executable, '-B', '-c', script],
            stdout=subprocess.PIPE, text=True, creationflags=live_processes.CREATE_NO_WINDOW)
        unrelated = subprocess.Popen([sys.executable, '-B', '-c', 'import time;time.sleep(60)'],
            creationflags=live_processes.CREATE_NO_WINDOW)
        try:
            child = int(service.stdout.readline())
            child_created = identity(child)
            self.assertIsNotNone(child_created)
            with tempfile.TemporaryDirectory(prefix='MOSES force AI fixture ') as folder:
                root = Path(folder)
                (root / 'runtime').mkdir()
                (root / 'runtime' / 'ai_service.json').write_text(json.dumps({
                    'pid': service.pid, 'created': identity(service.pid),
                    'project': project_id(root), 'token': 'fixture-key' * 4,
                    'port': 12345, 'service_id': 'fixture'}), encoding='utf-8')
                with patch.object(catalog, 'ROOT', root / 'Part3'):
                    self.assertTrue(runtime_lifecycle._force_ai_service(5)['ok'])
                service.wait(timeout=5)
                self.assertNotEqual(identity(child), child_created)
                self.assertIsNone(unrelated.poll())
        finally:
            if service.poll() is None:
                subprocess.run(['taskkill', '/PID', str(service.pid), '/T', '/F'],
                    capture_output=True, creationflags=live_processes.CREATE_NO_WINDOW, timeout=5)
                service.wait(timeout=5)
            service.stdout.close()
            unrelated.terminate()
            unrelated.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
