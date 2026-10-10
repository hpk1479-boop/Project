"""Public host lifecycle I/O; no strategy or Part3 dependency lives here.

Runtime records belong to the executing Part1 root and contain identities,
never machine-specific paths. A run token prevents old stop files from
affecting a later process that happens to receive the same PID.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

VERSION = 1
STATES = frozenset(('starting', 'ready', 'stopping', 'stopped', 'failed'))


def _identity_equal(left, right):
    try:
        # CIM has microsecond precision; Windows FILETIME has 100ns precision.
        return int(left) // 10 == int(right) // 10
    except (TypeError, ValueError, OverflowError):
        return False


def process_identity(pid):
    """Return a live process FILETIME, None if gone; query errors propagate."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00101000, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return None
        raise OSError(error, '프로세스 생존 상태를 확인하지 못했습니다.')
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise OSError(ctypes.get_last_error(), '프로세스 시작 시간을 확인하지 못했습니다.')
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _runtime_paths(root, pid):
    if isinstance(pid, bool) or not str(pid).isdigit() or int(pid) <= 0:
        raise ValueError('올바른 엔진 PID가 필요합니다.')
    directory = Path(root).resolve() / 'runtime' / 'engines'
    return directory / (str(int(pid)) + '.json'), directory / (str(int(pid)) + '.stop.json')


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.lifecycle-', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, separators=(',', ':'))
    try:
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                # Windows readers can briefly deny replacement while the
                # controller observes readiness or the completed save.
                if os.name != 'nt' or getattr(exc,'winerror',None) not in (5,32,33) or attempt==4:
                    raise
                time.sleep(.01*(attempt+1))
    finally:
        if temporary.exists():
            temporary.unlink()


