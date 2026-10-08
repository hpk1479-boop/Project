"""Web requests use the same validated post-alert policy as Part2."""
from copy import deepcopy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import unified_backtest
settings, ui_model, _ = unified_backtest._part2()
from event_backtest.virtual_contract import normalize_virtual_entry
from event_backtest.virtual_defaults import strategy_profile


class VirtualEntryWebTests(unittest.TestCase):
    def request(self, result_mode='VIRTUAL_ENTRY'):
        return {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08',
                'mode': 'BAR', 'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'],
                'special_settings': {'SPECIAL2': {'enabled': True,
                    'trigger': '무지성 브레이커 올존', 'time_filters': None}},
                'result_mode': result_mode,
                # SPECIAL2 completes on several frames, so its base frame stays its own (수정본142),
                # and every frame follows the base frame (수정본146).
                'virtual_entry': {'mode': 'CONFIRM', 'tf': 'SIGNAL',
                    'conditions': [{'kind': 'MA_POSITION', 'family': 'EMA', 'period': 200, 'tf': 'SIGNAL'},
                                   {'kind': 'ENGULFING'}],
                    'atr': {'tf': 'SIGNAL', 'period': 14},
                    'filters': [{'kind': 'CANDLE_ATR', 'measure': 'BODY', 'min': .3, 'max': 2},
                                {'kind': 'MA_DISTANCE_ATR', 'family': 'EMA', 'period': 200,
                                 'tf': 'SIGNAL', 'min': None, 'max': 1}],
                    'stop': {'kind': 'ATR', 'tf': 'SIGNAL', 'multiplier': 1.5}}}

    def test_policy_reaches_scenario_and_only_policy_is_saved(self):
        request = self.request()
        before = ui_model.load()
        with patch.object(ui_model, 'load', return_value=deepcopy(before)), \
             patch.object(ui_model, 'save_virtual_entry') as save, \
             patch.object(ui_model, 'save', side_effect=AssertionError('whole UI overwrite')):
            result = unified_backtest._scenario(request)
        expected = normalize_virtual_entry(request['virtual_entry'])
        self.assertEqual(result['virtual_entry'], expected)
        # 수정본144: the run's strategy-condition edits are saved with it (none here: the recipe's own).
        save.assert_called_once_with(expected, 'SPECIAL2', strategy=None)
        self.assertEqual(ui_model.load(), before)

    def test_invalid_policy_is_visible_as_validation_error_and_not_saved(self):
        request = self.request()
        request['virtual_entry']['filters'][0].update(min=2, max=2)
        with patch.object(ui_model, 'save_virtual_entry') as save:
            with self.assertRaisesRegex(ValueError, '최대 배수'):
                unified_backtest._scenario(request)
        save.assert_not_called()

    def test_a_chosen_frame_is_refused_and_not_saved(self):
        request = self.request()
        request['virtual_entry']['stop']['tf'] = '5m'
        with patch.object(ui_model, 'save_virtual_entry') as save:
            with self.assertRaisesRegex(ValueError, '기준 프레임으로만'):
                unified_backtest._scenario(request)
        save.assert_not_called()

    def test_alert_only_does_not_validate_hidden_virtual_fields(self):
        request = self.request('ALERT_ONLY')
        request['virtual_entry'] = {'mode': 'invalid', 'atr': {'period': 'bad'}}
        with patch.object(ui_model, 'save_virtual_entry') as save:
            result = unified_backtest._scenario(request)
        self.assertEqual(result['result_mode'], 'ALERT_ONLY')
        save.assert_not_called()

    def test_build_only_does_not_require_virtual_policy(self):
        request = self.request()
        request.update(build_only=True, virtual_entry={'mode': 'invalid'})
        with patch.object(ui_model, 'save_virtual_entry') as save:
            result = unified_backtest._scenario(request)
        self.assertTrue(result['build_only'])
        save.assert_not_called()

    def test_missing_policy_uses_the_strategy_defaults_not_another_saved_policy(self):
        request = self.request()
        saved = deepcopy(ui_model.load())
        saved['virtual_entry'] = normalize_virtual_entry({'mode': 'IMMEDIATE',
            'stop': {'kind': 'RECENT_EXTREME', 'bars': 9, 'tf': '15m'}})
        request.pop('virtual_entry')
        with patch.object(ui_model, 'load', return_value=saved), \
             patch.object(ui_model, 'save_virtual_entry') as save:
            result = unified_backtest._scenario(request)
        # SPECIAL2's recipe: conditional entry with closed candle + HMA6, HMA6·17 kept, B0 stop.
        recipe = strategy_profile('SPECIAL2')['default']
        self.assertEqual(result['virtual_entry'], recipe)
        save.assert_called_once_with(recipe, 'SPECIAL2', strategy=None)

    def test_virtual_entry_runs_one_strategy_and_alert_only_runs_many(self):
        request = self.request()
        request['specials'] = ['SPECIAL2', 'SPECIAL7']
        request['special_settings']['SPECIAL7'] = {'enabled': True, 'trigger': None, 'time_filters': None}
        with patch.object(ui_model, 'save_virtual_entry') as save:
            with self.assertRaisesRegex(ValueError, '전략 1개만'):
                unified_backtest._scenario(deepcopy(request))
        save.assert_not_called()
        request['result_mode'] = 'ALERT_ONLY'
        result = unified_backtest._scenario(request)
        self.assertEqual(result['strategies'], ['SPECIAL2', 'SPECIAL7'])
        self.assertIsNone(result['virtual_entry'])


if __name__ == '__main__':
    unittest.main()
