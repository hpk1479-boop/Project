"""Telegram routing: commands only from 1:1 command chats; alert-room messages optionally copied to them.

Only the host's input routing and 김매니저's recipient list change. The signal itself is untouched, so
strategies, WATCH and the backtest record (written from the signal's own recipients) stay the same.
"""
import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / 'Part1/program'), str(R / 'Part2')]
from command_interpreter import CommandInterpreter
from event_application import create_event_engine
from event_engine import Kind
from event_host import EventHost, HTTPServices
from manager_KIM import SignalOutput

ROOM = '-1001'               # the alert room (channel or group)
GROUP = '-2002'              # a group someone put on the command list
ME, FRIEND = '555', '777'    # 1:1 command chats
ON, OFF = 'true', 'false'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('no network in tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


# --- input: only 1:1 command chats reach the interpreter --------------------------------------------

def host():
    config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'WONBI_SIGMA': '3',
              'TELEGRAM_CHAT_ID': ROOM, 'TELEGRAM_COMMAND_CHAT_IDS': f'{ME},{GROUP}'}
    engine = create_event_engine(config, enabled_specials=())
    sent = []

    def transport(data):
        sent.append(dict(data))
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': 900 + len(sent)}})
    return engine, EventHost(engine, config, CommandInterpreter(config), transport=transport), sent


def update(chat_id, chat_type, text, key='message', mid=10):
    return {'update_id': mid, key: {'chat': {'id': chat_id, 'type': chat_type}, 'text': text,
                                     'message_id': mid, 'date': 1790380679}}


class Untouchable:
    def __getattr__(self, name):
        raise AssertionError('a blocked message reached the interpreter: ' + name)


def test_a_command_from_a_1to1_command_chat_works():
    engine, h, sent = host()
    assert h.inputs.telegram_update(update(int(ME), 'private', '골드 1분 상단 원비 터치 알려줘'))
    engine.run(); h.drain_outputs()
    assert not engine.error_log, engine.error_log
    assert sent and sent[0]['chat_id'] == ME and '감시' in sent[0]['text']


@pytest.mark.parametrize('blocked', [
    update(int(GROUP), 'group', '골드 1분 상단 원비 터치 알려줘'),       # a group, even on the command list
    update(-3003, 'group', '오늘 시장 어때요'),
    update(int(GROUP), 'supergroup', '취소', mid=11),
    update(int(GROUP), 'supergroup', '골드 1분 올존 알려줘'),
    update(int(ROOM), 'channel', '골드 1분 올존 알려줘', key='channel_post'),   # the alert room itself
    update(int(ROOM), 'channel', '취소', key='channel_post'),
    update(int(ME), 'group', '골드 1분 올존 알려줘'),                       # the right number, not a 1:1 chat
    update(int(FRIEND), 'private', '골드 1분 올존 알려줘'),                  # 1:1, not on the command list
])
def test_messages_outside_1to1_command_chats_are_never_commands(blocked):
    engine, h, sent = host()
    h.inputs.interpreter = Untouchable()
    assert h.inputs.telegram_update(blocked) is False
    assert len(engine.ingress) == 0
    engine.run(); h.drain_outputs()
    assert sent == []


def test_the_poller_asks_telegram_for_chat_messages_only():
    stop, calls = threading.Event(), []

    def get(url, params=None, timeout=None):
        calls.append(params)
        if len(calls) > 1:
            stop.set()
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'result': []})
    services = HTTPServices({'TELEGRAM_TOKEN': 'T'}, None)
    services.http = SimpleNamespace(get=get)
    services.telegram_loop(SimpleNamespace(telegram_update=lambda u: None), stop)
    assert json.loads(calls[-1]['allowed_updates']) == ['message']


# --- output: the alert room as before, 1:1 command chats when they opted in ------------------------

def output(observer=None, **settings):
    config = {'TELEGRAM_CHAT_ID': ROOM, 'TELEGRAM_COMMAND_CHAT_IDS': f'{ME},{FRIEND},{GROUP}', **settings}
    config = {k: v for k, v in config.items() if v is not None}
    sent = []

    def transport(data):
        sent.append(data['chat_id'])
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': 900 + len(sent)}})
    return SignalOutput(config, transport=transport, result_observer=observer), sent


def signal(signal_id, strategy='SPECIAL1', recipients=None):
    content = {'type': 'NOTIFICATION', 'message': 'alert ' + signal_id}
    if recipients is not None:
        content['recipients'] = list(recipients)
    return SimpleNamespace(kind=Kind.SIGNAL, source_time=1790380680000,
                           payload={'signal_id': signal_id, 'symbol': 'XAUUSD+', 'strategy': strategy, 'content': content})


