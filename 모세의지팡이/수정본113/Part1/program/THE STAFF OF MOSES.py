# -*- coding: utf-8 -*-
"""
THE STAFF OF MOSES 2.0
======================
MT5 EA `THE STAFF OF MOSES`가 Named Pipe로 전달하는 OHLC/지표 snapshot을 수신하고,
TREND / FVG / SWEEP / OZ / manager_KIM에 하나의 공통 시장데이터 계약으로 제공합니다.

핵심 원칙
---------
1) 임의 timeframe snapshot은 OHLC가 정상이라면 캐시합니다. 선택 지표의 결손은 snapshot 자체를 폐기하지 않습니다.
2) 각 요청이 실제로 요구한 MT5 지표만 응답 시점에 검증합니다.
3) 계산은 클라이언트가 소유합니다. 서버는 원시 Snapshot만 전달합니다.
4) 원비 원본은 고정 3σ MT5 열이며 config 적용은 공용 클라이언트 Fact의 책임입니다.
5) STAFF_ALLOWED_TIMEFRAMES가 비어 있으면 timeframe을 제한하지 않습니다.
"""
from __future__ import annotations
import uuid

import ctypes
from contextlib import contextmanager
import datetime as dt
import logging
import io
import json
import pickle
import os
import struct
import threading
import time
from pathlib import Path
from typing import Iterable, Optional
from typing import NamedTuple
from types import MappingProxyType
from zoneinfo import ZoneInfo

import numpy as np
import zmq
import staff_schema as wire
from symbol_settings import configured_symbols

LOG_DIR = Path(os.environ["MOSES_LOG_DIRECTORY"]) if os.environ.get("MOSES_LOG_DIRECTORY") else Path(__file__).resolve().parent / "logs"


def load_config(file_path: str = "config.txt") -> dict[str, str]:
    """시스템 공통 설정파일 config.txt를 읽습니다."""
    config: dict[str, str] = {}
    script_dir = Path(__file__).resolve().parent
    requested = Path(file_path)
    path = requested if requested.is_absolute() else script_dir / requested
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("THE STAFF OF MOSES 설정파일 로드: %s", path.resolve())
    except FileNotFoundError:
        logging.warning("[%s] 설정파일이 없습니다. STAFF 기본값으로 실행합니다.", path)
    return config

def csv_items(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]

# ---------------------------------------------------------------------
# Staff of Moses file cache
# ---------------------------------------------------------------------
BASE_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
MT5_COLUMNS = [
    "ema_20", "ema_50", "ema_200",
    "hma_6", "hma_17", "hma_50", "hma_168",
    "price_hma_6", "price_band_lower", "price_band_upper",
    "RSI_val", "RSI_db", "RSI_ub", "RSI_basis",
    "STO_val", "STO_db", "STO_ub", "STO_basis",
    "DI_val", "DI_db", "DI_ub", "DI_basis",
    "price_regime_basis", "price_regime_upper", "price_regime_lower",
    "RSI_regime_upper", "RSI_regime_lower",
    "STO_regime_upper", "STO_regime_lower",
    "DI_regime_upper", "DI_regime_lower",
]


PIPE_MAGIC = 0x534D4F53  # "SMOS"
PIPE_VERSION = 1
PIPE_VALUE_COLUMNS = wire.PIPE_VALUE_COLUMNS
PIPE_HEADER = struct.Struct("<IIqIIII")  # magic, version, snapshot, symbol_len, tf_len, bars, cols


@contextmanager
def staff_pipe_security():
    """Allow this Windows user at medium integrity, even when STAFF is elevated.

    Default elevated-token ACLs can deny a non-elevated MT5 client. Keep the
    grant restricted to the current user and SYSTEM; never use a NULL DACL.
    """
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    sid_text = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        user_sid = ctypes.wstring_at(sid_text)
        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{user_sid})S:(ML;;NW;;;ME)"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        yield SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor.value, False)
    finally:
        if descriptor.value:
            k32.LocalFree(descriptor)
        if sid_text.value:
            k32.LocalFree(sid_text)
        if token.value:
            k32.CloseHandle(token)


class StaffFrameTruncatedError(EOFError):
    """An injected frame ended before its declared payload."""


class StaffFrameTrailingError(ValueError):
    """A single-frame injection contained trailing bytes."""


class StaffSnapshot(NamedTuple):
    """One complete v1 publication. Arrays are backed by immutable bytes."""
    time: np.ndarray
    volume: np.ndarray
    values: np.ndarray
    seq: int
    source_epoch: str
    indicator_validity: MappingProxyType
    received_at: float
    legacy_wonbi: bool = False


class _SnapshotEntry:
    def __init__(self, snapshot):
        self.snapshot = snapshot


class StaffPublication(NamedTuple):
    packet: object
    response: object
    feeds: dict
    gaps: tuple


