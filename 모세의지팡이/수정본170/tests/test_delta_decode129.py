"""Capture decoding (수정본129): one-row children take a short path and replace the last row in place.

The decoded bytes must be the recorded bytes, and the codec's per-feed state must equal the arrays
the original rebuild rule makes, for the in-place case and for every case that falls back to it.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_backtest.delta import DeltaCodec

COLS = len(wire.PIPE_VALUE_COLUMNS)


def bars(times, base=0.):
    values = np.arange(len(times) * COLS, dtype='<f8').reshape(len(times), COLS) + base
    if len(times):
        # NaN payloads, signed zero and infinities must survive bit for bit.
        values.view('<u8')[0, 4:8] = [0x8000000000000000, 0x7ff8000000000001, 0x7ff0000000000000, 0xfff0000000000000]
    return values


def bundle(seq, frames):
    children = []
    for tf, kind, times, base in frames:
        if kind == wire.WIRE_HEARTBEAT:
            children.append(wire.pack_v2('XAUUSD+', tf, seq=seq, kind=kind))
            continue
        times = np.asarray(times, dtype='<i8')
        children.append(wire.pack_v2('XAUUSD+', tf, times, np.arange(len(times), dtype='<i8') + seq,
                                     bars(times, base), seq=seq, kind=kind))
    return wire.pack_bundle('XAUUSD+', children, seq=seq, sent_at_ms=seq * 1000)


FULL, ROW, BEAT = wire.WIRE_FULL, wire.WIRE_ROW, wire.WIRE_HEARTBEAT
SEQUENCE = [
    [('1m', FULL, range(1, 7), 0.), ('5m', FULL, range(1, 7), 9.)],
    [('1m', ROW, [6], 1.), ('5m', ROW, [6], 2.)],                       # replace the last row (in place)
    [('1m', ROW, [7], 3.), ('5m', BEAT, [], 0.)],                       # a new bar (rebuild)
    [('1m', ROW, [7], 4.), ('15m', ROW, [5], 0.)],                      # in place; a feed first seen as ROW
    [('1m', FULL, [1, 2, 3, 3, 4, 5], 5.)],                             # a repeated time inside
    [('1m', ROW, [3], 6.)],                                             # its time is not only the last row (rebuild)
    [('1m', ROW, [5], 7.)],                                             # unique last row (in place)
    [('1m', FULL, range(1000, 1650), 8.)],                              # 650 rows
    [('1m', ROW, [1650], 9.)],                                          # a new bar drops the oldest (rebuild)
    [('1m', ROW, [1650], 10.)],                                         # in place at 650 rows
]


def reference(feeds, raw):
    """The original rule, from the bundle itself: FULL replaces, ROW drops rows of its time and appends."""
    for frame in wire.decode_v2(raw).children:
        if frame.kind == BEAT:
            continue
        key = frame.symbol.encode() + frame.timeframe.encode()
        times = np.asarray(frame.times, dtype='<i8')
        values = np.column_stack([np.asarray(frame.volumes, dtype='<i8').view('<u8'),
                                  np.asarray(frame.values, dtype='<f8').view('<u8')])
        if frame.kind == FULL:
            feeds[key] = (times.copy(), values.copy())
        else:
            old_t, old_v = feeds.get(key, (times[:0], values[:0]))
            keep = old_t != times[0]
            feeds[key] = (np.concatenate((old_t[keep], times))[-650:], np.concatenate((old_v[keep], values))[-650:])


def same_state(codec, feeds):
    assert set(codec.feeds) == set(feeds)
    for key, (times, values) in feeds.items():
        got_t, got_v = codec.feeds[key]
        assert got_t.dtype == times.dtype and np.array_equal(got_t, times), key
        assert got_v.dtype == values.dtype and got_v.tobytes() == values.tobytes(), key


@pytest.mark.parametrize('verify_crc', [False, True])
def test_decoded_bytes_and_state_follow_the_original_rule(verify_crc):
    encoder, decoder, feeds = DeltaCodec(), DeltaCodec(verify_crc=verify_crc), {}
    for seq, frames in enumerate(SEQUENCE, 1):
        raw = bundle(seq, frames)
        structure, bits = encoder.encode(raw)
        assert decoder.decode(structure, bits) == raw
        reference(feeds, raw)
        same_state(decoder, feeds)
        same_state(encoder, feeds)


def test_an_earlier_decoded_bundle_is_not_touched_by_later_rows():
    encoder, decoder = DeltaCodec(), DeltaCodec()
    raws = [bundle(seq, frames) for seq, frames in enumerate(SEQUENCE[:4], 1)]
    decoded = [decoder.decode(*encoder.encode(raw)) for raw in raws]
    assert decoded == raws and all(type(item) is bytes for item in decoded)


@pytest.mark.parametrize('where', ['row_bits', 'row_crc'])
def test_a_corrupt_one_row_child_is_still_rejected_when_checked(where):
    encoder = DeltaCodec()
    encoder.encode(bundle(1, SEQUENCE[0]))
    structure, bits = encoder.encode(bundle(2, SEQUENCE[1]))
    decoder = DeltaCodec(verify_crc=True)
    decoder.decode(*DeltaCodec().encode(bundle(1, SEQUENCE[0])))
    if where == 'row_bits':
        bits = bits.copy(); bits[0] ^= 1
    else:
        # Layout: prefix size, prefix, trailer, then per child (head size, length, head, crc, ...).
        broken = bytearray(structure)
        child = 2 + int.from_bytes(broken[:2], 'little') + 4
        head_size = int.from_bytes(broken[child:child + 2], 'little')
        broken[child + 6 + head_size] ^= 1                      # one bit of the first child's stored CRC
        structure = bytes(broken)
    with pytest.raises(ValueError, match='delta CRC/length'):
        decoder.decode(structure, bits)
