"""151: validate a ROW against checked immutable history, through LIVE and replay ingress."""
from copy import deepcopy
import io
from pathlib import Path
import socket
import struct
import sys
import threading
import zlib

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_engine.model import Input, Kind
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff
from event_pipe_host import PipeReceiver

SYMBOL = 'XAUUSD+'
KEY = (SYMBOL, '5m')
COLS = len(wire.PIPE_VALUE_COLUMNS)
EMPTY = np.finfo(np.float64).max
TIMES = np.arange(30, dtype='<i8') * 300 + 1756684800


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network is forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


class Collector:
    def __init__(self):
        self.pending = []

    def post(self, kind, *, source, source_seq, source_time, payload, engine_time=None):
        self.pending.append(Input(source, source_seq, source_time, kind, payload, 0))


def values(rows=30):
    result = np.full((rows, COLS), 10., dtype='<f8')
    result[:, 0] = np.arange(rows) + 100.
    result[:, 1], result[:, 2], result[:, 3] = result[:, 0] + 2, result[:, 0] - 2, result[:, 0] + 1
    return result


def clean(array):
    result = np.array(array, dtype='<f8')
    result[np.abs(result) > 1e300] = np.nan
    return result


def full(seq, data=None, times=TIMES, tf='5m'):
    return wire.pack_v2(SYMBOL, tf, times, np.ones(len(times), dtype='<i8'),
                        values(len(times)) if data is None else data, seq=seq)


def row(seq, data=None, time=None, tf='5m'):
    return wire.pack_v2(SYMBOL, tf, [TIMES[-1] if time is None else time], [7],
                        values()[-1:] if data is None else np.asarray(data).reshape(1, COLS),
                        seq=seq, kind=wire.WIRE_ROW)


class Harness:
    def __init__(self, mode, tmp_path, max_bars=650):
        self.staff = load_staff()
        self.mode, self.clock = mode, [0.]
        self.cache = self.staff.StaffPipeCache('', max_bars=max_bars, health_session='CURRENT',
            monotonic=lambda: self.clock[0], gap_journal=tmp_path / (mode + '_gaps.jsonl'),
            published_timeframes={'1m'} if mode == 'shadow' else None)
        self.collector = Collector()
        self.adapter = StaffIngressAdapter(self.cache, self.collector)
        self.receiver = PipeReceiver(self.staff, self.cache, self.adapter, (), threading.Event())

    def raw(self, raw, observation=1000):
        if self.mode == 'live':
            return self.receiver.receive(io.BytesIO(raw).read, lambda reply: None, source_time=observation)
        return self.adapter.publish(raw, source_time=observation)

    def send(self, frames, seq):
        return self.raw(wire.pack_bundle(SYMBOL, frames, seq=seq, sent_at_ms=seq * 1000), seq * 1000)

    def snapshot(self):
        return self.cache.snapshots_with_age(SYMBOL, ['5m'])['5m'][0]

    def direct(self, frames, seq):
        # Exercise STAFF's legacy drop/duplicate rules before FeedSnapshot's stricter time contract.
        raw = wire.pack_bundle(SYMBOL, frames, seq=seq, sent_at_ms=seq * 1000)
        return self.cache.receive_publication(raw=raw, source_time=seq * 1000, require_observation=True)


@pytest.fixture(params=['live', 'replay', 'shadow'])
def harness(request, tmp_path):
    return Harness(request.param, tmp_path)


def assert_window(snapshot, expected, seq):
    np.testing.assert_allclose(snapshot.values, expected[:, :4] if snapshot.values.shape[1] == 4 else expected,
                               equal_nan=True)
    assert snapshot.seq == seq
    for array in (snapshot.time, snapshot.volume, snapshot.values):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.setflags(write=True)


def test_full_correction_row_and_retained_snapshot_have_the_intended_values(harness):
    data = values()
    data[3, 12], data[17, 20] = EMPTY, np.inf
    data[-1, 12] = EMPTY
    harness.send([full(1, data)], 1)
    before = harness.snapshot()
    expected = clean(data)
    incoming = data[-1].copy()
    incoming[3], incoming[12] = 147., EMPTY
    harness.send([row(2, incoming)], 2)
    expected[-1] = clean(incoming)
    assert_window(harness.snapshot(), expected, 2)
    np.testing.assert_allclose(before.values, clean(data)[:, :4] if harness.mode == 'shadow' else clean(data), equal_nan=True)
    corrected = data.copy()
    corrected[4, :4] = [210., 213., 209., 212.]
    harness.send([full(3, corrected)], 3)
    assert_window(harness.snapshot(), clean(corrected), 3)
    if harness.mode != 'shadow':
        assert [item.source_time for item in harness.collector.pending if item.kind == Kind.MARKET_BUNDLE] == [1000, 2000, 3000]


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf, EMPTY])
def test_invalid_new_row_ohlc_is_rejected_and_the_next_valid_row_still_works(harness, bad):
    harness.send([full(1)], 1)
    before = harness.snapshot()
    incoming = values()[-1].copy()
    incoming[0] = bad
    with pytest.raises(RuntimeError, match='invalid OHLC'):
        harness.send([row(2, incoming)], 2)
    assert harness.snapshot() is before
    harness.send([row(2)], 2)
    assert_window(harness.snapshot(), values(), 2)


