"""수정본185: a virtual-entry commission in dollars per lot, round trip, next to the spread.

There are no lots in a virtual entry: every trade's 1R is its loss at the stop per unit (spread included).
The commission per unit is dollars per lot ÷ the symbol's 1-lot size (LOT_<symbol>, the MT5 contract size),
and each closed trade's R loses commission per unit ÷ 1R. That is what sizing each trade to lose 1R at its
stop would cost: the lot count cancels out. The commission changes no fill, so it is after the replay.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.virtual_entry import RATIOS, VirtualEntry, pricing  # noqa: E402
from test_virtual_common113 import alert, policy, view  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


GOLD = {'POINT_XAUUSD+': '0.01', 'LOT_XAUUSD+': '100'}


def priced(spread=0, commission=0, config=GOLD):
    return pricing({'symbol': 'XAUUSD+', 'spread_points': {'XAUUSD+': spread}, 'commission': {'XAUUSD+': commission}},
                   [], dict(config))


# ---- dollars per lot to a price per unit ----------------------------------------------------------

def test_the_commission_per_lot_becomes_a_price_per_unit_over_the_lot_size():
    price = priced(spread=20, commission=7)
    assert price['spread_price'] == pytest.approx(0.2) and price['commission_price'] == pytest.approx(0.07)
    assert price['commission'] == 7 and price['lot_size'] == 100
    # A zero commission needs no lot size, and a commission needs no point.
    assert priced(spread=20, commission=0, config={'POINT_XAUUSD+': '0.01'})['commission_price'] == 0
    assert priced(spread=0, commission=7, config={'LOT_XAUUSD+': '100'})['commission_price'] == pytest.approx(0.07)
    assert priced()['lot_size'] is None and 'commission' in priced()


@pytest.mark.parametrize('commission,config,message', [
    (7, {'POINT_XAUUSD+': '0.01'}, r'종목 1랏 크기 \(XAUUSD\+\) 값이 없습니다'),
    (7, {'LOT_XAUUSD+': '0'}, '1랏 크기는 양수'),
    (-1, GOLD, '수수료는 0 이상'),
    ('seven', GOLD, '수수료 값이 없습니다'),
])
def test_a_commission_without_a_usable_lot_size_is_refused(commission, config, message):
    with pytest.raises(ValueError, match=message):
        priced(commission=commission, config=config)


# ---- each closed trade loses commission ÷ 1R ------------------------------------------------------

def filled(direction='LONG', *, won=True, spread=0., commission=0.):
    """An immediate entry at 100 on an OZ alert, stop at its B0 3 away; the next candle reaches every target
    (won) or the stop."""
    long = direction == 'LONG'
    a = alert(oz=True, direction=direction, signal_price=100, stop=97 if long else 103)
    calc = VirtualEntry([a], spread, policy('IMMEDIATE', stop={'kind': 'OZ_B0'}), commission_price=commission)
    far = (130, 100) if long else (100, 70)
    near = (100, 90) if long else (110, 100)
    high, low = far if won else near
    rows = [(0, 100, 100, 100, 100), (60, 100, high, low, 100), (120, 100, 100, 100, 100)]
    for i, row in enumerate(rows):
        calc.observe(row[0] * 1000, {'1m': view(rows[:i + 1])}, end_ms=10 ** 9)
    return calc


@pytest.mark.parametrize('direction,spread,risk', [('LONG', 0., 3.), ('LONG', .2, 3.2), ('SHORT', .2, 3.)])
def test_every_closed_trade_loses_the_commission_over_its_own_1r(direction, spread, risk):
    commission = .06                                 # 6 dollars per lot, 100 ounces a lot
    won = filled(direction, spread=spread, commission=commission)
    assert won.trades[0]['risk'] == pytest.approx(risk)
    cost = commission / risk
    summary, details = won.results()
    assert [row['average_r'] for row in summary] == pytest.approx([rr - cost for rr in RATIOS])
    assert [row['total_r'] for row in summary] == pytest.approx([rr - cost for rr in RATIOS])
    assert [row['r'] for row in details] == pytest.approx([rr - cost for rr in RATIOS])
    assert [row['commission_r'] for row in details] == pytest.approx([cost] * len(RATIOS))
    lost = filled(direction, won=False, spread=spread, commission=commission)
    assert [row['r'] for row in lost.results()[1]] == pytest.approx([-1 - cost] * len(RATIOS))
    # The fills are the same as without a commission: only the R is lower.
    plain = filled(direction, spread=spread)
    assert [row['result'] for row in plain.results()[1]] == [row['result'] for row in details] == ['WIN'] * len(RATIOS)
    assert plain.trades[0]['risk'] == won.trades[0]['risk'] and plain.results()[1][0]['commission_r'] == 0


@pytest.mark.parametrize('risk', [3., 3.2])
def test_sizing_each_trade_to_lose_100_dollars_gives_the_same_commission(risk):
    # 1 lot = 100 ounces, 6 dollars a lot: with 100 dollars at risk the lot count depends on the stop,
    # the commission in R does not.
    lots = 100 / (risk * 100)
    assert lots * 6 / 100 == pytest.approx(.06 / risk)


def test_a_trade_with_no_closed_result_has_no_commission():
    calc = filled(commission=.06)
    calc.trades[0]['exits'][1.0] = {'result': 'UNCERTAIN', 'r': None, 'exit_time': 0, 'target': 0}
    row = next(row for row in calc.results()[1] if row['rr'] == 1.0)
    assert row['r'] is None and row['commission_r'] is None


def test_the_commission_keys_no_replay_and_is_kept_in_the_run():
    from event_backtest import runner
    from event_backtest.settings import scenario
    from event_backtest.tested_settings import tested_settings
    assert 'commission' in runner.AFTER_REPLAY
    tasks = [{'scenario': scenario(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL2'],
                                   commission={'XAUUSD+': value}), 'start': 's', 'end': 'e', 'captures': []}
             for value in (0, 7)]
    assert runner.replay_key('code', 'config', tasks[:1]) == runner.replay_key('code', 'config', tasks[1:])
    kept = tested_settings({'symbol': 'XAUUSD+', 'strategies': []}, {**GOLD, 'WONBI_SIGMA': '3'}, {})
    assert kept['config'] == {'WONBI_SIGMA': '3', 'POINT_XAUUSD+': '0.01', 'LOT_XAUUSD+': '100'}


def test_the_command_line_takes_a_commission(monkeypatch, tmp_path):
    from event_backtest import __main__ as cli
    from event_backtest import workflow
    seen = []
    monkeypatch.setattr(workflow, 'proposal', lambda s, warehouse, **options: seen.append(s) or {'record': []})
    monkeypatch.setattr(sys, 'argv', ['event_backtest', 'plan', '--symbol', 'XAUUSD+', '--start', '2026-09-01',
                                      '--end', '2026-09-02', '--strategies', 'SPECIAL2', '--commission', '7',
                                      '--warehouse', str(tmp_path / 'w'), '--skip-cleanup'])
    assert cli.main() == 0 and seen[0]['commission'] == {'XAUUSD+': 7.0}


# ---- the settings screen: a 1-lot size per symbol -------------------------------------------------

BEFORE = 'SYMBOLS=XAUUSD+,NAS100\nPOINT_XAUUSD+=0.01\n'


@pytest.fixture
def config(tmp_path, monkeypatch):
    from lab import unified_settings
    path = tmp_path / 'config.txt'
    path.write_text(BEFORE, encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', path)
    return path


def sizes():
    from lab import unified_settings
    return {row['key']: row['value'] for row in unified_settings.read()['live'] if row['key'].startswith('LOT_')}


def test_each_symbol_has_a_lot_size_field_saved_like_the_point(config):
    from lab import unified_settings
    assert sizes() == {'LOT_XAUUSD+': '', 'LOT_NAS100': ''} and config.read_text('utf-8') == BEFORE
    unified_settings.save_live({'LOT_XAUUSD+': '100'})
    assert config.read_text('utf-8') == BEFORE + 'LOT_XAUUSD+=100\n' and sizes()['LOT_XAUUSD+'] == '100'
    unified_settings.save_live({'LOT_NAS100': '0.5'})
    assert sizes() == {'LOT_XAUUSD+': '100', 'LOT_NAS100': '0.5'}
    for value in ('0', '-1', 'abc', '', 'nan'):
        with pytest.raises(ValueError):
            unified_settings.save_live({'LOT_NAS100': value})
    from event_composer_domain import load_config
    assert priced(commission=7, config=load_config(str(config)))['commission_price'] == pytest.approx(0.07)


def test_the_installed_gold_size_is_the_standard_100_ounces():
    from event_composer_domain import load_config
    assert load_config(str(ROOT / 'Part1/program/config.txt'))['LOT_XAUUSD+'] in ('100', 100, 100.0)


# ---- the screen's request, the generated strategy and Part3's AI ---------------------------------

def test_the_screen_request_carries_the_commission_to_the_scenario(monkeypatch):
    from unittest.mock import patch
    from event_backtest import ui_model
    from lab import unified_backtest
    request = {'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'], 'symbol': 'XAUUSD+', 'start': '2026-01-01',
               'end': '2026-02-01', 'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 20, 'commission': 7}
    with patch.object(ui_model, 'load', return_value=copy.deepcopy(ui_model.load())), patch.object(ui_model, 'save_virtual_entry'):
        s = unified_backtest._scenario(dict(request))
        assert s['commission'] == {'XAUUSD+': 7.0} and s['spread_points'] == {'XAUUSD+': 20.0}
        assert unified_backtest._scenario({k: v for k, v in request.items() if k != 'commission'})['commission'] == {'XAUUSD+': 0.0}
        for bad in (-1, 'x', float('inf')):
            with pytest.raises(ValueError, match='수수료는 0 이상'):
                unified_backtest._scenario({**request, 'commission': bad})


def test_the_run_shows_the_commission_it_was_tested_with(tmp_path, monkeypatch):
    from lab import unified_backtest
    folder = tmp_path / ('a' * 32)
    folder.mkdir()
    (folder / 'result.json').write_text(json.dumps({'scenario': {'symbol': 'XAUUSD+', 'strategies': [],
        'spread_points': {'XAUUSD+': 20}, 'commission': {'XAUUSD+': 7}, 'result_mode': 'VIRTUAL_ENTRY'}}), encoding='utf-8')
    monkeypatch.setattr(unified_backtest, '_job', lambda identifier: {'folder': folder, 'scenario': {}})
    shown = unified_backtest.tested('a' * 32)
    assert shown['spread_points'] == 20 and shown['commission'] == 7


def ai_start(**changes):
    request = {'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'], 'filename': None, 'filenames': None, 'triggers': None,
               'watch_text': None, 'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR',
               'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 20, 'commission': None, 'build_only': False,
               'rebuild': False, 'available_only': True, 'virtual_entry': None}
    request.update(changes)
    return {'supported': True, 'action': 'START', 'request': request, 'job_id': None,
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '확인'}


def ai_snapshot(saved=None):
    return {'today': '2026-10-10', 'generated': [], 'jobs': [],
            'options': {'symbols': ['XAUUSD+'], 'specials': ['SPECIAL2'], 'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY',
                        **({'commission': saved} if saved is not None else {})},
            'execution_defaults': {}, 'execution_version': 'stable'}


def test_part3_ai_takes_the_commission_or_the_saved_one_and_shows_it():
    from jsonschema import Draft202012Validator
    from lab.ai import backtest_commands as commands
    Draft202012Validator(commands.command_schema()).validate(ai_start(commission=7))
    command, question = commands._normalize(ai_start(commission=7), ai_snapshot())
    assert question is None and command['request']['commission'] == 7
    assert '수수료: 1랏 왕복 7달러' in commands._preview(command, ai_snapshot())
    # Not said: the screen's saved commission of that symbol, else 0.
    assert commands._normalize(ai_start(), ai_snapshot({'XAUUSD+': 5}))[0]['request']['commission'] == 5
    assert commands._normalize(ai_start(), ai_snapshot())[0]['request']['commission'] == 0
    with pytest.raises(ValueError, match='수수료는 0 이상'):
        commands._normalize(ai_start(commission=-1), ai_snapshot())
    # A trigger comparison carries it into its lanes plan.
    lanes, _ = commands._normalize(ai_start(commission=7, triggers=['올존', '무지성 올존']), ai_snapshot())
    assert lanes['request']['lane_plan']['commission'] == 7


def test_the_installed_rules_name_the_commission_and_its_lot_size():
    text = (ROOT / '통합설치/releasekit/agent_rules.md').read_text('utf-8')
    for rule in ('`--commission`', '`commission`', '`종목 1랏 크기 (…) 값이 없습니다`', '1랏 크기', '`commission_r`',
                 '랏 수는 정하지 않는다'):
        assert rule in text


def test_the_external_model_gets_the_commission_rule_and_field():
    from common_ai.security import _POLICY_PROMPT, _selection
    from lab.ai import backtest_commands as commands
    assert 'commission은 가상진입 수수료로 1랏 왕복 달러' in _POLICY_PROMPT and '랏·포지션 크기는 묻지 않는다' in _POLICY_PROMPT
    assert 'commission은 가상진입 수수료로 1랏 왕복 달러' in commands._prompt({})
    assert _selection(ai_snapshot({'XAUUSD+': 5}), ())['options']['commission'] == {'XAUUSD+': 5}
