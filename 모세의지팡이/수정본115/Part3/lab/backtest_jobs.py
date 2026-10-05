"""Durable job ownership and web transport for the unchanged Part2 engine.

The supervisor owns stdout and progress. Servers send durable stop requests
and wait for saved results before an intentional application shutdown.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
VERSION = 1
TERMINAL = frozenset(('complete', 'cancelled', 'error', 'interrupted', 'planned'))
ACTIVE_PHASES = frozenset(('planning', 'plan', 'starting', 'run'))
_LOCAL_LOCKS = {}
_LOCAL_GUARD = threading.Lock()
_WORK_GUARD = threading.RLock()
_closing = False
_SESSION_CONTEXTS = {}
_SHUTDOWN_PENDING = {}


class ShutdownPending(RuntimeError):
    """The stop request remains valid; the desktop keeps checking automatically."""


class ForceShutdownRequested(RuntimeError):
    """The close worker should switch from saving to an approved forced close."""


@contextmanager
def _accept_work():
    # Publication must finish before shutdown takes its inventory. This gate
    # covers every caller (ordinary UI, generated strategies and AI commands).
    with _WORK_GUARD:
        if _closing:
            raise ValueError('프로그램 종료 중입니다. 새 백테스트를 시작할 수 없습니다.')
        yield


def _remember_context(warehouse, project_root, python_executable):
    context = dict(warehouse=warehouse, project_root=project_root, python_executable=python_executable)
    # Runtime only: no machine paths are added to portable job records.
    _SESSION_CONTEXTS[str(warehouse).casefold()] = context


def _identifier(identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('실행 ID를 확인하세요.')
    return identifier


def _context(warehouse=None, project_root=None, python_executable=None):
    if warehouse is None or project_root is None or python_executable is None:
        from . import storage
        connections = storage.connections()
        if project_root is None:
            project_root = storage.project_path() or ROOT
        if python_executable is None:
            python_executable = connections.get('python_executable') or sys.executable
        if warehouse is None:
            from . import unified_backtest
            warehouse = unified_backtest.warehouse()
    return Path(warehouse).expanduser().resolve(), Path(project_root).resolve(), str(python_executable)


def _folder(warehouse, identifier):
    return Path(warehouse).resolve() / 'runs' / _identifier(identifier)


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.job-', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, separators=(',', ':'))
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def job_lock(folder, timeout=15):
    """One cross-process lock covers cancellation, Popen and publication."""
    folder = Path(folder)
    key = str(folder.resolve()).casefold()
    with _LOCAL_GUARD:
        local = _LOCAL_LOCKS.setdefault(key, threading.RLock())
    with local:
        path = folder / '.job.lock'
        with path.open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('다른 요청이 이 백테스트를 처리 중입니다. 잠시 후 다시 시도하세요.')
                    time.sleep(.02)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _clean(value, warehouse, project_root):
    if isinstance(value, dict):
        return {str(k): _clean(v, warehouse, project_root) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, warehouse, project_root) for v in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        for root, label in ((warehouse, '<warehouse>'), (project_root, '<project>')):
            for representation in (str(root), json.dumps(str(root), ensure_ascii=False)[1:-1],
                                   json.dumps(str(root), ensure_ascii=True)[1:-1]):
                value = value.replace(representation, label)
        return re.sub(r'[A-Za-z]:[\\/][^\s\"\n]+', lambda m: PureWindowsPath(m[0]).name, value)
    return value


def _read(folder):
    try:
        value = json.loads((folder / 'job.json').read_text('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError('백테스트 실행 기록을 읽지 못했습니다.') from exc
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != VERSION or
            value.get('id') != folder.name or value.get('kind') not in ('normal', 'generated') or
            value.get('phase') not in ACTIVE_PHASES | TERMINAL | {'confirm'} or
            value.get('scenario_file') != 'web_scenario.json' or
            value.get('result_path') != 'runs/' + folder.name + '/result.json' or
            type(value.get('cancel_requested')) is not bool or
            not isinstance(value.get('scenario'), dict) or not isinstance(value.get('adapter'), dict)):
        raise ValueError('백테스트 실행 기록 형식이 올바르지 않습니다.')
    return value


def _write(folder, record):
    record['updated_at'] = time.time()
    _atomic(folder / 'job.json', record)


def _windows_pid_present(pid):
    """A failed identity query is not proof that a process is still running."""
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS only.
    if snapshot == ctypes.c_void_p(-1).value or snapshot is None:
        raise OSError(ctypes.get_last_error(), '백테스트 프로세스 목록을 확인하지 못했습니다.')
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ProcessID == int(pid):
                return True
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES; any other error leaves status unknown.
            raise OSError(error, '백테스트 프로세스 목록 조회가 중단됐습니다.')
        return False
    finally:
        kernel.CloseHandle(snapshot)


def _windows_process_snapshot():
    """Read precise creation identities even for protected/system processes.

    A system process can reuse a former worker's PID while OpenProcess is
    denied. SystemProcessInformation exposes its creation FILETIME without
    opening or changing that process. Names are never used as identities.
    """
    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                    ('Buffer', ctypes.c_void_p)]

    class ProcessHeader(ctypes.Structure):
        _fields_ = [('NextEntryOffset', wintypes.ULONG), ('NumberOfThreads', wintypes.ULONG),
                    ('WorkingSetPrivateSize', ctypes.c_int64), ('HardFaultCount', wintypes.ULONG),
                    ('NumberOfThreadsHighWatermark', wintypes.ULONG), ('CycleTime', ctypes.c_uint64),
                    ('CreateTime', ctypes.c_int64), ('UserTime', ctypes.c_int64),
                    ('KernelTime', ctypes.c_int64), ('ImageName', UnicodeString),
                    ('BasePriority', wintypes.LONG), ('UniqueProcessId', ctypes.c_void_p),
                    ('InheritedFromUniqueProcessId', ctypes.c_void_p)]

    query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
    query.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = ctypes.c_long
    size = 1024 * 1024
    while size <= 64 * 1024 * 1024:
        buffer = ctypes.create_string_buffer(size)
        used = wintypes.ULONG()
        status = query(5, buffer, size, ctypes.byref(used))
        if status == 0:
            break
        if status & 0xffffffff != 0xc0000004:  # STATUS_INFO_LENGTH_MISMATCH.
            raise OSError(status & 0xffffffff, '프로세스 생성시각 목록을 확인하지 못했습니다.')
        size = max(size * 2, used.value + 65536)
    else:
        raise OSError('프로세스 생성시각 목록이 너무 큽니다.')
    rows, offset = {}, 0
    limit = min(size, used.value or size)
    while offset + ctypes.sizeof(ProcessHeader) <= limit:
        row = ProcessHeader.from_buffer(buffer, offset)
        pid = int(row.UniqueProcessId or 0)
        if pid > 0:
            rows[pid] = {'pid': pid, 'created': str(row.CreateTime),
                         'parent_pid': int(row.InheritedFromUniqueProcessId or 0)}
        if not row.NextEntryOffset:
            return rows
        if row.NextEntryOffset < ctypes.sizeof(ProcessHeader):
            break
        offset += row.NextEntryOffset
    raise OSError('프로세스 생성시각 목록 형식을 확인하지 못했습니다.')


def _windows_identity_fallback(pid):
    row = _windows_process_snapshot().get(int(pid))
    return {'pid': row['pid'], 'created': row['created']} if row else None


def process_identity(pid, handle=None):
    """Windows FILETIME identity; absence is different from query failure."""
    if os.name != 'nt':
        # Linux is useful for offline tests; production identities use FILETIME.
        try:
            data = Path('/proc') / str(int(pid)) / 'stat'
            fields = data.read_text().rsplit(')', 1)[1].split()
            return {'pid': int(pid), 'created': fields[19]}
        except FileNotFoundError:
            return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    own = handle is None
    if own:
        handle = kernel.OpenProcess(0x00101000, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == 87 or not _windows_pid_present(pid):
            return None
        raise OSError(error, '백테스트 프로세스를 확인하지 못했습니다.')
    try:
        waited = kernel.WaitForSingleObject(handle, 0)
        if waited == 0:
            return None
        if waited != 258:
            error = ctypes.get_last_error()
            if own and not _windows_pid_present(pid):
                return None
            raise OSError(error, '백테스트 프로세스 종료 상태를 확인하지 못했습니다.')
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            error = ctypes.get_last_error()
            if own and not _windows_pid_present(pid):
                return None
            raise OSError(error, '백테스트 시작 시간을 확인하지 못했습니다.')
        return {'pid': int(pid), 'created': str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)}
    finally:
        if own:
            kernel.CloseHandle(handle)


def _alive(identity):
    if not isinstance(identity, dict) or type(identity.get('pid')) is not int:
        return False
    created = str(identity.get('created', ''))
    known_creation = created.isdigit() and int(created) > 0
    try:
        actual = process_identity(identity['pid'])
        if actual and not known_creation:
            return None
        return bool(actual and actual['created'] == created)
    except OSError as exc:
        if os.name == 'nt' and isinstance(exc.errno, int):
            try:
                actual = _windows_identity_fallback(identity['pid'])
                if actual and not known_creation:
                    return None
                return bool(actual and actual['created'] == created)
            except (OSError, AttributeError):
                pass
        return None


def _launch(folder, record, project_root, warehouse, python_executable, action):
    token = uuid.uuid4().hex
    record['supervisor'] = {'run_id': token, 'pid': None, 'created': None}
    _write(folder, record)
    args = [python_executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('backtest_supervisor.py')),
            '--warehouse', str(warehouse), '--project-root', str(project_root),
            '--job-id', record['id'], '--run-id', token, '--action', action,
            '--python', python_executable]
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    process = None
    try:
        with (folder / 'supervisor.log').open('a', encoding='utf-8') as output:
            process = subprocess.Popen(args, cwd=project_root, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0) |
                               getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        identity = process_identity(process.pid, getattr(process, '_handle', None))
        if identity is None:
            raise ValueError('백테스트 관리 프로세스가 시작 직후 종료되었습니다.')
        record['supervisor'] = {**identity, 'run_id': token}
        _write(folder, record)
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()  # This exact Popen child has not yet acquired the job lock.
            process.wait(timeout=3)
        record['phase'] = 'error'
        record['message'] = _clean(str(exc), warehouse, project_root)
        _write(folder, record)
        raise ValueError('백테스트 관리 프로세스를 시작하지 못했습니다.') from exc


def start(request):
    with _accept_work():
        return _start(request)


def _start(request):
    if not isinstance(request, dict) or request.get('kind', 'normal') not in ('normal', 'generated'):
        raise ValueError('백테스트 작업 종류를 확인하세요.')
    scenario, adapter = request.get('scenario'), request.get('adapter', {})
    if not isinstance(scenario, dict) or not isinstance(adapter, dict):
        raise ValueError('백테스트 실행 내용을 확인하세요.')
    warehouse, project_root, python_executable = _context(request.get('warehouse'), request.get('project_root'),
                                                         request.get('python_executable'))
    _remember_context(warehouse, project_root, python_executable)
    for path in (project_root / 'Part2', project_root / 'Part1/program'):
        if str(path) not in sys.path:sys.path.insert(0, str(path))
    from event_backtest.warehouse_cleanup import cleanup_warehouse,warehouse_activity
    # Clean before publishing our own live owner. The publication lease covers
    # the interval before its durable supervisor identity becomes available.
    cleanup_warehouse(warehouse,job_alive=_alive)
    with warehouse_activity(warehouse):
        identifier = uuid.uuid4().hex
        folder = _folder(warehouse, identifier)
        folder.mkdir(parents=True, exist_ok=False)
        _atomic(folder / 'web_scenario.json', scenario)
        record = {'version': VERSION, 'id': identifier, 'kind': request.get('kind', 'normal'),
            'scenario': scenario, 'scenario_file': 'web_scenario.json', 'adapter': adapter,
            'rebuild': bool(request.get('rebuild')), 'auto_run': not request.get('plan_only') and request.get('auto_run', True),
            'phase': 'planning', 'created_at': time.time(), 'updated_at': time.time(),
            'cancel_requested': False, 'plan': None, 'progress': None, 'message': '',
            'supervisor': None, 'process': None, 'returncode': None,
            'result_path': 'runs/' + identifier + '/result.json'}
        with job_lock(folder):
            _write(folder, record)
            _launch(folder, record, project_root, warehouse, python_executable, 'plan')
    return {'job_id': identifier, 'kind': record['kind'], 'phase': record['phase']}


def _legacy(folder):
    try:
        result = json.loads((folder / 'result.json').read_text('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError('이 창고에서 실행 기록을 찾지 못했습니다.') from exc
    if not isinstance(result, dict):
        raise ValueError('백테스트 결과 형식을 확인하세요.')
    scenario = result.get('scenario') or {}
    strategies = scenario.get('strategies') or []
    generated = next((name for name in strategies if re.fullmatch(r'Test_SPECIAL\d{3}', str(name))), None)
    phase = {'COMPLETE': 'complete', 'CANCELLED': 'cancelled', 'FAILED': 'error'}.get(result.get('status'), 'complete')
    return {'version': VERSION, 'id': folder.name, 'kind': 'generated' if generated else 'normal',
        'scenario': scenario, 'adapter': {'filename': generated + '.py'} if generated else {},
        'phase': phase, 'cancel_requested': phase == 'cancelled', 'plan': None, 'progress': None,
        'supervisor': None, 'process': None, 'message': '기존 결과를 불러왔습니다.',
        'created_at': (folder / 'result.json').stat().st_mtime,
        'updated_at': (folder / 'result.json').stat().st_mtime, 'legacy': True}


def get(identifier, *, warehouse=None, project_root=None, python_executable=None):
    warehouse, project_root, python_executable = _context(warehouse, project_root, python_executable)
    folder = _folder(warehouse, identifier)
    if not folder.is_dir():
        raise ValueError('이 창고에서 실행 기록을 찾지 못했습니다.')
    with job_lock(folder):
        record = _read(folder) if (folder / 'job.json').exists() else _legacy(folder)
        owner, child = _alive(record.get('supervisor')), _alive(record.get('process'))
        if record['phase'] in ACTIVE_PHASES | {'interrupted'} and owner is False:
            if child is not False:
                record['phase'] = 'interrupted'
                record['message'] = '작업 관리 연결이 중단되었습니다. 기존 정지 요청과 부분 결과를 확인할 수 있습니다.'
            elif (folder / 'result.json').is_file():
                recovered = _legacy(folder)
                record['phase'] = recovered['phase']
                record['message'] = '남아 있는 Part2 결과 파일로 실행 상태를 복구했습니다.'
            else:
                record['phase'] = 'cancelled' if record.get('cancel_requested') else 'interrupted'
                record['message'] = '진행 중이던 프로세스가 종료되었습니다. 새 실행을 자동으로 시작하지 않습니다.'
            if not record.get('legacy'):
                _write(folder, record)
    return {**record, 'folder': folder, 'warehouse': warehouse, 'project_root': project_root,
            'python_executable': python_executable, 'scenario_path': folder / 'web_scenario.json',
            'active': owner is not False or child is not False,
            'identity_warning': owner is None or child is None}


def _plan_public(job):
    plan = job.get('plan')
    if job['phase'] not in ('confirm', 'planned') or not isinstance(plan, dict):
        return None
    public = {key: plan.get(key, [] if key != 'estimate' else {}) for key in ('record', 'convert', 'estimate')}
    change = plan.get('period_adjustment')
    public['period_adjustment'] = ({key: change[key] for key in
        ('requested_start', 'requested_end', 'start', 'end', 'reason', 'message') if key in change}
        if isinstance(change, dict) else None)
    for name in ('available_periods', 'excluded_periods'):
        public[name] = [{key: row[key] for key in ('start', 'end', 'reason', 'message') if key in row}
            for row in (plan.get(name) or []) if isinstance(row, dict)]
    return public


def _revision(plan):
    return hashlib.sha256(str((plan or {}).get('approval_token', '')).encode()).hexdigest()[:24]


def status(identifier, **context):
    job = get(identifier, **context)
    scenario = job['scenario']
    return {'job_id': identifier, 'kind': job['kind'], 'phase': job['phase'], 'message': job.get('message', ''),
        'cancel_requested': job['cancel_requested'],
        'plan': _plan_public(job), 'plan_revision': _revision(job.get('plan')) if job.get('plan') else None,
        'progress': job.get('progress'), 'result_ready': (job['folder'] / 'result.json').is_file(),
        'active': job['active'], 'recoverable': True, 'source': job.get('adapter', {}).get('filename') or
            ', '.join(scenario.get('strategies') or []) or ('데이터 구축' if scenario.get('build_only') else 'WATCH'),
        'filename': job.get('adapter', {}).get('filename'), 'strategies': scenario.get('strategies', []),
        'symbol': scenario.get('symbol'), 'start': scenario.get('start'), 'end': scenario.get('end'),
        'mode': scenario.get('mode'), 'created_at': job.get('created_at'), 'updated_at': job.get('updated_at'),
        'scenario': {**{k: scenario.get(k) for k in ('symbol', 'start', 'end', 'mode', 'result_mode', 'build_only')},
                     'available_only': scenario.get('available_only', False)},
        'warnings': ['프로세스 상태 조회가 지연되어 종료 여부를 단정하지 않았습니다.'] if job['identity_warning'] else []}


def recent(limit=20, **context):
    warehouse, project_root, python_executable = _context(context.get('warehouse'), context.get('project_root'),
                                                         context.get('python_executable'))
    try:
        limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        raise ValueError('실행 목록 개수를 확인하세요.') from None
    root = warehouse / 'runs'
    if not root.is_dir():
        return {'items': [], 'warnings': []}
    folders = sorted((p for p in root.iterdir() if p.is_dir() and re.fullmatch('[0-9a-f]{32}', p.name)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    items, warnings = [], []
    for folder in folders:
        try:
            items.append(status(folder.name, warehouse=warehouse, project_root=project_root,
                                python_executable=python_executable))
        except ValueError:
            warnings.append(folder.name + ': 실행 기록을 읽지 못했습니다.')
        if len(items) >= limit:
            break
    return {'items': items, 'warnings': warnings[:20]}


def reconnect(identifier, **context):
    return status(identifier, **context)


def _deletion_folder(warehouse, identifier):
    """Only ordinary directories under this warehouse's runs can be removed."""
    warehouse = Path(warehouse).resolve()
    runs = warehouse / 'runs'
    folder = runs / identifier
    if runs.resolve() != runs or folder.resolve() != folder or not folder.is_dir():
        raise ValueError('이 창고의 백테스트 기록 폴더를 확인하세요.')
    # Never traverse a junction/symlink to original captures or another folder.
    remaining = [folder]
    while remaining:
        current = remaining.pop()
        for entry in current.iterdir():
            if entry.is_symlink() or entry.resolve() != entry:
                raise ValueError('다른 폴더로 연결된 파일이 있어 백테스트 기록을 삭제하지 않았습니다.')
            if entry.is_dir():
                remaining.append(entry)
    return folder


