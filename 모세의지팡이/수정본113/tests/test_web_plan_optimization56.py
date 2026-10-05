"""Forced rerecording skips old capture validation; normal reuse stays verified."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))
from event_backtest import build_plan


class Catalog:
    root = Path('.')

    def __init__(self, items=(), invalid=(), fail_on_check=False):
        self.items = list(items)
        self.invalid = set(invalid)
        self.fail_on_check = fail_on_check
        self.checked = []

    def available(self, symbol, mode):
        return self.items

    def find_capture(self, key):
        self.checked.append(key)
        if self.fail_on_check:
            raise AssertionError('forced rerecording must not read old capture files')
        return next((item for item in self.items if item['capture_id'] == key), None) if key not in self.invalid else None


class PlanOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {'symbol': 'XAUUSD+', 'mode': 'BAR', 'timer_ms': 1000,
                         'start': '2026-09-01', 'end': '2026-10-01', 'overlap_trading_days': 0}
        self.item = {'start': '2026-09-01', 'end': '2026-10-01', 'unit': 'MONTH',
                     'capture_id': 'old-capture', 'ea_build_hash': 'ea', 'schema_id': 50,
                     'timer_ms': 1000, 'storage': 'MSD2', 'reconstruction_verified': True}
        self.patches = [patch.object(build_plan, 'current_build', return_value='ea'),
                        patch.object(build_plan, 'schema_id', return_value=50),
                        patch.object(build_plan, 'compatible_hashes', return_value={'ea'})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def plan(self, catalog, **kwargs):
        return build_plan.make_plan(self.scenario, catalog, today=dt.date(2026, 10, 1), **kwargs)

    def test_rebuild_skips_old_files_and_keeps_record_plan_and_token(self):
        original = copy.deepcopy(self.item)
        existing = Catalog([self.item], fail_on_check=True)
        actual = self.plan(existing, rebuild=True)
        empty = self.plan(Catalog(), rebuild=True)
        self.assertEqual(actual, empty)
        self.assertEqual(existing.checked, [])
        self.assertEqual(actual['record'], [{'start': '2026-09-01', 'end': '2026-10-01', 'unit': 'MONTH'}])
        self.assertEqual(actual['reuse'], [])
        self.assertEqual(actual['convert'], [])
        self.assertEqual(self.item, original)
        with self.assertRaises(build_plan.ConfirmationRequired):
            build_plan.require_approval(actual, None)
        build_plan.require_approval(actual, actual['approval_token'])

    def test_normal_reuse_still_checks_and_corruption_requires_recording(self):
        valid = Catalog([self.item])
        reused = self.plan(valid)
        self.assertEqual(valid.checked, ['old-capture'])
        self.assertEqual(reused['reuse'], [self.item])
        invalid = Catalog([self.item], invalid={'old-capture'})
        missing = self.plan(invalid)
        self.assertEqual(invalid.checked, ['old-capture'])
        self.assertEqual(missing['reuse'], [])
        self.assertEqual(len(missing['record']), 1)

    def test_existing_unverified_storage_is_still_converted(self):
        item = {**self.item, 'storage': 'MSP3', 'reconstruction_verified': False}
        catalog = Catalog([item])
        plan = self.plan(catalog)
        self.assertEqual(catalog.checked, ['old-capture'])
        self.assertEqual(plan['convert'], [item])
        self.assertEqual(plan['record'], [])

    def test_build_only_overlap_zero_and_calendar_boundaries(self):
        # Web build-only uses zero overlap; preserve monthly/daily boundaries.
        self.scenario.update(start='2026-09-30', end='2026-10-03', overlap_trading_days=0)
        plan = build_plan.make_plan(self.scenario, Catalog([self.item], fail_on_check=True),
                                    rebuild=True, today=dt.date(2026, 10, 3))
        self.assertEqual(plan['record'], [
            {'start': '2026-09-01', 'end': '2026-10-01', 'unit': 'MONTH'},
            {'start': '2026-10-01', 'end': '2026-10-02', 'unit': 'DAY'},
            {'start': '2026-10-02', 'end': '2026-10-03', 'unit': 'DAY'}])

    def test_changed_plan_still_requires_new_approval(self):
        first = self.plan(Catalog(), rebuild=True)
        self.scenario['start'] = '2026-08-01'
        changed = self.plan(Catalog(), rebuild=True)
        self.assertNotEqual(first['approval_token'], changed['approval_token'])
        with self.assertRaises(build_plan.ConfirmationRequired):
            build_plan.require_approval(changed, first['approval_token'])

    def test_rebuild_uses_calendar_hints_without_reading_old_files(self):
        self.scenario.update(start='2026-12-28', end='2026-12-29', overlap_trading_days=3)
        rows = [{**self.item, 'capture_id': str(day), 'unit': 'DAY',
                 'start': f'2026-12-{day}', 'end': f'2026-12-{day + 1}',
                 'observed_days': [f'2026-12-{day}'] if day in (23, 24, 28) else []}
                for day in range(23, 29)]
        catalog = Catalog(rows, fail_on_check=True)
        first = build_plan.make_plan(self.scenario, catalog, rebuild=True, today=dt.date(2026, 12, 30))
        self.assertEqual(first['record'][0]['start'], '2026-12-22')
        self.assertEqual(first['reuse'], [])
        self.assertEqual(first['convert'], [])
        catalog.items.append({**rows[0], 'capture_id': '22', 'start': '2026-12-22',
                              'end': '2026-12-23', 'observed_days': []})
        second = build_plan.make_plan(self.scenario, catalog, rebuild=True, today=dt.date(2026, 12, 30))
        self.assertEqual(second['record'][0]['start'], '2026-12-21')
        with self.assertRaises(build_plan.ConfirmationRequired):
            build_plan.require_approval(second, first['approval_token'])
        self.assertEqual(catalog.checked, [])

    def test_web_build_only_scenario_keeps_engine_contract(self):
        sys.path.insert(0, str(ROOT / 'Part3'))
        from lab import unified_backtest
        request = {'symbol': 'XAUUSD+', 'start': '2026-09-01', 'end': '2026-10-01',
                   'mode': 'BAR', 'target_mode': 'SPECIAL', 'specials': ['SPECIAL1'],
                   'build_only': True, 'rebuild': True}
        scenario = unified_backtest._scenario(request)
        self.assertTrue(scenario['build_only'])
        self.assertEqual(scenario['strategies'], [])
        self.assertEqual(scenario['commands'], [])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PlanOptimizationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors)}
    folder = ROOT / '검증결과' / '웹UI최적화'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'plan_tests.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())
