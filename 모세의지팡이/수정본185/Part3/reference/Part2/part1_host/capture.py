"""STAFF pipe capture written by the MT5 EA in data-build BACKTEST (STAFF_PIPE_V1).

The EA records, every simulated second and for every feed, the same 45-column
payload that ``PublishFeed`` sends to Part1 over the Named Pipe LIVE:

  file header  <IIII>  magic 'MSP2', version 1, value_columns 45, max_bars 650
  record       <iqii>  kind (1 FULL / 2 ROW), observed_time (s), rows, family_flags
               times[rows] <q>, tick_volumes[rows] <q>, values[rows*45] <d>

FULL carries the whole payload (first record, every new bar, and while any
indicator family is not ready). ROW carries only the forming bar; the rest of
the frame is unchanged from the previous record. This module rebuilds the full
payload for each record and packs it into the exact LIVE wire message
(``PIPE_HEADER`` + symbol + tf + times + volumes + values) consumed by Part1.
"""
from __future__ import annotations

import heapq
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

CAPTURE_MAGIC = 0x4D535032
CAPTURE_VERSION = 1
CAPTURE_V2_MAGIC = 0x4D535033
CAPTURE_V2_POLICY = "STAFF_PIPE_V2"
V2_RECORD_HEADER = struct.Struct("<qiI")
CAPTURE_POLICY = 'STAFF_PIPE_V1'
FILE_HEADER = struct.Struct('<IIII')
RECORD_HEADER = struct.Struct('<iqii')
RECORD_FULL = 1
RECORD_ROW = 2

# Part1 THE STAFF OF MOSES wire constants (PIPE_HEADER '<IIqIIII', magic 'SMOS', version 1).
PIPE_MAGIC = 0x534D4F53
PIPE_VERSION = 1
PIPE_HEADER = struct.Struct('<IIqIIII')
VALUE_COLUMNS = 45
MAX_BARS = 650


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class CaptureFeed:
    index: int
    timeframe: str
    path: Path
    records: int


def parse_capture_manifest(root) -> dict:
    """Read the pipe-capture part of an MT5 native export manifest."""
    root = Path(root).expanduser().resolve()
    manifest = root / 'manifest.tsv'
    if not (root / 'complete.txt').is_file() or not manifest.is_file():
        raise CaptureError('NATIVE_EXPORT_INCOMPLETE')
    meta, feeds = {}, []
    for line in manifest.read_text('ascii', errors='strict').splitlines()[1:]:
        parts = line.rstrip('\r\n').split('\t')
        if not parts or not parts[0]:
            continue
        if parts[0] == 'pipe_feed':
            if len(parts) != 5:
                raise CaptureError('PIPE_CAPTURE_MANIFEST_FORMAT')
            path = (root / parts[3]).resolve()
            if path.parent != root or not path.is_file():
                raise CaptureError('PIPE_CAPTURE_FILE: ' + parts[3])
            feeds.append(CaptureFeed(int(parts[1]), parts[2].lower(), path, int(parts[4])))
        elif len(parts) == 2:
            meta[parts[0]] = parts[1]
    if meta.get('pipe_capture') not in (CAPTURE_POLICY, CAPTURE_V2_POLICY):
        raise CaptureError('PIPE_CAPTURE_REQUIRED: 이 MT5 export에는 LIVE와 같은 45열 STAFF 기록이 없습니다. '
                           '현재 Part1 THE_STAFF_OF_MOSES.mq5로 다시 구축해야 합니다.')
    if not feeds:
        raise CaptureError('PIPE_CAPTURE_NO_FEEDS')
    return {'root': root, 'symbol': meta.get('symbol', ''), 'session': meta.get('session', ''),
            'wire_version': 2 if meta.get('pipe_capture') == CAPTURE_V2_POLICY else 1,
            'feeds': sorted(feeds, key=lambda f: f.index)}