def delete_selected(identifiers, **context):
    """Delete selected finished run records/results; never follow result paths."""
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 100:
        raise ValueError('삭제할 백테스트를 선택하세요. 한 번에 100개까지 삭제할 수 있습니다.')
    identifiers = list(dict.fromkeys(_identifier(value) for value in identifiers))
    warehouse, project_root, python_executable = _context(context.get('warehouse'),
        context.get('project_root'), context.get('python_executable'))
    deleted, errors = [], []
    with _accept_work():
        for identifier in identifiers:
            try:
                folder = _deletion_folder(warehouse, identifier)
                with job_lock(folder):
                    record = _read(folder) if (folder / 'job.json').is_file() else _legacy(folder)
                    if record['phase'] not in TERMINAL or any(
                            _alive(record.get(key)) is not False for key in ('supervisor', 'process')):
                        raise ValueError('실행 중이거나 종료를 확인하지 못한 백테스트는 삭제할 수 없습니다.')
                    _deletion_folder(warehouse, identifier)
                # Terminal jobs cannot be confirmed/relaunched. Release the file
                # lock before removing its file (Windows does not unlink open files).
                shutil.rmtree(_deletion_folder(warehouse, identifier))
                deleted.append(identifier)
            except (OSError, ValueError) as exc:
                errors.append({'job_id': identifier, 'message': _clean(str(exc), warehouse, project_root)})
    return {'ok': not errors, 'deleted': deleted, 'errors': errors}


