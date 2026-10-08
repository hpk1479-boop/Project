"""Telegram confirmation, permissions and atomic saves with synthetic API replies.

The real settings functions and local HTTP route run. TelegramClient.call is
the sole API seam: no external Telegram request or message is ever sent.
"""
from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import server, telegram_connection as connection, unified_settings as settings

OLD = '123456789:synthetic_old_token_108'
NEW = '987654321:synthetic_new_token_108'
BOT = 123456789
PRIVATE = 321987654
CHANNEL = -100123456789
GROUP = -987654321


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    original = ('\ufeff# comment and CRLF must survive\r\n'
        f'TELEGRAM_TOKEN={OLD}\r\nTELEGRAM_CHAT_ID={CHANNEL}\r\n'
        f'TELEGRAM_COMMAND_CHAT_IDS={PRIVATE}\r\nWONBI_SIGMA=3.0\r\n'
        'STAFF_STALE_SEC=30\r\n').encode('utf-8')
    path.write_bytes(original)
    monkeypatch.setattr(settings, 'LIVE_CONFIG', path)
    api = SimpleNamespace(path=path, original=original, calls=[], hook=None,
        bot={'id': BOT, 'is_bot': True}, chats={}, members={})
    api.chats = {
        str(PRIVATE): {'id': PRIVATE, 'type': 'private'},
        str(CHANNEL): {'id': CHANNEL, 'type': 'channel'},
        '@MosesChannel108': {'id': CHANNEL, 'type': 'channel'},
        str(GROUP): {'id': GROUP, 'type': 'supergroup', 'permissions': {'can_send_messages': True}},
        '@MosesGroup108': {'id': GROUP, 'type': 'supergroup', 'permissions': {'can_send_messages': True}},
    }
    api.members = {
        CHANNEL: {'status': 'administrator', 'can_post_messages': True, 'user': {'id': BOT}},
        GROUP: {'status': 'member', 'user': {'id': BOT}},
    }

    def fake(client, method, params=None):
        assert method in ('getMe', 'getChat', 'getChatMember'), 'Only read-only checks may run'
        params = dict(params or {})
        api.calls.append((client.token, method, params))
        if api.hook is not None:
            api.hook(method, params)
        if method == 'getMe': return copy.deepcopy(api.bot)
        if method == 'getChat':
            target = str(params['chat_id'])
            if target not in api.chats:
                raise ValueError(connection._failure(400, 'chat not found ' + target, method))
            return copy.deepcopy(api.chats[target])
        assert params['user_id'] == BOT
        return copy.deepcopy(api.members[params['chat_id']])

    monkeypatch.setattr(connection.TelegramClient, 'call', fake)
    return api


def values(saved):
    return settings._live_values(saved.path.read_text('utf-8-sig').splitlines(keepends=True))


def safe(public, *secrets):
    text = json.dumps(public, ensure_ascii=False) if isinstance(public, dict) else str(public)
    for secret in (OLD, NEW, str(PRIVATE), str(CHANNEL), str(GROUP), *secrets):
        if len(secret) >= 4:
            assert secret not in text


def confirm(key, value, **extra):
    return settings.confirm_telegram({'key': key, 'value': value, **extra})


def test_token_is_verified_while_old_file_remains_then_saved_and_masked(saved):
    saved.hook = lambda *_: pytest.fail('Verification must precede saving') if saved.path.read_bytes() != saved.original else None
    reply = confirm('TELEGRAM_TOKEN', NEW)
    assert values(saved)['TELEGRAM_TOKEN'] == NEW
    assert [call[1] for call in saved.calls] == ['getMe']
    assert reply['ok'] and reply['configured']
    safe(reply)
    assert saved.path.read_bytes() == saved.original.replace(OLD.encode(), NEW.encode())


@pytest.mark.parametrize('bot', [{'id': 0, 'is_bot': True}, {'id': -1, 'is_bot': True},
    {'id': '123', 'is_bot': True}, {'id': BOT, 'is_bot': False}, {}, {'id': True, 'is_bot': True}])