def _read_v1_records(path: Path, expected: Optional[int] = None):
    with Path(path).open('rb') as f:
        head = f.read(FILE_HEADER.size)
        if len(head) != FILE_HEADER.size:
            raise CaptureError('PIPE_CAPTURE_HEADER_SHORT')
        magic, version, columns, max_bars = FILE_HEADER.unpack(head)
        if (magic, version, columns) != (CAPTURE_MAGIC, CAPTURE_VERSION, VALUE_COLUMNS):
            raise CaptureError('PIPE_CAPTURE_HEADER_MISMATCH')
        count = 0
        while True:
            raw = f.read(RECORD_HEADER.size)
            if not raw:
                break
            if len(raw) != RECORD_HEADER.size:
                raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            kind, observed, rows, flags = RECORD_HEADER.unpack(raw)
            if kind not in (RECORD_FULL, RECORD_ROW) or not (1 <= rows <= max_bars):
                raise CaptureError('PIPE_CAPTURE_RECORD_INVALID')
            if kind == RECORD_ROW and rows != 1:
                raise CaptureError('PIPE_CAPTURE_ROW_SIZE')
            body = f.read(rows * 16 + rows * VALUE_COLUMNS * 8)
            if len(body) != rows * 16 + rows * VALUE_COLUMNS * 8:
                raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            times = np.frombuffer(body, dtype='<i8', count=rows, offset=0)
            volumes = np.frombuffer(body, dtype='<i8', count=rows, offset=rows * 8)
            values = np.frombuffer(body, dtype='<f8', offset=rows * 16).reshape(rows, VALUE_COLUMNS)
            count += 1
            yield kind, int(observed), int(flags), times, volumes, values
        if expected is not None and count != expected:
            raise CaptureError(f'PIPE_CAPTURE_COUNT_MISMATCH: {count} != {expected}')


def wire_schema():
    # Load the packaged public Part1 registry, independently of whichever frozen
    # BEFORE runtime is currently bound in sys.modules. No mixed-version host.
    import functools
    return _wire_schema()


