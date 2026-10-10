"""Durable protocol records. No market or strategy decisions live here."""
import hashlib
import domain_memory
import json
import os
import threading
import tempfile
import heapq
from copy import deepcopy
from time import monotonic as _monotonic, sleep as _sleep
from pathlib import Path

_locks = {}
_locks_guard = threading.Lock()

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel.CreateMutexW.restype = wintypes.HANDLE
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]


class _StateLock:
    """Reentrant thread + process lock for one canonical state-file path."""
    def __init__(self, key):
        self.key = key
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        if not self.thread_lock.acquire(timeout=5):
            raise TimeoutError('State thread lock timed out: ' + self.key)
        try:
            depth = getattr(self.local, 'depth', 0)
            if depth == 0:
                if os.name == 'nt':
                    name = 'Local\\MosesState-' + hashlib.sha256(self.key.encode('utf-8')).hexdigest()
                    handle = _kernel.CreateMutexW(None, False, name)
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    result = _kernel.WaitForSingleObject(handle, 5000)
                    if result not in (0, 0x80):  # acquired or abandoned by a terminated owner
                        error = ctypes.get_last_error()
                        _kernel.CloseHandle(handle)
                        if result == 0x102:
                            raise TimeoutError('State process lock timed out: ' + self.key)
                        raise ctypes.WinError(error)
                    self.local.handle = handle
                else:
                    import fcntl
                    path = Path(self.key + '.lock')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handle = path.open('a+b')
                    until = _monotonic() + 5
                    try:
                        while True:
                            try:
                                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                break
                            except BlockingIOError:
                                if _monotonic() >= until:
                                    raise TimeoutError('State process lock timed out: ' + self.key)
                                _sleep(.02)
                    except BaseException:
                        handle.close()
                        raise
                    self.local.handle = handle
            self.local.depth = depth + 1
            return self
        except BaseException:
            self.thread_lock.release()
            raise

    def __exit__(self, *_):
        try:
            self.local.depth -= 1
            if self.local.depth == 0:
                if os.name == 'nt':
                    _kernel.ReleaseMutex(self.local.handle)
                    _kernel.CloseHandle(self.local.handle)
                else:
                    self.local.handle.close()
                del self.local.handle
        finally:
            self.thread_lock.release()


def state_lock(path):
    if domain_memory.active():return threading.RLock()
    key = os.path.normcase(str(Path(path).resolve()))
    with _locks_guard:
        return _locks.setdefault(key, _StateLock(key))


def _retry_sharing(operation, timeout=2.0):
    until = _monotonic() + timeout
    delay = .02
    while True:
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or _monotonic() >= until:
                raise
            _sleep(min(delay, max(0, until - _monotonic())))
            delay = min(delay * 2, .2)


def read_json(path):
    if domain_memory.active():return domain_memory.read(path)
    path = Path(path)
    with state_lock(path):
        # Decode only after the file handle is closed; keep reader occupancy short.
        text = _retry_sharing(lambda: path.read_text(encoding='utf-8'))
        return json.loads(text)

def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(',', ':')).encode('utf-8')).hexdigest()

def atomic_json(path, value, *, default=str, allow_nan=False, indent=None, retry_timeout=2.0):
    if domain_memory.active():return domain_memory.write(path,value,default=default,allow_nan=allow_nan,indent=indent)
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, default=default, allow_nan=allow_nan, indent=indent)
    with state_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            _retry_sharing(lambda: os.replace(temp, path), retry_timeout)
        finally:
            try:
                _retry_sharing(lambda: temp.unlink(missing_ok=True), .2)
            except OSError:
                pass  # Never delete another writer's temp or obscure the original error.

