"""Delta materialization: canonical STAFF inputs, bar decisions and corruption checks.

The optional benchmark uses generated packets only; it is not a correctness gate.
"""
from pathlib import Path
import io
import json
import socket
import statistics
import struct
import sys
import time
import tracemalloc

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_backtest.delta import DeltaCodec, update_hash, write_delta, verify_delta
from event_engine import EventEngine, IngressSequencer
from event_engine.model import Kind, Signal, Subscriptions
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff

COLS = len(wire.PIPE_VALUE_COLUMNS)
BASE = 1756684800
FULL, ROW, BEAT = wire.WIRE_FULL, wire.WIRE_ROW, wire.WIRE_HEARTBEAT


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('synthetic Delta tests must not access the network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)


def prices(rows, forming=110.):
    values = np.full((rows, COLS), 100., dtype='<f8')
    values[:, :4] = [100., 121., 95., 99.]
    values[-1, 3] = forming
    return values


def packet(seq, frames, stamp=None):
    children = []
    for tf, kind, times, values in frames:
        if kind == BEAT:
            children.append(wire.pack_v2('XAUUSD+', tf, seq=seq, kind=kind))
        else:
            children.append(wire.pack_v2('XAUUSD+', tf, times,
                np.arange(len(times), dtype='<i8') + 10, values, seq=seq, kind=kind))
    return wire.pack_bundle('XAUUSD+', children, seq=seq,
                            sent_at_ms=stamp or (BASE * 1000 + seq * 1000))


def observations():
    minute = np.arange(BASE - 19 * 60, BASE + 1, 60, dtype='<i8')
    five = np.arange(BASE - 19 * 300, BASE + 1, 300, dtype='<i8')
    a, b = prices(20), prices(20)
    yield packet(1, [('1m', FULL, minute, a), ('5m', FULL, five, b)])
    a[-1, 1:4] = b[-1, 1:4] = [131., 95., 120.]
    yield packet(2, [('1m', ROW, minute[-1:], a[-1:]), ('5m', ROW, five[-1:], b[-1:])])
    minute = np.append(minute, BASE + 60)
    a = np.vstack((a, prices(1, forming=99.)))
    yield packet(3, [('1m', FULL, minute, a), ('5m', BEAT, [], None)], (BASE + 60) * 1000)
    # A corrected closed candle is received through FULL, not a future forming close.
    a[-2, 3] = 95.
    yield packet(4, [('1m', FULL, minute, a), ('5m', ROW, five[-1:], b[-1:])], (BASE + 61) * 1000)
    a[-1, 1:4] = [141., 95., 130.]
    yield packet(5, [('1m', ROW, minute[-1:], a[-1:]), ('5m', BEAT, [], None)], (BASE + 62) * 1000)
    minute = np.append(minute, BASE + 120)
    five = np.append(five, BASE + 300)
    a, b = np.vstack((a, prices(1, forming=99.))), np.vstack((b, prices(1, forming=99.)))
    yield packet(6, [('1m', FULL, minute, a), ('5m', FULL, five, b)], (BASE + 300) * 1000)


class ClosedCandleProbe:
    name = 'DELTA_CLOSED_CANDLE'
    def __init__(self):
        self.judgments = []
    def subscriptions(self):
        return Subscriptions(kinds=(Kind.MARKET_BUNDLE,), timeframes=('1m', '5m'),
                             facts=('ATR14_GENERAL',))
    def on_event(self, event, board, state, emit):
        for tf in ('1m', '5m'):
            snapshot = board.snapshot('XAUUSD+', tf)
            open_, high, low, close = snapshot.values[-2, :4]
            atr = float(board.fact('ATR14_GENERAL', 'XAUUSD+', tf)[-2])
            bar = int(snapshot.time[-2])
            bullish = bool(close > open_ and atr > 0)
            self.judgments.append((event.source_time, tf, bar, float(close), atr, bullish))
            if bullish and state.get(tf) != bar:
                emit(Signal('XAUUSD+', tf + ':' + str(bar),
                            {'tf': tf, 'closed_close': float(close), 'atr': atr}))
            state[tf] = bar


def canonical_run(raws, path, *, live):
    consumer = ClosedCandleProbe()
    engine = EventEngine(IngressSequencer(), (consumer,))
    cache = load_staff().StaffPipeCache('', health_session='DELTA151',
        monotonic=lambda: 0., gap_journal=path / 'gaps.jsonl')
    adapter = StaffIngressAdapter(cache, engine.ingress)
    for raw in raws:
        if live:
            adapter.receive_one(io.BytesIO(raw).read)
        else:
            adapter.publish(raw)
        engine.run()
    assert not engine.error_log
    return consumer.judgments, [(event.source_time, dict(event.payload['content']))
                               for event in engine.signals]


@pytest.mark.parametrize('verify_crc', [False, True])
def test_original_live_and_restored_replay_have_same_closed_values_and_signals(tmp_path, verify_crc):
    original = list(observations())
    encoder, decoder = DeltaCodec(), DeltaCodec(verify_crc=verify_crc)
    restored = [decoder.decode(*encoder.encode(raw)) for raw in original]
    live = canonical_run(original, tmp_path / 'live', live=True)
    replay = canonical_run(restored, tmp_path / 'replay', live=False)
    assert live == replay
    assert [(stamp, signal['tf'], signal['closed_close']) for stamp, signal in replay[1]] == [
        ((BASE + 60) * 1000, '1m', 120.),
        ((BASE + 300) * 1000, '1m', 130.),
        ((BASE + 300) * 1000, '5m', 120.),
    ]
    assert all(not judgment[-1] for judgment in replay[0][:4])
    assert any(tf == '1m' and close == 95. and not bullish
               for _, tf, _, close, _, bullish in replay[0])


def test_every_feed_and_supplied_column_survives_including_unrequested_values():
    original = list(observations())
    encoder, decoder = DeltaCodec(), DeltaCodec()
    retained = []
    for raw in original:
        restored = decoder.decode(*encoder.encode(raw))
        retained.append(restored)
        expected, actual = wire.decode_v2(raw), wire.decode_v2(restored)
        assert type(restored) is bytes
        assert len(actual.children) == len(expected.children)
        for before, after in zip(expected.children, actual.children):
            assert (before.symbol, before.timeframe, before.kind, before.seq) == (
                after.symbol, after.timeframe, after.kind, after.seq)
            np.testing.assert_array_equal(before.times, after.times)
            np.testing.assert_array_equal(before.volumes, after.volumes)
            np.testing.assert_array_equal(before.values, after.values)
    # Later in-place ROW state updates may never alter a previously returned publication.
    for raw, restored in zip(original, retained):
        assert wire.decode_v2(restored).sent_at_ms == wire.decode_v2(raw).sent_at_ms
        assert restored == raw  # Storage-integrity diagnostic, alongside semantic checks above.


def test_hash_verification_still_checks_reconstructed_original_input(tmp_path):
    import hashlib
    rows = [(wire.decode_v2(raw).sent_at_ms, raw) for raw in observations()]
    hasher = hashlib.sha256()
    for stamp, raw in rows:
        update_hash(hasher, stamp, raw)
    expected = {'bundles': len(rows), 'bundle_sha256': hasher.hexdigest()}
    path = tmp_path / 'generated.delta.gz'
    assert write_delta(rows, path) == expected
    assert verify_delta(path, expected) == expected
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_delta(path, {**expected, 'bundle_sha256': '0' * 64})


def row_record():
    seed, row = list(observations())[:2]
    encoder, decoder = DeltaCodec(), DeltaCodec()
    decoder.decode(*encoder.encode(seed))
    structure, bits = encoder.encode(row)
    child = 2 + int.from_bytes(structure[:2], 'little') + 4
    size = struct.unpack_from('<H', structure, child)[0]
    mapping = child + 6 + size + 4
    changed = mapping + 2 + 4  # Existing row: no new time follows the row map.
    return decoder, structure, bits, child, mapping, changed


@pytest.mark.parametrize('corruption', ['truncated', 'trailing', 'short_bits', 'extra_bits',
    'invalid_mapping', 'invalid_cell', 'child_length', 'outer_length'])
def test_malformed_record_is_rejected(corruption):
    decoder, structure, bits, child, mapping, changed = row_record()
    broken = bytearray(structure)
    if corruption == 'truncated':
        broken = broken[:-1]
    elif corruption == 'trailing':
        broken += b'!'
    elif corruption == 'short_bits':
        bits = bits[:-1]
    elif corruption == 'extra_bits':
        bits = np.append(bits, np.uint64(1))
    elif corruption == 'invalid_mapping':
        struct.pack_into('<h', broken, mapping, 20)
    elif corruption == 'invalid_cell':
        struct.pack_into('<I', broken, changed, COLS + 1)
    elif corruption == 'child_length':
        length = struct.unpack_from('<I', broken, child + 2)[0]
        struct.pack_into('<I', broken, child + 2, length + 1)
    else:
        length = struct.unpack_from('<I', broken, 2 + 24)[0]
        struct.pack_into('<I', broken, 2 + 24, length + 1)
    if corruption == 'outer_length':
        # The storage codec preserves envelope headers; canonical STAFF checks them.
        restored = decoder.decode(bytes(broken), bits)
        with pytest.raises(wire.WireError, match='truncated|trailing'):
            wire.decode_v2(restored)
    else:
        with pytest.raises((ValueError, IndexError)):
            decoder.decode(bytes(broken), bits)


@pytest.mark.parametrize('verify_crc', [False, True])
@pytest.mark.parametrize('corruption', ['invalid_mapping', 'invalid_cell', 'short_bits'])
def test_full_record_rejects_invalid_axes_before_staff(verify_crc, corruption):
    original = list(observations())
    encoder, decoder = DeltaCodec(), DeltaCodec(verify_crc=verify_crc)
    for raw in original[:2]:
        decoder.decode(*encoder.encode(raw))
    structure, bits = encoder.encode(original[2])
    broken = bytearray(structure)
    child = 2 + int.from_bytes(structure[:2], 'little') + 4
    size = struct.unpack_from('<H', structure, child)[0]
    mapping_offset = child + 6 + size + 4
    rows = struct.unpack_from('<I', structure, child + 6 + 24)[0]
    mapping = np.frombuffer(structure, '<i2', rows, mapping_offset)
    changed = mapping_offset + rows * 2 + int((mapping < 0).sum()) * 8 + 4
    if corruption == 'invalid_mapping':
        struct.pack_into('<h', broken, mapping_offset, 20)
    elif corruption == 'invalid_cell':
        struct.pack_into('<I', broken, changed, rows * (COLS + 1))
    else:
        bits = bits[:-1]
    with pytest.raises((ValueError, IndexError)):
        decoder.decode(bytes(broken), bits)


@pytest.mark.parametrize('corruption', ['bits', 'child_crc', 'outer_crc'])
def test_crc_checks_may_only_be_deferred_to_canonical_staff(tmp_path, corruption):
    seed, row = list(observations())[:2]
    encoder = DeltaCodec()
    seed_record = encoder.encode(seed)
    structure, bits = encoder.encode(row)
    broken = bytearray(structure)
    if corruption == 'bits':
        bits = bits.copy(); bits[-1] ^= np.uint64(1)
    elif corruption == 'child_crc':
        child = 2 + int.from_bytes(structure[:2], 'little') + 4
        size = struct.unpack_from('<H', structure, child)[0]
        broken[child + 6 + size] ^= 1
    else:
        broken[2 + int.from_bytes(structure[:2], 'little')] ^= 1
    checked = DeltaCodec()
    checked.decode(*seed_record)
    with pytest.raises(ValueError, match='CRC'):
        checked.decode(bytes(broken), bits)
    delegated = DeltaCodec(verify_crc=False)
    delegated.decode(*seed_record)
    restored = delegated.decode(bytes(broken), bits)
    cache = load_staff().StaffPipeCache('', monotonic=lambda: 0.,
        gap_journal=tmp_path / 'gaps.jsonl')
    cache.receive_publication(raw=seed, require_observation=True)
    with pytest.raises(wire.WireError, match='CRC'):
        cache.receive_publication(raw=restored, require_observation=True)


def benchmark():
    """Report decode cost and allocation peaks for fixed generated workloads."""
    codecs = {'current': DeltaCodec}
    if '--compare' in sys.argv:
        import importlib.util
        path = Path(sys.argv[sys.argv.index('--compare') + 1])
        spec = importlib.util.spec_from_file_location('delta_decode_measurement_before', path)
        before = importlib.util.module_from_spec(spec); spec.loader.exec_module(before)
        codecs['before'] = before.DeltaCodec
    frames = ('1m', '2m', '3m', '5m', '10m', '15m', '1h')
    times = np.arange(BASE - 649 * 60, BASE + 1, 60, dtype='<i8')
    result = {'source': 'generated packets only', 'feeds': len(frames), 'rows': 650,
              'columns': COLS, 'workloads': {}}
    cases = ('full', 'row', 'mixed', 'rolling', 'bar_mixed')
    if '--cases' in sys.argv:
        cases = sys.argv[sys.argv.index('--cases') + 1].split(',')
        if not set(cases) <= {'full', 'row', 'mixed', 'rolling', 'bar_mixed'}:
            raise ValueError('unknown generated workload')
    minutes = dict(zip(frames, (1, 2, 3, 5, 10, 15, 60)))
    for case in cases:
        encoder = DeltaCodec()
        seed = encoder.encode(packet(1, [(tf, FULL,
            BASE + (times - BASE) * (minutes[tf] if case == 'bar_mixed' else 1), prices(650)) for tf in frames]))
        records = []
        for seq in range(2, 34):
            values = prices(650, forming=110. + seq / 10)
            children = []
            for tf in frames:
                if case == 'bar_mixed':
                    step = minutes[tf]
                    current = (seq - 1) // step * step * 60
                    tf_times = BASE + (times - BASE) * step + current
                    kind = FULL if (seq - 1) % step == 0 else ROW
                else:
                    tf_times = times + ((seq - 1) * 60 if case == 'rolling' else 0)
                    kind = FULL if case in ('full', 'rolling') or (case == 'mixed' and seq % 8 == 0) else ROW
                children.append((tf, kind, tf_times if kind == FULL else tf_times[-1:],
                                 values if kind == FULL else values[-1:]))
            records.append(encoder.encode(packet(seq, children)))
        for checked in (False, True):
            samples = {name: [] for name in codecs}
            cpu_samples = {name: [] for name in codecs}
            # Alternate the same input between implementations to reduce time/load drift.
            for trial in range(7):
                order = list(codecs.items())
                if trial % 2: order.reverse()
                for name, factory in order:
                    wall_ns = cpu_ns = 0
                    for _ in range(8):
                        # A moving window's encoded row map is relative to its seed,
                        # so every measured sequence starts at that same input state.
                        codec = factory(verify_crc=checked); codec.decode(*seed)
                        began = time.perf_counter_ns(); cpu_began = time.process_time_ns()
                        for record in records:
                            codec.decode(*record)
                        wall_ns += time.perf_counter_ns() - began
                        cpu_ns += time.process_time_ns() - cpu_began
                    samples[name].append(wall_ns / (8 * len(records)) / 1e6)
                    cpu_samples[name].append(cpu_ns / (8 * len(records)) / 1e6)
            measured = {}
            for name, factory in codecs.items():
                codec = factory(verify_crc=checked); codec.decode(*seed)
                tracemalloc.start()
                for record in records:
                    codec.decode(*record)
                _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
                measured[name] = {'median_wall_ms_per_bundle': statistics.median(samples[name]),
                    'samples_wall_ms_per_bundle': samples[name],
                    'median_cpu_ms_per_bundle': statistics.median(cpu_samples[name]),
                    'samples_cpu_ms_per_bundle': cpu_samples[name], 'peak_traced_bytes': peak,
                    'records_per_sample': 8 * len(records), 'samples': len(samples[name])}
            result['workloads'][case + ('_crc' if checked else '_staff_crc')] = measured
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__' and '--benchmark' in sys.argv:
    benchmark()