def test_a_full_history_correction_is_checked_even_after_many_rows(harness):
    harness.send([full(1)], 1)
    for seq in range(2, 6):
        harness.send([row(seq)], seq)
    before = harness.snapshot()
    bad = values()
    bad[8, 2] = EMPTY
    with pytest.raises(RuntimeError, match='invalid OHLC'):
        harness.send([full(6, bad)], 6)
    assert harness.snapshot() is before
    harness.send([row(6)], 6)
    assert_window(harness.snapshot(), values(), 6)


def test_new_bar_and_indicator_readiness_need_full_and_reject_atomically(harness):
    harness.send([full(1), full(1, tf='1m')], 1)
    before = harness.snapshot()
    for bad, message in ((row(2, time=int(TIMES[-1]) + 300), 'new bar'),
                         (row(2, np.where(np.arange(COLS) == 4, np.nan, values()[-1])), 'validity transition')):
        with pytest.raises(wire.WireError, match=message):
            harness.send([row(2, tf='1m'), bad], 2)
        assert harness.snapshot() is before
        assert harness.cache.snapshot(SYMBOL, '1m').seq == 1
    new = values()
    new[-1, 4] = np.nan
    harness.send([full(2, new)], 2)
    assert harness.snapshot().indicator_validity['EMA'] is False
    harness.send([row(3, new[-1])], 3)
    assert_window(harness.snapshot(), clean(new), 3)


def test_sequence_gaps_duplicates_stale_and_reconnect_keep_their_meaning(harness):
    harness.send([full(1)], 1)
    harness.send([row(3)], 3)
    assert harness.cache.wire_diagnostics()['gaps'][0]['first_missing_seq'] == 2
    before = harness.snapshot()
    harness.send([row(2)], 4)
    assert harness.snapshot() is before
    harness.clock[0] = 31.
    assert harness.cache.health(SYMBOL, ['5m'])['5m']['status'] == 'STALE'
    harness.send([row(4)], 5)
    assert harness.cache.health(SYMBOL, ['5m'])['5m']['status'] == 'FRESH'
    assert harness.cache.export_state(metadata_only=True)['health_epochs'][KEY] == 1
    harness.clock[0] = 62.
    harness.send([full(5)], 6)
    assert harness.cache.export_state(metadata_only=True)['health_epochs'][KEY] == 2
    harness.cache.reconnect()
    with pytest.raises(wire.WireError, match='requires FULL'):
        harness.send([row(6)], 7)
    harness.send([full(1)], 8)
    assert harness.cache.export_state(metadata_only=True)['health_epochs'][KEY] == 3


@pytest.mark.parametrize('new_limit', [0, 2, 6])
def test_a_changed_retention_limit_rechecks_the_history(harness, new_limit):
    harness.cache.max_bars = 3
    times, data = TIMES[-6:], values(6)
    data[0, 0] = np.nan                       # outside the retained last three bars
    harness.send([full(1, data, times)], 1)
    harness.send([row(2, data[-1])], 2)
    before = harness.snapshot()
    harness.cache.max_bars = new_limit
    with pytest.raises(RuntimeError, match='snapshot too short|invalid OHLC'):
        harness.send([row(3, data[-1])], 3)
    assert harness.snapshot() is before


def test_nat_duplicates_and_drop_order_do_not_gain_a_fast_row_proof(harness):
    times = np.array([100, np.iinfo(np.int64).min, 200, 100, 300, 400], dtype='<i8')
    data = values(6)
    data[[0, 1, 3], 0] = np.nan               # NaT, discarded duplicate and dropped oldest time
    harness.cache.max_bars = 3
    harness.direct([full(1, data, times)], 1)
    harness.direct([row(2, data[-1], time=400)], 2)
    assert_window(harness.snapshot(), data, 2)
    before = harness.snapshot()
    harness.cache.max_bars = 4               # now includes the last duplicate of time=100
    with pytest.raises(RuntimeError, match='invalid OHLC'):
        harness.direct([row(3, data[-1], time=400)], 3)
    assert harness.snapshot() is before


def test_corrupt_child_crc_and_malformed_row_shape_do_not_publish(harness):
    harness.send([full(1)], 1)
    before = harness.snapshot()
    raw = bytearray(wire.pack_bundle(SYMBOL, [row(2)], seq=2, sent_at_ms=2000))
    raw[-5] ^= 1
    struct.pack_into('<I', raw, len(raw) - 4, zlib.crc32(memoryview(raw)[40:-4]))
    with pytest.raises(wire.WireError, match='CRC'):
        harness.raw(bytes(raw), 2000)
    assert harness.snapshot() is before
    malformed = bytearray(wire.pack_bundle(SYMBOL, [row(2)], seq=2, sent_at_ms=2000))
    child = 40 + len(SYMBOL.encode()) + 12 + 4
    struct.pack_into('<I', malformed, child + 24, 2)         # ROW must contain exactly one bar
    struct.pack_into('<I', malformed, len(malformed) - 4, zlib.crc32(memoryview(malformed)[40:-4]))
    with pytest.raises(wire.WireError, match='invalid feed header'):
        harness.raw(bytes(malformed), 2000)
    assert harness.snapshot() is before


