"""Approved original sentences through the real Agent, with no model calls."""
import ast
import copy
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from confirmed_answers import cases, meaning, ma, cross, fvg, oz, branches, step
from lab import catalog
from lab.ai.agent import Agent
from lab.ai.intent import validate_intent
from lab.ai.provider import ScriptedProvider
from lab.compiler import compile_recipe

STAGES = ('agent_intent', 'apply', 'recipe', 'validator', 'compiler', 'syntax')


def original_cases():
    directory = ROOT.parent.parent / 'AI 교육'
    originals = {}
    pattern = re.compile(r'^#{1,3} #(?P<id>\d{3})\s*\n+#{2,3} INPUT\s*\n'
                         r'(?P<input>.*?)(?=\n#{2,3} )', re.M | re.S)
    for source in sorted({case['source'] for case in cases()}):
        text = (directory / source).read_text('utf-8-sig')
        for match in pattern.finditer(text):
            number = match['id']
            if number in originals:
                raise ValueError('Duplicate approved original: ' + number)
            originals[number] = match['input'].strip().strip('`')
    confirmed = list(cases())
    if set(originals) != {case['id'] for case in confirmed}:
        raise ValueError('The approved originals must cover all 50 meanings')
    for case in confirmed:
        yield {**case, 'user_text': originals[case['id']]}


def evaluate_original(case):
    row = {'id': case['id'], 'source': case['source'], 'user_text': case['user_text'], 'stages': {}}
    stage = STAGES[0]
    try:
        reply = {'content': json.dumps(case['intent'], ensure_ascii=False), 'tool_calls': []}
        provider = ScriptedProvider([copy.deepcopy(reply), copy.deepcopy(reply)])
        agent = Agent(provider)
        out = agent.send(case['user_text'])
        row['provider_calls'] = len(provider.seen)
        if not out['can_apply']:
            errors = [m['content'] for m in agent.messages
                      if m['role'] == 'user' and m['content'].startswith('조회 설명으로 종료하지 마세요.')]
            raise ValueError('; '.join(errors) or str(out.get('application_error') or out['result']))
        row['stages'][stage] = 'PASS'
        stage = STAGES[1]
        recipe = agent.apply()
        row['stages'][stage] = 'PASS'
        stage = STAGES[2]
        expected = validate_intent(copy.deepcopy(case['intent']))['interpretation']
        actual = copy.deepcopy(recipe['strategy_intent'])
        assert actual == expected, 'Agent changed the approved canonical meaning'
        row['stages'][stage] = 'PASS'
        stage = STAGES[3]
        catalog.validate(recipe)
        row['stages'][stage] = 'PASS'
        stage = STAGES[4]
        source = compile_recipe(recipe, 'Test_SPECIAL777.py')
        assert 'def register(manager)' in source
        row['stages'][stage] = 'PASS'
        stage = STAGES[5]
        ast.parse(source)
        compile(source, 'Test_SPECIAL777.py', 'exec')
        row['stages'][stage] = 'PASS'
    except Exception as exc:
        row['stages'][stage] = 'FAIL'
        row['error'] = str(exc)
    return row


