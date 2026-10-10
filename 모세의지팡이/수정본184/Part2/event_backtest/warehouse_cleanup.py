"""Remove only recognisable, abandoned work; recorded data and results stay put."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid


_GUARD = '.warehouse_cleanup.lock'
_ACTIVITY = '.warehouse_activity'
_UUID = re.compile(r'[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_PARTIAL = re.compile(r'\.partial_[0-9a-f]{32}\Z')
_MQL_STEMS = ('PRICE_of_Moses', 'RSI_of_Moses', 'STO_of_Moses',
              'DI_of_Moses', 'THE_STAFF_OF_MOSES')
_MQL_FILES = {stem + suffix for stem in _MQL_STEMS
              for suffix in ('.mq5', '.ex5', '.compile.log', '.ex5.backup')}
_MQL_FILES.update(('STAFF_Identity_Status.mqh', 'STAFF_Wire_Schema.mqh',
                   'STAFF_Wire_V2.mqh', 'STAFF_Symbol_Map.mqh'))
_CAPTURE_TEMP_FILES = {'capture.delta2', 'capture.delta.gz', 'storage.json',
                       'manifest.tsv', 'seq_gaps.jsonl', 'tester_tick_evidence.log'}
_TERMINAL = {'complete', 'cancelled', 'error', 'interrupted', 'planned'}
_JOB_PHASES = _TERMINAL | {'planning', 'plan', 'starting', 'run', 'confirm'}


class _Busy(OSError):
    pass


def _plain(path):
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
        raise ValueError('linked path')
    return value


def _root(path, *, create=False):
    path = Path(path).expanduser().absolute()
    # Resolving first would hide a junction or symlink at the selected root.
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            _plain(parent)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(_plain(path).st_mode):
        raise ValueError('warehouse is not a directory')
    return path.resolve(strict=True)


def _checked(root, path):
    relative = path.relative_to(root)
    if not relative.parts or any(part in ('.', '..') for part in relative.parts):
        raise ValueError('unsafe maintenance path')
    cursor = root
    _plain(cursor)
    for part in relative.parts:
        cursor /= part
        _plain(cursor)
    if path.resolve(strict=True) != path or not path.is_relative_to(root):
        raise ValueError('maintenance path escaped the warehouse')
    return path


@contextmanager
def _file_lock(path, *, timeout=0):
    # This is the same one-byte OS lock protocol as the durable job owner.
    if path.exists() or path.is_symlink():
        _plain(path)
    with path.open('a+b') as handle:
        _plain(path)
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise _Busy('warehouse work is active') from error
                time.sleep(.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def warehouse_activity(warehouse):
    """Hold a live lease through planning/build/replay without serialising jobs."""
    root = _root(warehouse, create=True)
    with _file_lock(root / _GUARD, timeout=15):
        directory = root / _ACTIVITY
        directory.mkdir(exist_ok=True)
        _checked(root, directory)
        lease = directory / (uuid.uuid4().hex + '.lock')
        lock = _file_lock(lease)
        lock.__enter__()
    try:
        yield
    finally:
        lock.__exit__(None, None, None)
        try:
            _checked(root, lease).unlink()
        except (OSError, ValueError):
            pass  # A later cleanup can remove the released lease.


@contextmanager
def warehouse_idle(warehouse):
    """Hold the maintenance guard while no planning, build or replay holds a lease (수정본163).

    Raises an OSError when work is active or the guard is taken. New work waits for the guard,
    so it cannot begin while the caller's short changes are under way.
    """
    root = _root(warehouse)
    with _file_lock(root / _GUARD):
        activity = root / _ACTIVITY
        if activity.exists():
            _checked(root, activity)
            for lease in activity.iterdir():
                _checked(root, lease)
                if not re.fullmatch(r'[0-9a-f]{32}\.lock', lease.name):
                    raise _Busy('unknown activity marker')
                with _file_lock(lease):
                    pass  # Released: its work has ended. A later cleanup removes the file.
        yield root


def _snapshot(root, path, allow):
    """Validate the whole candidate before deleting any of its files."""
    _checked(root, path)
    files, directories = [], []
    def visit(folder, relative):
        directories.append((folder, _plain(folder)))
        for child in folder.iterdir():
            _checked(root, child)
            info = _plain(child)
            name = relative / child.name
            if not allow(name, stat.S_ISDIR(info.st_mode)):
                raise ValueError('unrecognised work contents')
            if stat.S_ISDIR(info.st_mode):
                visit(child, name)
            elif stat.S_ISREG(info.st_mode):
                files.append((child, info))
            else:
                raise ValueError('non-regular work contents')
    visit(path, Path())
    return files, directories


@contextmanager
def _unused(path):
    """Windows sharing checks catch open logs/files before the first deletion."""
    if os.name != 'nt':
        with path.open('rb') as handle:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    # DELETE access + FILE_SHARE_DELETE permits our unlink but excludes readers
    # and writers, including log handles that normally permit shared reading.
    handle = create(str(path), 0x80000000 | 0x00010000, 4, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'work file is in use')
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def _remove(root, path, allow, report):
    relative = path.relative_to(root).as_posix()
    try:
        files, directories = _snapshot(root, path, allow)
        with ExitStack() as stack:
            for file, _ in files:
                stack.enter_context(_unused(file))
            for file, before in files:
                now = _plain(_checked(root, file))
                if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (
                        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                    raise ValueError('work contents changed')
            for file, info in files:
                _checked(root, file).unlink()
                report['removed_bytes'] += info.st_size
        for folder, before in reversed(directories):
            now = _plain(_checked(root, folder))
            if (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('work directory changed')
            folder.rmdir()
        report['removed'].append(relative)
    except (OSError, ValueError):
        report['skipped'].append({'path': relative, 'reason': 'unrecognised_or_in_use'})


def _build_run_file(path, directory):
    parts = path.parts
    if len(parts) == 1:
        return bool(_HASH.fullmatch(parts[0]) or parts[0] == 'deploy') if directory else bool(
            re.fullmatch(r'original_\d+', parts[0]))
    if len(parts) != 2 or directory:
        return False
    if _HASH.fullmatch(parts[0]):
        return bool(re.fullmatch(r'native_[0-9a-f]{32}\.ini', parts[1]))
    return parts[0] == 'deploy' and parts[1] in _MQL_FILES


def _identity_alive(identity):
    if not isinstance(identity, dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0:
        return None
    if os.name != 'nt':
        return None  # These legacy records contain Windows FILETIME identities.
    from .settings import PROGRAM
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    from event_lifecycle import process_identity
    try:
        current = process_identity(identity['pid'])
        if current is None:
            return False
        recorded = identity.get('created')
        if not isinstance(recorded, str) or not recorded.isdigit():
            return None
        return int(current) // 10 == int(recorded) // 10
    except (OSError, ValueError, TypeError):
        return None


def _legacy_jobs(root, stack, job_alive):
    """Keep copied results; short job locks guard a stable ownership snapshot."""
    directory = root / 'runs'
    if not directory.exists():
        return []
    _checked(root, directory)
    idle = []
    for folder in directory.iterdir():
        _checked(root, folder)
        if not folder.is_dir() or not _UUID.fullmatch(folder.name):
            continue
        lock = folder / '.job.lock'
        if lock.exists():
            _checked(root, lock)
            stack.enter_context(_file_lock(lock))
        job = folder / 'job.json'
        if not job.exists():
            # A UUID work folder with no durable owner can still be being created.
            if (folder / 'result.json').exists():
                idle.append(folder)
            continue
        _checked(root, job)
        value = json.loads(job.read_text('utf-8'))
        if not isinstance(value, dict) or value.get('phase') not in _JOB_PHASES:
            raise _Busy('a job is active or uncertain')
        identities = [value.get(name) for name in ('supervisor', 'process') if value.get(name)]
        if value['phase'] not in _TERMINAL and not identities:
            raise _Busy('a job publication is uncertain')
        for identity in identities:
            if job_alive(identity) is not False:
                raise _Busy('a job process is active or uncertain')
        idle.append(folder)
    return idle


def _capture_partials(root):
    captures = root / 'captures'
    if not captures.exists():
        return
    _checked(root, captures)
    # Walk folder layout only. Never enumerate files inside published captures.
    def visit(folder, depth):
        for child in folder.iterdir():
            _checked(root, child)
            if not child.is_dir():
                continue
            if _PARTIAL.fullmatch(child.name):
                yield child
            elif depth < 4 and ((depth == 0 and child.name not in ('.', '..')) or
                    (depth == 1 and child.name in ('BAR', 'TIMER')) or
                    (depth == 2 and re.fullmatch(r'\d{4}', child.name)) or
                    (depth == 3 and re.fullmatch(r'\d{2}', child.name))):
                yield from visit(child, depth + 1)
    yield from visit(captures, 0)


def cleanup_warehouse(warehouse, *, keep_builds=(), job_alive=None, emit=None):
    """Best-effort maintenance, with relative-only reporting and no DuckDB writes."""
    report = {'removed': [], 'removed_bytes': 0, 'skipped': [], 'busy': False}
    try:
        root = _root(warehouse)
        with _file_lock(root / _GUARD), ExitStack() as locks:
            activity = root / _ACTIVITY
            if activity.exists():
                _checked(root, activity)
                for lease in activity.iterdir():
                    _checked(root, lease)
                    if not re.fullmatch(r'[0-9a-f]{32}\.lock', lease.name):
                        raise _Busy('unknown activity marker')
                    with _file_lock(lease):
                        pass  # A copied PID is irrelevant; its OS lock is released.
                    lease.unlink()
            jobs = _legacy_jobs(root, locks, job_alive or _identity_alive)
            build_runs = root / 'build_runs'
            if build_runs.exists():
                _checked(root, build_runs)
                for folder in build_runs.iterdir():
                    _checked(root, folder)
                    if folder.is_dir() and _UUID.fullmatch(folder.name):
                        # Pending restoration and its backup are never temporary.
                        if any((folder / name).exists() for name in (
                                'restore_pending.json', 'restore_pending.json.partial', 'history_missing.json')):
                            report['skipped'].append({'path': folder.relative_to(root).as_posix(),
                                                      'reason': 'recovery_or_diagnosis'})
                        else:
                            _remove(root, folder, _build_run_file, report)
            builds = root / 'builds'
            if builds.exists():
                _checked(root, builds)
                for folder in builds.iterdir():
                    _checked(root, folder)
                    if (folder.is_dir() and _HASH.fullmatch(folder.name) and
                            folder.name not in keep_builds and not (folder / 'ready.json').exists()):
                        _remove(root, folder, lambda p, d: not d and len(p.parts) == 1
                                and p.name in _MQL_FILES, report)
            for folder in _capture_partials(root):
                if (folder / 'complete.txt').exists():
                    report['skipped'].append({'path': folder.relative_to(root).as_posix(),
                                              'reason': 'completed_capture'})
                else:
                    _remove(root, folder, lambda p, d: not d and len(p.parts) == 1 and (
                        p.name in _CAPTURE_TEMP_FILES or bool(re.fullmatch(r'pipe_\d+\.bin(?:\.gz)?', p.name))), report)
            for folder in jobs:
                for file in folder.iterdir():
                    if re.fullmatch(r'\.job-[A-Za-z0-9_-]+\.tmp', file.name) or file.name == 'stop.request':
                        try:
                            _checked(root, file)
                            with _unused(file):
                                size = file.stat().st_size
                                file.unlink()
                            report['removed'].append(file.relative_to(root).as_posix())
                            report['removed_bytes'] += size
                        except (OSError, ValueError):
                            report['skipped'].append({'path': file.relative_to(root).as_posix(), 'reason': 'in_use'})
    except _Busy:
        report['busy'] = True
    except (OSError, ValueError):
        report['skipped'].append({'path': '.', 'reason': 'unavailable_or_unsafe'})
    if emit is not None and (report['removed'] or report['busy'] or report['skipped']):
        emit('WAREHOUSE_CLEANUP', report)
    return report
