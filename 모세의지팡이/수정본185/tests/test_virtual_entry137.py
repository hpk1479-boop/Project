"""Revision 137: no AUTO entry/stop, strategy defaults as values, one target per virtual entry.

Since revision 139 the values are the strategy recipe's own virtual_entry; WATCH is alert-only.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.settings import scenario
from event_backtest.virtual_contract import immediate_virtual_entry, normalize_virtual_entry
from event_backtest.virtual_defaults import strategy_profile, target_policy
from event_backtest.virtual_entry import VirtualEntry
from test_virtual_common113 import alert, history, oz_policy, policy, replay, view


def test_each_alert_is_judged_on_its_own_frame_with_the_same_conditions():
    # One OZ strategy alerting on 1m and 5m: 1m uses the 1m candle/HMA6, 5m the 5m ones.
    one = alert(oz=True, signal_id='one')
    five = alert(oz=True, signal_id='five', tf='5m', signal_tf='5m')
    minutes = [(0, 10, 12, 8, 11, 9), (60, 10, 11, 9, 10, 9), (120, 10, 11, 9, 10, 9), (180, 10, 11, 9, 10, 9),
               (240, 10, 11, 9, 10, 9), (300, 10, 11, 9, 10, 9)]
    fives = [(0, 10, 12, 8, 11, 9), (300, 10, 11, 9, 10, 9)]
    calc = VirtualEntry([one, five], 0, oz_policy())
    for stamp in (60, 120, 180, 240, 300):
        calc.observe(stamp * 1000, {'1m': view([r for r in minutes if r[0] <= stamp]),
                                    '5m': view([r for r in fives if r[0] <= stamp])}, end_ms=10 ** 9)
    entries = {t['alert']['signal_id']: t['entry_time'] for t in calc.trades}
    assert entries == {'one': 60000, 'five': 300000}


def test_conditional_entry_without_closed_candle_condition_skips_that_check():
    # A bearish candle that closed above HMA6 (9).
    bearish = [(0, 12, 13, 9.5, 10, 9), (60, 10, 11, 9, 10, 9)]
    hma6 = {'kind': 'MA_POSITION', 'family': 'HMA', 'period': 6}
    assert replay(bearish, p=policy(conditions=[hma6])).trades[0]['status'] == 'WAITING'
    assert replay(bearish, p=policy(conditions=[hma6], candle=False)).trades[0]['entry_time'] == 60000


def test_atr_stop_uses_its_own_period_not_the_entry_limit_atr():
    p = policy('IMMEDIATE', stop={'kind': 'ATR', 'tf': '1m', 'period': 2, 'multiplier': 1},
               atr={'tf': '1m', 'period': 14})
    rows = [(i * 60, 10, 11, 9, 10.5) for i in range(-30, 0)] + [(0, 10, 14, 6, 10.5)]
    calc = VirtualEntry([alert(time=30000, signal_price=10)], 0, p)
    calc.observe(30000, {'1m': view(rows)}, end_ms=10 ** 9)
    # ATR2 of the last closed bars is 2, ATR14 would differ.
    assert calc.trades[0]['stop_price'] == pytest.approx(8)


@pytest.mark.parametrize('name,oz,frames', [
    ('SPECIAL1', True, ['1m', '2m', '3m', '4m', '5m', '6m', '10m', '12m', '15m', '20m', '30m', '1h']),
    ('SPECIAL2', True, ['1m', '2m', '3m', '4m', '5m', '6m', '10m', '12m', '15m', '20m', '30m', '1h']),
    ('SPECIAL5', True, ['1m', '2m', '3m']), ('SPECIAL7', True, ['1m']), ('SPECIAL8', False, ['1m']),
    ('SPECIAL9', False, ['1m'])])
def test_strategy_profile_reads_oz_trigger_and_alert_frames(name, oz, frames):
    profile = strategy_profile(name)
    assert (profile['oz'], profile['timeframes']) == (oz, frames)


def test_watch_is_alert_only():
    command = [{'strategy': 'WATCH', 'text': '골드 5분 무지성 브레이커 매수 올존 알려줘', 'chat_id': 'BACKTEST'}]
    with pytest.raises(ValueError, match='얼럿 온리'):
        target_policy(['WATCH'], command)
    with pytest.raises(ValueError, match='얼럿 온리'):
        scenario(strategies=['WATCH'], commands=command, result_mode='VIRTUAL_ENTRY')
    assert scenario(strategies=['WATCH'], commands=command, result_mode='ALERT_ONLY')['virtual_entry'] is None


def test_defaults_are_the_recipe_values_and_are_checked_against_the_strategy():
    assert target_policy(['SPECIAL7']) == strategy_profile('SPECIAL7')['default']
    assert target_policy(['SPECIAL7'])['stop']['kind'] == 'OZ_B0'
    assert target_policy(['SPECIAL8']) == strategy_profile('SPECIAL8')['default']
    assert target_policy(['SPECIAL8'])['stop']['kind'] == 'ATR'
    with pytest.raises(ValueError, match='올존 B0'):
        target_policy(['SPECIAL8'], policy=oz_policy())
    neckline = dict(immediate_virtual_entry(), mode='CONFIRM', conditions=[{'kind': 'NECKLINE_BREAK'}])
    with pytest.raises(ValueError, match='넥라인'):
        target_policy(['SPECIAL8'], policy=neckline)


@pytest.mark.parametrize('strategies', [['SPECIAL1', 'SPECIAL8'], ['ALL'], []])
def test_virtual_entry_runs_exactly_one_strategy(strategies):
    with pytest.raises(ValueError, match='전략 1개만'):
        scenario(strategies=strategies, result_mode='VIRTUAL_ENTRY')


def test_alert_only_and_build_only_take_any_strategies_without_policy():
    assert scenario(strategies=['SPECIAL1', 'SPECIAL8'], result_mode='ALERT_ONLY')['virtual_entry'] is None
    built = scenario(strategies=[], result_mode='VIRTUAL_ENTRY', build_only=True)
    assert built['virtual_entry'] is None


def test_scenario_fills_and_keeps_the_one_strategy_policy():
    assert (scenario(strategies=['SPECIAL5'], result_mode='VIRTUAL_ENTRY')['virtual_entry']
            == strategy_profile('SPECIAL5')['default'])
    chosen = policy(conditions=[{'kind': 'ENGULFING'}])
    assert scenario(strategies=['SPECIAL5'], result_mode='VIRTUAL_ENTRY', virtual_entry=chosen)['virtual_entry'] == chosen


# The stop AUTO and schema 1 inputs are in test_virtual_common113.py::test_invalid_and_ignored_policy_paths_are_rejected.
@pytest.mark.parametrize('bad', [{'mode': 'AUTO'}])
def test_auto_choices_no_longer_exist(bad):
    with pytest.raises(ValueError):
        normalize_virtual_entry(bad)


def test_old_saved_policy_is_not_converted_and_the_strategy_defaults_return(tmp_path):
    from event_backtest import ui_model
    path = tmp_path / 'ui.json'
    path.write_text(json.dumps({'target_mode': 'SPECIAL', 'specials': {}, 'watch': {'text': ''},
        'virtual_entry': {'schema': 1, 'mode': 'AUTO', 'tf': 'SIGNAL', 'conditions': [],
                          'atr': {'tf': 'SIGNAL', 'period': 14}, 'filters': [],
                          'stop': {'kind': 'AUTO', 'tf': 'SIGNAL', 'bars': 5, 'multiplier': 1.0}}}), encoding='utf-8')
    loaded = ui_model.load(path)
    assert loaded['virtual_entry'] is None and loaded['virtual_entry_target'] is None
    chosen = oz_policy()
    ui_model.save_virtual_entry(chosen, 'SPECIAL5', path)
    loaded = ui_model.load(path)
    restored = loaded['virtual_entry']
    values = {**restored, **{group: [{key: value for key, value in row.items() if key != 'recipe_index'}
                                    for row in restored[group]] for group in ('conditions', 'filters')}}
    assert (values, loaded['virtual_entry_target']) == (chosen, 'SPECIAL5')


# 수정본162: the scan for traces of the removed AUTO entry was deleted.
