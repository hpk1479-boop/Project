"""Missing broker symbols must not be mistaken for transient startup failures."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest.progress_view import ProgressView
from event_backtest.terminal_lifecycle import launch_once_retry
from generic_backtest import native_mt5 as native


class TesterStartTests(unittest.TestCase):
    def setUp(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('External services are forbidden in tests')

        for name in ('socket.socket.connect', 'socket.socket.sendto', 'subprocess.Popen'):
            guard = patch(name, forbidden)
            guard.start()
            self.addCleanup(guard.stop)

    def run_tester(self, root, process_factory, **overrides):
        executable = root / 'terminal64.exe'
        executable.touch()
        profile = {'executable': str(executable), 'account_server': 'ICMarketsSC-Demo'}
        with patch.object(native, 'common_files_root', return_value=root / 'common'), \
             patch.object(native, 'locate_compiled_expert', return_value=root / 'ea.ex5'), \
             patch.object(native, 'tester_expert_name', return_value='THE_STAFF_OF_MOSES'), \
             patch.object(native.subprocess, 'Popen', process_factory):
            return native.run_native_tester(profile, 'XAUUSD+', 0, 86400 * 10**9,
                root / 'work', capture_only=True, shutdown_terminal=True, **overrides)

    def test_symbol_error_no_retry_cleanup_and_ui_warning(self):
        for exit_code in (-1000012358, 3294954938):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                launches = []

                def process(args, **kwargs):
                    launches.append(args)
                    return SimpleNamespace(poll=lambda: exit_code)

                with self.assertRaisesRegex(ValueError, 'NATIVE_TESTER_SYMBOL_NOT_FOUND') as caught:
                    launch_once_retry(lambda: self.run_tester(root, process), {},
                        wait=lambda *args, **kwargs: None)
                self.assertEqual(len(launches), 1)
                message = str(caught.exception)
                self.assertIn('ICMarketsSC-Demo', message)
                self.assertIn('XAUUSD+', message)
                self.assertNotIn('이미 켜진 MT5', message)
                self.assertFalse((root / 'common/MosesDataBuild/native_request.txt').exists())
                self.assertFalse(list((root / 'common').rglob('complete.txt')))
                view = ProgressView()
                view.accept({'event': 'ERROR', 'message': message})
                self.assertEqual(view.phase, '중단 / 오류')
                self.assertIn('XAUUSD+', view.lines[-1])
                self.assertEqual(view.warnings, [message])

    def test_transient_start_failure_still_retries_once(self):
        attempts = []

        def launch():
            attempts.append(1)
            if len(attempts) == 1:
                raise ValueError('NATIVE_TESTER_DID_NOT_START: exit=1')
            return {'records': 1}

        result = launch_once_retry(launch, {}, wait=lambda *args, **kwargs: None)
        self.assertEqual(result, {'records': 1})
        self.assertEqual(len(attempts), 2)

    def test_repeated_transient_failure_remains_bounded(self):
        attempts = []

        def launch():
            attempts.append(1)
            raise ValueError('NATIVE_TESTER_DID_NOT_START: exit=1')

        with self.assertRaises(RuntimeError):
            launch_once_retry(launch, {}, wait=lambda *args, **kwargs: None)
        self.assertEqual(len(attempts), 2)

    def test_busy_terminal_blocks_launch(self):
        def busy(*args, **kwargs):
            raise RuntimeError('busy')

        with self.assertRaisesRegex(RuntimeError, 'busy'):
            launch_once_retry(lambda: self.fail('started while busy'), {}, wait=busy)

    def test_completed_capture_and_tester_config_still_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def process(args, **kwargs):
                request = root / 'common/MosesDataBuild/native_request.txt'
                session = request.read_text('ascii').splitlines()[1]
                output = request.parent / session
                output.mkdir()
                (output / 'started.txt').touch()
                (output / 'complete.txt').touch()
                (output / 'manifest.tsv').write_text(
                    'pipe_feed\tXAUUSD+\t1m\tpipe.bin\t1\n', encoding='ascii')
                return SimpleNamespace(poll=lambda: 0)

            result = self.run_tester(root, process, tester_inputs={'InpRecordingMode': 1})
            self.assertEqual((result['records'], result['feeds'], result['tester_model']), (1, 1, 4))
            self.assertFalse(result['empty'])
            self.assertFalse((root / 'common/MosesDataBuild/native_request.txt').exists())
            ini = next((root / 'work').glob('*.ini')).read_text('ascii')
            for value in ('Symbol=XAUUSD+', 'Model=4', 'ShutdownTerminal=1', 'InpRecordingMode=1'):
                self.assertIn(value, ini)


if __name__ == '__main__':
    unittest.main()