def test_invalid_bot_result_does_not_replace_existing_token(saved, bot):
    saved.bot = bot
    with pytest.raises(ValueError) as error: confirm('TELEGRAM_TOKEN', NEW)
    safe(error.value)
    assert saved.path.read_bytes() == saved.original


@pytest.mark.parametrize('token', ['not-a-token', '123:has spaces', '123:key\nnew-line', '', '123:key\x00'])
def test_bad_or_missing_token_does_not_reach_api_or_save(saved, token):
    if token == '': saved.path.write_bytes(saved.original.replace(OLD.encode(), b''))
    before = saved.path.read_bytes()
    with pytest.raises(ValueError) as error: confirm('TELEGRAM_TOKEN', token)
    safe(error.value, token if token else 'not-used')
    assert saved.path.read_bytes() == before and not saved.calls


def test_private_chat_checks_access_without_member_query(saved):
    reply = confirm('TELEGRAM_CHAT_ID', str(PRIVATE))
    assert values(saved)['TELEGRAM_CHAT_ID'] == str(PRIVATE)
    assert [call[1] for call in saved.calls] == ['getMe', 'getChat']
    safe(reply)


def test_channel_name_resolves_to_numeric_id_and_checks_own_bot_rights(saved):
    reply = confirm('TELEGRAM_CHAT_ID', '@MosesChannel108')
    assert values(saved)['TELEGRAM_CHAT_ID'] == str(CHANNEL)
    assert saved.calls[-1][2] == {'chat_id': CHANNEL, 'user_id': BOT}
    assert [call[1] for call in saved.calls] == ['getMe', 'getChat', 'getChatMember']
    safe(reply, '@MosesChannel108')


@pytest.mark.parametrize('member', [{'status': 'member'}, {'status': 'left'}, {'status': 'kicked'},
    {'status': 'administrator', 'can_post_messages': False}, {'status': 'administrator'}, {}])
def test_channel_without_post_permission_does_not_save(saved, member):
    saved.members[CHANNEL] = member
    with pytest.raises(ValueError) as error: confirm('TELEGRAM_CHAT_ID', '@MosesChannel108')
    safe(error.value)
    assert saved.path.read_bytes() == saved.original


@pytest.mark.parametrize('member,permissions,allowed', [
    ({'status': 'member'}, {'can_send_messages': True}, True),
    ({'status': 'member'}, {'can_send_messages': False}, False),
    ({'status': 'member'}, {}, False), ({'status': 'member'}, None, False),
    ({'status': 'administrator'}, None, True), ({'status': 'creator'}, None, True),
    ({'status': 'restricted', 'is_member': True, 'can_send_messages': True}, {}, True),
    ({'status': 'restricted', 'is_member': False, 'can_send_messages': True}, {}, False),
    ({'status': 'restricted', 'is_member': True, 'can_send_messages': False}, {}, False),
    ({'status': 'restricted', 'is_member': True}, {}, False),
    ({'status': 'left'}, {'can_send_messages': True}, False),
])
def test_group_membership_and_actual_text_send_permission(saved, member, permissions, allowed):
    saved.members[GROUP] = member
    saved.chats[str(GROUP)]['permissions'] = permissions
    if allowed:
        reply = confirm('TELEGRAM_CHAT_ID', str(GROUP))
        assert values(saved)['TELEGRAM_CHAT_ID'] == str(GROUP)
        safe(reply)
    else:
        with pytest.raises(ValueError) as error: confirm('TELEGRAM_CHAT_ID', str(GROUP))
        safe(error.value)
        assert saved.path.read_bytes() == saved.original


@pytest.mark.parametrize('chat', ['0', '-0', '1,2', '1 2', 'https://t.me/MosesChannel108',
    't.me/MosesChannel108', '@', 'abc', '+123'])
def test_invalid_single_chat_inputs_never_save_or_echo_input(saved, chat):
    with pytest.raises(ValueError) as error: confirm('TELEGRAM_CHAT_ID', chat)
    safe(error.value, chat)
    assert saved.path.read_bytes() == saved.original
    assert all(call[1] != 'getChat' for call in saved.calls)


