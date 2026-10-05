"""Waiting times and precondition checks, common to every Recipe.

within_sec is the time conditions may be apart; final_window_sec (or lifecycle seconds) is
how long the final is waited for. final_window_from chooses where that wait is counted
from. precondition_check chooses whether state conditions are checked only at the start.
"""
import copy

import pytest

from recipe_harness114 import SYMBOL
from strategy_recipe import registry
from strategy_recipe.contract import execution_plan
from strategy_recipe.runtime import IntentMachine

CROSS = {'kind': 'MA_CROSS', 'tfs': ['5m'], 'ma_left': 'EMA50', 'ma_right': 'EMA200', 'direction': 'LONG'}
NEW_FVG = {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'direction': 'LONG'}
STATE = {'kind': 'MA_STATE', 'tfs': ['5m'], 'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200, 'side': 'ABOVE'}
FINAL = {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ'}


def machine(steps, order='UNORDERED', **extra):
    raw = {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': order, 'steps': steps,
           'final': dict(FINAL), 'persistent': True, **extra}
    return IntentMachine(execution_plan(raw)['meaning'], SYMBOL, 'LONG')


def event(token, at, matched=True):
    return {'ready': True, 'matched': matched, 'event': True, 'token': token, 'at': at, 'source_tf': '5m'}


def state(matched=True, ready=True):
    return {'ready': ready, 'matched': matched, 'event': False, 'token': None, 'at': 0, 'source_tf': '5m'}


def deadline_of(order, first, second, now=None, **extra):
    """When the final stops being accepted for two events `first` < `second` seconds."""
    m = machine([CROSS, NEW_FVG], order, **extra)
    m.advance(first, [event('a', first), event('b', first, matched=False)])
    now = second if now is None else now
    return m, m.advance(now, [event('a', first, matched=False), event('b', second)])


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
@pytest.mark.parametrize('anchor,expected', [('ALL_CONDITIONS', 160 + 600), ('FIRST_CONDITION', 100 + 600)])
def test_the_final_wait_is_counted_from_the_chosen_condition(order, anchor, expected):
    m, actions = deadline_of(order, 100, 160, within_sec=300, final_window_sec=600, final_window_from=anchor)
    assert actions == ['ARM'] and m.active_until == expected
    # final_window_sec still accepts its own last instant and nothing after it.
    assert m.final_allowed(expected, [event('a', 100, False), event('b', 160, False)])
    assert not m.final_allowed(expected + 1, [event('a', 100, False), event('b', 160, False)])


def test_default_is_counted_from_when_all_conditions_are_gathered():
    m, _ = deadline_of('UNORDERED', 100, 160, within_sec=300, final_window_sec=600)
    assert m.active_until == 160 + 600


def test_conditions_apart_longer_than_within_sec_never_start_a_wait_however_long_the_final_window():
    for anchor in ('ALL_CONDITIONS', 'FIRST_CONDITION'):
        m, actions = deadline_of('UNORDERED', 100, 100 + 301, within_sec=300, final_window_sec=10 ** 6, final_window_from=anchor)
        assert actions == [] and not m.active


def test_the_two_durations_are_independent():
    # Same gap between the conditions; only the final wait differs.
    short, _ = deadline_of('UNORDERED', 100, 160, within_sec=300, final_window_sec=100, final_window_from='FIRST_CONDITION')
    long_, _ = deadline_of('UNORDERED', 100, 160, within_sec=300, final_window_sec=900, final_window_from='FIRST_CONDITION')
    assert (short.active_until, long_.active_until) == (200, 1000)
    # Same final wait; only the gap differs.
    tight, a = deadline_of('UNORDERED', 100, 160, within_sec=59, final_window_sec=900)
    assert a == [] and not tight.active


def test_a_wait_counted_from_the_first_condition_may_already_be_over_when_the_last_arrives():
    m, actions = deadline_of('UNORDERED', 100, 700, within_sec=900, final_window_sec=300, final_window_from='FIRST_CONDITION')
    assert actions == [] and not m.active           # 100 + 300 = 400 < 700: nothing is armed
    m, actions = deadline_of('UNORDERED', 100, 400, within_sec=900, final_window_sec=300, final_window_from='FIRST_CONDITION')
    assert actions == ['ARM'] and m.active_until == 400   # the last instant of a final_window_sec is still inside


def test_lifecycle_seconds_follow_the_same_choice_and_end_at_their_limit():
    steps = [CROSS, NEW_FVG]
    life = {'expires': {'seconds': 600}}
    for anchor, until in (('ALL_CONDITIONS', 160 + 600), ('FIRST_CONDITION', 100 + 600)):
        m = machine(steps, within_sec=300, lifecycle=copy.deepcopy(life), final_window_from=anchor)
        m.advance(100, [event('a', 100), event('b', 100, False)])
        assert m.advance(160, [event('a', 100, False), event('b', 160)]) == ['ARM'] and m.active_until == until
        # A lifecycle seconds limit ends the wait AT the limit; final_window_sec accepts its last instant.
        assert m.final_allowed(until - 1, [event('a', 100, False), event('b', 160, False)])
        assert not m.final_allowed(until, [event('a', 100, False), event('b', 160, False)])


def test_conditions_that_hold_together_start_counting_at_their_earliest_event():
    for anchor, until in (('FIRST_CONDITION', 1000 + 600), ('ALL_CONDITIONS', 1003 + 600)):
        m = machine([dict(CROSS), dict(STATE)], 'SIMULTANEOUS', final_window_sec=600, final_window_from=anchor)
        m.advance(999, [event('c', 0, False), state(False)])          # the first poll fixes what is already past
        assert m.advance(1003, [event('c', 1000), state()]) == ['ARM']
        assert m.active_until == until


@pytest.mark.parametrize('bad', [{'final_window_from': 'FIRST_CONDITION'}, {'final_window_from': 'EVER'}])
def test_contract_requires_a_wait_and_a_known_anchor(bad):
    with pytest.raises(ValueError):
        machine([CROSS, NEW_FVG], within_sec=300, **bad)


# --- SPECIAL3: the 수정본50 calculation against the current one ----------------------------------

def special3_branch(direction='LONG'):
    entry = registry.builtin_entries()['SPECIAL3']
    meaning = copy.deepcopy(entry['recipe']['strategy_intent'])
    meaning['symbols'] = [SYMBOL]
    unit = next(b for b in execution_plan(meaning)['meaning']['branches'] if b['direction'] == direction)
    return unit


def old_special3_deadline(first, second, final_window=7200, max_gap=1800):
    """수정본50 event_composer_domain.py L5493-5498 (UNORDERED pair) and L6005 (accepted until)."""
    pair_completed, pair_deadline = max(first, second), min(first, second) + max_gap
    return min(pair_completed + final_window, pair_deadline)


def current_special3_deadline(first, second, **overrides):
    unit = copy.deepcopy(special3_branch())
    unit.update(overrides)
    m = IntentMachine(unit, SYMBOL, 'LONG')
    m.advance(first, [event('cross', first), event('fvg', first, matched=False)], after_observations=[state(False)])
    m.advance(second, [event('cross', first, matched=False), event('fvg', second)], after_observations=[state(False)])
    return m.active_until if m.active else None


@pytest.mark.parametrize('first,gap', [(0, 0), (0, 600), (0, 1799), (0, 1800), (5400, 900)])
def test_special3_shipped_recipe_waits_longer_than_수정본50(first, gap):
    second = first + gap
    old, current = old_special3_deadline(first, second), current_special3_deadline(first, second)
    assert old == first + 1800                      # 7200 never binds: the pair deadline is always sooner
    assert current == second + 7200                 # the wait is counted from the gathered pair
    assert current - old == 7200 + gap - 1800


def test_special3_수정본50_calculation_is_reproduced_by_declaring_the_same_two_durations():
    # Not applied to the shipped recipe: shown only to state which settings reproduce 수정본50.
    for first, gap in ((0, 0), (0, 600), (0, 1799), (0, 1800), (5400, 900)):
        reproduced = current_special3_deadline(first, first + gap, final_window_sec=1800, final_window_from='FIRST_CONDITION')
        assert reproduced == old_special3_deadline(first, first + gap)
    # Beyond the gap, 수정본50 never started a wait at all; neither does within_sec.
    assert current_special3_deadline(0, 1801) is None


def test_special3_conditions_gap_is_unchanged_from_수정본50():
    unit = special3_branch()
    assert unit['within_sec'] == 1800 and unit['final_window_sec'] == 7200
    assert unit.get('final_window_from', 'ALL_CONDITIONS') == 'ALL_CONDITIONS'


# --- precondition check --------------------------------------------------------------------------

def watcher(check=None):
    life = {'precondition_check': check} if check else None
    m = machine([dict(STATE)], 'SIMULTANEOUS', **({'lifecycle': life} if life else {}))
    assert m.advance(1, [state()]) == ['ARM']
    return m


def test_default_checks_the_states_all_the_while():
    m = watcher()
    assert m.checks_while_active()
    assert m.final_allowed(2, [state()]) and not m.final_allowed(2, [state(False)])
    assert m.advance(3, [state(False)]) == ['CANCEL'] and not m.active


def test_at_start_checks_the_states_only_when_watching_begins():
    m = watcher('AT_START')
    assert not m.checks_while_active()
    assert m.advance(3, [state(False)]) == [] and m.active          # no cancel when the state later fails
    assert m.final_allowed(3, [state(False)])                       # and the final does not repeat it
    assert m.final_allowed(4, [state(ready=False, matched=False)])  # unknown data does not suppress either
    # It still has to hold at the start.
    never = machine([dict(STATE)], 'SIMULTANEOUS', lifecycle={'precondition_check': 'AT_START'})
    assert never.advance(1, [state(False)]) == [] and not never.active


def test_while_active_suppresses_the_final_on_unknown_data():
    m = watcher('WHILE_ACTIVE')
    assert m.advance(3, [state(ready=False, matched=False)]) == [] and m.active
    assert not m.final_allowed(3, [state(ready=False, matched=False)])


def test_at_start_never_relaxes_events_or_other_stages():
    m = machine([dict(STATE)], 'SIMULTANEOUS', lifecycle={'precondition_check': 'AT_START'},
                cancel_conditions=[dict(CROSS)])
    m.advance(1, [state()], )
    assert m.advance(2, [state()], cancelled=True) == ['CANCEL'] and not m.active


@pytest.mark.parametrize('bad', ['ALWAYS', '', 1])
def test_contract_rejects_unknown_check_modes(bad):
    with pytest.raises(ValueError):
        machine([dict(STATE)], 'SIMULTANEOUS', lifecycle={'precondition_check': bad})


def test_shipped_recipes_keep_their_default_check_without_declaring_anything():
    for name in ('SPECIAL1', 'SPECIAL5', 'SPECIAL6', 'SPECIAL7', 'SPECIAL8'):
        meaning = registry.builtin_entries()[name]['recipe']['strategy_intent']
        assert 'precondition_check' not in (meaning.get('lifecycle') or {}), name
        plan = execution_plan({**copy.deepcopy(meaning), 'symbols': [SYMBOL]})['meaning']
        for unit in plan.get('branches') or [plan]:
            assert IntentMachine(unit, SYMBOL, 'LONG').checks_while_active(), name
