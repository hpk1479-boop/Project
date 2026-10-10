"""Revision 138: only settings the engines read, save results, Telegram chat discovery,
a saved LoRA choice is refused and backtest pieces follow the workers.
(수정본162 removed the checks that only looked for traces of the removed ZMQ server, LoRA and work size.)"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import telegram_connection as connection, unified_settings as settings

REMOVED = ('STAFF_BIND_ENDPOINT', 'STAFF_ENDPOINT', 'MANAGER_ALERT_ENDPOINT', 'ZMQ_TIMEOUT_MS', 'STAFF_BARS',
           'STAFF_ALLOWED_TIMEFRAMES', 'CONFIG_RELOAD_SEC', 'ENGINE_SUBSCRIPTION_REFRESH_SEC', 'OZ_COMMAND_FILE')


@pytest.fixture
def old_config(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    path.write_text('SYMBOLS=XAUUSD+\nSTAFF_PIPE_NAME=\\\\.\\pipe\\StaffOfMoses_v1\nSTAFF_STALE_SEC=30\n'
                    'COMPOSER_POLL_SEC=0.5\nGEMINI_API_KEY=old\n' +
                    ''.join(key + '=1\n' for key in REMOVED), encoding='utf-8')
    monkeypatch.setattr(settings, 'LIVE_CONFIG', path)
    return path


def test_an_older_config_shows_and_saves_only_settings_the_engines_read(old_config):
    shown = {row['key'] for row in settings.read()['live']}
    assert {'SYMBOLS', 'STAFF_PIPE_NAME', 'STAFF_STALE_SEC'} <= shown
    assert not shown & {*REMOVED, 'COMPOSER_POLL_SEC', 'GEMINI_API_KEY'}
    for key in (*REMOVED, 'COMPOSER_POLL_SEC', 'GEMINI_API_KEY'):
        with pytest.raises(ValueError, match='저장할 수 없는 설정'):
            settings.save_live({key: '2'})
    assert settings.save_live({'STAFF_STALE_SEC': '45'})['message'] == '설정이 저장되었습니다 · 라이브 다시 시작 후 적용'
    assert 'STAFF_STALE_SEC=45' in old_config.read_text('utf-8')


def test_every_save_reports_the_same_short_result(tmp_path, monkeypatch):
    part2 = tmp_path / 'event_backtest.json'
    monkeypatch.setattr(settings, 'PART2_CONFIG', part2)
    assert settings.save_part2({'overlap_trading_days': 4}) == {'ok': True, 'message': '설정이 저장되었습니다'}


def updates_client(monkeypatch, updates, *, error=None):
    calls = []
    def call(client, method, params=None):
        calls.append((method, dict(params or {})))
        if method == 'getMe':
            return {'id': 1, 'is_bot': True}
        if error:
            raise ValueError(connection._failure(error, 'Conflict: terminated by other getUpdates request', method))
        return updates
    monkeypatch.setattr(connection.TelegramClient, 'call', call)
    return calls


def test_recent_chats_reads_private_group_and_channel_without_confirming(monkeypatch):
    updates = [
        {'update_id': 1, 'message': {'chat': {'id': 555, 'type': 'private', 'first_name': '홍', 'last_name': '길동',
                                              'username': 'hong'}, 'text': '/start'}},
        {'update_id': 2, 'my_chat_member': {'chat': {'id': -100777, 'type': 'channel', 'title': '알림채널'},
                                            'new_chat_member': {'status': 'administrator'}}},
        {'update_id': 3, 'my_chat_member': {'chat': {'id': -888, 'type': 'supergroup', 'title': '그룹'},
                                            'new_chat_member': {'status': 'member'}}},
        {'update_id': 4, 'my_chat_member': {'chat': {'id': -888, 'type': 'supergroup', 'title': '그룹'},
                                            'new_chat_member': {'status': 'left'}}},
        {'update_id': 5, 'channel_post': {'chat': {'id': -100777, 'type': 'channel', 'title': '알림채널'}}},
    ]
    calls = updates_client(monkeypatch, updates)
    chats = connection.recent_chats('123456789:synthetic_token_138')
    assert chats == [{'id': '555', 'type': 'private', 'name': '홍 길동', 'username': 'hong'},
                     {'id': '-100777', 'type': 'channel', 'name': '알림채널', 'username': ''}]
    method, params = calls[-1]
    assert method == 'getUpdates' and 'offset' not in params
    assert json.loads(params['allowed_updates']) == ['message', 'channel_post', 'my_chat_member']


def test_another_pc_reading_the_same_bot_is_named(monkeypatch):
    updates_client(monkeypatch, [], error=409)
    with pytest.raises(ValueError, match='다른 PC나 프로그램이 같은 봇'):
        connection.recent_chats('123456789:synthetic_token_138')


def test_chat_discovery_asks_to_pause_live_then_restarts_it(monkeypatch):
    from lab import unified_live
    events = []
    running = [{'pid': 1, 'created': 1, 'current_copy': True}]
    monkeypatch.setattr(unified_live, 'control', lambda: {'list_live_engines': lambda: running})
    monkeypatch.setattr(unified_live, 'stop', lambda: events.append('stop'))
    monkeypatch.setattr(unified_live, 'start', lambda request: events.append(('start', request)))
    monkeypatch.setattr(connection, 'recent_chats', lambda token: events.append(('read', token)) or [])
    token = '123456789:synthetic_token_138'
    assert settings.telegram_chats({'token': token}) == {'ok': True, 'needs_live_pause': True}
    assert events == []
    result = settings.telegram_chats({'token': token, 'pause_live': True})
    assert result == {'ok': True, 'chats': [], 'live_restarted': True}
    assert events == ['stop', ('read', token), ('start', {})]


def test_a_saved_lora_choice_is_rejected():
    from common_ai.provider import PROVIDERS, configured_settings
    assert PROVIDERS == ('gemini', 'ollama', 'local_gguf', 'disabled')
    with pytest.raises(ValueError, match='AI 실행 방식'):
        configured_settings({'provider': 'local_lora'})
    from common_ai.reply_format import parse_reply
    with pytest.raises(ValueError, match='AI 출력이 완전한 JSON'):
        parse_reply('{', {'type': 'object'}, [])
