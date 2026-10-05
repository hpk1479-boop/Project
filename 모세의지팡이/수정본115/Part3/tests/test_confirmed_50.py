"""Five structural gates for approved meanings, without calling any AI model."""
import ast
import copy
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent/'Part1/program'))
from confirmed_answers import cases
from lab import catalog
from lab.ai.intent import validate_intent, recipe_from_intent
from lab.compiler import compile_recipe

STAGES = ('schema','intent_to_recipe','validator','compiler','syntax')

def evaluate(case):
    result = {'id':case['id'],'source':case['source'],'stages':{},'meaning':case['intent']['interpretation']}
    stage = STAGES[0]
    try:
        original = copy.deepcopy(case['intent'])
        normalized = validate_intent(original)
        assert original == case['intent'], 'validator must not mutate the input'
        result['stages'][stage] = 'PASS'
        stage = STAGES[1]
        recipe = recipe_from_intent(normalized)
        assert recipe['strategy_intent'] == normalized['interpretation'], 'intent meaning was lost'
        result['stages'][stage] = 'PASS'
        stage = STAGES[2]
        catalog.validate(recipe)
        result['stages'][stage] = 'PASS'
        stage = STAGES[3]
        name = 'Test_SPECIAL777.py'
        source = compile_recipe(recipe,name)
        assert 'def register(manager)' in source
        result['stages'][stage] = 'PASS'
        stage = STAGES[4]
        ast.parse(source,filename=name)
        compile(source,name,'exec')
        module = types.ModuleType(name[:-3])
        sys.modules[module.__name__] = module
        exec(compile(source,name,'exec'),module.__dict__)
        assert callable(module.register)
        assert module.PART3_RECIPE == recipe
        result['stages'][stage] = 'PASS'
    except Exception as error:
        result['stages'][stage] = 'FAIL'
        result['error'] = f'{type(error).__name__}: {error}'
    return result

class ConfirmedMeanings(unittest.TestCase):
    def test_all_50_through_all_five_stages(self):
        all_cases = list(cases())
        self.assertEqual(len(all_cases),50)
        for case in all_cases:
            with self.subTest(answer=case['id']):
                result = evaluate(case)
                self.assertEqual(result['stages'],dict.fromkeys(STAGES,'PASS'),result)

if __name__ == '__main__': unittest.main()
