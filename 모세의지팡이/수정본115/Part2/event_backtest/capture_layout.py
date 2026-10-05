"""Portable, human-readable locations for verified recording pieces."""
import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path

from .settings import warehouse_path, relative_path, SYMBOL_FORM


_SYMBOL = re.compile(r'[A-Za-z0-9][A-Za-z0-9+_.-]*\Z')
_DEVICES = {'CON', 'PRN', 'AUX', 'NUL',
            *(prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)),
            *(prefix + number for prefix in ('COM', 'LPT') for number in '¹²³')}


def symbol_folder(symbol):
    """Keep existing names; reversibly escape other logical IDs within Windows limits."""
    if not isinstance(symbol, str) or not SYMBOL_FORM.fullmatch(symbol):
        raise ValueError('invalid capture symbol')
    try:
        symbol.encode('utf-16-le')
    except UnicodeError:
        raise ValueError('invalid capture symbol') from None
    if (_SYMBOL.fullmatch(symbol) and not symbol.endswith('.')
            and symbol.split('.')[0].upper() not in _DEVICES):
        return symbol
    last = len(symbol.rstrip('.'))
    return '~' + ''.join('~' + format(ord(char), '02X')
                         if char in '<>:"/\\|?*~' or (char == '.' and index >= last)
                         else char for index, char in enumerate(symbol))


def folder_symbol(folder):
    """Decode only canonical folder names produced by symbol_folder."""
    if not isinstance(folder, str):
        raise ValueError('invalid capture folder')
    if folder.startswith('~'):
        text = folder[1:]
        if re.search(r'~(?![0-9A-F]{2})', text):
            raise ValueError('invalid capture folder escape')
        symbol = re.sub(r'~([0-9A-F]{2})', lambda match: chr(int(match[1], 16)), text)
    else:
        symbol = folder
    if symbol_folder(symbol) != folder:
        raise ValueError('invalid capture folder')
    return symbol


def capture_parent(warehouse, symbol, mode, start):
    """Place every generation below symbol / recording mode / year / month."""
    folder = symbol_folder(symbol)
    if mode not in ('BAR', 'TIMER'):
        raise ValueError('invalid capture mode')
    day = dt.date.fromisoformat(str(start)[:10])
    parent = Path(warehouse) / 'captures' / folder / mode / f'{day.year:04d}' / f'{day.month:02d}'
    warehouse_path(warehouse, parent.relative_to(warehouse).as_posix())
    return parent


def check_destination(warehouse, symbol, mode, start):
    """Probe the final generation/file length before starting expensive MT5 work."""
    # Storage conversion also resolves its parent before creating a generation.
    # Relative Windows paths can hit MAX_PATH even when the absolute path works.
    warehouse = Path(warehouse).resolve()
    parent = capture_parent(warehouse, symbol, mode, start)
    created = []
    cursor = parent
    while not cursor.exists() and cursor != Path(warehouse):
        created.append(cursor)
        cursor = cursor.parent
    suffix = uuid.uuid4().hex
    staging = parent / ('.partial_' + suffix)
    final = parent / ('0' * 64 + '_' + suffix)
    active = staging
    allocated = False
    filename = 'tester_tick_evidence.log'
    try:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
            allocated = True
            (staging / filename).write_bytes(b'path check')
            staging.rename(final)
            active = final
            if (final / filename).read_bytes() != b'path check':
                raise OSError('path probe verification failed')
        finally:
            # A rename may succeed although opening the longer final filename
            # fails. Use extended Windows syntax ONLY for cleanup, after the
            # same root-boundary check; never save it or bypass the real probe.
            if allocated:
                cleanup = _cleanup_path(warehouse_path(warehouse, active.relative_to(warehouse).as_posix()))
                (cleanup / filename).unlink(missing_ok=True)
                cleanup.rmdir()
            for path in created:
                try:_cleanup_path(path).rmdir()
                except OSError:pass
    except OSError:
        raise ValueError('이 창고 위치에서 종목의 최종 녹화 경로를 사용할 수 없습니다. '
                         '더 짧은 창고 경로와 폴더 쓰기 권한을 확인하세요. 녹화는 시작하지 않았습니다.') from None
    return parent


def _cleanup_path(path):
    """Runtime-only alias for our own already validated probe on Windows."""
    value = str(path)
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def organize_existing(catalog):
    """Move registered flat pieces and their catalog paths as one DB transaction."""
    root = Path(catalog.root).resolve(strict=True)
    capture_root = (root / 'captures').resolve(strict=True)
    if capture_root.parent != root or capture_root.is_symlink():
        raise ValueError('invalid capture root')
    rows = [json.loads(row[0]) for row in catalog.db.execute('SELECT metadata FROM captures').fetchall()]
    planned = []
    targets = set()
    for row in rows:
        old = warehouse_path(root, row['path'])
        if old.parent != capture_root:
            continue
        if not old.is_dir() or old.is_symlink() or old.is_junction():
            raise ValueError('capture path missing or linked: ' + row['capture_id'])
        parent = capture_parent(root, row['symbol'], row['mode'], row['start'])
        target = parent / old.name
        if target.exists() or target in targets or not parent.resolve().is_relative_to(capture_root):
            raise ValueError('capture destination conflict: ' + row['capture_id'])
        targets.add(target)
        planned.append((row, old, target))
    moved = []
    catalog.db.execute('BEGIN TRANSACTION')
    try:
        for row, old, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            old.rename(target)
            moved.append((old, target))
            updated = {**row, 'path': relative_path(root, target)}
            catalog.db.execute('UPDATE captures SET path=?, metadata=? WHERE capture_id=?',
                               [updated['path'], json.dumps(updated, ensure_ascii=False), row['capture_id']])
        catalog.db.execute('COMMIT')
    except BaseException:
        catalog.db.execute('ROLLBACK')
        for old, target in reversed(moved):
            target.rename(old)
        raise
    return [{'capture_id': row['capture_id'], 'before': relative_path(root, old),
             'after': relative_path(root, target)} for row, old, target in planned]
