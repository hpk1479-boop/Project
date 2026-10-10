"""Validated web edits of settings that the current engines already read.

No new setting schema is introduced. Secrets are never returned to the page.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import uuid
from pathlib import Path

from . import catalog, storage

ROOT = catalog.ROOT.parent
LIVE_CONFIG = ROOT / 'Part1' / 'program' / 'config.txt'
PART2_CONFIG = ROOT / 'Part2' / 'event_backtest.json'
SECRET_KEYS = {'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS'}
BOOL_KEYS = {'ECONOMY_ENABLED', 'TRACE_ENABLED', 'LIVE_RECORD_ENABLED'}
# Settings added after a config.txt was written. An update keeps the user's config.txt as it is,
# so such a key is shown with its default and is added to the file when it is first saved.
LIVE_ADDED_DEFAULTS = {'LIVE_RECORD_ENABLED': 'true', 'PRIVATE_LIVE_ALERTS_ENABLED': 'false',
                       'PRIVATE_ECONOMY_ALERTS_ENABLED': 'false',
                       # The broker's server clock of MT5 times (Part1 server_time.py, 수정본172).
                       'SERVER_UTC_OFFSET': '2', 'SERVER_DST': 'US'}
TIME_KEYS = {'ASIA', 'LONDON', 'NEWYORK', 'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK',
             'OPENING_ASIA', 'OPENING_LONDON', 'OPENING_NEWYORK'}
# The live settings the engines read. Any other line of an older config.txt is neither shown nor saved.
LIVE_KEYS = ('SYMBOLS', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS',
             'PRIVATE_LIVE_ALERTS_ENABLED', 'PRIVATE_ECONOMY_ALERTS_ENABLED', 'ECONOMY_ENABLED',
             'LIVE_RECORD_ENABLED', 'STAFF_PIPE_NAME', 'STAFF_STALE_SEC', 'SERVER_UTC_OFFSET', 'SERVER_DST',
             'WONBI_SIGMA', *sorted(TIME_KEYS),
             'ECONOMY_FETCH_SEC', 'ECONOMY_POLL_SEC', 'TRACE_ENABLED', 'TRACE_RING_LINES',
             'TRACE_WARNING_BYTES', 'TRACE_WARNING_BACKUPS')
LIVE_PREFIXES = ('POINT_', 'LOT_')
# A 1-point price or 1-lot size whose name this file's KEY=VALUE lines can hold (LINE below).
POINT_NAME = re.compile(r'(POINT|LOT)_[A-Z0-9_+]+')
PART2_KEYS = {'warehouse', 'cores', 'overlap_trading_days', 'capture_start', 'oz_evaluation', 'broker_symbols'}
SAVED = '설정이 저장되었습니다'
SAVED_LIVE = SAVED + ' · 라이브 다시 시작 후 적용'
LINE = re.compile(r'^([ \t]*([A-Z][A-Z0-9_+]*)([ \t]*=[ \t]*))([^\r\n]*)(\r?\n)?$')
_LIVE_LOCK = threading.RLock()


def _live_lines():
    raw = LIVE_CONFIG.read_bytes()
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = raw.decode('utf-8-sig')
    program = ROOT / 'Part1' / 'program'
    if str(program) not in sys.path:
        sys.path.insert(0, str(program))
    from symbol_settings import migrate_symbol_lines
    return migrate_symbol_lines(text.splitlines(keepends=True)), bom


def _known(key):
    return key in LIVE_KEYS or key.startswith(LIVE_PREFIXES)


def _live_values(lines):
    values = {}
    for line in lines:
        match = LINE.match(line)
        if match and match[2] not in values:
            values[match[2]] = match[4].strip()
    return values


def _symbol_points(values):
    """An empty 1-point price (수정본175) and 1-lot size (수정본185) for every configured symbol: the screen shows
    them and a line is added when it is first saved. Only the virtual entry uses them: the spread is points ×
    point, the commission dollars per lot ÷ lot size."""
    from symbol_settings import configured_symbols
    names = (prefix + symbol for symbol in configured_symbols(values) for prefix in LIVE_PREFIXES)
    return {name: '' for name in names if POINT_NAME.fullmatch(name)}


def live_session_times(keys):
    """Read only the existing session clocks requested by the settings UI."""
    lines, _ = _live_lines()
    values = _live_values(lines)
    return {key: values.get(key, '') for key in keys}


def _number(key, value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(key + ': 숫자 형식이 아닙니다.') from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(key + ': 0보다 큰 유한한 수여야 합니다.')
    if key.endswith(('_MS', '_BARS', '_LINES', '_BYTES', '_BACKUPS')) and not number.is_integer():
        raise ValueError(key + ': 정수여야 합니다.')
    return str(int(number)) if number.is_integer() and key != 'WONBI_SIGMA' and not key.startswith('POINT_') else str(number)


def _clock(value):
    if not re.fullmatch(r'[0-2][0-9][0-5][0-9]', value):
        return False
    hour, minute = int(value[:2]), int(value[2:])
    return hour < 24 or (hour == 24 and minute == 0)


def _validate_live(key, incoming, previous):
    if key in SECRET_KEYS and incoming is None:
        return ''
    if not isinstance(incoming, str) or len(incoming) > 1024 or any(c in incoming for c in '\r\n\x00'):
        raise ValueError(key + ': 한 줄의 텍스트만 입력할 수 있습니다.')
    value = incoming.strip()
    if key in SECRET_KEYS:
        if key in ('TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS') and value and not re.fullmatch(r'[-@A-Za-z0-9_, ]+', value):
            raise ValueError(key + ': 채팅 ID 형식을 확인하세요.')
        return previous if not value else value
    if key in BOOL_KEYS or key.endswith('_ENABLED'):
        if value.lower() not in ('true', 'false'):
            raise ValueError(key + ': true 또는 false만 허용합니다.')
        return value.lower()
    if key in TIME_KEYS:
        bounds = value.split('-')
        if len(bounds) != 2 or not all(_clock(item) for item in bounds):
            raise ValueError(key + ': HHMM-HHMM 형식이어야 합니다.')
        return value
    if key == 'STAFF_PIPE_NAME':
        if not re.fullmatch(r'\\\\\.\\pipe\\[A-Za-z0-9_.-]+', value):
            raise ValueError('STAFF_PIPE_NAME: Windows Named Pipe 형식이어야 합니다.')
        return value
    if key == 'SERVER_UTC_OFFSET':
        if not re.fullmatch(r'[+-]?\d{1,2}', value) or not -12 <= int(value) <= 14:
            raise ValueError(key + ': -12에서 14 사이의 정수여야 합니다.')
        return str(int(value))
    if key == 'SERVER_DST':
        if value.upper() not in ('US', 'EU', 'NONE'):
            raise ValueError(key + ': US, EU, NONE 중 하나여야 합니다.')
        return value.upper()
    if key == 'SYMBOLS':
        symbols = list(dict.fromkeys(x.strip() for x in value.split(',') if x.strip()))
        if not symbols:
            raise ValueError(key + '를 비울 수 없습니다.')
        if any(not re.fullmatch(r'[^\s,\x00-\x1f\x7f]{1,64}', s) for s in symbols):
            raise ValueError(key + ': 종목 형식을 확인하세요.')
        return ','.join(symbols)
    if (key.endswith(('_SEC', '_MS', '_BARS', '_LINES', '_BYTES', '_BACKUPS'))
            or key == 'WONBI_SIGMA' or key.startswith(LIVE_PREFIXES)):
        return _number(key, value)
    if not value:
        raise ValueError(key + ': 빈 값을 저장할 수 없습니다.')
    return value


def app_revision(root=None):
    """Keep the source revision separate from the distributor's release label."""
    root = Path(root) if root is not None else ROOT
    metadata = root / 'runtime/app_version.json'
    if metadata.is_file():
        try:
            data = json.loads(metadata.read_text('utf-8'))
            revision = data.get('revision') if isinstance(data, dict) and data.get('schema') == 1 else None
            if type(revision) is int and revision > 0:
                return revision
        except (OSError, ValueError):
            pass
    match = re.fullmatch(r'수정본([1-9][0-9]*)', root.name)
    return int(match[1]) if match else None