def confirm(identifier, plan_revision=None, **context):
    with _accept_work():
        return _confirm(identifier, plan_revision, **context)


def _confirm(identifier, plan_revision=None, **context):
    job = get(identifier, **context)
    _remember_context(job['warehouse'], job['project_root'], job['python_executable'])
    with job_lock(job['folder']):
        record = _read(job['folder'])
        if record['phase'] != 'confirm' or record.get('cancel_requested'):
            raise ValueError('확인 대기 중인 구축 계획이 없습니다.')
        if plan_revision is not None and plan_revision != _revision(record.get('plan')):
            raise ValueError('데이터 구축 계획이 변경되었습니다. 새 계획을 확인하고 다시 승인하세요.')
        if not (record.get('plan') or {}).get('approval_token'):
            raise ValueError('데이터 구축 승인 정보를 확인하지 못했습니다.')
        record['phase'] = 'starting'
        _write(job['folder'], record)
        _launch(job['folder'], record, job['project_root'], job['warehouse'], job['python_executable'], 'run')
    return {'ok': True, 'job_id': identifier, 'phase': 'starting'}


def stop(identifier, *, force=False, **context):
    if type(force) is not bool:
        raise ValueError('강제 종료 선택을 확인하세요.')
    if force:
        return force_stop(identifier, **context)
    job = get(identifier, **context)
    with job_lock(job['folder']):
        record = _read(job['folder']) if not job.get('legacy') else job
        if record['phase'] in ('complete', 'cancelled', 'error', 'planned') and not job['active']:
            return {'ok': True, 'message': '이미 종료된 작업입니다.'}
        record['cancel_requested'] = True
        phase = record['phase']
        # The durable cancellation precedes acknowledgement. A later Popen
        # checks this same record under this same lock and cannot pass it.
        if phase in ('planning', 'plan', 'confirm', 'starting') and not record.get('process'):
            record['phase'] = 'cancelled'
        if phase in ('run', 'interrupted') or (record.get('process') or {}).get('action') == 'run':
            (job['folder'] / 'stop.request').write_text('STOP\n', encoding='ascii')
        if not job.get('legacy'):
            _write(job['folder'], record)
    return {'ok': True, 'message': '종료 중입니다. 진행 내용을 저장하고 있습니다.'}


