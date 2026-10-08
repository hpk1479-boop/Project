"""수정본148: 기존 칸은 자기 시간봉, 새 칸은 전략 시간봉을 사용한다.

슬롯 번호는 조건 종류나 이평 수치를 바꿔도 유지한다. 같은 종류의 다른 칸이나
삭제 후 새로 넣은 칸이 기존 칸의 시간봉을 가져오면 거부한다.
"""
from copy import deepcopy
from pathlib import Path
import json
import socket
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import ui_model  # noqa: E402
from event_backtest.settings import scenario  # noqa: E402
from event_backtest.virtual_contract import normalize_virtual_entry, schema  # noqa: E402
from event_backtest.virtual_defaults import (check_frames, recipe_on_base, strategy_on_base,  # noqa: E402
                                             strategy_profile, target_policy)
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402
from event_backtest.virtual_facts import VirtualFacts  # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS as C  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No network in recipe-slot tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def policy_of(name, base='SIGNAL'):
    return recipe_on_base(strategy_profile(name), base)


def run_of(name, policy):
    return scenario(symbol='XAUUSD+', start='2026-09-01', end='2026-10-01', strategies=[name],
                    result_mode='VIRTUAL_ENTRY', virtual_entry=policy)


def ai_command(policy, name='SPECIAL6'):
    from lab.ai import backtest_commands
    command = {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
               'clarification_question': None, 'message_ko': '확인', 'request': {
                   'target_mode': 'SPECIAL', 'specials': [name], 'filename': None, 'watch_text': None,
                   'symbol': 'TEST', 'start': '2026-09-01', 'end': '2026-10-01', 'mode': 'BAR',
                   'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0, 'build_only': False,
                   'rebuild': False, 'available_only': True, 'base_frame': None, 'virtual_entry': policy}}
    snapshot = {'today': '2026-10-07', 'jobs': [], 'generated': [], 'execution_version': 'stable',
                'execution_defaults': {}, 'options': {'specials': [name], 'symbols': ['TEST'],
                                                      'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY'}}
    return backtest_commands._normalize(command, snapshot)


@pytest.mark.parametrize('name', ['SPECIAL1', 'SPECIAL6', 'SPECIAL9'])
def test_native_policy_slots_are_valid_and_public_schema_preserves_them(name):
    from jsonschema import validate
    policy = policy_of(name)
    for key in ('conditions', 'filters'):
        assert [row['recipe_index'] for row in policy[key]] == list(range(len(policy[key])))
    assert target_policy([name], policy=policy) == normalize_virtual_entry(policy) == policy
    validate(policy, schema())


@pytest.mark.parametrize('name,base,top', [('SPECIAL8', '2m', None), ('SPECIAL8', '5m', None),
                                        ('SPECIAL9', '2m', '20m'), ('SPECIAL9', '5m', '1h')])
def test_base_change_keeps_native_slots_and_resets_recipe_values(name, base, top):
    profile = strategy_profile(name)
    before = deepcopy(profile)
    edited = recipe_on_base(profile, 'SIGNAL')
    edited['stop'].update(period=21, multiplier=2.5)
    edited['conditions'].append({'kind': 'MA_POSITION', 'family': 'EMA', 'period': 30, 'tf': 'SIGNAL'})
    moved = recipe_on_base(profile, base)
    assert moved['tf'] == base and moved['stop']['period'] == 14 and moved['stop']['multiplier'] == 1.
    assert len(moved['conditions']) == len(profile['default']['conditions'])
    assert [row['recipe_index'] for row in moved['filters']] == list(range(len(moved['filters'])))
    assert target_policy([name], policy=moved) == moved and profile == before
    meaning = strategy_on_base(profile, base)
    if top is None:
        assert all(step['tfs'] == [base] for step in meaning['steps'])
    else:
        assert meaning['steps'][0]['tfs'] == [base] and meaning['steps'][1]['tfs'] == [top]
        assert moved['filters'][1]['tf'] == top


@pytest.mark.parametrize('path', ['policy', 'scenario', 'ai', 'saved'])
def test_special6_same_kind_cannot_borrow_the_other_slots_environment_frame(path, tmp_path):
    policy = policy_of('SPECIAL6')
    policy['filters'][0]['tf'] = 'ENV'
    if path == 'saved':
        saved = tmp_path / 'backtest_ui.json'
        saved.write_text(json.dumps({'virtual_entry_target': 'SPECIAL6', 'virtual_entry': policy,
                                     'specials': {}}, ensure_ascii=False), encoding='utf-8')
        loaded = ui_model.load(saved)
        assert (loaded['virtual_entry'], loaded['virtual_entry_target']) == (None, None)
    else:
        with pytest.raises(ValueError):
            if path == 'policy':
                target_policy(['SPECIAL6'], policy=policy)
            elif path == 'scenario':
                run_of('SPECIAL6', policy)
            else:
                ai_command(policy)