def test_command_chats_are_all_checked_normalized_and_deduplicated(saved):
    reply = confirm('TELEGRAM_COMMAND_CHAT_IDS', f'{PRIVATE}, {PRIVATE}')
    assert values(saved)['TELEGRAM_COMMAND_CHAT_IDS'] == f'{PRIVATE}'
    safe(reply)
    assert all(call[1] in ('getMe', 'getChat') for call in saved.calls)


@pytest.mark.parametrize('targets', [f'{PRIVATE},0', f'{PRIVATE},', f',{PRIVATE}',
    f'{PRIVATE},@MosesChannel108', f'{PRIVATE},https://t.me/a', f'{PRIVATE},99999999',
    # Live takes commands only from 1:1 chats, so a group never passes the check.
    f'{PRIVATE},{GROUP}', f'{PRIVATE},@MosesGroup108'])
def test_any_invalid_command_chat_preserves_entire_old_value(saved, targets):
    with pytest.raises(ValueError) as error: confirm('TELEGRAM_COMMAND_CHAT_IDS', targets)
    safe(error.value, targets, '99999999')
    assert saved.path.read_bytes() == saved.original


@pytest.mark.parametrize('key', sorted(connection.TELEGRAM_KEYS))
def test_null_deletes_without_validation_or_network(saved, key):
    reply = confirm(key, None, token=NEW)
    assert values(saved)[key] == '' and not reply['configured']
    assert not saved.calls
    safe(reply)


@pytest.mark.parametrize('key', sorted(connection.TELEGRAM_KEYS))
def test_empty_confirm_revalidates_configured_current_value(saved, key):
    before = values(saved)[key]
    reply = confirm(key, '')
    assert values(saved)[key] == before and saved.calls
    assert reply['configured']
    safe(reply)


def test_unconfirmed_candidate_token_is_rejected_before_other_field_validation(saved):
    with pytest.raises(ValueError, match='먼저') as error:
        confirm('TELEGRAM_CHAT_ID', str(PRIVATE), token=NEW)
    safe(error.value)
    assert saved.path.read_bytes() == saved.original and not saved.calls
    assert confirm('TELEGRAM_CHAT_ID', str(PRIVATE), token='  ' + OLD + '  ')['ok']


def test_missing_bot_token_cannot_confirm_chat(saved):
    saved.path.write_bytes(saved.original.replace(OLD.encode(), b''))
    before = saved.path.read_bytes()
    with pytest.raises(ValueError, match='먼저'): confirm('TELEGRAM_CHAT_ID', str(PRIVATE))
    assert saved.path.read_bytes() == before and not saved.calls


def test_bulk_save_cannot_bypass_telegram_checks_and_is_atomic(saved):
    with pytest.raises(ValueError):
        settings.save_live({'TELEGRAM_COMMAND_CHAT_IDS': f'{PRIVATE},0', 'WONBI_SIGMA': '2.5'})
    assert saved.path.read_bytes() == saved.original
    saved.calls.clear()
    reply = settings.save_live({'TELEGRAM_TOKEN': NEW, 'TELEGRAM_CHAT_ID': '@MosesChannel108', 'WONBI_SIGMA': '2.5'})
    assert values(saved)['TELEGRAM_TOKEN'] == NEW and values(saved)['WONBI_SIGMA'] == '2.5'
    assert values(saved)['TELEGRAM_CHAT_ID'] == str(CHANNEL)
    assert all(call[0] == NEW for call in saved.calls)
    safe(reply)


def test_unrelated_bulk_save_and_unchanged_blank_secrets_do_not_call_api(saved):
    settings.save_live({'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': '', 'WONBI_SIGMA': '2.5'})
    assert not saved.calls
    assert values(saved)['TELEGRAM_TOKEN'] == OLD
    assert values(saved)['TELEGRAM_CHAT_ID'] == str(CHANNEL)
    assert values(saved)['WONBI_SIGMA'] == '2.5'


