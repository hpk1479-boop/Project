"""Window close stops all MOSES live engines and cannot silently leave them alive."""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import desktop_window, live_processes, unified_live
from lab import backtest_jobs, catalog, runtime_lifecycle
from lab.ai.model_runtime import RUNTIME
from common_ai import client as common_client


def completed(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, '')


def instance(pid, created):
    return {'pid': pid, 'created': str(created),
            'root': Path(tempfile.gettempdir()) / '모의 수정본' / 'Part1'}


class EngineProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows command line parser')
    def test_only_executed_live_scripts_match_after_relocation(self):
        with tempfile.TemporaryDirectory(prefix='moses close 이동 ') as folder:
            root = Path(folder) / '다른 위치'
            for revision in ('수정본51', '수정본54', '수정본57'):
                script = root / revision / 'Part1' / 'program' / 'event_host.py'
                command = subprocess.list2cmdline([sys.executable, '-X',
                    'pycache_prefix=' + str(root / '캐시'), str(script)])
                self.assertTrue(live_processes._is_engine(command), revision)
            script = str(root / '수정본57' / 'Part1' / 'program' / 'event_host.py')
            for args in (
                [sys.executable, '-c', 'print(' + repr(script) + ')'],
                [sys.executable, '-m', 'unittest', script],
                [sys.executable, str(root / 'other.py'), script],
                [sys.executable, str(root / 'OtherApp' / 'event_host.py')],
                [sys.executable, 'Part1/program/event_host.py'],
                [sys.executable, '-X', script],
                [sys.executable, str(root / 'Part2' / 'event_host.py')],
            ):
                self.assertFalse(live_processes._is_engine(subprocess.list2cmdline(args)), args)

    @unittest.skipUnless(os.name == 'nt', 'Windows command line parser')
    def test_query_includes_other_revision_and_excludes_other_python(self):
        root = Path(tempfile.gettempdir()) / '다른 프로젝트 위치'
        rows = [{'ProcessId': 101, 'Created': '100', 'CommandLine':
                 subprocess.list2cmdline([sys.executable,
                     str(root / '수정본51' / 'Part1' / 'program' / 'event_host.py')])},
                {'ProcessId': 102, 'Created': '200', 'CommandLine':
                 subprocess.list2cmdline([sys.executable, '-c', 'pass'])}]
        with patch.object(live_processes.subprocess, 'run', return_value=completed(json.dumps(rows))):
            self.assertEqual(live_processes.find_engines(), {101: 100})

    def test_query_failure_does_not_report_no_engines(self):
        cases = [completed('', 1), completed('not json'), completed('{"bad":"record"}'),
                 subprocess.TimeoutExpired('powershell', 10), OSError('denied')]
        for case in cases:
            with self.subTest(case=type(case).__name__):
                with patch.object(live_processes.subprocess, 'run') as run:
                    if isinstance(case, Exception):
                        run.side_effect = case
                    else:
                        run.return_value = case
                    with self.assertRaisesRegex(RuntimeError, '조회에 실패'):
                        live_processes.stop_all_engines()
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(run.call_args.args[0][0], 'powershell.exe')

    def test_stops_all_known_engines_and_verifies_they_are_gone(self):
        core = live_processes.process_api()
        active = {101, 102}
        def kill(args, **kwargs):
            active.discard(int(args[2]))
            return completed()
        real_stop = core.stop_engine_instances
        with patch.object(core, 'find_engine_instances', side_effect=[[instance(101, 1), instance(102, 2)], []]), \
             patch.object(core, 'lifecycle_lock', side_effect=lambda: nullcontext()), \
             patch.object(core, 'lifecycle', return_value=Mock(read_status=Mock(return_value=None), request_stop=Mock(return_value=False))), \
             patch.object(core, 'stop_engine_instances', side_effect=lambda rows, **kwargs: real_stop(rows, timeout=0)), \
             patch.object(core, 'process_active', side_effect=lambda pid, created: pid in active), \
             patch.object(core, '_hold_process') as hold, \
             patch.object(live_processes.subprocess, 'run', return_value=completed()) as run:
            hold.return_value.__enter__.return_value = True
            run.side_effect = kill
            self.assertTrue(live_processes.stop_all_engines()['ok'])
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['taskkill', '/PID', '101', '/T', '/F'],
                ['taskkill', '/PID', '102', '/T', '/F']])

    def test_termination_failure_still_stops_other_engine_and_reports_remaining(self):
        core = live_processes.process_api()
        active = {101, 102}
        def kill(args, **kwargs):
            if int(args[2]) == 101:
                raise subprocess.TimeoutExpired('taskkill', 10)
            active.discard(102)
            return completed()
        real_stop = core.stop_engine_instances
        with patch.object(core, 'find_engine_instances', side_effect=[[instance(101, 1), instance(102, 2)], [instance(101, 1)]]), \
             patch.object(core, 'lifecycle_lock', side_effect=lambda: nullcontext()), \
             patch.object(core, 'lifecycle', return_value=Mock(read_status=Mock(return_value=None), request_stop=Mock(return_value=False))), \
             patch.object(core, 'stop_engine_instances', side_effect=lambda rows, **kwargs: real_stop(rows, timeout=0)), \
             patch.object(core, 'process_active', side_effect=lambda pid, created: pid in active), \
             patch.object(core, '_hold_process') as hold, \
             patch.object(live_processes.subprocess, 'run') as run:
            hold.return_value.__enter__.return_value = True
            run.side_effect = kill
            with self.assertRaisesRegex(RuntimeError, '남은 PID: 101'):
                live_processes.stop_all_engines()
            self.assertEqual(run.call_count, 2)

    def test_exited_or_reused_pid_is_not_terminated(self):
        core = live_processes.process_api()
        with patch.object(core, 'find_engine_instances', side_effect=[[instance(101, 1)], []]), \
             patch.object(core, 'lifecycle_lock', side_effect=lambda: nullcontext()), \
             patch.object(core, '_hold_process') as hold, \
             patch.object(live_processes.subprocess, 'run') as run:
            hold.return_value.__enter__.return_value = False
            live_processes.stop_all_engines()
            run.assert_not_called()

    def test_verification_failure_is_not_success(self):
        core = live_processes.process_api()
        with patch.object(core, 'lifecycle_lock', side_effect=lambda: nullcontext()), \
             patch.object(core, 'find_engine_instances', side_effect=[[], RuntimeError('조회 실패')]):
            with self.assertRaisesRegex(RuntimeError, '조회 실패'):
                live_processes.stop_all_engines()


class WindowCloseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='moses close fixture ')
        self.addCleanup(temporary.cleanup)
        project = patch.object(catalog, 'ROOT', Path(temporary.name) / 'Part3')
        project.start()
        self.addCleanup(project.stop)
        self.ai_client = Mock(spec=['shutdown', 'close'])
        # Exercise live close feedback without reading current user jobs/models.
        for owner, name, result in (
            (common_client, 'Client', self.ai_client),
            (runtime_lifecycle, 'prepare_shutdown', None),
            (backtest_jobs, 'shutdown', {'ok': True, 'warnings': []}),
            (RUNTIME, 'shutdown', None),
            (desktop_window, '_show_close_error', None),
            (desktop_window, '_show_close_warning', None),
            (desktop_window, '_confirm_force_close', False),
        ):
            replacement = patch.object(owner, name, return_value=result)
            replacement.start()
            self.addCleanup(replacement.stop)
        for name, value in (('_closing', False), ('_SESSION_CONTEXTS', {}), ('_SHUTDOWN_PENDING', {})):
            replacement = patch.object(backtest_jobs, name, value, create=True)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_close_is_delayed_until_engines_stop_and_repeated_click_does_not_duplicate(self):
        entered, release = threading.Event(), threading.Event()
        window = Mock()
        closer = desktop_window._EngineCloser(window)

        def shutdown():
            entered.set()
            self.assertTrue(release.wait(5))

        with patch.object(unified_live, 'shutdown', side_effect=shutdown) as stop:
            try:
                self.assertFalse(closer.closing())
                self.assertTrue(entered.wait(5))
                self.assertFalse(closer.closing())
                window.destroy.assert_not_called()
            finally:
                release.set()
                closer.thread.join(5)
            self.assertFalse(closer.thread.is_alive())
            self.assertTrue(closer.closing())
            stop.assert_called_once()
            self.ai_client.shutdown.assert_called_once_with(timeout=45)
            self.ai_client.close.assert_called_once_with()
            window.destroy.assert_called_once()

    def test_close_failure_keeps_window_and_next_close_can_retry(self):
        window = Mock()
        closer = desktop_window._EngineCloser(window)
        with patch.object(unified_live, 'shutdown', side_effect=[RuntimeError('종료 실패'), None]) as stop, \
             patch.object(desktop_window, '_show_close_error') as report:
            self.assertFalse(closer.closing())
            closer.thread.join(5)
            self.assertFalse(closer.ready)
            window.destroy.assert_not_called()
            report.assert_called_once_with('종료 실패')
            self.assertFalse(closer.closing())
            closer.thread.join(5)
            self.assertTrue(closer.ready)
            self.assertEqual(stop.call_count, 2)
            window.destroy.assert_called_once()

    def test_gui_loop_exit_without_close_event_also_stops_engines(self):
        with patch.object(unified_live, 'shutdown') as stop:
            closer = desktop_window._EngineCloser(Mock())
            closer.finish()
            closer.finish()
            stop.assert_called_once()