def read():
    lines, _ = _live_lines()
    values = {**_live_values(lines)}
    for key, default in {**LIVE_ADDED_DEFAULTS, **_symbol_points(values)}.items():
        values.setdefault(key, default)
    live = [{'key': key, 'value': '' if key in SECRET_KEYS else value,
             'secret': key in SECRET_KEYS, 'configured': bool(value) if key in SECRET_KEYS else None}
            for key, value in values.items() if _known(key)]
    from .unified_backtest import _part2
    part2 = _part2()[0].settings()
    connections = storage.connections()
    from .server import ai_settings
    from common_ai.settings import masked_settings
    ai = ai_settings()
    cores, tuned = _default_workers()
    return {'app_revision': app_revision(), 'live': live,
            'part2': {key: part2.get(key) for key in sorted(PART2_KEYS - {'warehouse'}) if key in part2},
            'part2_cores': cores, 'part2_tuned': tuned,
            'connections': {'warehouse': connections.get('warehouse', ''),
                            'python_executable': connections.get('python_executable', ''),
                            'project_root': connections.get('project_root', '..')},
            'ai': {**masked_settings(ai), 'base_url': 'http://127.0.0.1:11434'}}


_CORES = []


def _default_workers():
    """(count used when the field is empty, this PC's measurement or None): measured, else physical cores."""
    from .unified_backtest import _part2
    _part2()
    from event_backtest.worker_tuning import saved_profile
    try:
        saved = saved_profile()
    except (OSError, ValueError):
        saved = None
    if saved is None:
        return _physical_cores(), None
    return saved['workers'], {key: saved.get(key) for key in ('workers', 'measured_at', 'rows')}


