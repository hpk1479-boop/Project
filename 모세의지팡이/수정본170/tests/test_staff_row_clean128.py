"""STAFF ROW updates clean only the new row once the snapshot is known clean (수정본128).

The result must be what cleaning the whole array gives: MT5 EMPTY_VALUE (~DBL_MAX) and
infinities become NaN in every row, the indicator validity follows the last row, and the
published arrays stay immutable. A restored snapshot is not trusted to be clean.
"""
from pathlib import Path
import io, socket, sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_backtest.bridge import Collector
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff

SYMBOL, EMPTY = 'XAUUSD+', 1.7976931348623157e308
COLS = len(wire.PIPE_VALUE_COLUMNS)
TIMES = np.arange(30, dtype='<i8') * 60 + 1756684800


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('real network is forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def bars(rows=30):
    values = np.full((rows, COLS), 10., dtype='<f8')
    values[:, 0] = np.arange(rows) + 100.
    values[:, 1], values[:, 2], values[:, 3] = values[:, 0] + 2, values[:, 0] - 2, values[:, 0] + 1
    return values


def clean(values):
    out = np.array(values, dtype='<f8')
    out[np.isinf(out) | (np.abs(out) > 1e300)] = np.nan
    return out


def reference_validity(staff, last):
    return {ind: all(index is not None and np.isfinite(float(last[index])) for index in indices)
            for ind, indices in staff._MT5_REQUIRED_COLUMN_INDICES.items()}


def setup(tmp_path):
    staff = load_staff()
    cache = staff.StaffPipeCache('', monotonic=lambda: 0., gap_journal=tmp_path / 'gaps.jsonl')
    return staff, cache, StaffIngressAdapter(cache, Collector())


def full(values, seq, tf='1m'):
    return wire.pack_v2(SYMBOL, tf, TIMES, np.ones(len(TIMES), dtype='<i8'), values, seq=seq)


def row(values, seq, tf='1m'):
    return wire.pack_v2(SYMBOL, tf, TIMES[-1:], np.full(1, 7, dtype='<i8'), values[None, :], seq=seq, kind=wire.WIRE_ROW)


def publish(adapter, frames, seq):
    adapter.receive_one(io.BytesIO(wire.pack_bundle(SYMBOL, frames, seq=seq, sent_at_ms=1000 * seq)).read)


def snap(cache, tf='1m'):
    return cache._entries[(SYMBOL, tf)]


HOLES = {'empty': (slice(5, 40), EMPTY), 'infinite': (slice(8, 12), -np.inf), 'normal': (slice(0, 0), 0.),
         'nan': (slice(9, 15), np.nan)}


@pytest.mark.parametrize('new_row', list(HOLES))
def test_row_update_equals_cleaning_the_whole_array(tmp_path, new_row):
    staff, cache, adapter = setup(tmp_path)
    columns, value = HOLES[new_row]
    first = bars()
    first[3, 10] = EMPTY; first[17, 20:25] = np.inf; first[-1, columns] = EMPTY   # a ROW may not change validity
    publish(adapter, [full(first, 1)], 1)
    assert snap(cache).clean
    incoming = bars()[-1].copy()
    incoming[columns] = value
    publish(adapter, [row(incoming, 2)], 2)
    expected = clean(first); expected[-1] = clean(incoming)
    published = snap(cache).snapshot
    assert published.values.tobytes() == expected.tobytes()
    assert dict(published.indicator_validity) == reference_validity(staff, expected[-1])
    assert list(published.indicator_validity) == list(staff._MT5_REQUIRED_COLUMN_INDICES)
    assert not published.values.flags.writeable and not published.volume.flags.writeable
    assert published.volume.tolist() == [1] * 29 + [7]


def test_a_restored_snapshot_is_cleaned_whole_on_its_next_row(tmp_path):
    staff, cache, adapter = setup(tmp_path)
    publish(adapter, [full(bars(), 1)], 1)
    state = cache.export_state()
    dirty = np.array(state['snapshots'][(SYMBOL, '1m')]['values'])
    dirty[4, 11] = EMPTY; dirty[20, 33] = np.inf          # e.g. an older keyframe that kept EMPTY_VALUE
    dirty[-1, 12] = EMPTY                                 # the same validity as the coming ROWs
    state['snapshots'][(SYMBOL, '1m')]['values'] = dirty.tolist()
    state['snapshots'][(SYMBOL, '1m')]['indicator_validity'] = reference_validity(staff, clean(dirty)[-1])
    cache.restore_state(state)
    assert not snap(cache).clean
    incoming = bars()[-1].copy(); incoming[12] = EMPTY
    publish(adapter, [row(incoming, 2)], 2)
    expected = clean(dirty); expected[-1] = clean(incoming)
    assert snap(cache).snapshot.values.tobytes() == expected.tobytes()
    assert snap(cache).clean
    incoming = bars()[-1].copy(); incoming[12] = np.inf
    publish(adapter, [row(incoming, 3)], 3)
    expected[-1] = clean(incoming)
    assert snap(cache).snapshot.values.tobytes() == expected.tobytes()


def test_a_heartbeat_keeps_the_mark_and_the_arrays(tmp_path):
    staff, cache, adapter = setup(tmp_path)
    publish(adapter, [full(bars(), 1)], 1)
    before = snap(cache).snapshot
    publish(adapter, [wire.pack_v2(SYMBOL, '1m', seq=2, kind=wire.WIRE_HEARTBEAT)], 2)
    assert snap(cache).clean and snap(cache).snapshot.values is before.values and snap(cache).snapshot.seq == 2


def test_a_rejected_bundle_restores_the_entries_with_their_marks(tmp_path):
    staff, cache, adapter = setup(tmp_path)
    publish(adapter, [full(bars(), 1), full(bars(), 1, '5m')], 1)
    entries = {tf: snap(cache, tf) for tf in ('1m', '5m')}
    bad = wire.pack_v2(SYMBOL, '5m', TIMES[-1:] + 60, np.ones(1, dtype='<i8'), bars()[-1:], seq=2, kind=wire.WIRE_ROW)
    with pytest.raises(wire.WireError, match='new bar'):
        publish(adapter, [row(bars()[-1], 2), bad], 2)
    assert all(snap(cache, tf) is entries[tf] and snap(cache, tf).clean for tf in entries)


@pytest.mark.parametrize('seed', range(5))
def test_validity_follows_the_column_rule(tmp_path, seed):
    staff, cache, adapter = setup(tmp_path)
    rng = np.random.default_rng(seed)
    values = bars()
    holes = rng.choice(np.arange(4, COLS), size=12, replace=False)
    values[-1, holes] = rng.choice([np.nan, EMPTY, np.inf], size=12)
    publish(adapter, [full(values, 1)], 1)
    assert dict(snap(cache).snapshot.indicator_validity) == reference_validity(staff, clean(values)[-1])
