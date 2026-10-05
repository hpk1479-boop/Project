"""Read-only Telegram configuration checks; never sends messages or updates."""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request


TELEGRAM_KEYS = {'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS'}
TOKEN = re.compile(r'[0-9]+:[A-Za-z0-9_-]+')
CHAT = re.compile(r'(?:-?[0-9]+|@[A-Za-z0-9_]+)')


def _failure(code, description, method):
    """Translate known API errors without exposing arbitrary remote text/URLs."""
    reason = str(description or '').lower()
    if code in (401, 404) or 'unauthorized' in reason:
        return '봇 연결 키가 올바르지 않거나 폐기되었습니다. BotFather에서 받은 키를 확인하세요.'
    if code == 429:
        return '텔레그램의 요청 제한에 걸렸습니다. 잠시 후 확인 버튼을 다시 눌러주세요.'
    if 'chat not found' in reason or 'peer_id_invalid' in reason:
        return '채팅방을 찾을 수 없습니다. 채팅방 번호·채널 이름과 봇 등록 여부를 확인하세요. 개인 대화방은 먼저 봇에게 /start를 보내세요.'
    if 'blocked' in reason:
        return '이 채팅방에서 봇이 차단되어 있습니다. 텔레그램에서 봇 차단을 해제하세요.'
    if code == 403 or any(word in reason for word in ('not a member', 'kicked', 'not enough rights', 'administrator')):
        return '봇이 이 채팅방에 접근할 수 없습니다. 봇 등록과 관리자·메시지 게시 권한을 확인하세요.'
    if 'user not found' in reason or 'member' in reason:
        return '채팅방에서 봇의 등록 상태를 확인할 수 없습니다. 봇을 채팅방에 추가하세요.'
    if code >= 500:
        return '텔레그램 서버에 일시적인 오류가 있습니다. 잠시 후 다시 확인하세요.'
    if method == 'getMe':
        return '봇 연결 키를 확인할 수 없습니다. BotFather에서 받은 키를 다시 확인하세요.'
    return '텔레그램이 채팅방 확인을 거부했습니다. 채팅방 번호·봇 등록·게시 권한을 확인하세요.'


class TelegramClient:
    def __init__(self, token, *, timeout=6, budget=25):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('봇 연결 키 형식이 올바르지 않습니다. BotFather에서 받은 전체 키를 입력하세요.')
        self.token, self.timeout = token, timeout
        self.deadline = time.monotonic() + budget

    def call(self, method, params=None):
        if method not in ('getMe', 'getChat', 'getChatMember'):
            raise ValueError('지원하지 않는 텔레그램 확인 요청입니다.')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('텔레그램 확인 시간이 초과되었습니다. 잠시 후 다시 확인하세요.')
        request = urllib.request.Request('https://api.telegram.org/bot' + self.token + '/' + method,
            data=urllib.parse.urlencode(params or {}).encode('utf-8'), method='POST')
        code = 200
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, remaining)) as response:
                raw = response.read(1024 * 1024)
        except urllib.error.HTTPError as error:
            code = error.code
            try:
                raw = error.read(1024 * 1024)
            except (TimeoutError, socket.timeout, OSError):
                raise ValueError('텔레그램에 연결할 수 없습니다. 인터넷 연결을 확인한 뒤 다시 확인하세요.') from None
            finally:
                error.close()
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ValueError('텔레그램에 연결할 수 없습니다. 인터넷 연결을 확인한 뒤 다시 확인하세요.') from None
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError, TypeError):
            raise ValueError('텔레그램 확인 응답을 읽을 수 없습니다. 잠시 후 다시 확인하세요.') from None
        if not isinstance(body, dict):
            raise ValueError('텔레그램 확인 응답이 올바르지 않습니다.')
        if code != 200 or body.get('ok') is not True:
            error_code = body.get('error_code', code)
            error_code = error_code if type(error_code) is int else code
            raise ValueError(_failure(error_code, body.get('description'), method))
        result = body.get('result')
        if not isinstance(result, dict):
            raise ValueError('텔레그램 확인 응답이 올바르지 않습니다.')
        return result


