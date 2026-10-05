"""SPECIAL2-style alerts name the external-liquidity level that was actually touched.

The declared selector ("ALL") is a rule input, never a place. Judgement is not
under test here; only that the accepted touch reaches the alert text unchanged.
"""
import copy
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from event_composer_domain import ComposerManager, SpecialPluginAPI
from event_composition import NotificationPort
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe.alerts import condition_text, render_strategy_alert
from strategy_recipe.contract import execution_plan
from strategy_recipe.port import IntentPort
from strategy_recipe.runtime import IntentMachine

SPECIAL2 = ROOT / 'Part1/program/SPECIAL/SPECIAL2.recipe.json'
NOW = 1500.


class Board:
    def __init__(self):
        values = np.full((3, len(PIPE_VALUE_COLUMNS)), 2400., dtype=float)
        self.feeds = {('TEST', '5m'): NS(time=np.array([900, 1200, 1500]),
            values=values, volume=np.ones(3), source_epoch='feed', seq=3)}
        self.health = {}; self.observed = {('TEST', '5m'): 1500000}
        self.source_time = 1500000; self._frame_cache = {}; self.publication_token = 1
    def snapshot(self, symbol, tf): return self.feeds[symbol, tf]


def manager():
    m = ComposerManager.__new__(ComposerManager)
    m._lock = threading.RLock(); m._delivery_context = threading.local()
    m.chat_id = 'TEST_CHANNEL'; m.config = {}
    m._special_shared_resources = {}; m._active_children = {}; m._config_chain_active = {}
    m._special_oz_event_handlers = {}; m._special_oz_registration_scopes = []
    m._special_subscription_providers = {}; m._special_fact_observers = {}
    m._strategy_state_providers = {}; m._special_watch_handlers = {}
    m.official_specs = {}; m.official_chain_specs = {}
    m._save_active_children_state_locked = lambda: None
    m.commands = []; m._push = m.commands.append
    m._watch_links_for_ids = lambda *args: []
    kernel = NS(board=Board(), symbol='TEST', timestamp=1500000, config={'TELEGRAM_CHAT_ID': m.chat_id},
        current_event={}, messages=[], manager=m, initial_files={})
    m.event_services = NS(kernel=kernel)
    m.notifier = NotificationPort(kernel)
    m.special_api = SpecialPluginAPI(m)
    m._time_policy = NS(allows=lambda filters: True)
    return m


def special2_meaning():
    entry = json.loads(SPECIAL2.read_text('utf-8-sig'))
    raw = copy.deepcopy(entry['recipe']['strategy_intent']); raw['symbols'] = ['TEST']
    return execution_plan(raw)['meaning']


def touch_fact(code, name, price, direction, at=1400.):
    return {'kind': 'SWEEP_TOUCH', 'strategy': 'SWEEP', 'symbol': 'TEST', 'source_tf': '5m',
        'direction': direction, 'level_code': code, 'level_name': name, 'level_id': f'{code}:x',
        'level_price': price, 'touch_time': at, 'event_time': at}


def observe_special2(facts, direction='LONG'):
    """Real SPECIAL2 recipe + real IntentPort.observe over an injected SWEEP Fact."""
    port = IntentPort(manager(), special2_meaning(), 'SPECIAL2', 'SPECIAL2')
    machine = next(m for m in port.machines if m.direction == direction and m.meaning['steps'][0]['tfs'] == ['5m'])
    step = machine.meaning['steps'][0]
    watch_id = port._dependency_id('TEST', '5m', 'SWEEP', step)
    key = ('SWEEP', 'TEST', '5m', watch_id)
    port.fact_snapshots[key] = {'kind': 'FACT_SNAPSHOT', 'strategy': 'SWEEP', 'symbol': 'TEST',
        'source_tf': '5m', 'watch_id': watch_id, 'facts': facts, 'source_health': {'sources': {'5m': 'feed'}}}
    port.fact_sources[key] = {'5m': ('feed', 3)}
    board = port.api.market_context()[0]
    return machine, port.observe(board, 'TEST', step, direction, NOW, machine)


def location(text):
    return next(line for line in text.splitlines() if line.startswith('• 위치:'))


def test_special2_recipe_still_declares_all_and_alert_never_shows_it():
    # The trading condition keeps its selector; only the alert text changes.
    entry = json.loads(SPECIAL2.read_text('utf-8-sig'))
    branches = entry['recipe']['strategy_intent']['branches']
    assert [b['steps'][0]['level'] for b in branches] == ['ALL', 'ALL']
    machine, observation = observe_special2([touch_fact('PDL', '전일저가(PDL)', 3842.15, 'LONG')])
    assert observation['matched'] and machine.meaning['steps'][0]['level'] == 'ALL'
    text = render_strategy_alert('외부유동성 스윕', machine, {'symbol': 'TEST', 'direction': 'LONG',
        'source_tf': '5m', 'current_price': 3850.}, observations=[observation])
    assert location(text) == '• 위치: 전일 저가 · 3,842.15'
    assert 'ALL' not in text