class ApprovedOriginalGuard(unittest.TestCase):
    def test_original_50_agent_apply_validator_compiler(self):
        with patch('lab.ai.provider.OpenAICompatible.chat', side_effect=AssertionError('Real model forbidden')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            samples = list(original_cases())
            self.assertEqual(len(samples), 50)
            for case in samples:
                with self.subTest(original=case['id']):
                    row = evaluate_original(case)
                    self.assertEqual(row['stages'], dict.fromkeys(STAGES, 'PASS'), row)
                    self.assertEqual(row['provider_calls'], 1)


class IntentBoundaries(unittest.TestCase):
    def value(self, interpretation):
        return validate_intent({'supported': True, 'intent': 'CREATE_STRATEGY',
            'interpretation': interpretation, 'needs_clarification': False})

    def apply(self, text, interpretation):
        value = self.value(interpretation)
        reply = {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}
        provider = ScriptedProvider([reply])
        agent = Agent(provider)
        self.assertTrue(agent.send(text)['can_apply'])
        self.assertEqual(len(provider.seen), 1)
        result = agent.apply()
        self.assertEqual(result['strategy_intent'], value['interpretation'])
        catalog.validate(result)
        ast.parse(compile_recipe(result, 'Test_SPECIAL777.py'))
        return result

    def test_ma_clauses_are_not_parsed_as_profile_modifiers(self):
        for text in ('EMA37이 EMA95 위일때 매수 올존',
                     'HMA73 아래에서 매도 브레이커 올존',
                     'WMA23이 SMA41 위 브레이커 올존',
                     '가격이 90헐 위에 있고 매수 브레이커 올존'):
            with self.subTest(text=text):
                self.apply(text, meaning([ma(fast=37, slow=95)]))

    def test_source_and_final_profiles_remain_distinct(self):
        self.apply('10분 매수 브레이커 올존 뜨면 2분 무지성 올존',
            meaning([step('OZ_ALERT', '10m', validation_mode='NORMAL', trigger_mode='BREAKER')],
                final=oz(['2m'], 'BLIND'), order_mode='SEQUENTIAL'))

    def test_unknown_structured_profile_still_fails(self):
        for mode in ('MAGIC', 'REGIME', 'SUPER', 'NORMAL BLIND'):
            value = meaning(final=oz(['1m']))
            value['final']['trigger_mode'] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.value(value)

    def test_nested_branch_intent_is_preserved(self):
        self.apply('1분 EMA37이 EMA95 위에 있으면 2분 올존',
            branches([{'steps': [ma(fast=37, slow=95)]}], final=oz(['2m'])))

    def test_branch_fvg_tf_is_not_overridden(self):
        self.apply('10분 상승 FVG에서 2분 올존',
            branches([{'steps': [fvg('10m')]}], final=oz(['2m'])))

    def test_after_condition_touch_is_preserved(self):
        self.apply('1분 골크 후 10분 FVG 터치하면 2분 올존',
            meaning([cross()], final=oz(['2m']), order_mode='SEQUENTIAL',
                    after_conditions=[fvg('10m', 'FVG_TOUCH')]))

    def test_final_and_cancel_conditions_are_preserved(self):
        for container in ('final_conditions', 'cancel_conditions'):
            with self.subTest(container=container):
                self.apply('10분 FVG 터치하면 2분 올존',
                    meaning(final=oz(['2m']), **{container: [fvg('10m', 'FVG_TOUCH')]}))

    def test_sessions_and_levels_do_not_require_domain_keyword_gate(self):
        samples = [('유로장 시작하면 알려줘', step('SESSION_START', session='LONDON')),
                   ('미국장 시작하면 알려줘', step('SESSION_START', session='NEWYORK')),
                   ('전일 고가 깨면 알려줘', step('LIQUIDITY_LEVEL', level='PDH', relation='BREAK_UP'))]
        for text, condition in samples:
            with self.subTest(text=text):
                self.apply(text, meaning([condition], final={'kind': 'NOTIFY'}))

    def test_arbitrary_condition_and_branch_counts_have_no_slot_conversion(self):
        for count in (0, 1, 5, 12, 25):
            with self.subTest(steps=count):
                recipe = self.apply('교육용 조건 목록', meaning([ma()] * count))
                self.assertEqual(len(recipe['strategy_intent']['steps']), count)
                self.assertNotIn('slots', recipe)
                self.assertNotIn('slots', recipe['strategy_intent'])
        value = branches([{'steps': [ma()]} for _ in range(12)])
        recipe = self.apply('교육용 독립 분기 목록', value)
        self.assertEqual(len(recipe['strategy_intent']['branches']), 12)


if __name__ == '__main__':
    unittest.main()
