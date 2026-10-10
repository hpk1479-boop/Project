"""The common contract for the lifecycle features: validation, lowering and the AI schema built on it."""
import copy
from pathlib import Path
import re
import sys

import pytest

from recipe_harness114 import SYMBOL
from strategy_recipe import registry
from strategy_recipe.contract import (PRECONDITION_CHECKS, WINDOW_ANCHORS, execution_plan, level_gate_rule,
                                      validate_meaning)
from oz_engine.common import EXTERNAL_ATR_MULT, EXTERNAL_ATR_PERIOD

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part3')]


def touch(**extra):
    return {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], 'side': 'LOW', 'level': 'ALL', 'capture': 'sweep', **extra}


def recipe(**extra):
    final = {'kind': 'OZ', 'tfs': ['SOURCE'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER',
             'level_gate': {'ref': 'sweep'}}
    raw = {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS', 'steps': [touch()], 'final': final}
    for key, value in extra.items():
        if key == 'gate': raw['final']['level_gate'] = value
        elif key == 'final': raw['final'].update(value)
        else: raw[key] = value
    return raw


def rejected(**extra):
    with pytest.raises(ValueError):
        execution_plan(recipe(**extra))


def plain_oz(**extra):
    """A recipe without any gate: a 1m OZ final after the same touch."""
    final = {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}
    return {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS', 'steps': [touch()],
            'final': final, **extra}


def test_a_gate_with_only_a_reference_uses_the_engines_own_rule():
    plan = execution_plan(recipe())['meaning']
    assert plan['steps'][0]['_level_gate'] == {'atr_period': EXTERNAL_ATR_PERIOD, 'atr_mult': EXTERNAL_ATR_MULT}
    assert level_gate_rule({}) == {'atr_period': 14, 'atr_mult': 1.5}


def test_the_declared_rule_reaches_only_the_referenced_touch():
    other = touch(capture='other', tfs=['1m'])
    plan = execution_plan(recipe(steps=[touch(), other], gate={'ref': 'sweep', 'atr_period': 20, 'atr_mult': 2}))['meaning']
    assert plan['steps'][0]['_level_gate'] == {'atr_period': 20, 'atr_mult': 2.}
    assert 'level_gate' not in plan['final'] or plan['final']['level_gate']['ref'] == 'sweep'
    assert '_level_gate' not in plan['steps'][1]


@pytest.mark.parametrize('gate', [
    {}, {'ref': 'missing'}, {'ref': 'sweep', 'atr_period': 0}, {'ref': 'sweep', 'atr_period': 501},
    {'ref': 'sweep', 'atr_period': True}, {'ref': 'sweep', 'atr_period': 1.5}, {'ref': 'sweep', 'atr_mult': 0},
    {'ref': 'sweep', 'atr_mult': -1}, {'ref': 'sweep', 'atr_mult': float('nan')}, {'ref': 'sweep', 'atr_mult': '2'},
    {'ref': 'sweep', 'extra': 1}, {'ref': 'sweep dash'}])
def test_invalid_gates_are_refused(gate):
    rejected(gate=gate)


def test_a_gate_needs_an_oz_final_on_the_touch_timeframe_and_a_real_touch_step():
    rejected(final={'tfs': ['5m']})
    rejected(final={'tfs': ['SOURCE', '1m']})
    with pytest.raises(ValueError):
        execution_plan({**recipe(), 'final': {'kind': 'NOTIFY', 'level_gate': {'ref': 'sweep'}}})
    rejected(steps=[{**touch(), 'negated': True}])
    rejected(steps=[{'kind': 'WONBI_TOUCH', 'tfs': ['5m'], 'side': 'LOWER', 'capture': 'sweep'}])
    rejected(steps=[touch(capture='a')])


def test_a_touch_capture_cannot_stand_in_for_an_area_or_an_oz_scope():
    fvg = {'kind': 'FVG_TOUCH', 'tfs': ['5m'], 'side': 'BULL', 'ref': 'sweep'}
    rejected(order_mode='SEQUENTIAL', steps=[touch(), fvg])
    rejected(order_mode='SEQUENTIAL', steps=[touch()], final={'scope_ref': 'sweep'})
    rejected(order_mode='SEQUENTIAL', steps=[touch()], final_conditions=[{'kind': 'FVG_STATE', 'tfs': ['5m'], 'scope_ref': 'sweep'}])
    # The capture is still accepted wherever it already was: FVG and OZ events.
    execution_plan(plain_oz(steps=[{'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'capture': 'zone'}]))


def test_branches_inherit_the_gate_and_each_resolves_its_own_touch():
    raw = recipe(steps=[], direction='BOTH', branches=[
        {'direction': 'LONG', 'steps': [touch(side='LOW')]}, {'direction': 'SHORT', 'steps': [touch(side='HIGH')]}])
    plan = execution_plan(raw)['meaning']
    assert [b['steps'][0]['_level_gate']['atr_period'] for b in plan['branches']] == [14, 14]
    raw['branches'][1]['steps'][0]['capture'] = 'different'
    with pytest.raises(ValueError):
        execution_plan(raw)


@pytest.mark.parametrize('lifecycle', [
    {'expires': {'bars': 10, 'bars_setting': 'MAX_BARS_AFTER_B0', 'tf': 'FINAL'}},
    {'precondition_check': 'AT_START'}, {'precondition_check': 'WHILE_ACTIVE'}])
def test_lifecycle_additions_are_accepted(lifecycle):
    assert execution_plan(plain_oz(lifecycle=copy.deepcopy(lifecycle)))['meaning']['lifecycle'] == lifecycle


@pytest.mark.parametrize('lifecycle', [
    {'expires': {'bars': 10, 'bars_setting': 'max_bars', 'tf': 'FINAL'}},
    {'expires': {'bars': 10, 'bars_setting': 7, 'tf': 'FINAL'}},
    {'expires': {'seconds': 60, 'bars_setting': 'MAX_BARS_AFTER_B0'}},
    {'expires': {'bars': 0, 'bars_setting': 'MAX_BARS_AFTER_B0', 'tf': 'FINAL'}},
    {'precondition_check': 'NEVER'}, {'precondition_check': True}])
def test_lifecycle_additions_are_refused_when_malformed(lifecycle):
    with pytest.raises(ValueError):
        execution_plan(plain_oz(lifecycle=lifecycle))


def test_the_wait_anchor_follows_a_declared_wait_only():
    for anchor in WINDOW_ANCHORS:
        assert execution_plan(plain_oz(final_window_sec=60, final_window_from=anchor))['meaning']['final_window_from'] == anchor
        assert execution_plan(plain_oz(lifecycle={'expires': {'seconds': 60}}, final_window_from=anchor))
    for bad in ({'final_window_from': 'FIRST_CONDITION'}, {'final_window_sec': 60, 'final_window_from': 'LAST'}):
        with pytest.raises(ValueError):
            execution_plan(plain_oz(**bad))


# --- the shipped recipes and the AI schema --------------------------------------------------------

def shipped():
    return registry.builtin_entries()


def test_every_shipped_recipe_validates_and_declares_what_the_features_need():
    plans = {}
    for name, entry in shipped().items():
        meaning = copy.deepcopy(entry['recipe']['strategy_intent']); meaning['symbols'] = [SYMBOL]
        plans[name] = execution_plan(meaning)['meaning']
    gate = plans['SPECIAL2']['branches'][0]['final']['level_gate']
    assert gate == {'ref': 'sweep', 'atr_period': 14, 'atr_mult': 1.5}
    assert all(b['steps'][0]['capture'] == 'sweep' and b['steps'][0]['_level_gate'] == {'atr_period': 14, 'atr_mult': 1.5}
               for b in plans['SPECIAL2']['branches'])
    assert plans['SPECIAL4']['final_window_from'] == 'FIRST_CONDITION'
    assert plans['SPECIAL5']['lifecycle']['expires'] == {'bars': 10, 'bars_setting': 'MAX_BARS_AFTER_B0', 'tf': 'FINAL'}
    for name in ('SPECIAL1', 'SPECIAL3', 'SPECIAL6', 'SPECIAL7', 'SPECIAL8'):
        assert 'final_window_from' not in plans[name] and 'precondition_check' not in (plans[name].get('lifecycle') or {})
    assert all(s.get('_level_gate') is None for s in plans['SPECIAL8']['steps'])


def test_ai_schema_accepts_every_shipped_recipe_and_takes_its_choices_from_the_contract():
    from jsonschema import Draft202012Validator
    from lab.ai.schema import output_schema, recipe_from_intent
    schema = output_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for name, entry in shipped().items():
        meaning = copy.deepcopy(entry['recipe']['strategy_intent']); meaning['symbols'] = ['XAUUSD+']
        intent = {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': meaning,
                  'needs_clarification': False, 'clarification_question': None, 'message_ko': ''}
        validator.validate(intent)
        assert recipe_from_intent(intent)['strategy_intent']['symbols'] == ['XAUUSD+'], name
    meaning_schema = schema['properties']['interpretation']['anyOf'][0]['properties']
    assert meaning_schema['final_window_from']['enum'] == list(WINDOW_ANCHORS)
    assert meaning_schema['lifecycle']['properties']['precondition_check']['enum'] == list(PRECONDITION_CHECKS)
    assert 'level_gate' in meaning_schema['final']['properties']
    assert 'bars_setting' in meaning_schema['lifecycle']['properties']['expires']['properties']


def test_ai_schema_refuses_what_the_contract_refuses():
    from jsonschema import Draft202012Validator
    from lab.ai.schema import output_schema
    validator = Draft202012Validator(output_schema())
    meaning = copy.deepcopy(shipped()['SPECIAL2']['recipe']['strategy_intent']); meaning['symbols'] = ['XAUUSD+']
    def intent(value): return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': value,
        'needs_clarification': False, 'clarification_question': None, 'message_ko': ''}
    for path, value in (('final_window_from', 'LAST'),):
        bad = copy.deepcopy(meaning); bad[path] = value
        assert list(validator.iter_errors(intent(bad)))
    bad = copy.deepcopy(meaning); bad['final']['level_gate'] = {'atr_period': 14}
    assert list(validator.iter_errors(intent(bad)))
    bad = copy.deepcopy(meaning); bad['final']['level_gate']['atr_mult'] = 0
    assert list(validator.iter_errors(intent(bad)))


def test_preset_explanation_names_the_new_fields_in_korean():
    from lab.ai import research_presets
    meaning = copy.deepcopy(shipped()['SPECIAL2']['recipe']['strategy_intent'])
    text = research_presets.example(meaning, '외부유동성 스윕')
    assert '외부유동성 가격과 최종 올존의 ATR 거리 검사' in text and 'ATR 기간: 14' in text and 'ATR 배수: 1.5' in text
    assert 'level_gate' not in text and 'atr_mult' not in text
    four = research_presets.example(copy.deepcopy(shipped()['SPECIAL4']['recipe']['strategy_intent']), '30분')
    assert '처음 조건이 발생한 때부터' in four and 'FIRST_CONDITION' not in four
    five = research_presets.example(copy.deepcopy(shipped()['SPECIAL5']['recipe']['strategy_intent']), '프렉탈')
    assert 'MAX_BARS_AFTER_B0' in five and 'bars_setting' not in five


def test_no_shared_module_branches_on_a_strategy_number():
    pattern = re.compile(r'SPECIAL\s*\d|special\d')
    folders = [ROOT / 'Part1/program/strategy_recipe', ROOT / 'Part1/program/oz_engine']
    # special_files.py only names the folder's file pattern (SPECIAL1~SPECIAL999); it decides nothing.
    offenders = [f'{path.name}:{number}' for folder in folders for path in folder.glob('*.py')
                 if path.name != 'special_files.py'
                 for number, line in enumerate(path.read_text('utf-8').splitlines(), 1) if pattern.search(line)]
    assert not offenders
