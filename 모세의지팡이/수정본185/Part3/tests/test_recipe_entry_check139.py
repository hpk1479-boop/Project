"""수정본139: 레시피의 가상진입 칸은 Part3 검사도 Part2와 같은 함수(recipe_policy)로 읽는다.

기본 스페셜과 Part3 레시피는 같은 형식이다: 얼럿 온리 칸(strategy_intent) + 가상진입 칸(virtual_entry).
가상진입 칸이 없으면 즉시 진입이다.
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'Part3')]
from strategy_recipe.registry import builtin_entries  # noqa: E402
from lab.ai.schema import validate_recipe  # noqa: E402
from lab.compiler import compile_recipe  # noqa: E402
from event_backtest.virtual_contract import immediate_virtual_entry  # noqa: E402
from event_backtest.virtual_defaults import recipe_policy, strategy_profile  # noqa: E402


def recipe(name):
    row = copy.deepcopy(builtin_entries()[name])
    return dict(row['recipe'], symbols=['XAUUSD+'], strategy_intent=dict(row['recipe']['strategy_intent'], symbols=['XAUUSD+']))


@pytest.mark.parametrize('name', sorted(builtin_entries()))
def test_a_shipped_recipe_passes_part3s_check_with_its_virtual_entry(name):
    value = recipe(name)
    assert 'virtual_entry' in value
    validate_recipe(value)
    default = copy.deepcopy(strategy_profile(name)['default'])
    for row in default['conditions'] + default['filters']:
        row.pop('recipe_index', None)   # revision148 editing origins are not recipe execution values
    assert recipe_policy(value) == default
    assert repr(value['virtual_entry']) in compile_recipe(value, 'Test_SPECIAL901.py')   # carried as data


def test_without_the_cell_a_recipe_enters_immediately():
    value = recipe('SPECIAL8')
    del value['virtual_entry']
    validate_recipe(value)
    assert recipe_policy(value) == immediate_virtual_entry()


@pytest.mark.parametrize('name,change', [
    ('SPECIAL8', lambda v: v['virtual_entry']['stop'].update(kind='OZ_B0')),          # B0 needs an OZ alert
    ('SPECIAL1', lambda v: v['virtual_entry']['filters'].append({'kind': 'MAX_BARS', 'bars': 0})),
    ('SPECIAL1', lambda v: v['virtual_entry'].update(mode='AUTO')),
])
def test_a_broken_virtual_entry_is_rejected_with_part2s_own_message(name, change):
    value = recipe(name)
    change(value)
    with pytest.raises(ValueError) as part2:
        recipe_policy(value)
    with pytest.raises(ValueError) as part3:
        validate_recipe(value)
    assert str(part3.value) == str(part2.value)
