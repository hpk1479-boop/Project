"""Current AI-first Part3 contract, independent of the old manual builder UI."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab import storage
from lab.ai.agent import Agent
from lab.ai.intent import recipe_from_intent, validate_intent
from lab.ai.provider import ScriptedProvider


def intent(step=None):
    return {
        'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {
            'direction': 'LONG', 'symbols': ['XAUUSD+'],
            'steps': [step or {'kind': 'MA_STATE', 'tfs': ['1m'], 'direction': 'LONG',
                               'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200}],
            'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL',
            'within_sec': None,
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL',
                      'trigger_mode': 'OZ'},
        },
        'needs_clarification': False, 'clarification_question': None,
    }


class AIIntentV2(unittest.TestCase):
    def test_natural_language_to_trusted_generation(self):
        reply = {'content': json.dumps(intent(), ensure_ascii=False), 'tool_calls': []}
        agent = Agent(ScriptedProvider([reply]))
        response = agent.send('1분 EMA50이 EMA200 위에 있으면 1분 올존 전략으로 만들어줘')
        self.assertTrue(response['can_apply'])
        recipe = agent.apply()
        self.assertEqual(recipe['strategy_intent']['steps'][0]['kind'], 'MA_STATE')
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            preview = storage.preview(recipe)
            self.assertIn('register(manager)', preview['code'])
            self.assertFalse((Path(folder) / 'generated').exists())
            generated = storage.generate(recipe)
            self.assertTrue(Path(generated['path']).is_file())
            self.assertIn('register(manager)', Path(generated['path']).read_text('utf-8'))

    def test_canonical_cross_preserves_four_families_and_arbitrary_periods(self):
        value = intent({'kind': 'MA_CROSS', 'tfs': ['1m'], 'direction': 'LONG',
                        'ma_left': 'WMA17', 'ma_right': 'SMA20'})
        normalized = validate_intent(value)
        self.assertEqual(normalized['interpretation']['steps'][0]['ma_left'], 'WMA17')
        self.assertEqual(normalized['interpretation']['steps'][0]['ma_right'], 'SMA20')
        self.assertEqual(recipe_from_intent(normalized)['strategy_intent']['steps'][0]['kind'], 'MA_CROSS')
        value['interpretation']['steps'][0]['ma_right'] = 'HMA270'
        self.assertEqual(validate_intent(value)['interpretation']['steps'][0]['ma_right'], 'HMA270')

    def test_state_is_not_replaced_by_touch(self):
        value = intent({'kind': 'FVG_STATE', 'tfs': ['5m'], 'side': 'BULL'})
        self.assertEqual(validate_intent(value)['interpretation']['steps'][0]['kind'], 'FVG_STATE')
        self.assertEqual(recipe_from_intent(value)['strategy_intent']['steps'][0]['kind'], 'FVG_STATE')

    def test_liquidity_side_keeps_the_actual_level(self):
        value = intent({'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['4h'],
                        'direction': 'LONG', 'side': 'LOW', 'level': '4H'})
        recipe = recipe_from_intent(value)
        from lab.ai_compiler import execution_plan
        self.assertEqual(execution_plan(recipe['strategy_intent'])['meaning']['steps'][0]['_level_codes'], ['PREV_4H_LOW'])
        value['interpretation']['steps'][0]['side'] = 'HIGH'
        self.assertEqual(execution_plan(recipe_from_intent(value)['strategy_intent'])['meaning']['steps'][0]['_level_codes'],
                         ['PREV_4H_HIGH'])
        value['interpretation']['steps'][0]['level'] = 'PDL'
        with self.assertRaisesRegex(ValueError, '맞지'):
            recipe_from_intent(value)

    def test_unused_model_fields_are_not_silently_dropped(self):
        value = intent()
        value['interpretation']['steps'][0]['level'] = 'PDL'
        with self.assertRaisesRegex(ValueError, '사용하지 않는 필드'):
            validate_intent(value)
        value = intent({'kind': 'MA_CROSS', 'tfs': ['1m'], 'ma_left': 'WMA17',
                        'ma_right': 'SMA20', 'ma_family': 'EMA'})
        with self.assertRaisesRegex(ValueError, '사용하지 않는 필드'):
            validate_intent(value)

    def test_unsupported_and_invented_fields_fail_closed(self):
        for change in ('code', 'bad_tf', 'zero_period', 'unknown_session', 'template'):
            value = intent()
            if change == 'code': value['source_text'] = 'open("x", "w")'
            elif change == 'bad_tf': value['interpretation']['steps'][0]['tfs'] = ['9h']
            elif change == 'zero_period': value['interpretation']['steps'][0]['fast_period'] = 0
            elif change == 'unknown_session': value['interpretation']['steps'] = [
                {'kind': 'SESSION_START', 'tfs': ['1m'], 'session': 'MOON'}]
            elif change == 'template': value['interpretation']['base_special'] = 'SPECIAL5'
            with self.subTest(change=change):
                if change == 'template':
                    validate_intent(value)
                    self.assertEqual(recipe_from_intent(value)['strategy_intent']['base_special'], 'SPECIAL5')
                else:
                    with self.assertRaises(ValueError):
                        validate_intent(value)

    def test_manual_builder_is_not_user_visible(self):
        page = (ROOT / 'web/index.html').read_text('utf-8')
        strategy = page.split('id="view-strategy"', 1)[1].split('id="view-live"', 1)[0]
        for old_id in ('tab-builder', 'strategy-name', 'add-slot', 'global-combine', 'code-editor'):
            self.assertNotIn(old_id, strategy)
        self.assertIn('id="ai-text"', strategy)
        self.assertIn('id="generate-button"', strategy)


if __name__ == '__main__':
    unittest.main()