@pytest.mark.parametrize('settings', [{}, {'PRIVATE_LIVE_ALERTS_ENABLED': OFF, 'PRIVATE_ECONOMY_ALERTS_ENABLED': OFF}])
def test_by_default_alerts_go_to_the_alert_room_only(settings):
    out, sent = output(**settings)
    out.accept(signal('a', recipients=[ROOM]))
    out.accept(signal('b'))                                   # no recipients: the alert room
    out.accept(signal('e', strategy='ECONOMY_HOST', recipients=[ROOM]))
    assert sent == [ROOM, ROOM, ROOM]


def test_private_live_alerts_on_copies_the_alert_to_each_1to1_command_chat():
    seen = []
    out, sent = output(lambda event, chat, status: seen.append((chat, status)), PRIVATE_LIVE_ALERTS_ENABLED=ON)
    out.accept(signal('a'))
    assert sent == [ROOM, ME, FRIEND], 'a group on the command list is not a 1:1 chat'
    assert seen == [(ROOM, '전송됨'), (ME, '전송됨'), (FRIEND, '전송됨')]


@pytest.mark.parametrize('live,economy,expected', [
    (OFF, OFF, [ROOM]), (ON, OFF, [ROOM]), (OFF, ON, [ROOM, ME, FRIEND]), (ON, ON, [ROOM, ME, FRIEND])])
def test_economy_alerts_follow_their_own_setting(live, economy, expected):
    out, sent = output(PRIVATE_LIVE_ALERTS_ENABLED=live, PRIVATE_ECONOMY_ALERTS_ENABLED=economy)
    out.accept(signal('e', strategy='ECONOMY_HOST', recipients=[ROOM]))
    assert sent == expected


def test_live_alerts_do_not_follow_the_economy_setting():
    out, sent = output(PRIVATE_ECONOMY_ALERTS_ENABLED=ON)
    out.accept(signal('a', recipients=[ROOM]))
    assert sent == [ROOM]


@pytest.mark.parametrize('commands', [f'{ME},{FRIEND}', None])     # None: no command list, the alert chat commands
def test_a_chat_that_is_both_alert_room_and_command_chat_gets_one_copy(commands):
    out, sent = output(TELEGRAM_CHAT_ID=ME, TELEGRAM_COMMAND_CHAT_IDS=commands,
                       PRIVATE_LIVE_ALERTS_ENABLED=ON, PRIVATE_ECONOMY_ALERTS_ENABLED=ON)
    out.accept(signal('a'))
    out.accept(signal('e', strategy='ECONOMY_HOST', recipients=[ME]))
    expected = [ME, FRIEND] if commands else [ME]
    assert sent == expected + expected


def test_a_signal_seen_again_is_not_sent_again_to_anyone():
    out, sent = output(PRIVATE_LIVE_ALERTS_ENABLED=ON)
    event = signal('a')
    out.accept(event); out.accept(event)
    assert sent == [ROOM, ME, FRIEND]


def test_a_reply_to_one_command_chat_is_not_copied_anywhere():
    out, sent = output(PRIVATE_LIVE_ALERTS_ENABLED=ON, PRIVATE_ECONOMY_ALERTS_ENABLED=ON)
    out.accept(signal('w', strategy='WATCH', recipients=[ME]))
    assert sent == [ME]


def test_a_failed_copy_does_not_stop_the_others():
    sent = []

    def transport(data):
        sent.append(data['chat_id'])
        ok = data['chat_id'] != ME
        return SimpleNamespace(status_code=200 if ok else 403, json=lambda: {'ok': ok, 'result': {'message_id': len(sent)}})
    out = SignalOutput({'TELEGRAM_CHAT_ID': ROOM, 'TELEGRAM_COMMAND_CHAT_IDS': f'{ME},{FRIEND}',
                        'PRIVATE_LIVE_ALERTS_ENABLED': ON}, transport=transport)
    assert [r['recipient'] for r in out.accept(signal('a'))] == [ROOM, FRIEND]


def test_the_signal_itself_is_unchanged():
    out, _ = output(PRIVATE_LIVE_ALERTS_ENABLED=ON, PRIVATE_ECONOMY_ALERTS_ENABLED=ON)
    for event in (signal('a', recipients=[ROOM]), signal('b'), signal('e', strategy='ECONOMY_HOST', recipients=[ROOM])):
        before = json.dumps(event.payload, sort_keys=True)
        out.accept(event)
        assert json.dumps(event.payload, sort_keys=True) == before


def test_the_shipped_config_keeps_both_copies_off():
    lines = (R / 'Part1/program/config.txt').read_text('utf-8-sig').splitlines()
    values = dict(line.split('=', 1) for line in lines if '=' in line and not line.lstrip().startswith('#'))
    values = {k.strip(): v.strip() for k, v in values.items()}
    assert values['PRIVATE_LIVE_ALERTS_ENABLED'] == 'false'
    assert values['PRIVATE_ECONOMY_ALERTS_ENABLED'] == 'false'
