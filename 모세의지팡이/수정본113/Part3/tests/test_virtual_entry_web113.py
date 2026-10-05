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


class VirtualEntryWebTests(unittest.TestCase):
    def request(self, result_mode='VIRTUAL_ENTRY'):
        return {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08',
                'mode': 'BAR', 'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'],
                'special_settings': {'SPECIAL2': {'enabled': True,
                    'trigger': '무지성 브레이커 올존', 'time_filters': None}},
                'result_mode': result_mode,
                'virtual_entry': {'mode': 'CONFIRM', 'tf': '5m',
                    'conditions': [{'kind': 'MA_POSITION', 'family': 'EMA', 'period': 200, 'tf': '1h'},
                                   {'kind': 'ENGULFING'}],
                    'atr': {'tf': '5m', 'period': 14},
                    'filters': [{'kind': 'CANDLE_ATR', 'measure': 'BODY', 'min': .3, 'max': 2},
                                {'kind': 'MA_DISTANCE_ATR', 'family': 'EMA', 'period': 200,
                                 'tf': '1h', 'min': None, 'max': 1}],
                    'stop': {'kind': 'ATR', 'tf': '5m', 'multiplier': 1.5}}}

    def test_policy_reaches_scenario_and_only_policy_is_saved(self):
        request = self.request()
        before = ui_model.load()
        with patch.object(ui_model, 'load', return_value=deepcopy(before)), \
             patch.object(ui_model, 'save_virtual_entry') as save, \
             patch.object(ui_model, 'save', side_effect=AssertionError('whole UI overwrite')):
            result = unified_backtest._scenario(request)
        expected = normalize_virtual_entry(request['virtual_entry'])
        self.assertEqual(result['virtual_entry'], expected)
        save.assert_called_once_with(expected)
        self.assertEqual(ui_model.load(), before)

    def test_invalid_policy_is_visible_as_validation_error_and_not_saved(self):
        request = self.request()
        request['virtual_entry']['filters'][0].update(min=2, max=2)
        with patch.object(ui_model, 'save_virtual_entry') as save:
            with self.assertRaisesRegex(ValueError, '최대 배수'):
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

    def test_missing_policy_uses_saved_policy(self):
        request = self.request()
        saved = deepcopy(ui_model.load())
        saved['virtual_entry'] = normalize_virtual_entry({'mode': 'IMMEDIATE',
            'stop': {'kind': 'RECENT_EXTREME', 'bars': 9, 'tf': '15m'}})
        request.pop('virtual_entry')
        with patch.object(ui_model, 'load', return_value=saved), \
             patch.object(ui_model, 'save_virtual_entry') as save:
            result = unified_backtest._scenario(request)
        self.assertEqual(result['virtual_entry'], saved['virtual_entry'])
        save.assert_called_once_with(saved['virtual_entry'])


if __name__ == '__main__':
    unittest.main()