class Records:
    """Resident engine records, or the original locked file protocol for I/O owners.

    Resident mutation never serializes JSON. Export is explicit. Receipt aging
    uses the owning symbol's source clock, not a wall clock or another symbol.
    """
    def __init__(self, path, *, resident=False, retention_seconds=None):
        self.path = Path(path)
        self.lock = state_lock(self.path)
        self.resident = resident
        self.retention_seconds = retention_seconds
        self._records = {}; self._times = {}; self._expiry = []; self._clock = None; self._unstamped = set()
        if resident:
            raw = self._load()
            self._records = raw.get('records', {})
            self._times = dict(raw.get('record_source_times', {}))
            self._expiry = [(float(stamp), key) for key, stamp in self._times.items() if key in self._records]
            heapq.heapify(self._expiry)
            self._clock = raw.get('record_source_clock')
            self._unstamped = self._records.keys() - self._times.keys()

    def _load(self):
        if not domain_memory.exists(self.path,file_only=False):return {'version':1,'records':{}}
        raw = read_json(self.path)
        if raw.get('version') != 1 or not isinstance(raw.get('records'), dict):
            raise ValueError('Unsupported protocol record file: ' + str(self.path))
        return raw

    def advance_time(self, source_seconds):
        if not self.resident or self.retention_seconds is None:return
        with self.lock:
            self._clock = max(float(source_seconds), self._clock if self._clock is not None else float(source_seconds))
            # Older state files have no timestamp: retain them for a full period
            # from restoration rather than dropping existing dedup evidence.
            for key in self._unstamped:
                self._times[key] = self._clock;heapq.heappush(self._expiry,(self._clock,key))
            self._unstamped.clear()
            limit=self._clock-self.retention_seconds
            while self._expiry and self._expiry[0][0] < limit:
                stamp,key=heapq.heappop(self._expiry)
                if self._times.get(key)==stamp:
                    self._times.pop(key,None);self._records.pop(key,None)

    def export_json(self):
        with self.lock:
            raw={'version':1,'records':self._records} if self.resident else self._load()
            if self.resident and self.retention_seconds is not None:
                raw.update(record_source_times=self._times,record_source_clock=self._clock)
            return json.dumps(raw,ensure_ascii=False,default=str,allow_nan=False)

    def all(self):
        with self.lock:
            return deepcopy(self._records) if self.resident else self._load()['records']

    def get(self, key, default=None):
        if self.resident:
            with self.lock:return deepcopy(self._records.get(key,default))
        return self.all().get(key, default)

    def put(self, key, value):
        with self.lock:
            if self.resident:
                self._records[key]=deepcopy(value)
                if self.retention_seconds is not None and self._clock is not None and self._times.get(key)!=self._clock:
                    self._times[key]=self._clock;heapq.heappush(self._expiry,(self._clock,key))
                elif self.retention_seconds is not None and self._clock is None:self._unstamped.add(key)
                return
            records = self.all()
            records[key] = value
            atomic_json(self.path, {'version': 1, 'records': records})

    def remove(self, key):
        with self.lock:
            if self.resident:
                self._records.pop(key,None);self._times.pop(key,None)
                self._unstamped.discard(key)
                return
            records = self.all()
            records.pop(key, None)
            atomic_json(self.path, {'version': 1, 'records': records})

def receipt_key(event):
    if event.get('kind') == 'FACT_SNAPSHOT': return None
    if not event.get('event_id'): return None  # legacy messages have no invented identity
    return identity(event.get('strategy'), event.get('kind'), event['event_id'],
                    event.get('request_chat_id'), event.get('watch_id'), event.get('chain_id'),
                    event.get('chain_stage'))

def fact_scope(event):
    return identity(event.get('strategy'), event.get('symbol'), event.get('source_tf'),
                    event.get('watch_id') if event.get('strategy') == 'SWEEP' else None,
                    'metrics' if event.get('kind') == 'TREND_METRIC_STATE' else 'state')

class FactStream:
    def __init__(self, path, family):
        self.family = family
        self.records = Records(path)
        with self.records.lock:
            self.generation = int(self.records.get('generation', 0)) + 1
            self.records.put('generation', self.generation)
        self.sequence = 0

    def prepare(self, event):
        if event.get('symbol') and event.get('source_tf') and not str(event.get('kind')).endswith('QUERY_RESULT'):
            if 'fact_revision' not in event:
                self.sequence += 1
                event['fact_revision'] = [self.generation, self.sequence]
                event.setdefault('event_id', identity(self.family, self.generation, self.sequence))
        return event

    def snapshot(self, symbol, tf, facts, **meta):
        return self.prepare(dict(kind='FACT_SNAPSHOT', strategy=self.family, symbol=symbol,
                                 source_tf=tf, complete=True, facts=facts, **meta))

def source_health(data, indicators=()):
    return {'sources': {tf: frame.attrs.get('source_epoch') for tf, frame in data.items()
                        if frame is not None}, 'indicators': list(indicators)}