def _physical_cores():
    """The machine's physical cores, asked once per server (수정본163; the query is immediate since 수정본170)."""
    if not _CORES:
        from event_backtest.system import physical_cores
        _CORES.append(physical_cores())
    return _CORES[0]


def _atomic_bytes(path, payload):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(payload)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _save_live(changes, *, force_telegram=False):
    if not isinstance(changes, dict):
        raise ValueError('LIVE 설정 형식이 올바르지 않습니다.')
    lines, bom = _live_lines()
    in_file = _live_values(lines)
    previous = {**LIVE_ADDED_DEFAULTS, **_symbol_points(in_file), **in_file}
    if any(not _known(key) for key in changes) or set(changes) - set(previous):
        raise ValueError('저장할 수 없는 설정입니다.')
    checked = {key: _validate_live(key, value, previous[key]) for key, value in changes.items()}
    from .telegram_connection import TELEGRAM_KEYS, validate_connection
    telegram = {key: value for key, value in checked.items()
                if key in TELEGRAM_KEYS and (force_telegram or value != previous[key])}
    if telegram:
        checked.update(validate_connection(previous, telegram))
    output = []
    for line in lines:
        match = LINE.match(line)
        if match and match[2] in checked:
            output.append(match[1] + checked[match[2]] + (match[5] or ''))
        else:
            output.append(line)
    absent = [key for key in checked if key not in in_file]
    if absent:
        if output and not output[-1].endswith('\n'):
            output[-1] += '\n'
        output.extend(key + '=' + checked[key] + '\n' for key in absent)
    data = ''.join(output).encode('utf-8')
    _atomic_bytes(LIVE_CONFIG, (b'\xef\xbb\xbf' if bom else b'') + data)
    return {'ok': True, 'message': SAVED_LIVE}


def save_live(changes):
    with _LIVE_LOCK:
        return _save_live(changes)


