"""수정본130: the engine snapshot shares one sealed validity map per content; EMPTY_VALUE cleaning uses one test.

Both give exactly what they gave before: the same validity content in the same order, and the same
cleaned bytes (infinities and |value| > 1e300 become NaN, NaN and everything else stay).
"""
from pathlib import Path
from types import MappingProxyType
import io, socket, sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_backtest.bridge import Collector
from event_engine.model import FrozenMap, Kind
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff

staff = load_staff()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('real network is forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


EDGES = [np.inf, -np.inf, np.nan, 1.7976931348623157e308, -1.7976931348623157e308, 1e300, -1e300,
         np.nextafter(1e300, np.inf), -1e301, 0.0, -0.0, 5e-324, 123.25]


def reference_clean(values):
    invalid = np.isinf(values) | (np.abs(values) > 1.0e300)
    out = values.copy(); out[invalid] = np.nan
    return out


def test_cleaning_gives_the_same_bytes_for_every_edge_value():
    values = np.array([EDGES, EDGES[::-1]], dtype='<f8')
    assert staff._clean_values(values).tobytes() == reference_clean(values).tobytes()


def test_a_clean_array_is_returned_without_a_copy():
    values = np.array([[np.nan, 1e300, -0.0, 5.0]], dtype='<f8')
    assert staff._clean_values(values) is values


def test_one_sealed_map_per_validity_content_in_the_same_order():
    adapter = StaffIngressAdapter(None, None)
    first = adapter._sealed_validity(MappingProxyType({'PRICE': True, 'RSI': False}))
    again = adapter._sealed_validity(MappingProxyType({'PRICE': True, 'RSI': False}))
    other = adapter._sealed_validity(MappingProxyType({'PRICE': True, 'RSI': True}))
    reordered = adapter._sealed_validity(MappingProxyType({'RSI': False, 'PRICE': True}))
    assert type(first) is FrozenMap and first is again
    assert dict(other) == {'PRICE': True, 'RSI': True} and other is not first
    assert list(reordered) == ['RSI', 'PRICE'] and reordered is not first


def test_the_shared_maps_stay_bounded():
    adapter = StaffIngressAdapter(None, None)
    for i in range(5000):
        sealed = adapter._sealed_validity({f'IND{i}': True})
        assert dict(sealed) == {f'IND{i}': True}
    assert len(adapter._validity) <= 4096


def test_published_snapshots_carry_the_staff_validity(tmp_path):
    cache = staff.StaffPipeCache('', monotonic=lambda: 0., gap_journal=tmp_path / 'gaps.jsonl')
    collector = Collector(); adapter = StaffIngressAdapter(cache, collector)
    cols = len(wire.PIPE_VALUE_COLUMNS)
    times = np.arange(30, dtype='<i8') * 60 + 1756684800
    values = np.full((30, cols), 10., dtype='<f8'); values[:, :4] = 100.
    values[-1, 20:30] = 1.7976931348623157e308
    for seq in (1, 2):
        frames = [wire.pack_v2('XAUUSD+', tf, times, np.ones(30, dtype='<i8'), values, seq=seq) for tf in ('1m', '5m')]
        adapter.receive_one(io.BytesIO(wire.pack_bundle('XAUUSD+', frames, seq=seq, sent_at_ms=1000 * seq)).read)
    bundles = [item for item in collector.pending if item.kind == Kind.MARKET_BUNDLE]
    assert len(bundles) == 2
    maps = [feed.indicator_validity for item in bundles for feed in item.payload['feeds'].values()]
    for tf in ('1m', '5m'):
        expected = cache._entries[('XAUUSD+', tf)].snapshot.indicator_validity
        assert list(bundles[-1].payload['feeds'][tf].indicator_validity.items()) == list(expected.items())
    assert all(m is maps[0] for m in maps), 'the same content is one shared, immutable map'
