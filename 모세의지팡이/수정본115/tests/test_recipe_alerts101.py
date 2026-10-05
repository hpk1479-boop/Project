"""Recipe main-channel messages at the real Composer/output boundary, offline."""
import copy
import json
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from composer_oz_dispatch import dispatch
from event_composer_domain import ComposerManager, SpecialPluginAPI
from event_composition import NotificationPort
from event_engine.model import Event, Kind
from manager_KIM import SignalOutput
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe.alerts import render_strategy_alert, SEPARATOR, condition_text
from strategy_recipe.contract import execution_plan, KINDS
from strategy_recipe.port import IntentPort
from strategy_recipe.runtime import IntentMachine


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Actual network is prohibited in alert tests')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


class Board:
    def __init__(self):
        values = np.full((3, len(PIPE_VALUE_COLUMNS)), 2400., dtype=float)
        self.feeds = {('TEST', '5m'): NS(time=np.array([900, 1200, 1500]),
            values=values, volume=np.ones(3), source_epoch='feed', seq=3)}
        self.health = {}; self.observed = {('TEST', '5m'): 1500000}
        self.source_time = 1500000; self._frame_cache = {}; self.publication_token = 1
    def snapshot(self, symbol, tf): return self.feeds[symbol, tf]


def rule(final='OZ', **extras):
    data = {'symbols': ['TEST'], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS',
        'persistent': True, 'steps': [{'kind': 'MA_PRICE_TOUCH', 'tfs': ['5m'],
            'ma_family': 'EMA', 'slow_period': 50}],
        'final': {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'}
                 if final == 'OZ' else {'kind': 'NOTIFY'}}
    data.update(extras)
    return execution_plan(data)['meaning']


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
    m._filter_config_chain_deadline_event = lambda e: (e, False)
    m.completions = []
    m.watch_orchestrator = NS(handle_oz_stage_event=lambda e: {'handled': False, 'all_internal': False},
        filter_deadline_event=lambda e: (e, False),
        complete_final_oz=lambda e, **kwargs: m.completions.append(e))
    kernel = NS(board=Board(), symbol='TEST', timestamp=1500000, config={'TELEGRAM_CHAT_ID': m.chat_id},
        current_event={}, messages=[], manager=m, initial_files={})
    m.event_services = NS(kernel=kernel)
    m.notifier = NotificationPort(kernel)
    m.special_api = SpecialPluginAPI(m)
    m._time_policy = NS(allows=lambda filters: True)
    return m, kernel


def final_event(port, **extras):
    machine = port.machines[0]
    data = {'kind': 'FINAL_ALERT', 'strategy': 'OZ', 'source_spec_id': port._sid(machine),
        'source_spec_ids': [port._sid(machine)], 'watch_ids': [port._wid(machine)],
        'direction': 'LONG', 'symbol': 'TEST', 'source_tf': '5m',
        'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER',
        'current_price': 2400.25, 'grade': 'A', 'indicators_text': 'PRICE·RSI·STO',
        'trigger_name': 'BO_BREAK', 'event_time': 1500, 'event_id': 'market-one',
        'message': 'old watch message'}
    data.update(extras)
    return data


def arm(port):
    m = port.machines[0]
    m.active = True; m.after_ready = True; m.setup_time = 1490
    for step in m.meaning['steps']:
        # A setup records the external-liquidity touch it armed on; a forced one must too.
        if step.get('capture') and step['kind'] == 'EXTERNAL_LIQUIDITY_TOUCH':
            m.captures[step['capture']] = {'kind': 'LIQUIDITY', 'id': 'PDL:x', 'tf': step['tfs'][0], 'code': 'PDL',
                'name': 'x', 'price': 2390., 'at': 1480., 'direction': m.direction,
                'watch_id': port._dependency_id('TEST', step['tfs'][0], 'SWEEP', step)}
    port._arm_watch(m)
    port.observe = lambda *args: {'ready': True, 'matched': True, 'event': True,
        'token': 'accepted', 'at': 1490, 'source_tf': '5m'}
    port.poll = lambda: None


def deliver_to_output(messages, namespace):
    sent = []
    def transport(data):
        sent.append(data)
        return NS(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': len(sent)}})
    output = SignalOutput({}, transport=transport)
    for index, message in enumerate(messages, 1):
        event = Event(index, 0, 'test', index, 1500000, Kind.SIGNAL,
            {'symbol': 'TEST', 'strategy': namespace, 'signal_id': message['event_id'],
             'content': {'type': 'NOTIFICATION', **message}})
        output.accept(event); output.accept(event)
    return sent


def test_main_recipe_final_reaches_channel_with_special_template_and_no_registration_ack():
    m, kernel = manager()
    port = IntentPort(m, rule(), '골드 추세 전략', 'USER_ALPHA')
    port.install(); arm(port)
    watch = m._active_children[port._wid(port.machines[0])]
    assert watch['request_chat_id'] is None
    response = m._handle_oz_event_core({'kind': 'CONTROL_ACK', 'watch_id': watch['watch_id'],
        'request_chat_id': watch['request_chat_id'], 'message': '✅ 등록됨'})
    assert response['suppressed'] and not kernel.messages
    result = dispatch(m, final_event(port))
    assert result['ok'] and len(kernel.messages) == 1
    sent = deliver_to_output(kernel.messages, 'USER_ALPHA')
    assert len(sent) == 1 and sent[0]['chat_id'] == 'TEST_CHANNEL'
    assert sent[0]['text'] == '\n'.join([
        '[골드 추세 전략] 🟢 매수 신호 발생', SEPARATOR,
        '• 종목: TEST', '• 위치: 5분 EMA50 가격 터치',
        '• 주기: 5분 무지성 브레이커 올존',
        '• 지표: PRICE·RSI·STO', '• 현재 가격: 2,400.25', SEPARATOR])
    assert len(m.completions) == 1 and not m._active_children
    assert kernel.messages[0]['signal_strategy'] == 'USER_ALPHA'


def test_private_watch_keeps_original_message_and_recipient():
    m, kernel = manager()
    original = {'kind': 'FINAL_ALERT', 'symbol': 'TEST', 'direction': 'SHORT',
        'source_tf': '5m', 'watch_ids': [], 'request_chat_id': 'PRIVATE_USER',
        'message': '🔔 5분 올존 A급 SHORT · TEST · B0'}
    assert m._handle_oz_event_core(original)['delivered']
    assert kernel.messages[0]['message'] == original['message']
    assert kernel.messages[0]['recipients'] == ['PRIVATE_USER']
    assert '• 조건:' not in kernel.messages[0]['message']


def test_main_notify_uses_same_frame_without_fake_oz_values():
    m, kernel = manager()
    port = IntentPort(m, rule('NOTIFY'), '가격 터치 전략', 'USER_NOTIFY')
    port.observe = lambda *args: {'ready': True, 'matched': True, 'event': True,
        'token': 'touch-one', 'at': 1500, 'source_tf': '5m'}
    port.poll(); port.poll()
    assert len(kernel.messages) == 1
    text = kernel.messages[0]['message']
    assert text.startswith('[가격 터치 전략] 🟢 매수 신호 발생\n' + SEPARATOR)
    assert '• 현재 가격: 2,400.00' in text
    assert '• 주기: 5분' in text
    assert '급' not in text and '올존' not in text and '• 트리거:' not in text and '• 지표:' not in text
    sent = deliver_to_output(kernel.messages, 'USER_NOTIFY')
    assert len(sent) == 1 and sent[0]['chat_id'] == m.chat_id


def test_internal_oz_source_is_silent_and_new_watch_migrates_previous_channel_recipient():
    m, kernel = manager()
    plan = rule(steps=[{'kind': 'OZ_ALERT', 'tfs': ['5m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}])
    port = IntentPort(m, plan, '체인 전략', 'USER_CHAIN')
    dependency = port._dependency_id('TEST', '5m', 'OZ_SOURCE', plan['steps'][0])
    old = {'action': 'MANUAL_WATCH', 'watch_id': dependency, 'symbol': 'TEST', 'timeframes': ['5m'],
        'request_chat_id': m.chat_id, 'persistent': True, 'watch_owner': 'KIM',
        'validation_mode': 'NORMAL', 'trigger_mode': 'OZ', 'source_name': '옛 이름', 'source_spec_id': dependency}
    m._active_children[dependency] = copy.deepcopy(old)
    port.install()
    changed = m._active_children[dependency]
    assert changed['request_chat_id'] is None and changed['source_name'] == '체인 전략'
    port.poll = lambda: None
    source_event = final_event(port, source_spec_id=dependency, source_spec_ids=[dependency],
        watch_ids=[dependency], b0_time=1400, b0_price=2399.)
    assert dispatch(m, source_event)['ok'] and not kernel.messages
    assert port.oz_sequence == 1


@pytest.mark.parametrize('direction,icon,side', [('LONG', '🟢', '매수'), ('SHORT', '🔴', '매도'), ('BOTH', '⚪', '조건')])
def test_heading_uses_actual_direction_and_missing_fields_are_not_invented(direction, icon, side):
    machine = IntentMachine(rule(), 'TEST', direction)
    text = render_strategy_alert('새 이름', machine, {'direction': direction, 'symbol': 'TEST'})
    assert f'[{"새 이름"}] {icon} {side}' in text
    assert '• 현재 가격: -' in text
    assert '• 등급:' not in text and '• 지표:' not in text and '• 트리거:' not in text


def test_fvg_capture_context_comes_from_accepted_resource_and_branch_has_no_id_dispatch():
    plan = rule(steps=[{'kind': 'FVG_TOUCH', 'tfs': ['1h'], 'side': 'BULL', 'capture': 'zone'}])
    machine = IntentMachine(plan, 'TEST', 'LONG')
    machine.captures['zone'] = {'kind': 'FVG', 'tf': '1h', 'lower': 2380., 'upper': 2390.}
    text = render_strategy_alert('상승 FVG', machine, {'current_price': 2385., 'source_tf': '5m'})
    assert '1시간 상승 FVG 터치 (2,380.00~2,390.00)' in text
    helper_source = (ROOT / 'Part1/program/strategy_recipe/alerts.py').read_text('utf-8')
    assert not any('SPECIAL' + str(index) in helper_source for index in range(1, 8))


def test_every_builtin_rule_can_render_with_actual_final_fields():
    registry = json.loads((ROOT / 'settings/strategy_registry.json').read_text('utf-8-sig'))
    kinds = set()
    for entry in registry['presets']:
        raw = copy.deepcopy(entry['recipe']['strategy_intent']); raw['symbols'] = ['TEST']
        lowered = execution_plan(raw)['meaning']
        for branch in lowered.get('branches') or [lowered]:
            machine = IntentMachine(branch, 'TEST', 'LONG')
            machine.source_tf = next((tf for step in branch['steps'] for tf in step['tfs'] if tf not in ('SOURCE', 'FINAL')), None)
            machine.final_tf = '5m'
            for key in ('steps', 'after_conditions', 'final_conditions'):
                for step in branch.get(key, ()): kinds.add(step['kind'])
            text = render_strategy_alert(entry['name'], machine, {'direction': 'LONG', 'source_tf': '5m',
                'current_price': 2400., 'grade': 'A', 'validation_mode': branch['final']['validation_mode'],
                'trigger_mode': branch['final']['trigger_mode'], 'indicators_text': 'PRICE·RSI·STO'})
            assert text.startswith('[' + entry['name'] + ']') and text.endswith(SEPARATOR)
            assert '• 주기: 5분 ' in text and '• 현재 가격: 2,400.00' in text
            assert 'SOURCE' not in text and 'FINAL' not in text
    assert {'FVG_TOUCH', 'WONBI_TOUCH', 'MA_STATE', 'EXTERNAL_LIQUIDITY_TOUCH'} <= kinds


def test_render_is_pure_and_renaming_changes_only_label():
    plan = rule()
    machine = IntentMachine(plan, 'TEST', 'LONG')
    event = {'direction': 'LONG', 'source_tf': '5m', 'current_price': 2400.}
    before_machine = machine.checkpoint(); before_plan = copy.deepcopy(plan); before_event = copy.deepcopy(event)
    old = render_strategy_alert('원래 이름', machine, event)
    new = render_strategy_alert('바뀐 이름', machine, event)
    assert old.splitlines()[1:] == new.splitlines()[1:]
    assert old.startswith('[원래 이름]') and new.startswith('[바뀐 이름]')
    assert machine.checkpoint() == before_machine and plan == before_plan and event == before_event


def test_registry_reload_uses_renamed_title_in_channel_alert_and_watch(monkeypatch):
    from strategy_recipe import registry
    raw = rule()
    for step in raw['steps']:
        for key in tuple(step):
            if key.startswith('_'): del step[key]
    entry = {'name': '승급 전 이름', 'symbol_source': 'RECIPE', 'time_filters': [],
        'recipe': {'schema_version': 2, 'base': 'AI', 'strategy_intent': raw}}
    monkeypatch.setattr(registry, 'entries', lambda *args, **kwargs: {'USER_RENAMED': copy.deepcopy(entry)})
    for title in ('승급 전 이름', '변경한 전략 이름'):
        entry['name'] = title
        m, kernel = manager()
        plugins = registry.load_plugins({'SYMBOLS': 'TEST'})
        plugins['USER_RENAMED'].register(m)
        port = m._strategy_state_providers['USER_RENAMED']
        arm(port)
        assert m._active_children[port._wid(port.machines[0])]['source_name'] == title
        assert dispatch(m, final_event(port))['ok']
        assert kernel.messages[0]['message'].startswith('[' + title + ']')


def test_failed_delivery_does_not_consume_one_shot_lifecycle():
    m, kernel = manager()
    port = IntentPort(m, rule(persistent=False), '재시도 전략', 'USER_RETRY')
    port.install(); arm(port)
    m.notifier.send = lambda *args, **kwargs: False
    result = dispatch(m, final_event(port))
    assert not result['ok'] and not port.machines[0].disabled
    assert port._wid(port.machines[0]) in m._active_children
    assert not kernel.messages and not m.completions


@pytest.mark.parametrize('order,combine', [('SEQUENTIAL', 'ALL'), ('SIMULTANEOUS', 'ANY')])
def test_hidden_conditions_do_not_change_declared_order_or_combination(order, combine):
    plan = rule(steps=[{'kind': 'BAR_CLOSE', 'tfs': ['30m']},
        {'kind': 'MA_PRICE_TOUCH', 'tfs': ['5m'], 'ma_family': 'EMA', 'slow_period': 50}],
        order_mode=order, global_combine=combine)
    machine = IntentMachine(plan, 'TEST', 'LONG')
    before = copy.deepcopy(plan)
    text = render_strategy_alert('순서 전략', machine, {'source_tf': '5m'})
    assert '• 위치: 5분 EMA50 가격 터치' in text
    assert '• 조건:' not in text and '30분 봉 마감' not in text
    assert machine.meaning == plan == before


def test_every_supported_condition_has_readable_presentation():
    examples = {
        'TREND': {},
        'CANDLE_STATE': {'side': 'BULL', 'bar_state': 'FORMING'},
        'TREND_METRIC': {'metric': 'test_metric', 'metric_operator': 'GTE', 'metric_value': 2},
        'MA_STATE': {'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200},
        'MA_PRICE_STATE': {'ma_family': 'EMA', 'slow_period': 50},
        'MA_SLOPE_STATE': {'ma_family': 'HMA', 'slow_period': 168, 'lookback': 2},
        'MA_CROSS': {'ma_left': 'HMA6', 'ma_right': 'HMA17'},
        'MA_PRICE_CROSS': {'ma_family': 'EMA', 'slow_period': 50, 'relation': 'BREAK_UP'},
        'MA_PRICE_TOUCH': {'ma_family': 'EMA', 'slow_period': 50},
        'FVG_STATE': {'state': 'EXISTS'}, 'FVG_NEW': {}, 'FVG_TOUCH': {},
        'WONBI_TOUCH': {}, 'EXTERNAL_LIQUIDITY_TOUCH': {'level': 'PDL', 'side': 'LOW'},
        'SESSION_START': {'session': 'MAIN_ASIA'}, 'BAR_CLOSE': {},
        'OZ_ALERT': {'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'},
        'REGIME_BAND': {'regime_families': ['PRICE', 'RSI'], 'relation': 'IN'},
        'PERCENTILE_OUT': {'families': ['PRICE']}, 'PERCENTILE_OUT_IN': {'families': ['RSI']},
        'PRICE_LEVEL': {'level': 2400., 'relation': 'BREAK_UP'},
        'LIQUIDITY_LEVEL': {'level': 'PDH', 'relation': 'TOUCH'}}
    assert set(examples) == KINDS
    machine = IntentMachine(rule(), 'TEST', 'LONG')
    for kind, values in examples.items():
        text = condition_text({'kind': kind, 'tfs': ['5m'], **values}, machine)
        assert text.startswith('5분') and kind not in text
