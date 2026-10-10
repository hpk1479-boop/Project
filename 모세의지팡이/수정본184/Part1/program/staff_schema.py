"""Finalized 50-column Wire v2 registry and its client frame."""
from __future__ import annotations
import numpy as np

SCHEMA_ID = 'staff-wire-v2-50'
PIPE_VALUE_COLUMNS = ['open', 'high', 'low', 'close', 'ema_20', 'ema_50', 'ema_200', 'hma_6', 'hma_17', 'hma_50', 'hma_168', 'open_band_4_mid', 'price_hma_6', 'price_band_lower', 'price_band_upper', 'RSI_val', 'RSI_db', 'RSI_ub', 'RSI_basis', 'STO_val', 'STO_db', 'STO_ub', 'STO_basis', 'DI_val', 'DI_db', 'DI_ub', 'DI_basis', 'price_regime_basis', 'price_regime_upper', 'price_regime_lower', 'RSI_regime_upper', 'RSI_regime_lower', 'STO_regime_upper', 'STO_regime_lower', 'DI_regime_upper', 'DI_regime_lower', 'wonbi_upper', 'wonbi_lower', 'price_lower_out', 'price_upper_out', 'price_regime_slope', 'RSI_lower_out', 'RSI_upper_out', 'RSI_regime_slope', 'STO_lower_out', 'STO_upper_out', 'STO_regime_slope', 'DI_lower_out', 'DI_upper_out', 'DI_regime_slope']

COLUMN_ALIASES = {'wonbi_mid': 'open_band_4_mid'}

def mt5_values(values):
    """This release accepts only the finalized schema, with no legacy conversion."""
    values = np.asarray(values, dtype='<f8')
    if values.ndim != 2 or values.shape[1] != len(PIPE_VALUE_COLUMNS):
        raise ValueError('STAFF schema mismatch: replay old captures with their original revision')
    return values

BASE_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

def legacy_frame(symbol, timeframe, snapshot, max_bars=650) -> pd.DataFrame:
    import pandas as pd  # Client-only normalization; server imports only the wire schema.
    times, volumes, values = snapshot.time, snapshot.volume, mt5_values(snapshot.values)

    data = {"time": pd.to_datetime(times, unit="s"), "volume": volumes}
    for idx, col in enumerate(PIPE_VALUE_COLUMNS):
        data[col] = values[:, idx]
    df = pd.DataFrame(data)
    ordered = ["time", "open", "high", "low", "close", "volume"] + PIPE_VALUE_COLUMNS[4:]
    df = df[ordered]
    df = df.dropna(subset=["time"]).drop_duplicates(subset=["time"], keep="last")
    df = df.sort_values("time").tail(max_bars).reset_index(drop=True)
    if len(df) < 3:
        raise RuntimeError(f"[{symbol} {timeframe}] snapshot too short: {len(df)}")
    if not np.isfinite(df[['open','high','low','close']].to_numpy(dtype=float)).all():
        raise RuntimeError(f'[{symbol} {timeframe}] invalid OHLC snapshot')

    # 2.0: 선택 지표가 비어 있다는 이유로 OHLC snapshot 전체를 버리지 않습니다.
    # FVG/SWEEP/TREND처럼 OHLC만 필요한 엔진은 어떤 optional indicator 상태와도
    # 독립적으로 같은 timeframe 데이터를 받을 수 있어야 합니다.
    # RSI/STO/DI/PRICE/HMA 등은 실제 요청 시 validate_mt5_snapshot()에서 검증합니다.

    df['wonbi_mid'] = df[COLUMN_ALIASES['wonbi_mid']]
    df.attrs['source_epoch'] = snapshot.source_epoch
    df.attrs['indicator_validity'] = dict(snapshot.indicator_validity)
    return df

# EA Wire v2. The client SNAPSHOT API above deliberately retains its v1 identity.
import struct
import zlib
from typing import NamedTuple

WIRE_MAGIC = 0x534D4F53
WIRE_SCHEMA_BYTES = ('\n'.join(PIPE_VALUE_COLUMNS)).encode('utf-8')
WIRE_SCHEMA_ID = zlib.crc32(WIRE_SCHEMA_BYTES) & 0xffffffff
WIRE_SCHEMAS = {WIRE_SCHEMA_ID: tuple(PIPE_VALUE_COLUMNS)}
WIRE_HEADER = struct.Struct('<IIqIIIIII')
WIRE_FULL, WIRE_ROW, WIRE_HEARTBEAT, WIRE_HELLO, WIRE_BUNDLE, WIRE_ACK = range(1, 7)
# A new bar without resending the window: the closed bar's final row, then the new bar's row.
WIRE_APPEND = 7
FEED_KINDS = (WIRE_FULL, WIRE_ROW, WIRE_HEARTBEAT, WIRE_APPEND)
FEED_ROWS = {WIRE_ROW: 1, WIRE_HEARTBEAT: 0, WIRE_APPEND: 2}
# A FULL carries at most this many bars; an APPEND keeps the window within it.
WIRE_MAX_BARS = 650
WIRE_MAX_PAYLOAD = 32 * 1024 * 1024


