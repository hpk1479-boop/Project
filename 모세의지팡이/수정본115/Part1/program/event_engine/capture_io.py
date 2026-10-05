"""Explicit I/O adapter for existing MSP3; core engine does not import this.

No Part2 dependency. Older MSP3 observations are seconds; new manifests may
declare milliseconds. Equal-time feeds are bundled in manifest feed order.
"""
from pathlib import Path
import heapq
import struct
import gzip
import staff_schema as wire

FILE_HEADER = struct.Struct('<IIII')
RECORD_HEADER = struct.Struct('<qiI')


def records(path, expected, symbol, timeframe, *, milliseconds=False):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rb') as f:
        raw = f.read(FILE_HEADER.size)
        if len(raw) != FILE_HEADER.size:
            raise ValueError('truncated MSP3 header')
        magic, version, cols, bars = FILE_HEADER.unpack(raw)
        if magic != 0x4D535033 or version != 2 or cols != len(wire.PIPE_VALUE_COLUMNS) or not 3 <= bars <= 650:
            raise ValueError('invalid MSP3 header')
        count = 0; previous = None
        while True:
            raw = f.read(RECORD_HEADER.size)
            if not raw:
                break
            if len(raw) != RECORD_HEADER.size:
                raise ValueError('truncated MSP3 record')
            observed, flags, length = RECORD_HEADER.unpack(raw)
            if not wire.WIRE_HEADER.size + 4 <= length <= wire.WIRE_MAX_PAYLOAD + 256:
                raise ValueError('invalid MSP3 length')
            data = f.read(length)
            if len(data) != length:
                raise ValueError('truncated MSP3 payload')
            packet = wire.decode_v2(data)
            if (packet.symbol, packet.timeframe) != (symbol, timeframe) or len(wire.WIRE_SCHEMAS[packet.schema_id]) != cols:
                raise ValueError('MSP3 identity/schema mismatch')
            if previous is not None and observed <= previous:
                raise ValueError('MSP3 observation order')
            previous = observed; count += 1
            yield observed if milliseconds else observed * 1000, data
        if count != expected:
            raise ValueError('MSP3 record count mismatch')


def capture_bundles(root):
    root = Path(root).resolve()
    if not (root/'complete.txt').is_file():
        raise ValueError('incomplete capture')
    metadata = {}; feeds = []
    for line in (root/'manifest.tsv').read_text('ascii').splitlines()[1:]:
        parts = line.split('\t')
        if parts[0] == 'pipe_feed':
            if len(parts) != 5:
                raise ValueError('invalid feed manifest')
            path = (root/parts[3]).resolve()
            if path.parent != root:
                raise ValueError('capture path escape')
            feeds.append((int(parts[1]), parts[2], path, int(parts[4])))
        elif len(parts) == 2:
            metadata[parts[0]] = parts[1]
    if metadata.get('pipe_capture') != 'STAFF_PIPE_V2' or not feeds:
        raise ValueError('MSP3 capture required')
    heap = []
    for index, tf, path, count in sorted(feeds):
        iterator = iter(records(path, count, metadata['symbol'], tf,
                        milliseconds=metadata.get('pipe_observation_unit') == 'milliseconds'))
        item = next(iterator, None)
        if item:
            heapq.heappush(heap, (item[0], index, item[1], iterator))
    seq = 0
    while heap:
        timestamp = heap[0][0]; children = []
        while heap and heap[0][0] == timestamp:
            observed, index, data, iterator = heapq.heappop(heap)
            children.append(data)
            following = next(iterator, None)
            if following:
                heapq.heappush(heap, (following[0], index, following[1], iterator))
        seq += 1
        yield timestamp, wire.pack_bundle(metadata['symbol'], children, seq=seq, sent_at_ms=timestamp)