def log(identifier, **context):
    from .log_tail import read_tail
    job = get(identifier, **context)
    path = job['folder'] / 'console.log'
    if not path.is_file():
        path = job['folder'] / 'progress.jsonl'
    return {'text': read_tail(path), 'available': path.is_file()}


def active_jobs(**context):
    """All owned jobs in the selected warehouse, without the recent-list limit."""
    if not context:
        with _WORK_GUARD:
            contexts = list(_SESSION_CONTEXTS.values())
        warehouse, project_root, python_executable = _context()
        contexts.append(dict(warehouse=warehouse, project_root=project_root, python_executable=python_executable))
        found = {}
        for selected in contexts:
            for job in active_jobs(**selected):
                found[(str(job['warehouse']).casefold(), job['id'])] = job
        return list(found.values())
    warehouse, project_root, python_executable = _context(context.get('warehouse'),
        context.get('project_root'), context.get('python_executable'))
    root = warehouse / 'runs'
    if not root.is_dir():
        return []
    active = []
    for folder in root.iterdir():
        if not (folder.is_dir() and re.fullmatch('[0-9a-f]{32}', folder.name)
                and (folder / 'job.json').is_file()):
            continue
        job = get(folder.name, warehouse=warehouse, project_root=project_root,
                  python_executable=python_executable)
        if job['active'] or job['phase'] in ACTIVE_PHASES | {'confirm'}:
            active.append(job)
    return active


