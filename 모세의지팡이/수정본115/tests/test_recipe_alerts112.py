"""Visible SPECIAL fields at the registration and output boundaries, offline."""
import copy
from pathlib import Path
import sys

import pytest

from test_recipe_alerts101 import (
    arm, deliver_to_output, dispatch, final_event, manager, rule,
)
from strategy_recipe.registry import load_plugins

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab.ai_compiler import compile_ai


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('No network or real Telegram in alert verification')
    monkeypatch.setattr(socket, 'create_connection', denied)
    for method in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, method, denied)


@pytest.mark.parametrize('name', [*[f'SPECIAL{i}' for i in range(1, 8)], 'Test_SPECIAL001'])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_builtin_and_generated_registration_share_clean_alerts_and_retain_signal_data(name, direction, monkeypatch):
    m, kernel = manager()
    if name.startswith('SPECIAL'):
        plugin = load_plugins({'SYMBOLS': 'TEST'}, selected={name})[name]
        plugin.register(m)
    else:
        from lab.ai import schema
        context = schema.contract_context()
        context['symbols'] = ('TEST',)
        monkeypatch.setattr(schema, 'contract_context', lambda: context)
        recipe = {'schema_version': 2, 'base': 'AI', 'name': '새로운 전략',
            'strategy_intent': {'symbols': ['TEST'], 'direction': direction,
                'order_mode': 'SIMULTANEOUS', 'persistent': True,
                'steps': [{'kind': 'MA_PRICE_TOUCH', 'tfs': ['5m'],
                           'ma_family': 'EMA', 'slow_period': 50}],
                'final': {'kind': 'OZ', 'tfs': ['5m'],
                          'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'}}}
        namespace = {}
        exec(compile(compile_ai(recipe, name + '.py'), name + '.py', 'exec'), namespace)
        namespace['register'](m)
    port = m._strategy_state_providers[name]
    arm(port)
    machine = port.machines[0]
    accepted = final_event(port, direction=direction,
        source_tf=machine.final_tf or '5m',
        validation_mode=machine.meaning['final']['validation_mode'],
        trigger_mode=machine.meaning['final']['trigger_mode'])
    before = copy.deepcopy(accepted)
    assert dispatch(m, accepted)['ok']
    assert accepted == before
    assert len(kernel.messages) == 1
    message = kernel.messages[0]
    sent = deliver_to_output(kernel.messages, name)
    assert len(sent) == 1 and sent[0]['chat_id'] == 'TEST_CHANNEL'
    text = sent[0]['text']
    for unwanted in ('• 조건:', '• 트리거:', '• 등급:', 'BO_BREAK', 'A급'):
        assert unwanted not in text
    for retained in ('• 종목: TEST', '• 주기:', '• 지표: PRICE·RSI·STO',
                     '• 현재 가격: 2,400.25'):
        assert retained in text
    assert message['grade'] == 'A'
    assert message['trigger_name'] == 'BO_BREAK'
    assert message['direction'] == direction
    assert message['source_tf'] == accepted['source_tf']
    assert message['event_time'] == accepted['event_time']
    assert message['signal_strategy'] == name
    assert len(m.completions) == 1


def test_failed_final_condition_remains_silent_after_display_cleanup():
    from strategy_recipe.port import IntentPort
    m, kernel = manager()
    port = IntentPort(m, rule(final_conditions=[{'kind': 'CANDLE_STATE', 'tfs': ['5m'],
        'side': 'BULL', 'bar_state': 'FORMING'}]), '조건 검증 전략', 'USER_REJECT')
    port.install()
    arm(port)
    port.observe = lambda *args: {'ready': True, 'matched': False, 'event': False,
        'token': 'rejected', 'at': 1490, 'source_tf': '5m'}
    result = dispatch(m, final_event(port))
    assert result['ok'] and not kernel.messages and not m.completions
    assert port.machines[0].active