class StaffPipeCache:
    """Named Pipe receiver + atomic full-snapshot cache.

    Writer is MT5 Staff EA. A feed is replaced only after its complete binary
    message has been received, validated and converted. An interrupted message
    never modifies the last normal Snapshot. No DataFrame or derived calculation is retained here.
    """
    def __init__(self, pipe_name: str, max_bars: int = 650, stale_seconds: float = 30.0,
                 *, health_session: Optional[str] = None, monotonic=None, gap_journal=None):
        self.pipe_name = pipe_name or r"\\.\pipe\StaffOfMoses_v1"
        self.max_bars = int(max_bars)
        self.stale_seconds = float(stale_seconds)
        self._entries: dict[tuple[str, str], _SnapshotEntry] = {}
        self._updated: dict[tuple[str, str], float] = {}
        self._snapshot: dict[tuple[str, str], int] = {}
        self._health_session = uuid.uuid4().hex if health_session is None else health_session
        self._monotonic = monotonic if monotonic is not None else lambda: time.monotonic()
        self._health_epochs = {}
        self._schema_errors = {}
        self._schema_connection_error = None
        self._requires_full = set()
        self._connection_count = 0
        self._reconnect_count = 0
        self._wire_stats = {}
        self._gap_records = []
        self._ea_build_hash = None
        self._gap_journal = Path(gap_journal) if gap_journal is not None else LOG_DIR / ('pipe_gaps_' + self._health_session + '.jsonl')
        self._lock = threading.RLock()
        self._wire_inspection = threading.local()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    def start(self, stop_event: threading.Event) -> None:
        if os.name != "nt":
            raise RuntimeError("Named Pipe Staff 서버는 Windows에서만 실행할 수 있습니다.")
        self._stop_event = stop_event
        self._thread = threading.Thread(target=self._receiver_loop, name="StaffNamedPipe", daemon=True)
        self._thread.start()


    def snapshot(self, symbol: str, timeframe: str) -> StaffSnapshot:
        """Read the latest immutable internal snapshot (not a ZMQ API)."""
        with self._lock:
            entry = self._entries.get((symbol, timeframe.lower()))
            if entry is None:
                raise RuntimeError(f"[{symbol} {timeframe}] MT5 Named Pipe snapshot 없음")
            return entry.snapshot

    def keys(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(self._entries)

    def snapshot_with_age(self, symbol: str, timeframe: str):
        """Capture one immutable publication and its age under the same lock."""
        with self._lock:
            key = (symbol, timeframe.lower())
            entry = self._entries.get(key)
            if self._schema_connection_error or key in self._schema_errors:
                return None, None
            if entry is None:
                return None, None
            snapshot = entry.snapshot
            return snapshot, max(0.0, self._monotonic() - snapshot.received_at)

    @property
    def health_session(self):
        return self._health_session

    def snapshots_with_age(self, symbol, timeframes):
        """Capture a coherent bundle view; release the lock before client encoding."""
        with self._lock:
            return {tf:self.snapshot_with_age(symbol, tf) for tf in timeframes}

    def export_state(self, *, metadata_only=False) -> dict:
        """Public continuation boundary; metadata-only views omit array serialization."""
        with self._lock:
            snapshots = {key: entry.snapshot for key, entry in self._entries.items()}
            state = {'schema': 'staff-cache-state/v1', 'health_session': self._health_session,
                     'updated': dict(self._updated), 'sequences': dict(self._snapshot),
                     'health_epochs': dict(self._health_epochs),
                     'wire_v2': {'schema_errors':dict(self._schema_errors),
                         'connection_error':self._schema_connection_error,
                         'requires_full':tuple(self._requires_full),
                         'connections':self._connection_count,'reconnects':self._reconnect_count,
                         'stats':{k:dict(v) for k,v in self._wire_stats.items()},
                         'gaps':[dict(v) for v in self._gap_records], 'ea_build_hash':self._ea_build_hash}}
        if metadata_only:
            return state
        state['snapshots'] = {key: {
            'time': item.time.tolist(), 'volume': item.volume.tolist(), 'values': item.values.tolist(),
            'seq': item.seq, 'source_epoch': item.source_epoch,
            'indicator_validity': dict(item.indicator_validity), 'received_at': item.received_at, 'legacy_wonbi': item.legacy_wonbi,
        } for key, item in snapshots.items()}
        return state

    def restore_state(self, state: dict) -> None:
        """Restore a continuation captured by export_state without advancing epochs."""
        if state.get('schema') != 'staff-cache-state/v1' or state.get('health_session') != self._health_session:
            raise ValueError('STAFF cache continuation mismatch')
        entries = {}
        for key, item in state['snapshots'].items():
            times = np.asarray(item['time'], dtype='<i8')
            volumes = np.asarray(item['volume'], dtype='<i8')
            values = wire.mt5_values(item['values'])
            if times.ndim != 1 or volumes.shape != times.shape or values.shape != (len(times), len(PIPE_VALUE_COLUMNS)):
                raise ValueError('STAFF cache continuation shape mismatch')
            snapshot = StaffSnapshot(
                np.frombuffer(times.tobytes(), dtype='<i8'),
                np.frombuffer(volumes.tobytes(), dtype='<i8'),
                np.frombuffer(values.tobytes(), dtype='<f8').reshape(values.shape),
                item['seq'], item['source_epoch'], MappingProxyType(dict(item['indicator_validity'])),
                item['received_at'], item.get('legacy_wonbi', len(item['values'][0]) == 45))
            entries[key] = _SnapshotEntry(snapshot)
        with self._lock:
            self._entries = entries
            self._updated = dict(state['updated'])
            self._snapshot = dict(state['sequences'])
            self._health_epochs = dict(state['health_epochs'])
            v2=state.get('wire_v2', {})
            self._schema_errors=dict(v2.get('schema_errors', {}))
            self._schema_connection_error=v2.get('connection_error')
            self._requires_full=set(v2.get('requires_full', ()))
            self._connection_count=v2.get('connections', 0)
            self._reconnect_count=v2.get('reconnects', 0)
            self._wire_stats={k:dict(v) for k,v in v2.get('stats', {}).items()}
            self._gap_records=[dict(v) for v in v2.get('gaps', ())]
            self._ea_build_hash=v2.get('ea_build_hash')

    def reconnect(self) -> None:
        """New writer session: keep normal data/freshness; reset seq checks only."""
        with self._lock:
            if self._connection_count or self._entries:
                self._reconnect_count += 1
            self._connection_count += 1
            self._snapshot.clear()
            self._requires_full.update(self._entries)
            self._ea_build_hash = None

    def has(self, symbol: str, timeframe: str) -> bool:
        key = (symbol, timeframe.lower())
        with self._lock:
            return key in self._entries

    def freshness(self, symbol: str, timeframe: str) -> Optional[float]:
        key = (symbol, timeframe.lower())
        with self._lock:
            updated = self._updated.get(key)
        return None if updated is None else max(0.0, self._monotonic() - updated)

    def health(self, symbol: str, timeframes: Iterable[str], closed: bool = False) -> dict:
        with self._lock:
            now = self._monotonic()
            feeds = {}
            for tf in timeframes:
                key = (symbol, tf)
                entry = self._entries.get(key)
                updated = self._updated.get(key)
                age = None if updated is None else max(0.0, now - updated)
                feeds[tf] = {
                    'status': 'UNAVAILABLE' if (self._schema_connection_error or key in self._schema_errors) else
                              'CLOSED' if closed else 'UNAVAILABLE' if age is None else
                              'STALE' if age > self.stale_seconds else 'FRESH',
                    'source_epoch': entry.snapshot.source_epoch if entry is not None else None,
                    'indicators': dict(entry.snapshot.indicator_validity) if entry is not None else {},
                    'age_seconds': age, 'stale_seconds': self.stale_seconds,
                }
            return feeds

    @staticmethod
    def _read_exact(k32, handle, size: int) -> bytes:
        out = bytearray(size)
        offset = 0
        while offset < size:
            chunk = min(size - offset, 1 << 20)
            buf = (ctypes.c_ubyte * chunk).from_buffer(out, offset)
            got = ctypes.c_uint32(0)
            ok = k32.ReadFile(handle, ctypes.byref(buf), chunk, ctypes.byref(got), None)
            if not ok or got.value == 0:
                err = ctypes.get_last_error()
                raise OSError(err, f"Named Pipe ReadFile 실패 ({offset}/{size})")
            offset += got.value
        return bytes(out)

    def _consume_one(self, k32, handle) -> None:
        reply = self.receive_one(lambda size: self._read_exact(k32, handle, size))
        if reply is not None:
            buf = ctypes.create_string_buffer(reply)
            written = ctypes.c_uint32()
            if not k32.WriteFile(handle, buf, len(reply), ctypes.byref(written), None) or written.value != len(reply):
                raise OSError(ctypes.get_last_error(), 'Named Pipe HELLO reply failed')

    def publish_frame(self, raw: bytes) -> None:
        """Inject exactly one wire-v1 frame through the real receiver parser."""
        stream = io.BytesIO(raw)
        def read_exact(size):
            data = stream.read(size)
            if len(data) != size:
                raise StaffFrameTruncatedError('truncated STAFF pipe message')
            return data
        def finish():
            if stream.read(1):
                raise StaffFrameTrailingError('extra bytes after STAFF pipe message')
        return self.receive_one(read_exact, finish=finish)

    def inspect_publication(self, raw):
        """Validate bytes for header/continuity inspection before publication.

        Inspection never publishes or skips the canonical seq/health checks.
        Only this thread's identical immutable bytes can reuse its decoding.
        """
        packet = wire.decode_v2(raw)
        if type(raw) is bytes:
            self._wire_inspection.value = (raw, packet)
        else:
            self._wire_inspection.value = None
        return packet

    def _decode_publication(self, raw):
        inspected = getattr(self._wire_inspection, 'value', None)
        self._wire_inspection.value = None
        if inspected is not None and type(raw) is bytes and inspected[0] is raw:
            return inspected[1]
        return wire.decode_v2(raw)

    def receive_publication(self, *, raw=None, read_exact=None, allowed_symbols=None,
                            source_time=None, require_observation=False):
        """Decode/validate once and atomically return this publication's feeds.

        Both the LIVE pipe host and replay adapter enter here. No consumer can
        inject an unvalidated WireFrame or read a later thread's publication.
        """
        if (raw is None)==(read_exact is None):raise ValueError('one wire byte source required')
        try:
            packet=(self._decode_publication(raw) if raw is not None else
                    wire.read_v2(read_exact(wire.WIRE_HEADER.size),read_exact))
        except wire.UnknownWireSchema as exc:
            with self._lock:
                if exc.symbol and exc.timeframe:self._schema_errors[exc.symbol,exc.timeframe]=exc.schema_id
                else:self._schema_connection_error=exc.schema_id or -1
            raise
        control=packet.kind in (wire.WIRE_HELLO,wire.WIRE_ACK)
        children=() if control else packet.children if packet.kind==wire.WIRE_BUNDLE else (packet,)
        if allowed_symbols is not None:
            for frame in children:
                if frame.symbol not in allowed_symbols:raise ValueError('disallowed symbol: '+frame.symbol)
        if require_observation and not control and not (packet.sent_at_ms if source_time is None else source_time):
            raise ValueError('explicit observation source_time required')
        with self._lock:
            old={(f.symbol,f.timeframe):self._entries.get((f.symbol,f.timeframe)) for f in children}
            gap_start=len(self._gap_records)
            try:response=self._receive_v2(packet)
            except Exception as exc:
                exc.staff_symbol=packet.symbol
                exc.staff_source_time=packet.sent_at_ms if source_time is None else source_time
                raise
            feeds={}
            for frame in children:
                key=(frame.symbol,frame.timeframe);entry=self._entries.get(key)
                if entry is not None and entry is not old[key]:feeds[frame.timeframe]=entry.snapshot
            return StaffPublication(packet,response,feeds,tuple(dict(v) for v in self._gap_records[gap_start:]))

    def receive_one(self, read_exact, *, finish=None) -> None:
        """Read one complete frame; callable(size) supplies exactly size bytes."""
        header = read_exact(PIPE_HEADER.size)
        magic, version, snapshot_id, sym_len, tf_len, bars, cols = PIPE_HEADER.unpack(header)
        if magic != PIPE_MAGIC:
            raise RuntimeError(f"Named Pipe magic mismatch: 0x{magic:08X}")
        if version == 2:
            try:
                frame = wire.read_v2(header + read_exact(8), read_exact)
                if finish is not None:
                    finish()
            except wire.UnknownWireSchema as exc:
                with self._lock:
                    if exc.symbol and exc.timeframe:
                        self._schema_errors[(exc.symbol, exc.timeframe)] = exc.schema_id
                    else:
                        self._schema_connection_error = exc.schema_id or -1
                raise
            return self._receive_v2(frame)
        with self._lock:
            self._schema_connection_error = -1
        raise wire.UnknownWireSchema(0)  # v1 has no schema id; use the original revision.

    @staticmethod
    def _immutable_array(array, dtype):
        # Reuse only storage whose ultimate owner is immutable bytes. A readonly
        # flag or memoryview of a caller-owned bytearray is not sufficient.
        array = np.asarray(array, dtype=dtype)
        owner = array
        while True:
            if isinstance(owner,np.ndarray) and owner.base is not None:owner=owner.base
            elif isinstance(owner,memoryview):owner=owner.obj
            else:break
        if isinstance(owner, bytes) and array.flags.c_contiguous:
            return array
        return np.frombuffer(array.tobytes(), dtype=dtype).reshape(array.shape)

    @staticmethod
    def _replace_last_row(array, row):
        # Build the new immutable publication in one full-size allocation.
        # Existing readers retain their bytes owner; no writable full copy is
        # made before the immutable copy. Both Wire arrays are C-contiguous.
        row = np.ascontiguousarray(row, dtype=array.dtype)
        tail_bytes = array.strides[0]
        payload = b''.join((memoryview(array).cast('B')[:-tail_bytes], memoryview(row).cast('B')))
        return np.frombuffer(payload, dtype=array.dtype).reshape(array.shape)

    def _publish_arrays(self, symbol, timeframe, snapshot_id, times, volumes, values, *, advance_epoch=True, legacy_wonbi=None):
        if legacy_wonbi is None:
            legacy_wonbi = values.shape[1] == 45
        values = wire.mt5_values(values)
        bars, cols = values.shape
        times = self._immutable_array(times, '<i8')
        volumes = self._immutable_array(volumes, '<i8')
        # MQL5 EMPTY_VALUE ~= DBL_MAX. DataFrame 생성 전에 NumPy에서 in-place 정제.
        # NaN is already the normalized unavailable value. Re-copy only infinities
        # or MT5 EMPTY_VALUE, including when a retained history contains NaNs.
        invalid = np.isinf(values) | (np.abs(values) > 1.0e300)
        if invalid.any():
            values = values.copy()
            values[invalid] = np.nan

        # Match the rows retained by the legacy pandas pipeline for validation,
        # without constructing a DataFrame on the receiving thread. NaT is the
        # int64 minimum; duplicate seconds retain their last original row.
        if len(times) and times[0] != np.iinfo(np.int64).min and np.all(times[1:] > times[:-1]):
            retained = slice(max(0, len(times)-self.max_bars), len(times)) if self.max_bars else slice(0, 0)
        else:
            retained = np.flatnonzero(times != np.iinfo(np.int64).min)
            _, reverse = np.unique(times[retained][::-1], return_index=True)
            retained = retained[len(retained) - 1 - reverse]
            retained = retained[-self.max_bars:] if self.max_bars else retained[:0]
        if len(times[retained]) < 3:
            raise RuntimeError(f"[{symbol} {timeframe}] snapshot too short: {len(retained)}")
        if not np.isfinite(values[retained, :4]).all():
            raise RuntimeError(f'[{symbol} {timeframe}] invalid OHLC snapshot')
        last = values[retained][-1]
        valid = {ind: all(index is not None and np.isfinite(float(last[index]))
                          for index in indices)
                 for ind, indices in _MT5_REQUIRED_COLUMN_INDICES.items()}
        # setflags(write=False) alone can be undone on an owning ndarray.
        # A bytes-backed view makes immutability apply to retained readers too.
        values = self._immutable_array(values, '<f8')
        key = (symbol, timeframe)
        with self._lock:
            previous = self._snapshot.get(key, -1)
            if snapshot_id <= previous:
                return
            old = self._entries.get(key)
            gap = key not in self._updated or self._monotonic() - self._updated[key] > self.stale_seconds
            if not advance_epoch and (old is None or old.snapshot.indicator_validity != valid):
                raise wire.WireError('ROW validity transition requires FULL')
            if advance_epoch and (gap or previous == -1 or old is None or old.snapshot.indicator_validity != valid):
                self._health_epochs[key] = self._health_epochs.get(key, 0) + 1
            source_epoch = f'{self._health_session}:{self._health_epochs[key]}'
            received_at = self._monotonic()
            snapshot = StaffSnapshot(times, volumes, values, snapshot_id, source_epoch,
                                     MappingProxyType(valid), received_at, legacy_wonbi)
            self._entries[key] = _SnapshotEntry(snapshot)
            self._updated[key] = received_at
            self._snapshot[key] = snapshot_id
            self._requires_full.discard(key)
            self._schema_errors.pop(key, None)


    def wire_diagnostics(self):
        """Copyable diagnostics, separate from the unchanged SOURCE_HEALTH contract."""
        with self._lock:
            return {'schema':'staff-wire-diagnostics/v1', 'connections':self._connection_count,
                    'reconnects':self._reconnect_count, 'ea_build_hash':self._ea_build_hash,
                    'feeds':{k:dict(v) for k,v in self._wire_stats.items()},
                    'gaps':[dict(v) for v in self._gap_records], 'journal':str(self._gap_journal)}

    def _receive_v2(self, packet):
        if packet.kind == wire.WIRE_HELLO:
            with self._lock:
                self._ea_build_hash = packet.build_hash
                self._schema_connection_error = None
            return wire.pack_hello(packet.build_hash, ack=True, schema_id=packet.schema_id)
        if packet.kind == wire.WIRE_ACK:
            raise wire.WireError('unexpected HELLO acknowledgement at server')
        children = packet.children if packet.kind == wire.WIRE_BUNDLE else (packet,)
        now_ms = time.time_ns() // 1000000
        sent = packet.sent_at_ms
        pending_gaps = []
        # Decode/CRC finishes before publication. Hold a single lock for the entire
        # bundle; rollback all mutations on a bad child. No latest-only queue.
        with self._lock:
            saved = (dict(self._entries),dict(self._updated),dict(self._snapshot),
                     dict(self._health_epochs),set(self._requires_full),dict(self._schema_errors))
            stats = {k:dict(v) for k,v in self._wire_stats.items()}
            try:
                for frame in children:
                    key=(frame.symbol,frame.timeframe)
                    previous=self._snapshot.get(key, -1)
                    stat=stats.setdefault(key, {'received':0,'accepted':0,'duplicate_or_reverse':0,
                                                'missing_sequences':0,'last_receive_delay_ms':None,
                                                'reconnects':0})
                    stat['received'] += 1
                    if frame.seq <= previous:
                        stat['duplicate_or_reverse'] += 1
                        continue
                    if previous < 0 and stat['accepted']:
                        stat['reconnects'] = stat.get('reconnects',0)+1
                    stat['last_connection'] = self._connection_count
                    if previous >= 0 and frame.seq > previous + 1:
                        missing={'schema':'staff-seq-gap/v1','received_at_unix_ms':now_ms,
                            'sent_at_unix_ms':sent or None, 'symbol':frame.symbol,'timeframe':frame.timeframe,
                            'first_missing_seq':previous+1,'last_missing_seq':frame.seq-1,
                            'next_received_seq':frame.seq,'connection':self._connection_count,
                            'ea_build_hash':self._ea_build_hash}
                        pending_gaps.append(missing)
                        stat['missing_sequences'] += frame.seq-previous-1
                    if frame.kind == wire.WIRE_FULL:
                        self._publish_arrays(*key, frame.seq, frame.times, frame.volumes, frame.values)
                    else:
                        if previous < 0 or key in self._requires_full or key not in self._entries:
                            raise wire.WireError('ROW/HEARTBEAT requires FULL after connect')
                        old=self._entries[key].snapshot
                        if frame.kind == wire.WIRE_ROW:
                            if frame.times[0] != old.time[-1]:
                                raise wire.WireError('ROW new bar requires FULL')
                            volumes=self._replace_last_row(old.volume,frame.volumes)
                            values=self._replace_last_row(old.values,wire.mt5_values(frame.values))
                            self._publish_arrays(*key,frame.seq,old.time,volumes,values,advance_epoch=False,legacy_wonbi=old.legacy_wonbi)
                        else:
                            snapshot=old._replace(seq=frame.seq,received_at=self._monotonic())
                            self._entries[key]=_SnapshotEntry(snapshot)
                            self._updated[key]=snapshot.received_at
                            self._snapshot[key]=frame.seq
                    stat['accepted'] += 1
                    stat['last_seq'] = frame.seq
                    stat['last_receive_delay_ms'] = max(0,now_ms-sent) if sent else None
                # Rare gap records are append-only JSONL: failure is visible and
                # aborts publication; it never silently drops an event.
                if pending_gaps:
                    self._gap_journal.parent.mkdir(parents=True,exist_ok=True)
                    with self._gap_journal.open('a',encoding='utf-8') as log:
                        for gap in pending_gaps:
                            log.write(json.dumps(gap,ensure_ascii=False,separators=(',',':'))+'\n')
                        log.flush()
                    self._gap_records.extend(pending_gaps)
                self._wire_stats=stats
            except Exception:
                (self._entries,self._updated,self._snapshot,self._health_epochs,
                 self._requires_full,self._schema_errors)=saved
                raise

    def _receiver_loop(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        PIPE_ACCESS_DUPLEX = 0x00000003
        PIPE_TYPE_BYTE = 0x00000000
        PIPE_READMODE_BYTE = 0x00000000
        PIPE_WAIT = 0x00000000
        PIPE_UNLIMITED_INSTANCES = 255
        ERROR_PIPE_CONNECTED = 535

        k32.CreateNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_uint32, ctypes.c_void_p]
        k32.CreateNamedPipeW.restype = ctypes.c_void_p
        k32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.ConnectNamedPipe.restype = ctypes.c_int
        k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k32.ReadFile.restype = ctypes.c_int
        k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k32.WriteFile.restype = ctypes.c_int
        k32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                with staff_pipe_security() as security:
                    handle = k32.CreateNamedPipeW(
                        self.pipe_name,
                        PIPE_ACCESS_DUPLEX,
                        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                        PIPE_UNLIMITED_INSTANCES,
                        1 << 20,
                        1 << 20,
                        0,
                        ctypes.byref(security),
                    )
                    create_error = ctypes.get_last_error()
            except OSError:
                logging.exception("❌ [Named Pipe] 현재 사용자 연결 권한 설정 실패")
                self._stop_event.wait(1.0)
                continue
            if handle == INVALID_HANDLE_VALUE or not handle:
                logging.error("❌ [Named Pipe] 생성 실패 err=%d", create_error)
                self._stop_event.wait(1.0)
                continue

            try:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("🟢 [MT5 Named Pipe] 생성 완료 / 연결 대기 (동일 사용자 일반·관리자 권한 지원) - %s", self.pipe_name)
                ok = k32.ConnectNamedPipe(handle, None)
                if not ok and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
                    raise OSError(ctypes.get_last_error(), "ConnectNamedPipe 실패")

                # 새 Named Pipe 연결은 새로운 writer 세션일 수 있습니다.
                # MT5 EA만 재시작되면 snapshot sequence가 0부터 다시 시작하므로,
                # 이전 세션의 sequence 비교 기록만 초기화해 새 snapshot을 정상 수용합니다.
                # 정상 Snapshot 캐시와 freshness 상태는 그대로 유지합니다.
                self.reconnect()

                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("✅ [MT5 Named Pipe 연결] %s", self.pipe_name)
                while not self._stop_event.is_set():
                    self._consume_one(k32, handle)
            except Exception as e:
                if not self._stop_event.is_set():
                    logging.warning("⚠️ [MT5 Named Pipe 재연결 대기] %s", e)
            finally:
                try:
                    k32.DisconnectNamedPipe(handle)
                except Exception:
                    pass
                k32.CloseHandle(handle)



# ---------------------------------------------------------------------
# Runtime control metadata (calculation is client-owned)


# ---------------------------------------------------------------------










WONBI_LENGTH = 4

WONBI_DEFAULT_SIGMA = 3.0


# ---------------------------------------------------------------------
# MT5 snapshot / indicator health validation
# ---------------------------------------------------------------------
MT5_REQUIRED_BY_INDICATOR = {
    # TREND/FVG/SWEEP 2.0은 OHLCV만 요청할 수 있으므로 지표를 무조건 요구하지 않습니다.
    "EMA": ["ema_20", "ema_50", "ema_200"],
    # PRICE percentile을 요청하는 OZ는 HMA6/17 구조도 함께 사용합니다.
    "PRICE": [
        "price_hma_6", "price_band_lower", "price_band_upper", "hma_6", "hma_17",
        "price_regime_basis", "price_regime_upper", "price_regime_lower",
    ],
    # 전략 HMA 공용 계약: MT5 EA가 OPEN 기준으로 전달하는 6/17/50/168을 모두 검증합니다.
    "HMA": ["hma_6", "hma_17", "hma_50", "hma_168"],
    "RSI": ["RSI_val", "RSI_db", "RSI_ub", "RSI_basis", "RSI_regime_upper", "RSI_regime_lower"],
    "STO": ["STO_val", "STO_db", "STO_ub", "STO_basis", "STO_regime_upper", "STO_regime_lower"],
    "DI": ["DI_val", "DI_db", "DI_ub", "DI_basis", "DI_regime_upper", "DI_regime_lower"],
    # WONBI는 EA가 계산한 원본 열만 사용합니다.
    "WONBI": ['open_band_4_mid', 'wonbi_upper', 'wonbi_lower'],
}

# Schema and indicator requirements are fixed for this module's lifetime.
# None preserves the original False result for a missing required column;
# scalar float/isfinite and all() short-circuit semantics remain unchanged.
_MT5_REQUIRED_COLUMN_INDICES = {
    ind: tuple(PIPE_VALUE_COLUMNS.index(column) if column in PIPE_VALUE_COLUMNS else None
               for column in columns)
    for ind, columns in MT5_REQUIRED_BY_INDICATOR.items()
}




# ---------------------------------------------------------------------
# ZMQ server - old SWEEP/FVG compatible protocol
# ---------------------------------------------------------------------
class SnapshotUnavailable(RuntimeError):
    def __init__(self, symbol: str, timeframe: str):
        self.symbol, self.timeframe = symbol, timeframe
        super().__init__(f"[{symbol} {timeframe}] MT5 통신 실패: Named Pipe snapshot 없음")


class DataServer:
    def __init__(self, config: dict[str, str], *, cache=None,
                 allowed_symbols=None, allowed_timeframes=None):
        self.config = config
        self.cache = cache if cache is not None else StaffPipeCache(
            config.get("STAFF_PIPE_NAME", r"\\.\pipe\StaffOfMoses_v1"),
            max_bars=int(config.get("STAFF_BARS", "650")),
            stale_seconds=float(config.get("STAFF_STALE_SEC", "30")),
        )
        self.endpoint = config.get("STAFF_BIND_ENDPOINT", "tcp://127.0.0.1:5555")
        self.allowed_symbols = set(configured_symbols(config))
        self.allowed_tfs = {x.lower() for x in csv_items(config.get("STAFF_ALLOWED_TIMEFRAMES"))}
        if allowed_symbols is not None:
            self.allowed_symbols = set(allowed_symbols)
        if allowed_timeframes is not None:
            self.allowed_tfs = set(allowed_timeframes)

        # 주말 휴장 중에는 골드/나스닥 계열의 데이터 요청을 정상 대기({})로 응답합니다.
        # BTC/XBT 계열은 24/7 거래이므로 이 휴장 처리에서 제외합니다.
        self._weekend_closed_logged: set[str] = set()
        self._market_state_lock = threading.Lock()

        # ZMQ REP 소켓도 run()에서 bind합니다.
        # 먼저 Named Pipe 수신기를 기동한 뒤 전략 API를 엽니다.
        self.context = None
        self.sock = None
        self._feed_wait_logs: dict[tuple[str, str], float] = {}
        # Serialize requests independently of the receiver publication lock.
        self._request_lock = threading.RLock()
        # Request-thread only. Bounded slots retain just the newest publication
        # for a requested feed set; receiver never waits for response encoding.
        self._encoded_replies = {}

    def _feed_wait_reply(self, exc: SnapshotUnavailable) -> dict:
        key = (exc.symbol, exc.timeframe)
        now = time.monotonic()
        last = self._feed_wait_logs.get(key)
        if last is None or now - last >= 30:
            logging.warning("[MT5 데이터 준비 대기] %s %s: 아직 snapshot 미수신; 다른 feed는 계속 처리합니다.", *key)
            self._feed_wait_logs[key] = now
        return {"error": f"request processing failed: RuntimeError: {exc}",
                "error_code": "FEED_NOT_READY", "retryable": True,
                "symbol": exc.symbol, "timeframe": exc.timeframe}

    def validate_target(self, symbol: str, tf: str) -> None:
        if self.allowed_symbols and symbol not in self.allowed_symbols:
            raise ValueError(f"config SYMBOLS 밖의 symbol 요청: {symbol}")
        if self.allowed_tfs and tf.lower() not in self.allowed_tfs:
            # 기존 전략은 base뿐 아니라 upper/top TF도 요청할 수 있으므로
            # 기본값은 제한 없음입니다. 제한이 필요할 때만 STAFF_ALLOWED_TIMEFRAMES를 설정합니다.
            raise ValueError(f"config STAFF_ALLOWED_TIMEFRAMES 밖의 timeframe 요청: {tf}")

    @staticmethod
    def _is_crypto_weekend_symbol(symbol: str) -> bool:
        s = str(symbol or "").upper()
        return "BTC" in s or "XBT" in s

    def _is_weekend_market_closed(self, symbol: str) -> bool:
        """골드/나스닥 등 주중시장 주말 휴장 판정. BTC/XBT는 항상 제외."""
        if self._is_crypto_weekend_symbol(symbol):
            return False

        # 미국 동부시간 기준: 금요일 17:00 이후 ~ 일요일 18:00 이전을 주말 휴장으로 봅니다.
        # ZoneInfo가 DST를 자동 반영하므로 한국시간 고정 시각으로 하드코딩하지 않습니다.
        now_et = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo("America/New_York"))
        wd = now_et.weekday()  # Mon=0 ... Sun=6
        if wd == 4 and now_et.hour >= 17:
            return True
        if wd == 5:
            return True
        if wd == 6 and now_et.hour < 18:
            return True
        return False

    def _weekend_wait_response(self, symbol: str) -> Optional[dict]:
        closed = self._is_weekend_market_closed(symbol)
        with self._market_state_lock:
            if closed:
                if symbol not in self._weekend_closed_logged:
                    if logging.getLogger().isEnabledFor(logging.INFO):
                        logging.info("🌙 [주말 휴장] %s · 전략 데이터 요청 조용히 대기 (BTC/XBT 제외)", symbol)
                    self._weekend_closed_logged.add(symbol)
                return {}
            if symbol in self._weekend_closed_logged:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("🟢 [주말 휴장 종료] %s · 전략 데이터 공급 재개", symbol)
                self._weekend_closed_logged.discard(symbol)
        return None

    def handle(self, req: dict) -> dict:
        with self._request_lock:
            if isinstance(req, dict) and str(req.get('kind', '')).upper() == 'SNAPSHOT':
                return self.snapshot_reply(req)
            return self._handle_request(req)

    def snapshot_reply(self, req):
        """S3 opt-in multipart response; no pandas or feature calculation here."""
        from staff_snapshot import encode_reply
        with self._request_lock:
            symbol = str(req.get('symbol', '')).strip()
            tfs = req.get('timeframes', [])
            indicators = req.get('indicators', [])
            if not symbol:
                raise ValueError('symbol이 비어 있습니다.')
            if not isinstance(tfs, (list, tuple)):
                raise ValueError('timeframes는 list/tuple 이어야 합니다.')
            if not isinstance(indicators, (list, tuple)):
                raise ValueError('indicators는 list/tuple 이어야 합니다.')
            indicators = [str(x).strip().upper() for x in indicators if str(x).strip()]
            sigma = WONBI_DEFAULT_SIGMA
            if self._weekend_wait_response(symbol) is not None:
                return encode_reply(sigma=sigma, closed=True, max_bars=self.cache.max_bars)
            feeds = {}
            captured = self.cache.snapshots_with_age(symbol, [str(tf).strip().lower() for tf in tfs])
            for tf in tfs:
                tf = str(tf).strip().lower()
                self.validate_target(symbol, tf)
                snapshot, age = captured[tf]
                if snapshot is None:
                    raise SnapshotUnavailable(symbol, tf)
                if age > self.cache.stale_seconds:
                    raise RuntimeError(
                        f'[{symbol} {tf}] MT5 통신 끊김/정지 의심: Named Pipe snapshot이 {age:.1f}초 동안 갱신되지 않음')
                if self._feed_wait_logs.pop((symbol, tf), None) is not None:
                    if logging.getLogger().isEnabledFor(logging.INFO):
                        logging.info('[MT5 데이터 수신 재개] %s %s', symbol, tf)
                # Match legacy per-TF validation order before checking the next
                # feed. All v1 columns exist; optional buffers may contain NaN.
                requested = {str(ind).strip().upper() for ind in indicators if str(ind).strip()}
                required = list(dict.fromkeys(c for ind in requested
                    for c in MT5_REQUIRED_BY_INDICATOR.get(ind, [])))
                retained = np.flatnonzero(snapshot.time != np.iinfo(np.int64).min)
                _, reverse = np.unique(snapshot.time[retained][::-1], return_index=True)
                retained = retained[len(retained) - 1 - reverse][-self.cache.max_bars:]
                bad = [col for col in required
                       if not np.isfinite(float(snapshot.values[retained[-1], PIPE_VALUE_COLUMNS.index(col)]))]
                if bad:
                    raise RuntimeError(
                        f"[{symbol} {tf}] 요청 지표값 비정상(최근 20봉 전부 NaN): {', '.join(bad)} | "
                        f"요청={','.join(sorted(requested)) or 'OHLCV'}")
                feeds[tf] = snapshot
            if not feeds:
                raise RuntimeError(f'[{symbol}] 유효한 MT5 응답 데이터가 하나도 없음')
            slot = (symbol, tuple(feeds), sigma, self.cache.max_bars)
            cached = self._encoded_replies.get(slot)
            publications = tuple(feeds.values())
            if cached is None or not all(a is b for a, b in zip(cached[0], publications)):
                parts = encode_reply(((symbol, tf, snapshot) for tf, snapshot in feeds.items()),
                                     sigma=sigma, max_bars=self.cache.max_bars)
                cached = (publications, tuple(parts))
                if len(self._encoded_replies) >= 128 and slot not in self._encoded_replies:
                    self._encoded_replies.pop(next(iter(self._encoded_replies)))
                self._encoded_replies[slot] = cached
            # bytes are immutable; return a fresh container to injected callers.
            return list(cached[1])

    def dispatch_multipart(self, parts):
        """Public transport boundary; tagged JSON never enters legacy unpickling."""
        from staff_schema import REQUEST_TAG
        from staff_snapshot import encode_reply
        snapshot_mode = bool(parts) and parts[0] == REQUEST_TAG
        try:
            if snapshot_mode:
                if len(parts) != 2:
                    raise ValueError('SNAPSHOT request requires tag and JSON')
                req = json.loads(parts[1])
                if not isinstance(req, dict) or req.get('kind') != 'SNAPSHOT':
                    raise ValueError('invalid SNAPSHOT request')
                return self.snapshot_reply(req)
            if len(parts) != 1:
                raise ValueError('legacy request requires one frame')
            req = pickle.loads(parts[0])
            if isinstance(req, dict) and str(req.get('kind', '')).upper() == 'SNAPSHOT':
                raise ValueError('SNAPSHOT requires tagged JSON multipart')
            resp = self.handle(req)
        except SnapshotUnavailable as exc:
            resp = self._feed_wait_reply(exc)
        except Exception as exc:
            resp = {'error': f'request processing failed: {type(exc).__name__}: {exc}'}
        if snapshot_mode:
            return encode_reply(error=resp, sigma=WONBI_DEFAULT_SIGMA, max_bars=self.cache.max_bars)
        return [pickle.dumps(resp, protocol=pickle.DEFAULT_PROTOCOL)]

    def _handle_request(self, req: dict) -> dict:
        if not isinstance(req, dict):
            raise ValueError("요청 형식이 dict가 아닙니다.")

        if str(req.get("kind", "")).upper() == "PING":
            return {
                "ok": True,
                "pong": True,
                "service": "THE STAFF OF MOSES",
                "version": "2.0",
                "wonbi_source": "OPEN",
                "wonbi_length": WONBI_LENGTH,
                "wonbi_sigma": WONBI_DEFAULT_SIGMA,
            }

        if str(req.get('kind', '')).upper() != 'SOURCE_HEALTH':
            return {"error": "SNAPSHOT API 사용: legacy 데이터 요청은 지원하지 않습니다.",
                    "error_code": "SNAPSHOT_API_REQUIRED", "retryable": False}

        symbol = str(req.get("symbol", "")).strip()
        tfs = req.get("timeframes", [])
        indicators = req.get("indicators", [])

        if not symbol:
            raise ValueError("symbol이 비어 있습니다.")
        if not isinstance(tfs, (list, tuple)):
            raise ValueError("timeframes는 list/tuple 이어야 합니다.")
        if not isinstance(indicators, (list, tuple)):
            raise ValueError("indicators는 list/tuple 이어야 합니다.")
        indicators = [str(x).strip().upper() for x in indicators if str(x).strip()]

        if str(req.get('kind', '')).upper() == 'SOURCE_HEALTH':
            closed = self._weekend_wait_response(symbol) is not None
            health_tfs = [str(tf).lower() for tf in tfs]
            for tf in health_tfs:
                self.validate_target(symbol, tf)
            feeds = self.cache.health(symbol, health_tfs, closed)
            return {'ok':True, 'feeds':feeds}

        return {"error": "SNAPSHOT API 사용: legacy 데이터 요청은 지원하지 않습니다.",
                "error_code": "SNAPSHOT_API_REQUIRED", "retryable": False}

    def run(self, stop_event: threading.Event) -> None:
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟢 [THE STAFF OF MOSES] 시작")

        # 핵심 데이터 경로를 가장 먼저 시작합니다.
        self.cache.start(stop_event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟢 [MT5 Named Pipe] 수신기 시작 요청 - %s", self.cache.pipe_name)

        # 전략용 ZMQ는 Named Pipe 기동 후 엽니다.
        try:
            self.context = zmq.Context()
            self.sock = self.context.socket(zmq.REP)
            self.sock.setsockopt(zmq.LINGER, 0)
            self.sock.bind(self.endpoint)
        except Exception:
            logging.exception(
                "❌ [THE STAFF OF MOSES] 전략 ZMQ bind 실패 %s - "
                "같은 THE STAFF OF MOSES 서버가 이미 실행 중인지 확인하십시오.",
                self.endpoint,
            )
            stop_event.set()
            return

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟢 [THE STAFF OF MOSES/ZMQ] 전략 요청 대기 - %s", self.endpoint)

        poller = zmq.Poller()
        poller.register(self.sock, zmq.POLLIN)
        try:
            while not stop_event.is_set():
                events = dict(poller.poll(500))
                if self.sock not in events:
                    continue

                try:
                    parts = self.sock.recv_multipart()
                    resp = self.dispatch_multipart(parts)
                except SnapshotUnavailable as e:
                    resp = self._feed_wait_reply(e)
                except Exception as e:
                    logging.exception("요청 처리 오류 - THE STAFF OF MOSES는 계속 실행됩니다.")
                    resp = {"error": f"request processing failed: {type(e).__name__}: {e}"}

                try:
                    if isinstance(resp, dict):
                        self.sock.send_pyobj(resp)
                    else:
                        self.sock.send_multipart(resp)
                except Exception:
                    logging.exception("응답 전송 오류")
        finally:
            try:
                if self.sock is not None:
                    self.sock.close(0)
            finally:
                if self.context is not None:
                    self.context.term()
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("[THE STAFF OF MOSES] 종료")


def main() -> None:
    config = load_config("config.txt")
    from module_diagnostics import configure
    configure(Path(__file__).resolve().parent.parent,config)
    stop_event = threading.Event()
    server = DataServer(config)
    try:
        server.run(stop_event)
    except KeyboardInterrupt:
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("종료 요청")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