def test_special6_environment_slot_accepts_average_and_period_edits_without_moving():
    policy = policy_of('SPECIAL6')
    policy['filters'][1].update(family='HMA', fast=10, slow=30, bar_state='CLOSED')
    policy['stop'].update(kind='ATR', period=21, multiplier=2.5, bars=7)
    assert policy['filters'][1]['recipe_index'] == 1 and policy['filters'][1]['tf'] == 'ENV'
    assert run_of('SPECIAL6', policy)['virtual_entry'] == policy


def test_special9_condition_change_retains_the_existing_top_frame():
    policy = policy_of('SPECIAL9', '2m')
    policy['filters'][1].update(condition='MA_STATE', family='HMA', fast=6, slow=17)
    assert policy['filters'][1]['recipe_index'] == 1 and policy['filters'][1]['tf'] == '20m'
    assert run_of('SPECIAL9', policy)['virtual_entry'] == policy


def test_normalization_preserves_origins_when_a_slot_has_no_timeframe_field():
    from jsonschema import validate
    policy = policy_of('SPECIAL9', '2m')
    policy['conditions'][0] = {'kind': 'CANDLE_SHAPE', 'shape': 'HAMMER', 'recipe_index': 0}
    policy['filters'][2] = {'kind': 'CANDLE_ATR', 'measure': 'BODY', 'min': None, 'max': 2.,
                            'recipe_index': 2}
    assert normalize_virtual_entry(policy) == target_policy(['SPECIAL9'], policy=policy) == policy
    validate(policy, schema())


class TracedFacts(VirtualFacts):
    def __init__(self):
        super().__init__()
        self.reads = []

    def ma_series(self, tf, stamp, names, *, closed, price=None):
        self.reads.append((tf, tuple(names), closed))
        return super().ma_series(tf, stamp, names, closed=closed, price=price)


def market_views(upper_holds):
    """2m HMA6<17, 2m HMA17>50; the changed 20m HMA6/17 slot alone varies."""
    result = {}
    for tf, seconds in (('1m', 60), ('2m', 120), ('20m', 1200)):
        times = np.asarray([-seconds * k for k in range(60, -1, -1)], dtype=np.int64)
        values = np.ones((len(times), len(C)))
        native = {'open': 100., 'high': 101., 'low': 99., 'close': 100.,
                  'hma_6': 30. if tf == '20m' and upper_holds else 10., 'hma_17': 20., 'hma_50': 10.}
        for name, value in native.items():
            values[:, C.index(name)] = value
        result[tf] = SimpleNamespace(time=times, values=values, columns=C)
    return result


@pytest.mark.parametrize('upper_holds,status', [(True, 'ENTERED'), (False, 'PASS_ENV')])
def test_changed_special9_slot_reads_actual_20m_facts_instead_of_opposite_2m(upper_holds, status):
    policy = policy_of('SPECIAL9', '2m')
    policy['filters'][1].update(condition='MA_STATE', family='HMA', fast=6, slow=17)
    policy.update(mode='IMMEDIATE', conditions=[])
    policy['filters'] = [row for row in policy['filters'] if row['kind'] != 'MAX_BARS']
    policy = target_policy(['SPECIAL9'], policy=policy)
    alert = dict(signal_id='s', strategy='SPECIAL9', symbol='X', tf='2m', direction='LONG', time_ms=0,
                 signal_source='SIGNAL', signal_tf='2m', env_tf='2m', signal_price=100.)
    facts = TracedFacts()
    calculator = VirtualEntry([alert], 0, policy, facts=facts)
    calculator.observe(0, market_views(upper_holds), end_ms=10 ** 9)
    assert calculator.trades[0]['status'] == status
    assert ('20m', ('HMA6', 'HMA17'), False) in facts.reads
    assert ('2m', ('HMA6', 'HMA17'), False) not in facts.reads
    if upper_holds:
        assert calculator.trades[0]['risk'] == pytest.approx(2.)


def test_added_or_copied_trend_without_origin_cannot_take_the_recipe_top_frame():
    policy = policy_of('SPECIAL9', '2m')
    copied = deepcopy(policy['filters'][1])
    copied.pop('recipe_index')
    copied['bar_state'] = 'CLOSED'
    policy['filters'].append(copied)
    with pytest.raises(ValueError):
        target_policy(['SPECIAL9'], policy=policy)


def test_deleted_then_added_trend_is_signal_and_remaining_slots_keep_their_origins():
    policy = policy_of('SPECIAL9', '2m')
    policy['filters'].pop(1)
    policy['filters'].append({'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL',
                              'bar_state': 'CLOSED'})
    assert [row.get('recipe_index') for row in policy['filters']] == [0, 2, None]
    assert target_policy(['SPECIAL9'], policy=policy) == policy
    policy['filters'][0].update(fast=10, slow=30)
    policy['stop'].update(period=21, multiplier=2.5, bars=7)
    assert run_of('SPECIAL9', policy)['virtual_entry'] == policy


