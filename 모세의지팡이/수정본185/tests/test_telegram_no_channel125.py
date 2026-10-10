"""No alert room (TELEGRAM_CHAT_ID empty): the opted-in 1:1 command chats get the live and economy alerts.

Someone using the bot alone, without a channel or group, turns both settings on and gets live, economy
and their own WATCH alerts in the 1:1 chat. With the settings off nothing changes: a broadcast still
has nowhere to go. With an alert room everything is as in 수정본124.
"""
import json
import socket
import threading
from types import SimpleNamespace

import pytest

import engine_harness114 as eh
from engine_harness114 import Engine, run_cycle
from event_composition import NotificationPort
from event_engine import Kind
from event_host import EconomyOutputPort
from manager_KIM import SignalOutput

ME, FRIEND, GROUP = '555', '777', '-2002'
ON, OFF = 'true', 'false'


def settings(room='', live=OFF, economy=OFF):
    return {'TELEGRAM_CHAT_ID': room, 'TELEGRAM_COMMAND_CHAT_IDS': f'{ME},{FRIEND},{GROUP}',
            'PRIVATE_LIVE_ALERTS_ENABLED': live, 'PRIVATE_ECONOMY_ALERTS_ENABLED': economy}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('no network in tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def output(config, seen=None):
    sent = []

    def transport(data):
        sent.append(data['chat_id'])
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': 900 + len(sent)}})
    observer = (lambda event, chat, status: seen.append((chat, status))) if seen is not None else None
    return SignalOutput(config, transport=transport, result_observer=observer), sent


def signal(signal_id, strategy='SPECIAL1', recipients=None):
    content = {'type': 'NOTIFICATION', 'message': 'alert ' + signal_id}
    if recipients is not None:
        content['recipients'] = list(recipients)
    return SimpleNamespace(kind=Kind.SIGNAL, source_time=1790380680000,
                           payload={'signal_id': signal_id, 'symbol': 'XAUUSD+', 'strategy': strategy, 'content': content})


# --- 김매니저: who gets a broadcast without an alert room -------------------------------------------

def test_without_a_room_live_alerts_go_to_the_opted_in_1to1_chats():
    seen = []
    out, sent = output(settings(live=ON), seen)
    out.accept(signal('a', recipients=['']))
    out.accept(signal('b'))
    assert sent == [ME, FRIEND, ME, FRIEND], 'the group on the command list is not a 1:1 chat'
    assert ('', '전송 실패') not in seen


def test_without_a_room_and_settings_off_nothing_is_sent_as_before():
    seen = []
    out, sent = output(settings(), seen)
    out.accept(signal('a', recipients=['']))
    out.accept(signal('e', strategy='ECONOMY_HOST', recipients=['']))
    assert sent == []
    assert seen == [('', '전송 실패'), ('', '전송 실패')]


@pytest.mark.parametrize('live,economy,expected_live,expected_economy', [
    (ON, OFF, [ME, FRIEND], []), (OFF, ON, [], [ME, FRIEND]), (ON, ON, [ME, FRIEND], [ME, FRIEND])])
def test_without_a_room_each_setting_covers_its_own_alerts(live, economy, expected_live, expected_economy):
    out, sent = output(settings(live=live, economy=economy))
    out.accept(signal('a', recipients=['']))
    assert sent == expected_live
    sent.clear()
    out.accept(signal('e', strategy='ECONOMY_HOST', recipients=['']))
    assert sent == expected_economy


def test_without_a_room_a_reply_stays_in_its_own_chat():
    out, sent = output(settings(live=ON, economy=ON))
    out.accept(signal('w', strategy='WATCH', recipients=[ME]))
    assert sent == [ME]


def test_with_a_room_nothing_changes():
    out, sent = output(settings(room='-1001', live=ON))
    out.accept(signal('a'))
    out.accept(signal('w', strategy='WATCH', recipients=[ME]))
    assert sent == ['-1001', ME, FRIEND, ME]


# --- the notification port: a broadcast is made when someone will get it ----------------------------

def kernel(config):
    return SimpleNamespace(config=config, messages=[], current_event=None, timestamp=1790380680000, manager=None)


@pytest.mark.parametrize('live,made', [(OFF, False), (ON, True)])
def test_without_a_room_a_broadcast_is_made_only_when_1to1_chats_opted_in(live, made):
    k = kernel(settings(live=live))
    assert NotificationPort(k).send('SPECIAL 알림', event_id='e1') is made
    assert [m['recipients'] for m in k.messages] == ([['']] if made else [])


@pytest.mark.parametrize('live', [OFF, ON])
def test_with_a_room_or_a_chat_the_port_is_unchanged(live):
    k = kernel(settings(room='-1001', live=live))
    port = NotificationPort(k)
    assert port.send('SPECIAL 알림', event_id='e1') and port.send('답장', chat_id=ME, event_id='e2')
    assert [m['recipients'] for m in k.messages] == [['-1001'], [ME]]


# --- a real engine: the shipped SPECIAL2 alert reaches the 1:1 chat without a room -----------------

@pytest.mark.parametrize('live,expected', [(OFF, []), (ON, [ME, FRIEND])])
def test_a_special_alert_without_a_room_reaches_the_opted_in_chats(live, expected):
    config = settings(live=live)
    engine = Engine('SPECIAL2', live=True, config=config)
    try:
        alerts = run_cycle(engine)
        assert len(alerts) == (1 if expected else 0)
        out, sent = output({**eh.CONFIG, **config})
        for event in engine.engine.signals:
            out.accept(event)
        assert sent == expected
    finally:
        engine.close()


def test_backtest_always_has_its_own_room_so_the_settings_do_not_matter():
    alerts = []
    for live in (OFF, ON):
        engine = Engine('SPECIAL2', live=False, config={**settings(room='BACKTEST', live=live)})
        try:
            alerts.append([{k: a.get(k) for k in ('recipients', 'direction', 'source_tf', 'event_time', 'message')}
                           for a in run_cycle(engine)])
        finally:
            engine.close()
    assert alerts[0] == alerts[1] and len(alerts[0]) == 1 and list(alerts[0][0]['recipients']) == ['BACKTEST']


# --- economy: delivered means the room got it, or without a room an opted-in 1:1 chat -------------

def economy_host(config):
    out, sent = output(config)
    condition = threading.Condition()

    def post(kind, *, source, source_seq, source_time, payload):
        out.accept(SimpleNamespace(kind=kind, payload=payload, source_time=source_time))
        with condition:
            condition.notify_all()
    host = SimpleNamespace(config=config, inputs=SimpleNamespace(symbols=('XAUUSD+',)), output=out,
                           engine=SimpleNamespace(ingress=SimpleNamespace(post=post)),
                           delivery_condition=condition, stop=threading.Event(), notice_attempts={})
    return EconomyOutputPort(host), sent


@pytest.mark.parametrize('config,expected', [
    (settings(economy=ON), [ME, FRIEND]),
    (settings(room='-1001'), ['-1001']),
    (settings(room='-1001', economy=ON), ['-1001', ME, FRIEND])])
def test_an_economy_notice_is_delivered(config, expected):
    port, sent = economy_host(config)
    assert port.send('경제지표 알림') is True
    assert sent == expected