def read_status(root, pid, created=None, run_id=None):
    """Read a matching lifecycle record, returning None for stale/invalid data."""
    try:
        path, _ = _runtime_paths(root, pid)
        row = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] != VERSION or
                type(row.get('pid')) is not int or row['pid'] != int(pid) or row.get('state') not in STATES or
                not isinstance(row.get('created'), str) or not row['created'].isdigit() or
                not isinstance(row.get('run_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['run_id']) or
                not isinstance(row.get('error'), str) or
                any(not isinstance(row.get(key), bool)
                    for key in ('pipe_bound', 'pipe_connected', 'state_saved'))):
            return None
        if created is not None and not _identity_equal(row['created'], created):
            return None
        if run_id is not None and row['run_id'] != run_id:
            return None
        return row
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def request_stop(root, pid, created, run_id=None):
    """Request orderly shutdown only for the currently matching engine run."""
    row = read_status(root, pid, created=created, run_id=run_id)
    if row is None or row['state'] in ('stopped', 'failed'):
        return False
    _, path = _runtime_paths(root, pid)
    try:
        _atomic_json(path, {'version': VERSION, 'action': 'stop', 'pid': row['pid'],
                            'created': row['created'], 'run_id': row['run_id']})
        return True
    except OSError:
        return False


def _clean_error(error):
    # Persist useful user-facing diagnostics without recording machine paths.
    text = re.sub(r'[A-Za-z]:[\\/][^\s\"\n]+', '[경로]', str(error))
    return text[:600]


class HostLifecycle:
    def __init__(self, root, stop, *, pid=None, created=None, run_id=None,
                 owner_pid=None, owner_created=None, identity=None):
        self.stop = stop
        self.pid = os.getpid() if pid is None else int(pid)
        self.identity = identity or process_identity
        created = self.identity(self.pid) if created is None else str(created)
        if created is None or not created.isdigit():
            raise RuntimeError('엔진의 시작 시간을 확인하지 못했습니다.')
        token = run_id or os.environ.get('MOSES_ENGINE_RUN_ID') or uuid.uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', token):
            raise ValueError('올바른 엔진 실행 ID가 필요합니다.')
        self.path, self.stop_path = _runtime_paths(root, self.pid)
        self.lock = threading.RLock()
        self.services_ready = False
        self.row = {'version': VERSION, 'pid': self.pid, 'created': created, 'run_id': token,
                    'state': 'starting', 'pipe_bound': False, 'pipe_connected': False,
                    'state_saved': False, 'error': ''}
        owner_pid = owner_pid if owner_pid is not None else os.environ.get('MOSES_UI_OWNER_PID')
        owner_created = owner_created if owner_created is not None else os.environ.get('MOSES_UI_OWNER_CREATED')
        self.owner = None
        if owner_pid is not None or owner_created is not None:
            if (not str(owner_pid).isdigit() or int(owner_pid) <= 0 or
                    not str(owner_created).isdigit()):
                raise ValueError('웹 창의 프로세스 정보가 올바르지 않습니다.')
            self.owner = (int(owner_pid), str(owner_created))
        self.owner_query_warning = False
        self.thread = None
        self._publish()

    def _publish(self):
        try:
            _atomic_json(self.path, self.row)
            return True
        except OSError:
            # A full/read-only disk must still reach the host's cleanup path.
            # The controller cannot claim ready/saved without a fresh record.
            self.stop.set()
            self.row['state'] = 'failed'
            self.row['state_saved'] = False
            self.row['error'] = '엔진 제어 상태를 저장하지 못했습니다.'
            logging.exception(self.row['error'])
            return False

    def _ready(self):
        if (self.row['state'] == 'starting' and not self.stop.is_set() and
                self.services_ready and self.row['pipe_bound']):
            self.row['state'] = 'ready'

    def pipe_bound(self, bound):
        with self.lock:
            self.row['pipe_bound'] = bool(bound)
            if not bound:
                self.row['pipe_connected'] = False
            self._ready()
            self._publish()

    def pipe_connected(self, connected):
        with self.lock:
            self.row['pipe_connected'] = bool(connected)
            self._publish()

    def services_started(self):
        with self.lock:
            self.services_ready = True
            self._ready()
            self._publish()

    def fail(self, error):
        self.stop.set()
        with self.lock:
            self.row['state'] = 'failed'
            self.row['error'] = _clean_error(error)
            self._publish()

    def begin_stopping(self):
        self.stop.set()
        with self.lock:
            if self.row['state'] != 'failed':
                self.row['state'] = 'stopping'
            self._publish()

    def finish(self, state_saved):
        self.stop.set()
        with self.lock:
            self.row['state_saved'] = bool(state_saved)
            self.row['pipe_bound'] = False
            self.row['pipe_connected'] = False
            if self.row['state'] != 'failed':
                self.row['state'] = 'stopped' if state_saved else 'failed'
                if not state_saved and not self.row['error']:
                    self.row['error'] = '엔진 상태 저장을 완료하지 못했습니다.'
            self._publish()

    def check_requests(self):
        """One bounded check; public so host regression tests need no sleeps."""
        try:
            request = json.loads(self.stop_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            request = None
        if (isinstance(request, dict) and type(request.get('version')) is int and request['version'] == VERSION and
                request.get('action') == 'stop' and type(request.get('pid')) is int and request['pid'] == self.pid and
                _identity_equal(request.get('created'), self.row['created']) and
                request.get('run_id') == self.row['run_id']):
            self.begin_stopping()
            return True
        if self.owner is not None:
            try:
                actual = self.identity(self.owner[0])
                if actual is None or not _identity_equal(actual, self.owner[1]):
                    logging.warning('웹 창이 종료되어 엔진 상태를 저장하고 종료합니다.')
                    self.begin_stopping()
                    return True
            except OSError:
                if not self.owner_query_warning:
                    self.owner_query_warning = True
                    logging.warning('웹 창의 생존 상태 조회가 지연되어 다음 확인을 기다립니다.')
        return False

    def start(self):
        self.thread = threading.Thread(target=self._watch, name='engine-lifecycle', daemon=True)
        self.thread.start()

    def _watch(self):
        while not self.stop.is_set():
            try:
                if self.check_requests():
                    return
            except Exception:
                logging.exception('엔진 종료 요청 확인 실패; 안전 종료합니다.')
                self.fail('엔진 종료 요청을 확인하지 못해 안전 종료합니다.')
                return
            self.stop.wait(.1)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
