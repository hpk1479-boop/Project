"""Current common language: data flows, not historical SPECIAL implementation."""
from __future__ import annotations
import copy
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Part1/program'))
sys.path.insert(0, str(ROOT / 'Part3'))
from strategy_recipe.contract import execution_plan, validate_meaning
from strategy_recipe.registry import entries
from lab.ai.schema import recipe_from_intent, output_schema
from lab.compiler import compile_recipe


def meaning(steps, **values):
    result = {'symbols': ['XAUUSD+'], 'direction': 'LONG', 'steps': steps,
        'order_mode': 'SEQUENTIAL', 'final': {'kind': 'NOTIFY'}, 'persistent': True}
    result.update(values)
    return result


def step(kind, tf='5m', **values):
    return dict(kind=kind, tfs=[tf], **values)


class CommonRecipeContract(unittest.TestCase):
    def test_every_registered_preset_uses_same_lowering(self):
        for name, row in entries().items():
            with self.subTest(preset=name):
                value = dict(row['recipe']['strategy_intent'], symbols=['XAUUSD+'])
                plan = execution_plan(value)
                self.assertEqual(plan['mode'], 'CANONICAL')
                self.assertNotIn('inherit_base_rules', plan['meaning'])
                recipe = dict(row['recipe'], symbols=['XAUUSD+'], strategy_intent=value)
                source = compile_recipe(recipe, 'Test_SPECIAL901.py')
                compile(source, 'Test_SPECIAL901.py', 'exec')
                self.assertIn('from strategy_recipe.port import IntentPort', source)
                self.assertNotIn('from lab', source)
                self.assertNotIn('from SPECIAL', source)

    def test_sequential_and_unordered_without_timeout(self):
        steps = [step('MA_CROSS', ma_left='WMA17', ma_right='SMA20'), step('FVG_NEW')]
        for order in ('SEQUENTIAL', 'UNORDERED'):
            plan = execution_plan(meaning(steps, order_mode=order))
            self.assertIsNone(plan['meaning'].get('within_sec'))
            self.assertTrue(plan['meaning']['steps'][0]['_event_mode'])

    def test_registered_metrics_percentile_barclose_and_dayopen(self):
        steps = [step('TREND_METRIC', metric='rsi14', metric_operator='LTE', metric_value=30),
            step('BAR_CLOSE', '30m'), step('PERCENTILE_OUT', families=['RSI', 'DI'], side='LOWER'),
            step('PERCENTILE_OUT_IN', families=['RSI', 'DI'], side='LOWER'),
            step('PRICE_LEVEL', level='DAY_OPEN', relation='TOUCH')]
        self.assertEqual(len(execution_plan(meaning(steps))['meaning']['steps']), 5)

    def test_captured_fvg_same_identity_and_scope_remain_data(self):
        value = meaning([step('FVG_NEW', capture='zone'), step('FVG_TOUCH', ref='zone')],
            final={'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL',
                'trigger_mode': 'OZ', 'scope_ref': 'zone'}, lifecycle={'invalidate_refs': True})
        plan = execution_plan(value)['meaning']
        self.assertEqual(plan['steps'][1]['ref'], 'zone')
        self.assertEqual(plan['final']['scope_ref'], 'zone')
        bad = copy.deepcopy(value); bad['steps'][1]['ref'] = 'other'
        with self.assertRaises(ValueError): execution_plan(bad)

    def test_nested_oz_scopes_preserve_parent_and_child(self):
        profile = {'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}
        value = meaning([step('OZ_ALERT', '1h', capture='parent', **profile),
            step('OZ_ALERT', '15m', capture='child', scope_ref='parent', **profile)],
            final={'kind': 'OZ', 'tfs': ['1m', '2m', '3m'], 'scope_ref': 'child',
                'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}, lifecycle={'invalidate_refs': True})
        self.assertEqual(execution_plan(value)['meaning']['final']['scope_ref'], 'child')

    def test_snapshot_bar_expiry_and_excursion_contract(self):
        lifecycle = {'expires': {'bars': 2, 'tf': 'FINAL'},
            'snapshots': {'anchor': {'tf': '30m', 'field': 'close'},
                'risk': {'tf': '1m', 'field': 'ATR14', 'bar_state': 'CLOSED'}},
            'excursion': {'anchor': 'anchor', 'snapshot': 'risk', 'multiplier': 1.25},
            'replace': {'scope': 'SYMBOL_DIRECTION'}, 'first_success': True,
            'restart_on': [step('BAR_CLOSE', '30m')]}
        value = meaning([step('BAR_CLOSE', '30m')], lifecycle=lifecycle,
            final={'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'})
        self.assertEqual(execution_plan(value)['meaning']['lifecycle']['expires']['bars'], 2)
        bad = copy.deepcopy(value); bad['lifecycle']['expires']['seconds'] = 300
        with self.assertRaises(ValueError): execution_plan(bad)
        bad = copy.deepcopy(value); bad['final'] = {'kind': 'NOTIFY'}
        with self.assertRaisesRegex(ValueError, 'FINAL'): execution_plan(bad)

    def test_preset_identity_is_not_a_strategy_branch(self):
        template = meaning([step('MA_PRICE_TOUCH', ma_family='HMA', slow_period=87)])
        context = {'preset_meaning': lambda name, symbols=None: dict(template, symbols=symbols or template['symbols'])}
        for name in ('custom_alpha', 'CUSTOM998'):
            self.assertEqual(validate_meaning({'preset': name, 'symbols': ['XAUUSD+']}, context)['steps'], template['steps'])

    def test_branch_action_kind_replaces_previous_action_fields(self):
        value = meaning([], final={'kind': 'OZ', 'tfs': ['1m'],
            'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'},
            branches=[{'steps': [step('BAR_CLOSE', '30m')], 'final': {'kind': 'NOTIFY'}}])
        lowered = execution_plan(value)['meaning']['branches'][0]
        self.assertEqual(lowered['final'], {'kind': 'NOTIFY'})

    def test_price_level_both_is_rejected_while_ma_price_both_remains_valid(self):
        for kind in ('PRICE_LEVEL', 'LIQUIDITY_LEVEL'):
            level = 100 if kind == 'PRICE_LEVEL' else 'PDL'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                execution_plan(meaning([step(kind, level=level, relation='BOTH')]))
        value = meaning([step('MA_PRICE_CROSS', ma_family='SMA', slow_period=20, relation='BOTH')])
        self.assertEqual(execution_plan(value)['meaning']['steps'][0]['relation'], 'BOTH')

    def test_canonical_recipe_uses_shared_validator(self):
        value = meaning([step('MA_PRICE_CROSS', ma_family='EMA', slow_period=73, relation='BOTH')])
        recipe = recipe_from_intent({'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': value})
        self.assertEqual(recipe['schema_version'], 2)
        for field in ('inherit_base_rules', 'special_parameters', 'base_special'):
            bad = copy.deepcopy(value); bad[field] = True
            with self.assertRaises(ValueError): execution_plan(bad)

    def test_json_schema_refs_work_inside_research_envelope(self):
        try: import jsonschema
        except ImportError: self.skipTest('jsonschema library unavailable')
        from lab.ai.research_interpreter import response_schema
        value = {'supported': True, 'intent': 'CREATE_STRATEGY',
            'interpretation': meaning([step('BAR_CLOSE', '30m')]),
            'needs_clarification': False, 'clarification_question': None, 'message_ko': ''}
        jsonschema.validate(value, output_schema())
        jsonschema.validate(value, response_schema('strategy'))
        jsonschema.validate(value, response_schema('chat'))


if __name__ == '__main__': unittest.main()