class WireError(RuntimeError):
    pass


class UnknownWireSchema(WireError):
    def __init__(self, schema_id, symbol='', timeframe=''):
        self.schema_id, self.symbol, self.timeframe = schema_id, symbol, timeframe
        super().__init__(f'unknown STAFF schema_id: {schema_id:08x}')


class WireFrame(NamedTuple):
    seq: int
    schema_id: int
    kind: int
    symbol: str
    timeframe: str
    times: object = None
    volumes: object = None
    values: object = None
    children: tuple = ()
    sent_at_ms: int = 0
    build_hash: str = ''


def _wire_packet(seq, kind, sym_len, tf_len, bars, payload, schema_id=WIRE_SCHEMA_ID):
    return (WIRE_HEADER.pack(WIRE_MAGIC, 2, seq, sym_len, tf_len, bars, len(WIRE_SCHEMAS.get(schema_id, PIPE_VALUE_COLUMNS)), schema_id, kind)
            + payload + struct.pack('<I', zlib.crc32(payload) & 0xffffffff))


def pack_v2(symbol, timeframe, times=None, volumes=None, values=None, *, seq, kind=WIRE_FULL,
            schema_id=None):
    if schema_id is None:
        schema_id = WIRE_SCHEMA_ID
    cols = len(WIRE_SCHEMAS.get(schema_id, PIPE_VALUE_COLUMNS))
    sym, tf = symbol.encode('utf-8'), timeframe.encode('utf-8')
    if not (1 <= len(sym) <= 128 and 1 <= len(tf) <= 16):
        raise WireError('invalid feed identity')
    if kind == WIRE_HEARTBEAT:
        rows, body = 0, b''
    elif kind in (WIRE_FULL, WIRE_ROW, WIRE_APPEND):
        t = np.asarray(times, dtype='<i8')
        v = np.asarray(volumes, dtype='<i8')
        x = np.asarray(values, dtype='<f8')
        rows = len(t)
        if t.shape != (rows,) or v.shape != (rows,) or x.shape != (rows, cols):
            raise WireError('v2 payload shape mismatch')
        if not (3 <= rows <= WIRE_MAX_BARS if kind == WIRE_FULL else rows == FEED_ROWS[kind]):
            raise WireError('v2 bar count invalid')
        body = t.tobytes() + v.tobytes() + x.tobytes()
    else:
        raise WireError('not a feed frame')
    return _wire_packet(int(seq), kind, len(sym), len(tf), rows, sym + tf + body, schema_id)


def pack_hello(build_hash, *, ack=False, schema_id=WIRE_SCHEMA_ID):
    if len(build_hash) != 64 or any(c not in '0123456789abcdef' for c in build_hash):
        raise WireError('EA build hash must be lowercase SHA256')
    return _wire_packet(0, WIRE_ACK if ack else WIRE_HELLO, 64, 0, 0,
                        build_hash.encode('ascii'), schema_id)


def pack_bundle(symbol, frames, *, sent_at_ms=0, seq=0):
    frames = tuple(frames)
    if not 1 <= len(frames) <= 64:
        raise WireError('bundle count invalid')
    sym = symbol.encode('utf-8')
    parts = [struct.pack('<qI', sent_at_ms, len(frames))]
    for raw in frames:
        frame = decode_v2(raw)
        if frame.kind not in FEED_KINDS or frame.symbol != symbol:
            raise WireError('bundle feed mismatch')
        parts.extend((struct.pack('<I', len(raw)), raw))
    body=b''.join(parts)
    return _wire_packet(seq, WIRE_BUNDLE, len(sym), 0, len(body), sym + body)


def _v2_size(header):
    if len(header)!=WIRE_HEADER.size:raise WireError('truncated v2 header')
    fields=WIRE_HEADER.unpack(header)
    magic, version, seq, ns, nt, rows, cols, schema, kind = fields
    if magic != WIRE_MAGIC or version != 2 or not 1 <= cols <= 256 or seq < 0:
        raise WireError('invalid v2 header')
    if kind in (WIRE_HELLO, WIRE_ACK):
        if (seq, ns, nt, rows) != (0, 64, 0, 0):
            raise WireError('invalid HELLO header')
        size = 64
    elif kind == WIRE_BUNDLE:
        if not (1 <= ns <= 128 and nt == 0 and 12 <= rows <= WIRE_MAX_PAYLOAD):
            raise WireError('invalid bundle header')
        size = ns + rows
    elif kind in FEED_KINDS:
        allowed = 3 <= rows <= WIRE_MAX_BARS if kind == WIRE_FULL else rows == FEED_ROWS[kind]
        if not (1 <= ns <= 128 and 1 <= nt <= 16 and allowed and seq >= 1):
            raise WireError('invalid feed header')
        size = ns + nt + rows * (16 + cols * 8)
    else:
        raise WireError('unknown v2 kind')
    return fields,size