def confirm_telegram(data):
    from .telegram_connection import TELEGRAM_KEYS
    if not isinstance(data, dict) or set(data) - {'key', 'value', 'token'}:
        raise ValueError('텔레그램 확인 요청 형식이 올바르지 않습니다.')
    key = data.get('key')
    if key not in TELEGRAM_KEYS or 'value' not in data:
        raise ValueError('확인할 텔레그램 입력칸을 확인하세요.')
    value = data['value']
    if value is not None and not isinstance(value, str):
        raise ValueError('확인할 값을 텍스트로 입력하세요.')
    with _LIVE_LOCK:
        previous = _live_values(_live_lines()[0])
        if key not in previous:
            raise ValueError('현재 설정 파일에 텔레그램 입력 항목이 없습니다.')
        candidate = data.get('token')
        if candidate is not None and not isinstance(candidate, str):
            raise ValueError('봇 연결 키를 텍스트로 입력하세요.')
        if key != 'TELEGRAM_TOKEN' and value is not None and candidate and candidate.strip() != previous.get('TELEGRAM_TOKEN', ''):
            raise ValueError('먼저 봇 토큰 옆의 확인을 눌러주세요.')
        if value is not None and not value.strip() and not previous[key]:
            raise ValueError('확인할 값을 입력하세요.')
        try:
            _save_live({key: value}, force_telegram=value is not None)
        except OSError:
            raise ValueError('설정을 저장하지 못했습니다. 모세 설치 폴더의 쓰기 권한을 확인하세요.') from None
        configured = bool(_live_values(_live_lines()[0]).get(key))
    message = ('확인하고 저장했습니다' if configured else '저장값을 지웠습니다') + ' · 라이브 다시 시작 후 적용'
    return {'ok': True, 'key': key, 'configured': configured, 'message': message}


def confirm_gemini_key(data):
    """The Gemini key's own [확인], the same contract as the bot token's (수정본177).

    A typed key is checked with Google and saved, null deletes the saved key, and an empty value
    checks the saved key again without changing anything."""
    if (not isinstance(data, dict) or set(data) - {'key', 'value'} or data.get('key') != 'gemini_api_key'
            or 'value' not in data):
        raise ValueError('Gemini API 키 확인 요청 형식이 올바르지 않습니다.')
    value = data['value']
    if value is not None and not isinstance(value, str):
        raise ValueError('확인할 값을 텍스트로 입력하세요.')
    if value is None or value.strip():
        save_ai({'gemini_api_key': value})
        configured = value is not None
        return {'ok': True, 'key': 'gemini_api_key', 'configured': configured,
                'message': '확인하고 저장했습니다' if configured else '저장값을 지웠습니다'}
    from . import server
    saved = server.ai_settings()
    if not saved.get('gemini_api_key'):
        raise ValueError('확인할 값을 입력하세요.')
    from common_ai.gemini import check_api_key
    check_api_key(saved)
    return {'ok': True, 'key': 'gemini_api_key', 'configured': True, 'message': '확인했습니다'}


def telegram_chats(data):
    """Chats the bot sees, read while the live engine is paused; it starts again afterwards."""
    if not isinstance(data, dict) or set(data) - {'token', 'pause_live'} or type(data.get('pause_live', False)) is not bool:
        raise ValueError('텔레그램 불러오기 요청 형식이 올바르지 않습니다.')
    token = data.get('token') or _live_values(_live_lines()[0]).get('TELEGRAM_TOKEN', '')
    if not isinstance(token, str) or not token.strip():
        raise ValueError('먼저 봇 토큰을 입력하세요.')
    from . import unified_live
    running = [row for row in unified_live.control()['list_live_engines']() if row['current_copy']]
    if running and not data.get('pause_live'):
        return {'ok': True, 'needs_live_pause': True}
    if running:
        unified_live.stop()
    from .telegram_connection import recent_chats
    try:
        chats = recent_chats(token.strip())
    finally:
        if running:
            unified_live.start({})
    return {'ok': True, 'chats': chats, 'live_restarted': bool(running)}


