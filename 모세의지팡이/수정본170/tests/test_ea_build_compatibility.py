"""Approved EA build equivalence is limited to the explicitly verified pair."""
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))

from event_backtest.build_compat import compatible_hashes
from event_backtest.build_plan import current_build, make_plan
from event_backtest.recording import prepare
from event_backtest.runner import capture_build_provenance, resolve_captures
from event_backtest.settings import scenario

OLD = 'af30fe4bf841bcbce9e99b1670d15452dd976fba090c075a66e09d9945c1ff32'
NEW = 'e434827fdc453013a40942f55bd574bc6d2b35f38bdcd33fd8602bbec4eaab5c'
SYMBOL_MAP = '905946cad255c9d10f2ceedf3568789f6aff8a0493dfa991f9c6fc2a4bedc50f'  # 수정본35, same day bundles
UNKNOWN = 'f' * 64


class Catalog:
    def __init__(self, root, items):
        self.root = root
        self.items = items

    def available(self, symbol, mode):
        return [item for item in self.items if item['symbol'] == symbol and item['mode'] == mode]

    def find_capture(self, capture_id):
        return next((item for item in self.items if item['capture_id'] == capture_id), None)

    def close(self):
        pass


class BuildCompatibilityTests(TestCase):
    def setUp(self):
        self.scenario = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-10-01',
                                 mode='BAR', strategies=['SPECIAL1'], overlap_trading_days=0)
        self.old = {'capture_id': 'old-piece', 'symbol': 'XAUUSD+', 'mode': 'BAR',
                    'start': '2025-09-01', 'end': '2025-10-01', 'unit': 'MONTH',
                    'ea_build_hash': OLD, 'schema_id': 933044592, 'timer_ms': 1000,
                    'history_missing': [], 'storage': 'MSD2', 'reconstruction_verified': True,
                    'path': 'captures/old-piece', 'recorded_at': '2025-10-02T00:00:00+00:00'}
        self.catalog = Catalog(Path('unused-warehouse'), [self.old])

    def test_approved_pair_only(self):
        # The approved group grows when a release registers its copy of the same MT5 source,
        # so check membership of the verified builds rather than the group's current size.
        group = compatible_hashes(NEW)
        self.assertTrue(frozenset((OLD, NEW, SYMBOL_MAP)) <= group)
        self.assertEqual(compatible_hashes(SYMBOL_MAP), group)
        self.assertEqual(compatible_hashes(OLD), group)
        self.assertEqual(compatible_hashes(UNKNOWN), frozenset((UNKNOWN,)))

    def test_existing_period_reuses_without_recorder(self):
        with patch('event_backtest.build_plan.current_build', return_value=NEW), \
             patch('event_backtest.build_plan.schema_id', return_value=933044592), \
             patch('event_backtest.recording.Warehouse', return_value=self.catalog), \
             patch('event_backtest.recording.native.run_native_tester', side_effect=AssertionError('MT5 must not start')):
            plan = make_plan(self.scenario, self.catalog)
            self.assertEqual(plan['record'], [])
            self.assertEqual([c['capture_id'] for c in plan['reuse']], ['old-piece'])
            self.assertEqual(prepare(self.scenario, self.catalog.root, plan=plan), [self.old])
            chosen, missing = resolve_captures(self.catalog, self.scenario, ea_build_hash=NEW)
            self.assertEqual(missing, [])
            self.assertEqual(chosen, [self.old])

    def test_unknown_build_requires_recording(self):
        with patch('event_backtest.build_plan.current_build', return_value=UNKNOWN), \
             patch('event_backtest.build_plan.schema_id', return_value=933044592):
            plan = make_plan(self.scenario, self.catalog)
            self.assertEqual(len(plan['record']), 1)
            self.assertEqual(plan['reuse'], [])
            chosen, missing = resolve_captures(self.catalog, self.scenario, ea_build_hash=UNKNOWN)
            self.assertEqual(chosen, [])
            self.assertEqual(missing, [('2025-09-01', '2025-10-01')])

    def test_exact_build_wins_when_both_recordings_exist(self):
        newer = {**self.old, 'capture_id': 'new-piece', 'ea_build_hash': NEW,
                 'path': 'captures/new-piece', 'recorded_at': '2025-10-01T00:00:00+00:00'}
        self.catalog.items = [self.old, newer]
        with patch('event_backtest.build_plan.current_build', return_value=NEW), \
             patch('event_backtest.build_plan.schema_id', return_value=933044592):
            plan = make_plan(self.scenario, self.catalog)
            self.assertEqual(plan['reuse'][0]['capture_id'], 'new-piece')
            chosen, missing = resolve_captures(self.catalog, self.scenario, ea_build_hash=NEW)
            self.assertEqual(missing, [])
            self.assertEqual(chosen[0]['capture_id'], 'new-piece')

    def test_result_keeps_actual_hash_and_compatibility_flag(self):
        info = capture_build_provenance([self.old], NEW)
        self.assertEqual(info['ea_builds'], [OLD])
        self.assertEqual(info['capture_ea_build_hashes'], {'old-piece': OLD})
        self.assertTrue(info['ea_build_compatibility_applied'])
        self.assertFalse(info['mixed_unapproved_ea_builds'])
        approved_mix = capture_build_provenance([self.old, {**self.old, 'capture_id': 'new', 'ea_build_hash': NEW}], NEW)
        self.assertFalse(approved_mix['mixed_unapproved_ea_builds'])
        self.assertTrue(approved_mix['ea_build_compatibility_applied'])
        mixed = capture_build_provenance([self.old, {**self.old, 'capture_id': 'unknown', 'ea_build_hash': UNKNOWN}], NEW)
        self.assertTrue(mixed['mixed_unapproved_ea_builds'])
        self.assertFalse(capture_build_provenance([{**self.old, 'ea_build_hash': NEW}], NEW)['ea_build_compatibility_applied'])


if __name__ == '__main__':
    main()