def test_general_restore_does_not_trust_prior_history_validation(tmp_path):
    harness = Harness('replay', tmp_path)
    harness.send([full(1)], 1)
    state = harness.cache.export_state()
    state['snapshots'][KEY]['values'][7][0] = np.nan
    harness.cache.restore_state(state)
    before = harness.snapshot()
    with pytest.raises(RuntimeError, match='invalid OHLC'):
        harness.send([row(2)], 2)
    assert harness.snapshot() is before


def test_metadata_restore_shares_validated_arrays_and_restores_feed_age(harness):
    harness.send([full(1), full(1, tf='1m')], 1)
    before = harness.snapshot()
    state = harness.cache.export_state(metadata_only=True)
    state['health_session'] = 'BUILD_SESSION'
    state['updated'] = {key: 3. for key in state['updated']}
    state['health_epochs'] = {key: 7 for key in state['health_epochs']}
    state['wire_v2'].update(connections=8, reconnects=3, ea_build_hash='f' * 64)
    harness.clock[0] = 40.
    harness.cache.restore_metadata(state)
    restored, age = harness.cache.snapshots_with_age(SYMBOL, ['5m'])['5m']
    assert age == 37. and restored.received_at == 3.
    assert harness.cache.health_session == 'CURRENT'
    assert all(getattr(restored, name) is getattr(before, name) for name in ('time', 'volume', 'values', 'indicator_validity'))
    if harness.mode != 'shadow':
        assert restored.source_epoch == 'CURRENT:7'
    assert harness.cache.export_state(metadata_only=True)['wire_v2'] == state['wire_v2']
    harness.send([row(2)], 2)
    assert_window(harness.snapshot(), values(), 2)
    assert harness.cache.export_state(metadata_only=True)['health_epochs'][KEY] == 7


@pytest.mark.parametrize('damage', ['sequence', 'missing_sequence', 'extra_sequence', 'epoch', 'updated', 'wire', 'schema'])
def test_bad_metadata_cannot_partly_replace_any_validated_state(harness, damage):
    harness.send([full(1), full(1, tf='1m')], 1)
    before = harness.snapshot()
    original = harness.cache.export_state(metadata_only=True)
    state = deepcopy(original)
    state['health_epochs'][(SYMBOL, '1m')] = 99             # valid staged change before the damage
    if damage == 'sequence':
        state['sequences'][KEY] = 2
    elif damage == 'missing_sequence':
        del state['sequences'][KEY]
    elif damage == 'extra_sequence':
        state['sequences'][(SYMBOL, '1h')] = 1
    elif damage == 'epoch':
        state['health_epochs'][KEY] = -1
    elif damage == 'updated':
        state['updated'][KEY] = float('nan')
    elif damage == 'wire':
        state['wire_v2']['stats'][KEY] = None
    else:
        state['schema'] = 'unknown'
    with pytest.raises((ValueError, TypeError)):
        harness.cache.restore_metadata(state)
    assert harness.snapshot() is before
    assert harness.cache.export_state(metadata_only=True) == original


def test_metadata_requires_existing_validated_windows(tmp_path):
    harness = Harness('replay', tmp_path)
    harness.send([full(1)], 1)
    state = harness.cache.export_state(metadata_only=True)
    fresh = Harness('replay', tmp_path)
    with pytest.raises(ValueError, match='validated windows'):
        fresh.cache.restore_metadata(state)
    assert not fresh.cache.keys()


@pytest.mark.parametrize('restore_metadata', [False, True])
def test_checked_rows_scan_only_the_new_ohlc_and_validity_with_the_same_output(harness, monkeypatch, restore_metadata):
    harness.send([full(1)], 1)
    if restore_metadata:
        harness.cache.restore_metadata(harness.cache.export_state(metadata_only=True))
    finite_shapes, time_order_shapes = [], []
    class NumpyProbe:
        def __getattr__(self, name):
            return getattr(np, name)
        def isfinite(self, array):
            finite_shapes.append(np.asarray(array).shape)
            return np.isfinite(array)
        def all(self, array, *args, **kwargs):
            time_order_shapes.append(np.asarray(array).shape)
            return np.all(array, *args, **kwargs)
    monkeypatch.setattr(harness.staff, 'np', NumpyProbe())
    harness.send([row(2)], 2)
    assert_window(harness.snapshot(), values(), 2)
    # Diagnostic for the intended reduction, alongside the output/rejection tests above.
    assert finite_shapes == [(4,), (COLS,)]
    assert time_order_shapes == []