def test_deleting_a_first_slot_does_not_renumber_the_remaining_environment_slots():
    policy = policy_of('SPECIAL6')
    policy['filters'].pop(0)
    assert [row['recipe_index'] for row in policy['filters']] == [1, 2]
    assert target_policy(['SPECIAL6'], policy=policy) == policy


@pytest.mark.parametrize('origin', [True, -1, 999, '0', 0.0])
def test_invalid_slot_reference_is_refused(origin):
    policy = policy_of('SPECIAL9', '2m')
    policy['filters'][0]['recipe_index'] = origin
    with pytest.raises(ValueError):
        target_policy(['SPECIAL9'], policy=policy)


def test_duplicate_slot_reference_is_refused_even_when_the_second_row_has_new_numbers():
    policy = policy_of('SPECIAL6')
    copied = deepcopy(policy['filters'][0])
    copied.update(fast=10, slow=30)
    policy['filters'].append(copied)
    with pytest.raises(ValueError):
        target_policy(['SPECIAL6'], policy=policy)


def test_retained_slots_cannot_be_reordered():
    policy = policy_of('SPECIAL6')
    policy['filters'][0], policy['filters'][1] = policy['filters'][1], policy['filters'][0]
    with pytest.raises(ValueError):
        target_policy(['SPECIAL6'], policy=policy)


@pytest.mark.parametrize('field', ['atr', 'stop'])
def test_a_recipe_fixed_atr_or_stop_frame_cannot_be_changed_to_signal(field):
    profile = deepcopy(strategy_profile('SPECIAL8'))
    profile['default'][field]['tf'] = '15m'
    policy = recipe_on_base(profile, '2m')
    assert policy[field]['tf'] == '20m'
    check_frames(profile, policy)
    policy[field]['tf'] = 'SIGNAL'
    with pytest.raises(ValueError):
        check_frames(profile, policy)


def without_origins(policy):
    old = deepcopy(policy)
    for key in ('conditions', 'filters'):
        for row in old[key]:
            row.pop('recipe_index', None)
    return old


def load_saved(tmp_path, name, policy):
    path = tmp_path / 'backtest_ui.json'
    path.write_text(json.dumps({'virtual_entry_target': name, 'virtual_entry': policy, 'specials': {}},
                               ensure_ascii=False), encoding='utf-8')
    return ui_model.load(path)


@pytest.mark.parametrize('name,base', [('SPECIAL6', 'SIGNAL'), ('SPECIAL9', '2m')])
def test_old_native_layout_recovers_slots_and_keeps_nonframe_edits(name, base, tmp_path):
    policy = policy_of(name, base)
    policy['filters'][0].update(family='EMA', fast=10, slow=30)
    policy['stop'].update(period=21, multiplier=2.5)
    loaded = load_saved(tmp_path, name, without_origins(policy))
    assert loaded['virtual_entry'] == policy and loaded['virtual_entry_target'] == name


def test_old_special6_unsafe_environment_frame_is_not_upgraded_into_a_slot(tmp_path):
    policy = without_origins(policy_of('SPECIAL6'))
    policy['filters'][0]['tf'] = 'ENV'
    loaded = load_saved(tmp_path, 'SPECIAL6', policy)
    assert (loaded['virtual_entry'], loaded['virtual_entry_target']) == (None, None)


@pytest.mark.parametrize('change', ['delete', 'reorder'])
def test_ambiguous_old_non_signal_rows_do_not_receive_new_slot_references(change, tmp_path):
    policy = without_origins(policy_of('SPECIAL6'))
    if change == 'delete':
        policy['filters'].pop(0)
    else:
        policy['filters'][0], policy['filters'][1] = policy['filters'][1], policy['filters'][0]
    loaded = load_saved(tmp_path, 'SPECIAL6', policy)
    assert (loaded['virtual_entry'], loaded['virtual_entry_target']) == (None, None)


def test_partial_origin_policy_keeps_an_added_signal_row_without_inventing_an_origin(tmp_path):
    policy = policy_of('SPECIAL9', '2m')
    policy['filters'].pop(1)
    policy['filters'].insert(1, {'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL',
                               'bar_state': 'CLOSED'})
    loaded = load_saved(tmp_path, 'SPECIAL9', policy)
    assert loaded['virtual_entry'] == policy
    assert 'recipe_index' not in loaded['virtual_entry']['filters'][1]
    assert loaded['virtual_entry']['filters'][2]['recipe_index'] == 2


@pytest.mark.parametrize('path', ['policy', 'ai'])
def test_external_non_signal_rows_without_origin_never_use_saved_policy_migration(path):
    policy = without_origins(policy_of('SPECIAL9', '2m'))
    with pytest.raises(ValueError):
        if path == 'policy':
            target_policy(['SPECIAL9'], policy=policy)
        else:
            ai_command(policy, 'SPECIAL9')