import functools
@functools.lru_cache(maxsize=1)
def _wire_schema():
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'Part1/program/staff_schema.py'
    spec=importlib.util.spec_from_file_location('part2_wire_schema',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def capture_version(path):
    with Path(path).open('rb') as f:
        head=f.read(FILE_HEADER.size)
    if len(head)!=FILE_HEADER.size:raise CaptureError('PIPE_CAPTURE_HEADER_SHORT')
    magic,version,cols,bars=FILE_HEADER.unpack(head)
    if cols != len(wire_schema().PIPE_VALUE_COLUMNS) or not 3<=bars<=650:raise CaptureError('PIPE_CAPTURE_HEADER_MISMATCH')
    if (magic,version,cols)==(CAPTURE_MAGIC,1,45):return 1
    if (magic,version)==(CAPTURE_V2_MAGIC,2):return 2
    raise CaptureError('PIPE_CAPTURE_HEADER_MISMATCH')


def read_v2_records(path, expected=None):
    """MSP3 record: observed second i64, family flags i32, byte size u32, v2 frame."""
    if capture_version(path)!=2:raise CaptureError('MSP3_REQUIRED')
    w=wire_schema();count=0;last_seq={};previous=None
    with Path(path).open('rb') as f:
        file_columns=FILE_HEADER.unpack(f.read(FILE_HEADER.size))[2]
        while True:
            header=f.read(V2_RECORD_HEADER.size)
            if not header:break
            if len(header)!=V2_RECORD_HEADER.size:raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            observed,flags,length=V2_RECORD_HEADER.unpack(header)
            if not w.WIRE_HEADER.size+4<=length<=w.WIRE_MAX_PAYLOAD+256:
                raise CaptureError('MSP3_RECORD_SIZE')
            raw=f.read(length)
            if len(raw)!=length:raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            try:frame=w.decode_v2(raw)
            except w.WireError as exc:raise CaptureError(str(exc)) from exc
            if len(w.WIRE_SCHEMAS[frame.schema_id]) != file_columns:
                raise CaptureError('MSP3_SCHEMA_COLUMNS_MISMATCH')
            if frame.kind not in (w.WIRE_FULL,w.WIRE_ROW,w.WIRE_HEARTBEAT):
                raise CaptureError('MSP3_EXPECTS_FEED_RECORD')
            key=(frame.symbol,frame.timeframe)
            if frame.seq<=last_seq.get(key,0):raise CaptureError('MSP3_SEQUENCE_ORDER')
            if previous is not None and observed<=previous:raise CaptureError('PIPE_CAPTURE_TIME_ORDER')
            last_seq[key]=frame.seq;previous=observed;count+=1
            yield int(observed),int(flags),frame,raw
    if expected is not None and count!=expected:raise CaptureError('PIPE_CAPTURE_COUNT_MISMATCH')


def _read_records(path: Path, expected: Optional[int] = None):
    if capture_version(path)==1:
        yield from _read_v1_records(path,expected)
    else:
        for observed,flags,frame,raw in read_v2_records(path,expected):
            yield frame.kind,observed,flags,frame.times,frame.volumes,frame.values


def iter_v2_publications(root, symbol=None, *, start_s=None, end_s=None):
    info=parse_capture_manifest(root);symbol=symbol or info['symbol']
    if symbol!=info['symbol']:raise CaptureError('PIPE_CAPTURE_SYMBOL_MISMATCH')
    heap=[];pending={};w=wire_schema()
    for feed in info['feeds']:
        it=iter(read_v2_records(feed.path,feed.records));item=next(it,None)
        if item:heapq.heappush(heap,(item[0],feed.index,feed.timeframe,item,it))
    while heap:
        observed,index,tf,item,it=heapq.heappop(heap)
        _,_,frame,raw=item
        if (frame.symbol,frame.timeframe)!=(symbol,tf):raise CaptureError('MSP3_FEED_IDENTITY')
        if end_s is not None and observed>=end_s:continue
        if start_s is not None and observed<start_s:
            prior=pending.get(index)
            if frame.kind==w.WIRE_FULL:state=[a.copy() for a in (frame.times,frame.volumes,frame.values)]
            elif prior is None:raise CaptureError('PIPE_CAPTURE_ROW_BEFORE_FULL')
            else:
                state=prior[2]
                if frame.kind==w.WIRE_ROW:
                    if state[0][-1]!=frame.times[0]:raise CaptureError('PIPE_CAPTURE_ROW_BAR_MISMATCH')
                    state[1][-1]=frame.volumes[0];state[2][-1]=frame.values[0]
            pending[index]=(tf,frame.seq,state)
        else:
            if pending:
                for j in sorted(pending):
                    ptf,seq,state=pending[j]
                    yield start_s,ptf,w.pack_v2(symbol,ptf,*state,seq=seq)
                pending={}
            yield observed,tf,raw
        nxt=next(it,None)
        if nxt:heapq.heappush(heap,(nxt[0],index,tf,nxt,it))
    for j in sorted(pending):
        tf,seq,state=pending[j]
        yield start_s,tf,w.pack_v2(symbol,tf,*state,seq=seq)


class FeedReplay:
    """Rebuild the LIVE payload of one feed from FULL/ROW records."""

    def __init__(self, symbol: str, feed: CaptureFeed):
        self.symbol = symbol
        self.timeframe = feed.timeframe
        self.feed = feed
        self.times = None
        self.volumes = None
        self.values = None

    def __iter__(self):
        previous = None
        for kind, observed, flags, times, volumes, values in _read_records(self.feed.path, self.feed.records):
            if previous is not None and observed <= previous:
                raise CaptureError(f'PIPE_CAPTURE_TIME_ORDER {self.timeframe}')
            previous = observed
            if kind == RECORD_FULL:
                self.times = np.array(times, dtype='<i8')
                self.volumes = np.array(volumes, dtype='<i8')
                self.values = np.array(values, dtype='<f8')
            elif kind == RECORD_ROW:
                if self.times is None:
                    raise CaptureError(f'PIPE_CAPTURE_ROW_BEFORE_FULL {self.timeframe}')
                if int(times[0]) != int(self.times[-1]):
                    raise CaptureError(f'PIPE_CAPTURE_ROW_BAR_MISMATCH {self.timeframe}')
                self.volumes[-1] = volumes[0]
                self.values[-1, :] = values[0]
            elif self.times is None:
                raise CaptureError(f'PIPE_CAPTURE_HEARTBEAT_BEFORE_FULL {self.timeframe}')
            yield observed, self.times, self.volumes, self.values


_sequence = 0


def pack_wire(symbol: str, timeframe: str, times, volumes, values, snapshot: Optional[int] = None) -> bytes:
    """Exact LIVE STAFF Named Pipe message (same as MT5 WritePipeSnapshot)."""
    global _sequence
    if snapshot is None:
        _sequence += 1
        snapshot = _sequence
    sym = symbol.encode('utf-8')
    tf = timeframe.encode('utf-8')
    times = np.ascontiguousarray(times, dtype='<i8')
    volumes = np.ascontiguousarray(volumes, dtype='<i8')
    values = np.ascontiguousarray(values, dtype='<f8')
    bars = len(times)
    if values.shape != (bars, VALUE_COLUMNS) or len(volumes) != bars:
        raise CaptureError('payload shape mismatch')
    return (PIPE_HEADER.pack(PIPE_MAGIC, PIPE_VERSION, int(snapshot), len(sym), len(tf), bars, VALUE_COLUMNS)
            + sym + tf + times.tobytes() + volumes.tobytes() + values.tobytes())


def iter_publications(root, symbol: Optional[str] = None, *, start_s: Optional[int] = None,
                      end_s: Optional[int] = None) -> Iterator[tuple[int, str, bytes]]:
    """Merge all feeds in observed-time order (feed index order within a second).

    Yields (observed_second, timeframe, wire_bytes). Records before start_s are
    applied to the payload state but not yielded; the last one is yielded at
    start_s so the run starts from the LIVE state at that second.
    """
    info = parse_capture_manifest(root)
    if info['wire_version']==2:
        yield from iter_v2_publications(root,symbol,start_s=start_s,end_s=end_s)
        return
    symbol = symbol or info['symbol']
    if info['symbol'] and symbol != info['symbol']:
        raise CaptureError('PIPE_CAPTURE_SYMBOL_MISMATCH')
    iterators = []
    for feed in info['feeds']:
        iterators.append((feed.index, FeedReplay(symbol, feed)))
    heap = []
    live = {}
    for index, replay in iterators:
        it = iter(replay)
        first = next(it, None)
        if first is not None:
            heapq.heappush(heap, (first[0], index, replay.timeframe, first, it))
    pending_start = {}
    while heap:
        observed, index, tf, item, it = heapq.heappop(heap)
        _obs, times, volumes, values = item
        if end_s is not None and observed >= end_s:
            continue
        if start_s is not None and observed < start_s:
            pending_start[index] = (tf, times.copy(), volumes.copy(), values.copy())
        else:
            if pending_start:
                for p_index in sorted(pending_start):
                    p_tf, p_times, p_volumes, p_values = pending_start[p_index]
                    yield int(start_s), p_tf, pack_wire(symbol, p_tf, p_times, p_volumes, p_values)
                pending_start = {}
            yield observed, tf, pack_wire(symbol, tf, times, volumes, values)
        nxt = next(it, None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], index, tf, nxt, it))
    if pending_start and start_s is not None:
        for p_index in sorted(pending_start):
            p_tf, p_times, p_volumes, p_values = pending_start[p_index]
            yield int(start_s), p_tf, pack_wire(symbol, p_tf, p_times, p_volumes, p_values)