def _bot(client):
    result = client.call('getMe')
    if type(result.get('id')) is not int or result['id'] <= 0 or result.get('is_bot') is not True:
        raise ValueError('봇 연결 키를 확인할 수 없습니다. BotFather에서 받은 키를 확인하세요.')
    return result['id']


def _chat(client, bot_id, target, *, command=False):
    if not CHAT.fullmatch(target) or (not target.startswith('@') and int(target) == 0):
        raise ValueError('채팅방 번호 또는 @채널이름을 입력하세요. 초대 링크·여러 번호는 알림 채팅방에 사용할 수 없습니다.')
    chat = client.call('getChat', {'chat_id': target})
    if type(chat.get('id')) is not int or chat['id'] == 0 or chat.get('type') not in ('private', 'group', 'supergroup', 'channel'):
        raise ValueError('텔레그램 채팅방 정보를 확인할 수 없습니다.')
    if command and chat['type'] == 'channel':
        raise ValueError('명령을 보낼 채팅방에는 개인 대화방 또는 그룹 번호를 입력하세요. 채널은 알림을 받을 채팅방에 지정하세요.')
    if chat['type'] != 'private':
        member = client.call('getChatMember', {'chat_id': chat['id'], 'user_id': bot_id})
        status = member.get('status')
        if status in ('left', 'kicked') or status not in ('creator', 'administrator', 'member', 'restricted'):
            raise ValueError('봇이 이 채팅방에 등록되어 있지 않습니다. 봇을 채팅방에 추가하세요.')
        if chat['type'] == 'channel':
            if status != 'creator' and (status != 'administrator' or member.get('can_post_messages') is not True):
                raise ValueError('봇에게 채널 메시지 게시 권한이 없습니다. 채널 관리자에서 봇의 메시지 게시 권한을 켜주세요.')
        elif status == 'restricted':
            if member.get('is_member') is not True or member.get('can_send_messages') is not True:
                raise ValueError('봇의 그룹 메시지 전송 권한이 제한되어 있습니다. 그룹 권한을 확인하세요.')
        elif status == 'member':
            permissions = chat.get('permissions')
            if not isinstance(permissions, dict):
                raise ValueError('그룹 메시지 전송 권한을 확인할 수 없습니다. 잠시 후 다시 확인하세요.')
            if permissions.get('can_send_messages') is not True:
                raise ValueError('봇에게 그룹 메시지 전송 권한이 없습니다. 그룹 권한을 확인하세요.')
    # Incoming Telegram messages use numeric chat IDs, including channel posts.
    return str(chat['id'])


def validate_connection(config, changes):
    """Validate supplied nonempty values and normalize IDs before any save."""
    effective = {**config, **changes}
    checked = dict(changes)
    nonempty = {key for key, value in changes.items() if key in TELEGRAM_KEYS and value}
    if not nonempty:
        return checked
    token = effective.get('TELEGRAM_TOKEN', '').strip()
    if not token:
        raise ValueError('먼저 텔레그램 봇 연결 키를 입력하고 옆의 확인 버튼을 눌러주세요.')
    client = TelegramClient(token)
    bot_id = _bot(client)
    if 'TELEGRAM_CHAT_ID' in nonempty:
        checked['TELEGRAM_CHAT_ID'] = _chat(client, bot_id, effective['TELEGRAM_CHAT_ID'])
    if 'TELEGRAM_COMMAND_CHAT_IDS' in nonempty:
        targets = [item.strip() for item in effective['TELEGRAM_COMMAND_CHAT_IDS'].split(',')]
        if not all(targets):
            raise ValueError('명령을 보낼 채팅방 번호를 쉼표로 구분해 입력하세요. 빈 항목은 사용할 수 없습니다.')
        checked['TELEGRAM_COMMAND_CHAT_IDS'] = ','.join(dict.fromkeys(
            _chat(client, bot_id, target, command=True) for target in dict.fromkeys(targets)))
    return checked
