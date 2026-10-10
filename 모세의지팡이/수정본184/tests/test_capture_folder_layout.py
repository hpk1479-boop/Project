"""New recording layout and portable migration of existing capture paths."""
import hashlib
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))

from event_backtest.capture_layout import capture_parent, organize_existing, folder_symbol
from event_backtest.warehouse import Warehouse
from event_backtest.settings import scenario


class CaptureFolderLayoutTests(unittest.TestCase):
    def test_new_capture_parent(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            self.assertEqual(capture_parent(root, 'XAUUSD+', 'BAR', '2025-09-01'),
                             root / 'captures/XAUUSD+/BAR/2025/09')
            self.assertEqual(capture_parent(root, 'BTCUSD', 'TIMER', '2026-01-31'),
                             root / 'captures/BTCUSD/TIMER/2026/01')
            for symbol in ('../outside', 'XAU/USD', '.'):
                parent = capture_parent(root, symbol, 'BAR', '2025-09-01')
                self.assertTrue(parent.resolve().is_relative_to(root.resolve()))
                self.assertEqual(folder_symbol(parent.relative_to(root).parts[1]), symbol)

    def test_migrate_and_move_entire_warehouse(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / 'warehouse'
            old = root / 'captures' / 'piece_a'
            old.mkdir(parents=True)
            (old / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
            (old / 'capture.delta2').write_bytes(b'unchanged recording bytes')
            files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in old.iterdir()}
            row = {'capture_id': 'piece_a', 'symbol': 'XAUUSD+', 'start': '2025-09-01',
                   'end': '2025-10-01', 'unit': 'MONTH', 'mode': 'BAR',
                   'ea_build_hash': 'a' * 64, 'schema_id': 933044592,
                   'timer_ms': 1000, 'storage': 'MSD2', 'reconstruction_verified': True,
                   'tick_evidence': {'actual': 'REAL_TICKS'}, 'path': 'captures/piece_a',
                   'files': files, 'stored_bytes': sum(p.stat().st_size for p in old.iterdir()),
                   'recorded_at': '2025-10-02T00:00:00+00:00'}
            catalog = Warehouse(root)
            catalog.register(row)
            moved = organize_existing(catalog)
            self.assertEqual(moved, [{'capture_id': 'piece_a', 'before': 'captures/piece_a',
                                      'after': 'captures/XAUUSD+/BAR/2025/09/piece_a'}])
            self.assertEqual(organize_existing(catalog), [])
            updated = catalog.find_capture('piece_a')
            self.assertEqual(updated['path'], moved[0]['after'])
            self.assertEqual((root / updated['path'] / 'capture.delta2').read_bytes(),
                             b'unchanged recording bytes')
            catalog.close()
            relocated = Path(base) / 'relocated'
            shutil.copytree(root, relocated)
            catalog = Warehouse(relocated)
            self.assertEqual(catalog.find_capture('piece_a'), updated)
            catalog.close()

    def test_new_recording_uses_organized_parent(self):
        from event_backtest import build_plan, recording

        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / 'warehouse'
            common = Path(base) / 'common'
            source = common / 'MosesDataBuild' / 'session'
            source.mkdir(parents=True)
            (source / 'pipe_000.bin').write_bytes(b'probe')
            request = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-10-01',
                               mode='BAR', strategies=['SPECIAL1'], overlap_trading_days=0)
            parents = []

            def fake_convert(source_path, parent, key, **kwargs):
                parents.append(Path(parent))
                dest = Path(parent) / (key + '_generation')
                dest.mkdir(parents=True)
                (dest / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
                return dest, {'storage': 'MSD2', 'reconstruction_verified': True,
                              'bundles': 1, 'bundle_sha256': '0' * 64}

            @contextmanager
            def installed(*args, **kwargs):
                yield 'a' * 64

            with patch.object(build_plan, 'current_build', return_value='a' * 64), \
                 patch.object(build_plan, 'schema_id', return_value=933044592), \
                 patch.object(recording, 'source_hash', return_value='source'), \
                 patch.object(recording, 'deployment_targets', return_value=[]), \
                 patch.object(recording, 'installed_build', installed), \
                 patch.object(recording, 'journal_positions', return_value={}), \
                 patch.object(recording, 'tick_evidence', return_value={'actual': 'REAL_TICKS',
                              'journal_lines': ['XAUUSD+ : real ticks used']}), \
                 patch('event_backtest.storage.convert', fake_convert), \
                 patch('event_backtest.calendar.capture_calendar', return_value={}), \
                 patch.object(recording.native, 'common_files_root', return_value=common), \
                 patch.object(recording.native, 'run_native_tester',
                              return_value={'export': str(source), 'session': 'session',
                                            'records': 1, 'elapsed_seconds': 1}), \
                 patch('event_backtest.terminal_lifecycle.launch_once_retry',
                       side_effect=lambda fn, *args, **kwargs: fn()):
                catalog = Warehouse(root)
                plan = build_plan.make_plan(request, catalog)
                catalog.close()
                result = recording.prepare(request, root, profile={}, plan=plan,
                                           approved_token=plan['approval_token'])
            self.assertEqual(parents, [root / 'captures/XAUUSD+/BAR/2025/09'])
            self.assertTrue(result[0]['path'].startswith('captures/XAUUSD+/BAR/2025/09/'))


if __name__ == '__main__':
    unittest.main()