class ShutdownRaceTests(unittest.TestCase):
    def test_successful_shutdown_blocks_new_start(self):
        with patch.object(unified_live, '_closing', False), \
             patch.object(live_processes, 'stop_all_engines', return_value={'ok': True}), \
             patch.object(unified_live, 'control') as control:
            unified_live.shutdown()
            with self.assertRaisesRegex(ValueError, '종료 중'):
                unified_live.start()
            control.assert_not_called()

    def test_failed_shutdown_allows_retry_and_start(self):
        owner = {'start_live': Mock(return_value=(True, 'started'))}
        with patch.object(unified_live, '_closing', False), \
             patch.object(live_processes, 'stop_all_engines', side_effect=RuntimeError('failed')), \
             patch.object(unified_live, 'control', return_value=owner):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                unified_live.shutdown()
            self.assertTrue(unified_live.start()['ok'])
            owner['start_live'].assert_called_once()

    def test_inflight_start_finishes_before_shutdown_checks_processes(self):
        entered, release = threading.Event(), threading.Event()
        order = []

        def start(*args, **kwargs):
            entered.set()
            release.wait(5)
            order.append('started')
            return True, 'started'

        owner = {'start_live': start}
        with patch.object(unified_live, '_closing', False), \
             patch.object(live_processes, 'stop_all_engines', side_effect=lambda: order.append('stopped')), \
             patch.object(unified_live, 'control', return_value=owner):
            starter = threading.Thread(target=unified_live.start)
            stopper = threading.Thread(target=unified_live.shutdown)
            starter.start()
            self.assertTrue(entered.wait(5))
            stopper.start()
            release.set()
            starter.join(5)
            stopper.join(5)
            self.assertFalse(starter.is_alive() or stopper.is_alive())
            self.assertEqual(order, ['started', 'stopped'])


@unittest.skipUnless(os.name == 'nt' and os.environ.get('MOSES_PROCESS_SMOKE') == '1',
                     'Explicitly enabled Windows process smoke test')