def test_failed_atomic_replace_preserves_config_and_hides_error_secrets(saved, monkeypatch):
    def fail_replace(*_): raise OSError('synthetic write failure ' + NEW + ' ' + str(PRIVATE))
    monkeypatch.setattr(settings.os, 'replace', fail_replace)
    with pytest.raises(ValueError, match='저장하지 못') as error: confirm('TELEGRAM_TOKEN', NEW)
    safe(error.value)
    assert saved.path.read_bytes() == saved.original
    assert not list(saved.path.parent.glob('config.txt.*.tmp'))


@pytest.mark.parametrize('code,reason', [(401, 'Unauthorized'), (403, 'bot was blocked'),
    (400, 'chat not found'), (400, 'not enough rights'), (429, 'retry'), (500, 'server error')])
def test_remote_failure_translation_does_not_echo_response_body_or_secret_ids(code, reason):
    message = connection._failure(code, reason + ' ' + NEW + ' ' + str(CHANNEL), 'getChat')
    safe(message)
    assert message


def test_validation_and_save_are_one_locked_transaction_with_unrelated_settings(saved):
    entered, release, other_started, other_done = (threading.Event() for _ in range(4))
    failures = []
    def block(method, _):
        if method == 'getMe':
            assert saved.path.read_bytes() == saved.original
            entered.set()
            assert release.wait(5)
    saved.hook = block
    def run_confirm():
        try: confirm('TELEGRAM_TOKEN', NEW)
        except BaseException as error: failures.append(error)
    def run_other():
        other_started.set()
        try: settings.save_live({'WONBI_SIGMA': '2.5'})
        except BaseException as error: failures.append(error)
        finally: other_done.set()
    first, second = threading.Thread(target=run_confirm), threading.Thread(target=run_other)
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        assert other_started.wait(5)
        assert not other_done.wait(.1)
    finally:
        release.set()
        first.join(5)
        if second.ident is not None: second.join(5)
    assert not failures and not first.is_alive() and not second.is_alive()
    assert values(saved)['TELEGRAM_TOKEN'] == NEW and values(saved)['WONBI_SIGMA'] == '2.5'


def test_actual_local_http_confirmation_success_failure_and_authentication(saved, capsys):
    host = server.LabServer(0)
    worker = threading.Thread(target=host.serve_forever, daemon=True)
    worker.start()
    def post(payload, *, authorized=True):
        client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
        try:
            client.request('POST', '/api/mo/settings/telegram', body=json.dumps(payload),
                headers={'Content-Type': 'application/json', 'X-Lab-Token': host.token if authorized else 'wrong'})
            reply = client.getresponse()
            return reply.status, json.loads(reply.read())
        finally: client.close()
    try:
        status, response = post({'key': 'TELEGRAM_TOKEN', 'value': NEW}, authorized=False)
        assert status == 403 and not saved.calls and saved.path.read_bytes() == saved.original
        safe(response)
        status, response = post({'key': 'TELEGRAM_TOKEN', 'value': NEW})
        assert status == 200 and values(saved)['TELEGRAM_TOKEN'] == NEW
        safe(response)
        status, response = post({'key': 'TELEGRAM_CHAT_ID', 'value': '@MosesChannel108'})
        assert status == 200 and values(saved)['TELEGRAM_CHAT_ID'] == str(CHANNEL)
        safe(response, '@MosesChannel108')
        before = saved.path.read_bytes()
        status, response = post({'key': 'TELEGRAM_CHAT_ID', 'value': 'https://t.me/private-secret108'})
        assert status == 400 and saved.path.read_bytes() == before
        safe(response, 'private-secret108')
        status, response = post({'key': 'TELEGRAM_CHAT_ID', 'value': None})
        assert status == 200 and not response['configured']
        safe(response)
    finally:
        host.shutdown()
        host.server_close()
        worker.join(5)
    captured = capsys.readouterr()
    safe(captured.out + captured.err)