def immutable_byte_view(raw):
    view=memoryview(raw)
    owner=view.obj
    while isinstance(owner,memoryview):owner=owner.obj
    # A readonly flag over a bytearray/ndarray does not confer ownership.
    if not isinstance(owner,bytes):view=memoryview(bytes(view))
    return view.cast('B')


def _decode_payload(fields,payload,crc):
    magic,version,seq,ns,nt,rows,cols,schema,kind=fields
    if (zlib.crc32(payload) & 0xffffffff) != crc:
        raise WireError('STAFF payload CRC mismatch')
    symbol = bytes(payload[:ns]).decode('utf-8', errors='strict')
    tf = bytes(payload[ns:ns+nt]).decode('utf-8', errors='strict').lower()
    if schema not in WIRE_SCHEMAS:
        raise UnknownWireSchema(schema, '' if kind in (WIRE_HELLO, WIRE_ACK) else symbol, tf)
    if cols != len(WIRE_SCHEMAS[schema]):
        raise WireError('schema column count mismatch')
    if kind in (WIRE_HELLO, WIRE_ACK):
        if any(c not in '0123456789abcdef' for c in symbol):
            raise WireError('invalid HELLO build hash')
        return WireFrame(seq, schema, kind, '', '', build_hash=symbol)
    if kind == WIRE_BUNDLE:
        sent, count = struct.unpack_from('<qI', payload, ns)
        if not 1 <= count <= 64:
            raise WireError('invalid bundle count')
        offset, children = ns + 12, []
        for _ in range(count):
            if offset + 4 > len(payload):
                raise WireError('truncated bundle child size')
            length = struct.unpack_from('<I', payload, offset)[0]
            offset += 4
            if length < WIRE_HEADER.size + 4 or offset + length > len(payload):
                raise WireError('truncated bundle child')
            # Nested bundles are forbidden before recursive decoding.
            if WIRE_HEADER.unpack_from(payload, offset)[-1] not in FEED_KINDS:
                raise WireError('nested/non-feed bundle child')
            child = _decode_view(payload[offset:offset+length])
            if child.symbol != symbol:
                raise WireError('bundle symbol mismatch')
            children.append(child)
            offset += length
        if offset != len(payload):
            raise WireError('bundle trailing bytes')
        return WireFrame(seq, schema, kind, symbol, '', children=tuple(children), sent_at_ms=sent)
    offset = ns + nt
    t = np.frombuffer(payload, '<i8', rows, offset)
    v = np.frombuffer(payload, '<i8', rows, offset + rows*8)
    x = np.frombuffer(payload, '<f8', rows*cols, offset + rows*16).reshape(rows, cols)
    return WireFrame(seq, schema, kind, symbol, tf, t, v, x)


def _decode_view(view):
    fields,size=_v2_size(view[:WIRE_HEADER.size])
    if len(view)!=WIRE_HEADER.size+size+4:
        raise WireError('truncated v2 frame' if len(view)<WIRE_HEADER.size+size+4 else 'v2 trailing bytes')
    return _decode_payload(fields,view[WIRE_HEADER.size:-4],struct.unpack_from('<I',view,len(view)-4)[0])


def read_v2(header, read_exact):
    fields,size=_v2_size(header)
    payload=read_exact(size);trailer=read_exact(4)
    if len(payload)!=size or len(trailer)!=4:raise WireError('truncated v2 frame')
    return _decode_payload(fields,immutable_byte_view(payload),struct.unpack('<I',trailer)[0])


def decode_v2(raw):
    return _decode_view(immutable_byte_view(raw))


def generated_mqh(build_hash):
    """CRC32 over UTF-8 newline-joined names, no trailing newline. No formulas."""
    return ('// Generated by staff_schema.py; do not edit.\n'
            '#ifndef STAFF_WIRE_SCHEMA_MQH\n#define STAFF_WIRE_SCHEMA_MQH\n'
            f'const int STAFF_WIRE_VALUE_COLUMNS = {len(PIPE_VALUE_COLUMNS)};\n'
            f'const uint STAFF_WIRE_SCHEMA_ID = 0x{WIRE_SCHEMA_ID:08X};\n'
            f'const string STAFF_EA_BUILD_HASH = "{build_hash}";\n'
            'const int STAFF_WIRE_FULL=1, STAFF_WIRE_ROW=2, STAFF_WIRE_HEARTBEAT=3;\n'
            'const int STAFF_WIRE_HELLO=4, STAFF_WIRE_BUNDLE=5, STAFF_WIRE_ACK=6;\n'
            'const int STAFF_WIRE_APPEND=7;\n'
            + ''.join(f'const int STAFF_COL_{name.upper()} = {i}; // {name}\n' for i,name in enumerate(PIPE_VALUE_COLUMNS))
            + '#endif\n')