class CaptureWriter:
    """Writes STAFF_PIPE_V1 files exactly as the EA does (tests / tooling)."""

    def __init__(self, root, symbol: str, timeframes, session: str = 'offline-capture-0001', *, wire_version=2, value_columns=None):
        self.wire_version = int(wire_version)
        self.value_columns = value_columns or len(wire_schema().PIPE_VALUE_COLUMNS)
        if self.value_columns != len(wire_schema().PIPE_VALUE_COLUMNS):raise CaptureError("CAPTURE_COLUMNS")
        if self.wire_version != 2:raise CaptureError("current schema requires Wire v2")
        self.previous_row = {}
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol
        self.session = session
        self.timeframes = list(timeframes)
        self.files = []
        self.counts = []
        self.last_bar = []
        self.flags = []
        for i in range(len(self.timeframes)):
            f = (self.root / f'pipe_{i:03d}.bin').open('wb')
            f.write(FILE_HEADER.pack(CAPTURE_V2_MAGIC if self.wire_version==2 else CAPTURE_MAGIC,
                                     self.wire_version, self.value_columns, MAX_BARS))
            self.files.append(f)
            self.counts.append(0)
            self.last_bar.append(None)
            self.flags.append(None)

    def write(self, index: int, observed: int, times, volumes, values, flags: int = 31):
        """Apply the EA rule: FULL on first/new bar/not-ready, else ROW (forming bar only)."""
        times = np.asarray(times, dtype='<i8')
        volumes = np.asarray(volumes, dtype='<i8')
        values = np.asarray(values, dtype='<f8')
        if values.shape != (len(times), self.value_columns):raise CaptureError('CAPTURE_COLUMNS_MISMATCH')
        f = self.files[index]
        full = (self.last_bar[index] is None or int(times[-1]) != self.last_bar[index] or self.flags[index] != 31)
        if self.wire_version==2:
            w=wire_schema()
            full=full or self.flags[index]!=flags
            row=times[-1:].tobytes()+volumes[-1:].tobytes()+values[-1:].tobytes()
            kind=w.WIRE_FULL if full else w.WIRE_HEARTBEAT if self.previous_row.get(index)==row else w.WIRE_ROW
            if kind==w.WIRE_HEARTBEAT:
                raw=w.pack_v2(self.symbol,self.timeframes[index],seq=self.counts[index]+1,kind=kind)
            else:
                data=(times,volumes,values) if full else (times[-1:],volumes[-1:],values[-1:])
                raw=w.pack_v2(self.symbol,self.timeframes[index],*data,seq=self.counts[index]+1,kind=kind)
            f.write(V2_RECORD_HEADER.pack(int(observed),int(flags),len(raw)));f.write(raw)
            self.last_bar[index]=int(times[-1]);self.flags[index]=int(flags)
            self.previous_row[index]=row;self.counts[index]+=1
            return
        if full:
            rows = len(times)
            f.write(RECORD_HEADER.pack(RECORD_FULL, int(observed), rows, int(flags)))
            f.write(times.tobytes() + volumes.tobytes() + values.tobytes())
            self.last_bar[index] = int(times[-1])
            self.flags[index] = int(flags)
        else:
            f.write(RECORD_HEADER.pack(RECORD_ROW, int(observed), 1, int(self.flags[index])))
            f.write(times[-1:].tobytes() + volumes[-1:].tobytes() + values[-1:].tobytes())
        self.counts[index] += 1

    def close(self):
        for f in self.files:
            f.close()
        lines = ['MOSES_NATIVE_BUILD_V1', 'session\t' + self.session, 'symbol\t' + self.symbol,
                 'format_version\t1', 'value_columns\t42', 'sampling_policy\tSECOND_SNAPSHOT_V1',
                 'pipe_capture\t' + (CAPTURE_V2_POLICY if self.wire_version==2 else CAPTURE_POLICY)]
        lines += [f'pipe_feed\t{i}\t{tf}\tpipe_{i:03d}.bin\t{self.counts[i]}' for i, tf in enumerate(self.timeframes)]
        (self.root / 'manifest.tsv').write_text('\r\n'.join(lines) + '\r\n', encoding='ascii')
        (self.root / 'complete.txt').write_text('MOSES_NATIVE_BUILD_V1\r\n' + self.session + '\r\n', encoding='ascii')
