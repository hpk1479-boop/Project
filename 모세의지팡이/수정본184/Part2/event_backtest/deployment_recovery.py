"""Durable EA restoration records; every stored path is relative to its root."""
from pathlib import Path
import json
import os
import shutil
import uuid

from .settings import digest, file_hash, relative_path, warehouse_path

JOURNAL = 'restore_pending.json'


def profile_key(profile):
    # An opaque machine identity avoids persisting terminal paths or account data.
    return digest({key: os.path.normcase(str(Path(profile[key]).resolve()))
                   for key in ('data_root', 'executable')})


def _write(path, data):
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def backup(profile, directory, targets):
    """Call only after closure was confirmed, before replacing any installed file."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / JOURNAL
    if journal.exists():
        raise RuntimeError('미완료 MT5 복구 기록이 있습니다. 원본 복구가 먼저 필요합니다.')
    rows = []
    for index, target in enumerate(targets):
        relative = relative_path(profile['data_root'], target)
        target = warehouse_path(profile['data_root'], relative)
        name = 'original_' + str(index)
        saved = warehouse_path(directory, name)
        row = {'target': relative, 'backup': None, 'sha256': None}
        if target.exists():
            shutil.copy2(target, saved)
            row.update(backup=name, sha256=file_hash(saved))
        rows.append(row)
    _write(journal, {'version': 1, 'profile_id': profile_key(profile), 'files': rows})
    return journal


def restore(profile, journal, targets):
    """Validate the entire mapping first; retain it on any failed restoration."""
    journal = Path(journal)
    data = json.loads(journal.read_text('utf-8'))
    if data.get('version') != 1 or data.get('profile_id') != profile_key(profile):
        raise ValueError('MT5 복구 기록의 대상이 현재 선택과 다릅니다.')
    allowed = {relative_path(profile['data_root'], path) for path in targets}
    rows = data.get('files')
    if not isinstance(rows, list) or {row.get('target') for row in rows} != allowed or len(rows) != len(allowed):
        raise ValueError('MT5 복구 대상 목록이 올바르지 않습니다.')
    verified = []
    for row in rows:
        target = warehouse_path(profile['data_root'], row['target'])
        saved = None if row['backup'] is None else warehouse_path(journal.parent, row['backup'])
        if saved is not None and (not saved.is_file() or file_hash(saved) != row['sha256']):
            raise ValueError('MT5 원본 백업 검증에 실패했습니다. 복구 기록을 보존합니다.')
        verified.append((target, saved, row['sha256']))
    for target, saved, sha in verified:
        if saved is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.restore_' + uuid.uuid4().hex)
            try:
                shutil.copy2(saved, temporary)
                if file_hash(temporary) != sha:
                    raise ValueError('MT5 복구 복사 검증에 실패했습니다.')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    journal.unlink()


def pending_for(profile, directory):
    """Find this installation's journals without changing terminal files."""
    key = profile_key(profile)
    pending = []
    for journal in sorted(Path(directory).glob('*/' + JOURNAL)):
        # Resolve links before reading any backup mapping outside the work root.
        warehouse_path(directory, journal.relative_to(directory).as_posix())
        data = json.loads(journal.read_text('utf-8'))
        if data.get('profile_id') == key:
            pending.append(journal)
    if len(pending) > 1:
        raise RuntimeError('같은 MT5의 미완료 복구 기록이 여러 개입니다. 새 백업을 만들지 않습니다.')
    return pending


def recover_pending(profile, directory, targets, *, emit=lambda *a: None):
    """Restore previous attempts before this run is allowed to make new backups."""
    pending = pending_for(profile, directory)
    for journal in pending:
        restore(profile, journal, targets)
        emit('MT5_RESTORED', {'message': '이전 실행의 EA·지표 원본 복구를 완료했습니다.'})
