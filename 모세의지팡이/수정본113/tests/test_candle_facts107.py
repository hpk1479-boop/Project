"""Candle polarity semantics and forming/closed boundaries on all Fact paths."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))

from indicator_facts import FactStore, MT5_TIMEFRAMES, standalone_frame
from indicator_facts_numpy import ArrayFactFrame
from event_engine.facts import EventFacts
from event_engine.market import COLUMNS, MarketView
from event_engine.model import FeedSnapshot


def view(opens, closes, *, times=None, seq=1, epoch='candle107'):
    opens, closes = np.asarray(opens, dtype=float), np.asarray(closes, dtype=float)
    values = np.full((len(opens), len(COLUMNS)), 100., dtype=float)
    values[:, COLUMNS['open']] = opens
    values[:, COLUMNS['close']] = closes
    values[:, COLUMNS['high']] = np.maximum(opens, closes) + 1.
    values[:, COLUMNS['low']] = np.minimum(opens, closes) - 1.
    times = np.arange(len(opens), dtype='int64') * 60 + 1791158400 if times is None else times
    return MarketView(FeedSnapshot(times, np.ones(len(opens), dtype='int64'), values, seq, epoch))


def dataframe(market):
    return pd.DataFrame({
        'time': pd.to_datetime(market.time, unit='s'),
        'open': market.column('open'), 'high': market.column('high'),
        'low': market.column('low'), 'close': market.column('close'),
        'volume': market.volume,
    }, index=pd.Index(np.arange(len(market)) + 17, name='bar'))


def all_paths(market, tf='1h'):
    events = EventFacts()
    events.invalidate({('GOLD', tf): market.snapshot})
    return (
        standalone_frame(dataframe(market), tf)['candle_direction'].to_numpy(),
        ArrayFactFrame(market, tf)['candle_direction'],
        events.get('GOLD', tf, market.snapshot, 'candle_direction', 'test'),
    )


@pytest.mark.parametrize('opened,closed,expected', [
    (100., 101., 1.), (100., 99., -1.), (100., 100., 0.),
    (0., -0., 0.), (-1., 0., 1.), (1., 0., -1.),
    (-1.e308, 1.e308, 1.), (1.e308, -1.e308, -1.),
    (100., np.nextafter(100., np.inf), 1.),
    (100., np.nextafter(100., -np.inf), -1.),
    (np.nan, 100., np.nan), (100., np.nan, np.nan),
    (np.inf, 100., np.nan), (-np.inf, 100., np.nan),
    (100., np.inf, np.nan), (100., -np.inf, np.nan),
    (np.inf, np.inf, np.nan), (-np.inf, -np.inf, np.nan),
])
def test_candle_polarity_strict_comparison_and_invalid_inputs(opened, closed, expected):
    for result in all_paths(view([opened], [closed])):
        np.testing.assert_allclose(result, [expected], equal_nan=True)


@pytest.mark.parametrize('tf', MT5_TIMEFRAMES)
def test_same_candle_meaning_for_every_supported_timeframe(tf):
    for result in all_paths(view([100., 100., 100.], [101., 99., 100.]), tf):
        np.testing.assert_array_equal(result, [1., -1., 0.])


def test_series_keeps_input_bar_index_and_non_numeric_values_are_invalid():
    data = pd.DataFrame({'open': ['100', 'bad', '100'], 'close': ['101', '99', None]},
                        index=[101, 203, 509])
    result = standalone_frame(data, '1h')['candle_direction']
    assert result.index.equals(data.index)
    np.testing.assert_allclose(result.to_numpy(), [1., np.nan, np.nan], equal_nan=True)


def test_forming_price_flip_does_not_reuse_old_direction_or_change_closed_bars():
    initial = view([100., 100., 100.], [101., 99., 101.])
    incremental = ArrayFactFrame(initial, '1h')
    store, events = FactStore(), EventFacts()
    for seq, (close, expected) in enumerate(((101., 1.), (99., -1.), (100., 0.),
                                            (np.nan, np.nan), (101., 1.)), 1):
        current = view([100., 100., 100.], [101., 99., close], seq=seq)
        incremental.update(current)
        events.invalidate({('GOLD', '1h'): current.snapshot})
        results = (
            incremental['candle_direction'],
            store.frame('GOLD', '1h', dataframe(current))['candle_direction'].to_numpy(),
            events.get('GOLD', '1h', current.snapshot, 'candle_direction', 'test'),
        )
        for result in results:
            np.testing.assert_allclose(result, [1., -1., expected], equal_nan=True)
            assert result[-2] == -1.


def test_forming_open_correction_changes_direction_with_same_close():
    original = view([100., 100., 100.], [101., 99., 101.])
    incremental = ArrayFactFrame(original, '1h')
    assert incremental['candle_direction'][-1] == 1.
    corrected = view([100., 100., 102.], [101., 99., 101.], seq=2)
    incremental.update(corrected)
    np.testing.assert_array_equal(incremental['candle_direction'], [1., -1., -1.])
    for result in all_paths(corrected):
        np.testing.assert_array_equal(result, incremental['candle_direction'])


def test_previous_forming_direction_is_available_after_bar_closes():
    initial = view([100., 100., 100.], [101., 99., 101.])
    incremental = ArrayFactFrame(initial, '1h')
    assert incremental['candle_direction'][-1] == 1.
    closed = view([100., 100., 100., 200.], [101., 99., 101., 199.],
                  times=np.r_[initial.time, initial.time[-1] + 3600], seq=2)
    incremental.update(closed)
    expected = [1., -1., 1., -1.]
    np.testing.assert_array_equal(incremental['candle_direction'], expected)
    for result in all_paths(closed):
        np.testing.assert_array_equal(result, expected)
    assert incremental['candle_direction'][-2] == 1.


def test_closed_history_correction_is_not_left_in_cached_prefix():
    initial = view([100., 100., 100.], [101., 99., 101.])
    incremental = ArrayFactFrame(initial, '1h')
    np.testing.assert_array_equal(incremental['candle_direction'], [1., -1., 1.])
    corrected = view([100., 100., 100.], [99., 101., 101.], seq=2)
    incremental.update(corrected)
    np.testing.assert_array_equal(incremental['candle_direction'], [-1., 1., 1.])
    for result in all_paths(corrected):
        np.testing.assert_array_equal(result, incremental['candle_direction'])


def test_event_fact_does_not_require_dataframe_creation(monkeypatch):
    market = view([100., 100.], [101., 99.])
    events = EventFacts()
    events.invalidate({('GOLD', '1h'): market.snapshot})

    def no_dataframe(*args, **kwargs):
        raise AssertionError('candle direction must use its registered array calculation')

    monkeypatch.setattr(pd, 'DataFrame', no_dataframe)
    result = events.get('GOLD', '1h', market.snapshot, 'candle_direction', 'test')
    np.testing.assert_array_equal(result, [1., -1.])
