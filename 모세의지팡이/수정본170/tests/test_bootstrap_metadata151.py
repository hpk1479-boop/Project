"""Seek restores continuation metadata after canonical FULL validation.

The reader and replay publish identical judged values, validity and epochs;
neither restoration serializes validated market arrays through Python lists.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'Part1/program'), str(ROOT/'Part2'), str(ROOT/'tests')]
from event_backtest.bridge import CaptureInputs
from event_engine.model import Kind
from event_host import load_staff
from test_parallel_oz import keyframe_fixture, offline  # noqa: F401


def observed(source):
    return [item for item in source if item.kind == Kind.MARKET_BUNDLE]


@pytest.mark.parametrize('published', [None, {'1m'}])
@pytest.mark.parametrize('transport', ['live', 'replay'])
def test_seek_preserves_judged_data_health_and_continuation(tmp_path, monkeypatch, published, transport):
    rows, _, capture = keyframe_fixture(tmp_path)
    staff = load_staff()
    results = []
    caches = []
    for mode in ('beginning', 'keyframe'):
        clock = [0.]
        cache = staff.StaffPipeCache('', health_session='TEST', monotonic=lambda: clock[0],
                                     gap_journal=tmp_path/(mode+'.jsonl'), published_timeframes=published)
        # Diagnostic check: no array serialization is needed for a canonical seek.
        original = cache.export_state
        def metadata_export(*, metadata_only=False):
            assert metadata_only, 'validated arrays must not be serialized during seek'
            return original(metadata_only=True)
        monkeypatch.setattr(cache, 'export_state', metadata_export)
        source = CaptureInputs(staff, cache, [capture], transport=transport, clock=clock,
                               start_ms=rows[3][0], capture_start=mode)
        results.append(observed(source))
        caches.append(cache)
        if mode == 'keyframe':
            assert source.seek_info['prefix_bundles_skipped'] == 3
        assert not cache.wire_diagnostics()['gaps']
    assert len(results[0]) == len(results[1]) == 6
    for direct, sought in zip(*results):
        assert (direct.source_seq, direct.source_time) == (sought.source_seq, sought.source_time)
        assert set(direct.payload['feeds']) == set(sought.payload['feeds'])
        for tf, first in direct.payload['feeds'].items():
            second = sought.payload['feeds'][tf]
            assert first.source_epoch == second.source_epoch == 'TEST:2'
            assert first.seq == second.seq
            assert first.indicator_validity == second.indicator_validity
            # Values used by candle/MA consumers remain the actual captured values.
            assert np.array_equal(first.time, second.time)
            assert np.array_equal(first.volume, second.volume)
            assert np.array_equal(first.values, second.values, equal_nan=True)
            assert np.isfinite(second.values[:, :4]).all()
            with pytest.raises(ValueError):
                second.values[-1, 3] = 0.
    for tf in ('1m', '5m'):
        first, first_age = caches[0].snapshot_with_age('XAUUSD+', tf)
        second, second_age = caches[1].snapshot_with_age('XAUUSD+', tf)
        assert first.seq == second.seq and first_age == second_age
        assert first.indicator_validity == second.indicator_validity
        assert np.array_equal(first.values[:, :4], second.values[:, :4], equal_nan=True)