def test_special2_short_branch_names_the_touched_high():
    machine, observation = observe_special2(
        [touch_fact('PREV_4H_HIGH', '4시간봉 이전봉 고가', 25941.3, 'SHORT')], direction='SHORT')
    text = render_strategy_alert('외부유동성 스윕', machine, {'symbol': 'TEST', 'direction': 'SHORT',
        'source_tf': '5m'}, observations=[observation])
    assert location(text) == '• 위치: 4시간 고가 · 25,941.30'


def test_observation_carries_code_name_and_price_without_changing_the_judgement():
    _, observation = observe_special2([touch_fact('PWL', '주봉저가', 3700., 'LONG', at=1300.)])
    assert observation['matched'] and observation['at'] == 1300.
    assert observation['touched_levels'] == ({'id': 'PWL:x', 'code': 'PWL', 'name': '주봉저가', 'price': 3700., 'at': 1300.},)
    # A level the step does not select, or the other direction, is not a touch.
    for facts in ([], [touch_fact('PDH', '전일고가(PDH)', 3900., 'SHORT')]):
        _, other = observe_special2(facts)
        assert not other['matched'] and not other['touched_levels']


@pytest.mark.parametrize('code,name,direction,expected', [
    ('PDL', '전일저가(PDL)', 'LONG', '전일 저가'), ('PDH', '전일고가(PDH)', 'SHORT', '전일 고가'),
    ('PWL', '주봉저가', 'LONG', '전주 저가'), ('PWH', '주봉고가', 'SHORT', '전주 고가'),
    ('PREV_4H_LOW', '4시간봉 이전봉 저가', 'LONG', '4시간 저가'),
    ('PREV_4H_HIGH', '4시간봉 이전봉 고가', 'SHORT', '4시간 고가'),
    ('PREV_8H_LOW', '8시간봉 이전봉 저가', 'LONG', '8시간 저가'),
    ('PREV_8H_HIGH', '8시간봉 이전봉 고가', 'SHORT', '8시간 고가'),
    ('PREV_SESSION_LOW', '이전 세션 저가', 'LONG', '이전 세션 저가'),
    ('PREV_SESSION_HIGH', '이전 세션 고가', 'SHORT', '이전 세션 고가')])
def test_every_level_the_sweep_produces_gets_a_korean_name(code, name, direction, expected):
    machine = IntentMachine(special2_meaning()['branches'][0], 'TEST', direction)
    step = {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], 'side': 'LOW' if direction == 'LONG' else 'HIGH',
            'level': 'ALL', '_resolved_direction': direction}
    observation = {'matched': True, 'touched_levels': ({'code': code, 'name': name, 'price': 1234.5, 'at': 1.},)}
    assert condition_text(step, machine, observation) == f'{expected} · 1,234.50'


def test_several_active_touches_show_the_outermost_level():
    machine = IntentMachine(special2_meaning()['branches'][0], 'TEST', 'LONG')
    low = {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], 'side': 'LOW', 'level': 'ALL', '_resolved_direction': 'LONG'}
    high = dict(low, side='HIGH', _resolved_direction='SHORT')
    levels = ({'code': 'PDL', 'name': 'x', 'price': 3800., 'at': 5.},
              {'code': 'PREV_4H_LOW', 'name': 'x', 'price': 3790., 'at': 1.},
              {'code': 'PWL', 'name': 'x', 'price': 3790., 'at': 9.})
    assert condition_text(low, machine, {'matched': True, 'touched_levels': levels}) == '전주 저가 · 3,790.00'
    assert condition_text(high, machine, {'matched': True, 'touched_levels': levels}) == '전일 저가 · 3,800.00'


def test_without_an_actual_touch_the_declared_selector_is_not_printed_as_a_place():
    machine = IntentMachine(special2_meaning()['branches'][0], 'TEST', 'LONG')
    step = {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['1m'], 'side': 'LOW', 'level': 'ALL'}
    for observation in (None, {'matched': False, 'touched_levels': ()},
                        {'matched': False, 'touched_levels': ({'code': 'PDL', 'name': 'x', 'price': 1., 'at': 1.},)}):
        assert condition_text(step, machine, observation) == '1분 하단 외부유동성 터치'
    negated = dict(step, negated=True)
    touched = {'matched': True, 'touched_levels': ({'code': 'PDL', 'name': 'x', 'price': 1., 'at': 1.},)}
    assert condition_text(negated, machine, touched) == '1분 미성립: 하단 외부유동성 터치'


def test_explicit_level_selection_keeps_its_existing_text():
    machine = IntentMachine(special2_meaning()['branches'][0], 'TEST', 'LONG')
    step = {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], 'side': 'LOW', 'level': 'PDL'}
    assert condition_text(step, machine) == '5분 하단 외부유동성 PDL 터치'
    assert condition_text(dict(step, level=['PDL', '4H']), machine) == '5분 하단 외부유동성 PDL/4H 터치'