def begin_shutdown():
    """Freeze publication before taking the application's close inventory."""
    global _closing
    with _WORK_GUARD:
        _closing = True


def shutdown_pending_jobs():
    """Read close-only saved-result waits without changing ordinary job lists."""
    return [{'id': identifier, **job_context, 'running': running}
            for (_warehouse, identifier), (job_context, running) in list(_SHUTDOWN_PENDING.items())]


def shutdown(timeout=45, force_requested=None, **context):
    """Cooperatively stop every known job, then verify process exit and saving.

    A timeout does not force-kill Part2 or discard its output. The desktop
    automatically retries while the existing stop request and start gate stay valid.
    """
    global _closing
    deadline = time.monotonic() + timeout
    begin_shutdown()
    from . import backtest_sequences
    backtest_sequences.begin_shutdown()
    def interrupted():
        if force_requested is not None and force_requested.is_set():
            raise ForceShutdownRequested('강제 종료가 요청되었습니다.')
    try:
        interrupted()
        warnings, errors = [], []
        for job in active_jobs(**context):
            identifier = job['id']
            job_context = {key: job[key] for key in ('warehouse', 'project_root', 'python_executable')}
            key = (str(job['warehouse']).casefold(), identifier)
            running = job.get('run_started') is True or job['phase'] == 'run' or (job.get('process') or {}).get('action') == 'run'
            previous = _SHUTDOWN_PENDING.get(key)
            _SHUTDOWN_PENDING[key] = (job_context, running or bool(previous and previous[1]))
        pending = dict(_SHUTDOWN_PENDING)
        for key, (job_context, running) in list(pending.items()):
            interrupted()
            identifier = key[1]
            try:
                stop(identifier, **job_context)
                latest = get(identifier, **job_context)
                running = running or latest.get('run_started') is True or latest['phase'] == 'run' or (latest.get('process') or {}).get('action') == 'run'
                pending[key] = _SHUTDOWN_PENDING[key] = (job_context, running)
            except (OSError, ValueError) as exc:
                errors.append(identifier + ': ' + str(exc))
        if errors:
            raise RuntimeError('백테스트 정지를 요청하지 못했습니다. ' + ' · '.join(errors))
        stopped = [key[1] for key in pending]
        unknown = set()
        while pending:
            interrupted()
            for key, (job_context, running) in list(pending.items()):
                interrupted()
                identifier = key[1]
                job = get(identifier, **job_context)
                if job.get('identity_warning'):
                    unknown.add(key)
                else:
                    unknown.discard(key)
                if job['active']:
                    continue  # Unknown PID identity also prevents an unsafe success.
                if job['phase'] not in TERMINAL:
                    raise RuntimeError('백테스트 종료 상태를 확인하지 못했습니다: ' + identifier)
                if running and job['phase'] in ('complete', 'cancelled') and not job.get('forced_shutdown'):
                    try:
                        result = json.loads((job['folder'] / 'result.json').read_text('utf-8'))
                        if not isinstance(result, dict) or result.get('status') not in ('COMPLETE', 'CANCELLED'):
                            raise ValueError('결과 형식 오류')
                    except (OSError, UnicodeError, ValueError) as exc:
                        raise RuntimeError('백테스트 결과 저장을 확인하지 못했습니다: ' + identifier) from exc
                elif running and job['phase'] in ('error', 'interrupted'):
                    warnings.append(identifier + ': 오류로 종료되었습니다. 저장된 결과와 실행 로그를 확인하세요.')
                del pending[key]
                _SHUTDOWN_PENDING.pop(key, None)
            if pending:
                if time.monotonic() >= deadline:
                    message = ('백테스트 프로세스 상태를 재확인하고 있습니다.' if unknown & pending.keys()
                               else '백테스트 정지·저장이 진행 중입니다.')
                    raise ShutdownPending(message + ' 완료되면 자동으로 창을 닫습니다. 남은 작업: '
                                          + ', '.join(key[1] for key in pending))
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
        return {'ok': True, 'stopped': stopped, 'warnings': warnings}
    except (ShutdownPending, ForceShutdownRequested):
        raise  # Keep the start gate closed while the desktop continues waiting.
    except Exception:
        with _WORK_GUARD:
            _closing = False
        raise