def save_part2(changes):
    if not isinstance(changes, dict) or set(changes) - PART2_KEYS:
        raise ValueError('Part2 설정 항목을 확인하세요.')
    current = json.loads(PART2_CONFIG.read_text('utf-8-sig')) if PART2_CONFIG.exists() else {}
    updated = dict(current)
    for key, value in changes.items():
        if key == 'warehouse':
            raise ValueError('창고 경로는 컴퓨터별 연결 설정에서 변경하세요.')
        if key == 'cores':
            if value is not None and (type(value) is not int or not 1 <= value <= 256):
                raise ValueError('코어 수는 비우거나 1~256이어야 합니다.')
        elif key == 'overlap_trading_days':
            if type(value) is not int or not 0 <= value <= 30:
                raise ValueError('겹침 기간은 0~30 거래일이어야 합니다.')
        elif key == 'capture_start' and value not in ('keyframe', 'beginning'):
            raise ValueError('키프레임 시작 형식을 확인하세요.')
        elif key == 'oz_evaluation' and value not in ('selected', 'all'):
            raise ValueError('OZ 평가 범위를 확인하세요.')
        elif key == 'broker_symbols':
            if not isinstance(value, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or
                not re.fullmatch(r'[^\s,\x00-\x1f\x7f]{1,64}', k) or
                not re.fullmatch(r'[^\s,\x00-\x1f\x7f]{1,64}', v)
                for k, v in value.items()):
                raise ValueError('브로커 종목 매핑 형식을 확인하세요.')
        updated[key] = value
    _atomic_bytes(PART2_CONFIG, (json.dumps(updated, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return {'ok': True, 'message': SAVED}


def save_connections(changes):
    if not isinstance(changes, dict) or set(changes) - {'warehouse', 'python_executable'}:
        raise ValueError('연결 설정 항목을 확인하세요.')
    current = storage.connections()
    merged = {**current, **changes}
    binary = merged.get('python_executable', '')
    if binary and (not Path(binary).is_file() or Path(binary).name.lower() not in ('python.exe', 'pythonw.exe', 'python')):
        raise ValueError('Python 실행 파일을 확인하세요.')
    storage.save_connections(merged)
    return {'ok': True, 'message': SAVED}


def save_ai(changes):
    from .ai.provider import AI_SETTING_KEYS, configured_settings, model_name
    if not isinstance(changes, dict) or not changes or set(changes) - AI_SETTING_KEYS:
        raise ValueError('AI 설정 항목을 확인하세요.')
    from . import server
    with server._AI_LOCK:
        server.ai_settings()  # Validate current or legacy settings before changing anything.
        source = server.AI_SETTINGS
        if not source.is_file() and source == server.ROOT.parent / 'settings/ai_settings.json':
            source = server.ROOT / 'projects/ai_settings.json'
        previous = json.loads(source.read_text('utf-8')) if source.is_file() else {}
        updated = configured_settings({**previous, **changes})
        if updated['provider'] == 'ollama':
            updated['model'] = model_name(updated['model'])
        elif updated['provider'] == 'local_gguf':
            from .ai.local_gguf import validate_gguf_settings
            updated = validate_gguf_settings(updated)
        elif updated['provider'] == 'gemini':
            from common_ai.gemini import validate_settings
            deleting_key = 'gemini_api_key' in changes and changes['gemini_api_key'] is None
            # The key's own confirmation precedes model selection on a fresh installation.
            # Other settings still require a complete Gemini selection, as does inference.
            key_only = set(changes) == {'gemini_api_key'} and bool(updated.get('gemini_api_key'))
            updated = validate_settings(updated, required=not (deleting_key or key_only))
        timeout = updated['timeout']
        if type(timeout) is not int or not 10 <= timeout <= 300:
            raise ValueError('AI 응답 제한 시간은 10~300초여야 합니다.')
        if changes.get('gemini_api_key'):
            from common_ai.gemini import check_api_key
            check_api_key(updated)
        storage.json_write(server.AI_SETTINGS, updated)
        from common_ai.client import Client
        client=Client(server.ROOT.parent)
        try:client.invalidate(updated)
        finally:client.close()
        from .ai.model_runtime import RUNTIME
        RUNTIME.invalidate()
        server.AI_SESSIONS.clear()
        server.BACKTEST_COMMAND_SESSIONS.clear()
        server.RESEARCH_SESSIONS.clear()
    return {'ok': True, 'message': SAVED}