class WindowsProcessSmokeTests(unittest.TestCase):
    def test_window_close_terminates_both_revisions_and_child_but_keeps_unrelated_python(self):
        evidence = PART3.parent / '검증결과' / '웹창종료'
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='이동 시험 ', dir=evidence) as folder:
            root = Path(folder)
            engines, children = [], []
            unrelated = subprocess.Popen([sys.executable, '-B', '-c',
                'import time; time.sleep(90)'], creationflags=live_processes.CREATE_NO_WINDOW)
            try:
                for revision in ('수정본51', '수정본57'):
                    script = root / revision / 'Part1' / 'program' / 'event_host.py'
                    script.parent.mkdir(parents=True)
                    script.write_text('import subprocess,sys,time\n'
                        'child=subprocess.Popen([sys.executable,"-B","-c",'
                        '"import time; time.sleep(90)"])\n'
                        'print(child.pid,flush=True)\n'
                        'time.sleep(90)\n', encoding='utf-8')
                    process = subprocess.Popen([sys.executable, '-B', '-X',
                        'pycache_prefix=' + str(root / 'cache'), str(script)],
                        stdout=subprocess.PIPE, text=True,
                        creationflags=live_processes.CREATE_NO_WINDOW)
                    engines.append(process)
                    children.append(int(process.stdout.readline().strip()))
                owned = {process.pid for process in engines}
                core = live_processes.process_api()
                real_find = live_processes.find_engines
                real_instances = core.find_engine_instances
                found = real_find()
                self.assertTrue(owned <= found.keys())
                self.assertNotIn(unrelated.pid, found)
                with patch.object(core, '_engine_script', return_value=PureWindowsPath(str(root / 'Part1/program/event_host.py'))):
                    child_created = {pid: created for pid, created in real_find().items()
                                     if pid in children}
                self.assertEqual(set(child_created), set(children))
                for pid, created in child_created.items():
                    with live_processes._hold_process(pid, created) as active:
                        self.assertTrue(active)
                # Stop only the processes this test created, even if real engines exist.
                def find_owned():
                    return {pid: created for pid, created in real_find().items() if pid in owned}
                def owned_instances():
                    return [row for row in real_instances() if row['pid'] in owned]
                real_stop = core.stop_engine_instances
                window = Mock()
                closer = desktop_window._EngineCloser(window)
                with patch.object(unified_live, '_closing', False), \
                     patch.object(runtime_lifecycle, 'prepare_shutdown'), \
                     patch.object(backtest_jobs, 'shutdown', return_value={'ok': True, 'warnings': []}), \
                     patch.object(backtest_jobs, '_closing', False), \
                     patch.object(backtest_jobs, '_SESSION_CONTEXTS', {}), \
                     patch.object(backtest_jobs, '_SHUTDOWN_PENDING', {}), \
                     patch.object(catalog, 'ROOT', root / 'Part3'), \
                     patch.object(common_client, 'Client', return_value=Mock(spec=['shutdown', 'close'])), \
                     patch.object(RUNTIME, 'shutdown'), \
                     patch.object(desktop_window, '_show_close_error') as report, \
                     patch.object(desktop_window, '_show_close_warning'), \
                     patch.object(core, 'find_engine_instances', side_effect=owned_instances), \
                     patch.object(core, 'stop_engine_instances', side_effect=lambda rows, **kwargs: real_stop(rows, timeout=0.1)):
                    self.assertFalse(closer.closing())
                    closer.thread.join(30)
                    self.assertFalse(closer.thread.is_alive())
                    self.assertTrue(closer.ready)
                    report.assert_not_called()
                    window.destroy.assert_called_once()
                    for process in engines:
                        process.wait(timeout=5)
                    self.assertEqual(find_owned(), {})
                self.assertIsNone(unrelated.poll())
                # A child no longer has a usable process identity after taskkill /T.
                for pid, created in child_created.items():
                    with live_processes._hold_process(pid, created) as active:
                        self.assertFalse(active)
            finally:
                for process in engines:
                    if process.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                            capture_output=True, creationflags=live_processes.CREATE_NO_WINDOW)
                    process.wait(timeout=5)
                    if process.stdout:
                        process.stdout.close()
                unrelated.terminate()
                unrelated.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
