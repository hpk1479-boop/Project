"""Exercise unchanged acquisition, cache, bar and LIVE_PARITY half-open boundaries.

Inputs are explicit synthetic native-format ticks, NOT broker history.
"""
from pathlib import Path
from types import SimpleNamespace
import datetime as dt
import shutil
import sys
import uuid

import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generic_backtest.gui import parse_ui_date_range, parse_utc_ns
from generic_backtest.history.provider import ReadOnlyMT5Provider
from generic_backtest.history.prepare import GenericHistoryService
from generic_backtest.history.cache import GenericArchiveReader, RawChunkWriter
from generic_backtest.contracts import ROOT, GenericError, InstrumentSpec
from generic_backtest.market import GenericMarketCore
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.live_parity import timeline
from pit.archive.reader import TICK_DTYPE
from integration_fixtures import INSTRUMENT, CAL


def sample_rows(start_ms, end_ms):
    stamps = [start_ms - 1, start_ms, start_ms + 1, start_ms + 12 * 3600 * 1000,
              end_ms - 60000, end_ms - 1, end_ms, end_ms + 1]
    rows = np.zeros(len(stamps), dtype=TICK_DTYPE)
    rows['time_msc'] = stamps; rows['time'] = np.array(stamps) // 1000
    rows['bid'] = 100 + np.arange(len(rows)) * .1
    rows['ask'] = rows['bid'] + .1; rows['last'] = rows['bid']
    rows['volume'] = 1; rows['volume_real'] = 1.; rows['flags'] = 2
    return rows


def synthetic_provider(rows):
    calls = []
    def copy_ticks_range(symbol, start, end, flags):
        calls.append((symbol, start, end, flags))
        return rows.copy()  # Deliberately broad endpoint response; provider must trim it.
    provider = ReadOnlyMT5Provider.__new__(ReadOnlyMT5Provider)
    provider._validate_connection = lambda: None  # Identity guards tested separately.
    provider.mt5 = SimpleNamespace(COPY_TICKS_ALL=0, copy_ticks_range=copy_ticks_range,
                                   last_error=lambda: (1, 'test-only'))
    provider.instrument = lambda symbol: INSTRUMENT
    provider.build = ('SYNTHETIC_TEST_ONLY',)
    provider.input_kind = 'SYNTHETIC_VALIDATION_ONLY'
    return provider, calls


@pytest.mark.parametrize('date,next_utc', [
    ('2026-09-22', '2026-09-22T15:00:00+00:00'),
    ('2026-02-28', '2026-02-28T15:00:00+00:00'),
    ('2026-04-30', '2026-04-30T15:00:00+00:00'),
    ('2026-12-31', '2026-12-31T15:00:00+00:00'),
    ('2024-02-29', '2024-02-29T15:00:00+00:00'),
])
def test_actual_provider_filter_includes_last_millisecond_excludes_midnight(date, next_utc):
    start, end = parse_ui_date_range(date, date)
    rows = sample_rows(start // 10**6, end // 10**6)
    provider, calls = synthetic_provider(rows)
    actual = provider.ticks('TEST', start // 10**6, end // 10**6)
    assert list(actual['time_msc']) == list(rows['time_msc'][1:6])
    assert int(actual['time_msc'][-1]) * 10**6 == end - 10**6
    assert calls[0][2].isoformat() == next_utc
    assert calls[0][1].tzinfo == calls[0][2].tzinfo == dt.timezone.utc
    assert end == parse_utc_ns(next_utc)


def test_history_owned_chunks_cache_hit_bars_and_parity_end_boundary():
    start, end = parse_ui_date_range('2026-09-22', '2026-09-22')
    rows = sample_rows(start // 10**6, end // 10**6)
    provider, calls = synthetic_provider(rows)
    cache = ROOT / 'generic_cache' / ('ui_boundary_test_' + uuid.uuid4().hex)
    try:
        service = GenericHistoryService(provider, cache)
        result = service.prepare('TEST', start, end)
        reader = GenericArchiveReader(result['archive'])
        ticks = list(reader)
        stamps = [t.time_msc * 10**6 for t in ticks]
        assert stamps == [int(ms) * 10**6 for ms in rows['time_msc'][1:6]]
        assert reader.manifest['coverage_start_ns'] == start
        assert reader.manifest['coverage_end_ns'] == end
        assert len(calls) == 24
        before_hit = len(calls)
        hit = service.prepare('TEST', start, end)
        assert hit['cache_hit'] and len(calls) == before_hit
        assert hit['manifest']['archive_identity'] == result['manifest']['archive_identity']
        assert Path(hit['archive']).name == Path(result['archive']).name
        # Build the existing PIT bars from exactly the acquired owned ticks.
        core = GenericMarketCore(InstrumentSpec(**INSTRUMENT), CalendarRegistry(CAL), start, {'1m': 5}, 'boundary-test')
        for tick in ticks:
            view, _ = core.step(tick)
        bars = view.bars('1m')
        assert bars and view.token.now_ns == end - 10**6
        assert all(bar.open_ns < end for bar in bars)
        # Existing timeline includes neither raw ticks nor timer events at end.
        events = list(timeline(iter(ticks), start, end, {'producer_ms': 60000, 'poll_ms': 60000}))
        assert all(now < end for _, now, _ in events)
        assert [now for kind, now, _ in events if kind == 'RAW'] == stamps
    finally:
        shutil.rmtree(cache, ignore_errors=True)


@pytest.mark.parametrize('offset', [0, 1])
def test_cache_writer_still_rejects_exclusive_end_and_later(tmp_path, offset):
    start, end = parse_ui_date_range('2026-09-22', '2026-09-22')
    rows = sample_rows(start // 10**6, end // 10**6)[6 + offset:7 + offset]
    with pytest.raises(GenericError, match='E_CHUNK_CORRUPT'):
        RawChunkWriter(tmp_path / 'raw', 'test').write(0, rows, start, end)


def test_provider_api_failure_still_rejected():
    provider, _ = synthetic_provider(np.zeros(0, dtype=TICK_DTYPE))
    provider.mt5.copy_ticks_range = lambda *a: None
    start, end = parse_ui_date_range('2026-09-22', '2026-09-22')
    with pytest.raises(GenericError, match='E_HISTORY_PARTIAL'):
        provider.ticks('TEST', start // 10**6, end // 10**6)