@contextmanager
def _hold_force_process(identity):
    """Yield an identity-checked termination handle, never a reusable PID."""
    if (not isinstance(identity, dict) or type(identity.get('pid')) is not int or
            identity['pid'] <= 0 or not str(identity.get('created', '')).isdigit() or
            int(identity['created']) <= 0 or identity['pid'] == os.getpid()):
        if isinstance(identity, dict) and _alive(identity) is False:
            yield None
            return
        raise ValueError('백테스트 프로세스 소유 정보를 확인하지 못했습니다.')
    if os.name != 'nt':
        raise OSError('이 환경에서는 안전한 강제 종료를 지원하지 않습니다.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00101001, False, identity['pid'])
    if not handle:
        error = ctypes.get_last_error()
        if error == 87 or _alive(identity) is False:
            yield None
            return
        raise OSError(error, '해당 백테스트의 강제 종료 권한을 확인하지 못했습니다.')
    try:
        actual = process_identity(identity['pid'], handle)
        if not actual or actual['created'] != str(identity['created']):
            yield None
            return
        yield {'kernel': kernel, 'handle': handle, 'identity': actual}
    finally:
        kernel.CloseHandle(handle)


def _force_arguments(held):
    """Read the command of the pinned process for job/run ownership checks."""
    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                    ('Buffer', ctypes.c_void_p)]
    query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
    query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                      wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = ctypes.c_long
    size = 65536
    buffer = ctypes.create_string_buffer(size)
    used = wintypes.ULONG()
    if query(held['handle'], 60, buffer, size, ctypes.byref(used)) != 0:
        raise OSError('백테스트 프로세스 실행 명령을 확인하지 못했습니다.')
    value = UnicodeString.from_buffer(buffer)
    base = ctypes.addressof(buffer)
    if (value.Length % 2 or not value.Buffer or value.Buffer < base or
            value.Buffer + value.Length > base + size):
        raise OSError('백테스트 프로세스 실행 명령 형식을 확인하지 못했습니다.')
    command_line = ctypes.wstring_at(value.Buffer, value.Length // 2)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = held['kernel']
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    values = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not values:
        raise OSError('백테스트 프로세스 실행 명령을 나누지 못했습니다.')
    try:
        return [values[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(values)


def _argument_value(args, flag):
    if args.count(flag) != 1:
        return None
    index = args.index(flag) + 1
    return args[index] if index < len(args) else None


def _path_argument(value, expected):
    if not isinstance(value, str):
        return False
    try:
        return os.path.normcase(str(Path(value).resolve())) == os.path.normcase(str(Path(expected).resolve()))
    except (OSError, ValueError):
        return False


def _verify_force_owner(job, held):
    args = _force_arguments(held)
    supervisor = job.get('supervisor') or {}
    return (bool(re.fullmatch('[0-9a-f]{32}', str(supervisor.get('run_id', '')))) and
            _argument_value(args, '--job-id') == job['id'] and
            _argument_value(args, '--run-id') == supervisor['run_id'] and
            _path_argument(_argument_value(args, '--warehouse'), job['warehouse']) and
            _path_argument(_argument_value(args, '--project-root'), job['project_root']) and
            any(_path_argument(arg, Path(__file__).with_name('backtest_supervisor.py')) for arg in args))


def _verify_force_orphan(job, held):
    # Normal Part2 embeds both this job ID and both data paths in its command.
    # Generated workers receive identity through environment variables; without
    # a verified supervisor ancestry their ownership cannot be proved here.
    if job['kind'] != 'normal':
        return False
    args = _force_arguments(held)
    return (_argument_value(args, '--session-id') == job['id'] and
            _argument_value(args, '-m') == 'event_backtest' and
            _path_argument(_argument_value(args, '--warehouse'), job['warehouse']) and
            _path_argument(_argument_value(args, '--scenario'), job['scenario_path']))


def _terminate_held(held, deadline):
    kernel, handle = held['kernel'], held['handle']
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    waited = kernel.WaitForSingleObject(handle, 0)
    if waited != 0 and (waited != 258 or not kernel.TerminateProcess(handle, 1)):
        raise OSError(ctypes.get_last_error(), '백테스트 프로세스를 강제로 종료하지 못했습니다.')
    remaining = max(0, int((deadline - time.monotonic()) * 1000))
    if kernel.WaitForSingleObject(handle, remaining) != 0:
        raise OSError('백테스트 프로세스 강제 종료를 확인하지 못했습니다.')
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise OSError(ctypes.get_last_error(), '백테스트 프로세스 종료 시각을 확인하지 못했습니다.')
    held['exited'] = (times[1].dwHighDateTime << 32) | times[1].dwLowDateTime
    if not held['exited']:
        raise OSError('백테스트 프로세스 종료 시각을 확인하지 못했습니다.')


def _force_job(job, deadline):
    """Freeze the durable job and prove ownership before terminating its tree."""
    with job_lock(job['folder'], timeout=max(.1, deadline - time.monotonic())):
        record = _read(job['folder'])
        if any(record.get(key) != job.get(key) for key in ('supervisor', 'process')):
            raise ValueError('백테스트 실행 소유 정보가 변경되어 강제 종료하지 않았습니다.')
        with ExitStack() as stack:
            held, roots = {}, {}
            for key in ('supervisor', 'process'):
                identity = record.get(key)
                if identity is None:
                    continue
                pinned = stack.enter_context(_hold_force_process(identity))
                if pinned is not None:
                    held[identity['pid']] = roots[key] = pinned
            snapshot = _windows_process_snapshot() if held else {}
            owner, child = roots.get('supervisor'), roots.get('process')
            if owner is not None and not _verify_force_owner(job, owner):
                raise ValueError('백테스트 관리 실행 ID를 확인하지 못해 강제 종료하지 않았습니다.')
            if child is not None:
                row = snapshot.get(child['identity']['pid'])
                if row is None or row['created'] != child['identity']['created']:
                    raise ValueError('백테스트 자식 프로세스 소유 정보를 확인하지 못했습니다.')
                if owner is not None:
                    valid = (row['parent_pid'] == owner['identity']['pid'] and
                             int(row['created']) >= int(owner['identity']['created']))
                else:
                    valid = _verify_force_orphan(job, child)
                if not valid:
                    raise ValueError('해당 실행에 속한 백테스트인지 확인하지 못해 강제 종료하지 않았습니다.')

            def discover(rows):
                added = False
                for row in rows.values():
                    parent = held.get(row['parent_pid'])
                    if parent is None or row['pid'] in held:
                        continue
                    current_parent = rows.get(row['parent_pid'])
                    if current_parent is not None and current_parent['created'] != parent['identity']['created']:
                        continue
                    if int(row['created']) < int(parent['identity']['created']):
                        continue
                    if parent.get('exited') and int(row['created']) > parent['exited']:
                        continue
                    pinned = stack.enter_context(_hold_force_process(row))
                    if pinned is not None:
                        held[row['pid']] = pinned
                        added = True
                return added

            while held and discover(snapshot):
                pass
            # Parents stop producing new workers first. Retain every verified
            # parent handle so its PID cannot be reassigned during late discovery.
            terminated = set()
            while held:
                for pid, pinned in list(held.items()):
                    if pid not in terminated:
                        _terminate_held(pinned, deadline)
                        terminated.add(pid)
                if not discover(_windows_process_snapshot()):
                    break
                if time.monotonic() >= deadline:
                    raise OSError('백테스트 자식 프로세스 종료 확인 시간이 초과되었습니다.')
            record = _read(job['folder'])
            if any(record.get(key) != job.get(key) for key in ('supervisor', 'process')):
                raise ValueError('강제 종료 중 백테스트 소유 정보가 변경되었습니다.')
            unfinished = record['phase'] not in TERMINAL
            if (held or unfinished or job.get('_force_pending')) and record['phase'] not in ('complete', 'error', 'planned'):
                record['phase'] = 'cancelled'
                record['cancel_requested'] = True
                record['forced_shutdown'] = True
                record['message'] = '사용자가 강제로 종료했습니다. 저장되지 않은 결과는 유실되었을 수 있습니다.'
                _write(job['folder'], record)
            return bool(held or unfinished or job.get('_force_pending'))


def force_shutdown(timeout=5, **context):
    """Force only verified backtest owners/children after an explicit choice."""
    deadline = time.monotonic() + max(0, timeout)
    begin_shutdown()
    from . import backtest_sequences
    backtest_sequences.begin_shutdown()
    warnings, errors, stopped = [], [], []
    inventory = {(str(job['warehouse']).casefold(), job['id']): job for job in active_jobs(**context)}
    for pending in shutdown_pending_jobs():
        key = (str(pending['warehouse']).casefold(), pending['id'])
        if key not in inventory:
            try:
                job_context = {name: pending[name] for name in ('warehouse', 'project_root', 'python_executable')}
                inventory[key] = get(pending['id'], **job_context)
            except (OSError, ValueError) as exc:
                errors.append(pending['id'] + ': ' + str(exc))
                continue
        inventory[key]['_force_pending'] = True
    for job in inventory.values():
        try:
            forced = _force_job(job, deadline)
            job_context = {key: job[key] for key in ('warehouse', 'project_root', 'python_executable')}
            latest = get(job['id'], **job_context)
            if latest['active']:
                raise ValueError('백테스트 종료 여부를 확인하지 못했습니다.')
            stopped.append(job['id'])
            _SHUTDOWN_PENDING.pop((str(job['warehouse']).casefold(), job['id']), None)
            if forced:
                warnings.append(job['id'] + ': 강제 종료했습니다. 저장되지 않은 결과는 유실되었을 수 있습니다.')
        except (OSError, ValueError) as exc:
            errors.append(job['id'] + ': ' + str(exc))
    if errors:
        raise RuntimeError('백테스트를 강제로 종료하지 못했습니다. ' + ' · '.join(errors))
    return {'ok': True, 'stopped': stopped, 'warnings': warnings}


def force_stop(identifier, timeout=5, **context):
    """Escalate one already cancelled job without closing other work gates."""
    job = get(identifier, **context)
    if not job['cancel_requested']:
        raise ValueError('정상 종료를 먼저 요청한 뒤 강제 종료를 선택하세요.')
    if not job['active'] and job['phase'] in TERMINAL:
        return {'ok': True, 'message': '이미 종료된 작업입니다.'}
    forced = _force_job(job, time.monotonic() + max(0, timeout))
    latest = get(identifier, **context)
    if latest['active']:
        raise ValueError('백테스트 종료 여부를 확인하지 못했습니다.')
    _SHUTDOWN_PENDING.pop((str(job['warehouse']).casefold(), identifier), None)
    return {'ok': True, 'forced': forced, 'message':
            '강제 종료했습니다.' if forced else '이미 종료된 작업입니다.'}


def cancel_shutdown():
    """Allow another attempt if a different engine failed to close."""
    global _closing
    with _WORK_GUARD:
        _closing = False


def command(job, action, approved=None):
    if job['kind'] == 'generated':
        from .backtest_adapters import command as generated_command
        return generated_command(job, action, approved=approved)
    args = [job['python_executable'], '-B', '-X', 'utf8', '-m', 'event_backtest', action,
            '--scenario', str(job['scenario_path']), '--warehouse', str(job['warehouse']), '--session-id', job['id']]
    if job.get('rebuild'):
        args.append('--rebuild')
    if action == 'run' and approved:
        args += ['--approved-token', approved]
    return args, {}, job['project_root'] / 'Part2'
