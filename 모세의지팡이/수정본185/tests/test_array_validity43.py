"""Full array predicates, publication lifetime, live health and temporal boundaries."""
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from event_engine import market
from event_engine.board import Board
from event_engine.facts import EventFacts
from event_engine.model import FeedSnapshot, Event, Kind, Subscriptions
from oz_engine.runtime import ready
from staff_compat import MT5_REQUIRED_BY_INDICATOR

SYMBOL, TF, OBS = 'XAUUSD+', '1m', 1756684800000
COLUMNS = market.COLUMNS


def snapshot(rows=650, changes=(), seq=1, epoch='43', shift=0):
    values = np.full((rows, len(COLUMNS)), 100., dtype='<f8')
    values[:, COLUMNS['high']] = 102.
    values[:, COLUMNS['low']] = 98.
    values[:, COLUMNS['wonbi_upper']] = 103.
    values[:, COLUMNS['wonbi_lower']] = 97.
    for row, column, value in changes:
        values[row, COLUMNS[column]] = value
    return FeedSnapshot(np.arange(rows, dtype='<i8') * 60 + OBS // 1000 + shift,
                        np.ones(rows, dtype='<i8'), values, seq, epoch)


CONSUMER = SimpleNamespace(subscriptions=lambda: Subscriptions(facts=('WONBI_BANDS',)))


def event(snapshot=None, *, kind=Kind.MARKET_BUNDLE, status=None, symbol=SYMBOL, tf=TF,
          when=OBS, seq=1):
    payload = {'symbol': symbol}
    if snapshot is not None:
        payload['feeds'] = {tf: snapshot}
    if status is not None:
        payload['status'] = status
    return Event(seq, 0, 'probe43', seq, when, kind, payload)


def board_with(snap, *, when=OBS, symbol=SYMBOL, tf=TF):
    board = Board(EventFacts())
    board.commit(event(snap, when=when, symbol=symbol, tf=tf))
    return board


def view(board, when=OBS):
    return board.view({}, CONSUMER, when)


def count_scans(monkeypatch):
    scans = []
    def isfinite(values):
        scans.append(values.shape)
        return np.isfinite(values)
    monkeypatch.setattr(market, 'np', SimpleNamespace(isfinite=isfinite))
    return scans


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize('row', [0, -2, -1])
@pytest.mark.parametrize('column', ['open', 'high', 'low', 'close'])
def test_every_ohlc_row_is_checked_even_after_cached_rejections(bad, row, column):
    snap = snapshot(changes=[(row, column, bad)])
    b = view(board_with(snap))
    for _ in range(3):
        assert market.select(b, SYMBOL, TF) is None
        assert not ready(b, SYMBOL, TF)
        assert not market.finite_values(b, SYMBOL, TF, snap)
    assert np.isfinite(snap.values[:, :4]).all() == False


@pytest.mark.parametrize('group', list(MT5_REQUIRED_BY_INDICATOR))
@pytest.mark.parametrize('row', [-2, -1])
@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf])
def test_only_requested_indicators_and_current_row_gate_selection(group, row, bad):
    column = MT5_REQUIRED_BY_INDICATOR[group][0]
    snap = snapshot(changes=[(row, column, bad)])
    b = view(board_with(snap))
    for _ in range(2):
        assert market.select(b, SYMBOL, TF) is not None
        assert bool(ready(b, SYMBOL, TF))
        assert (market.select(b, SYMBOL, TF, (group,)) is not None) == (row == -2)
        other = 'RSI' if group == 'EMA' else 'EMA'
        assert market.select(b, SYMBOL, TF, (other,)) is not None


def test_common_checks_shared_across_oz_views_and_required_groups():
    snap = snapshot()
    board = board_with(snap)
    one, two = view(board), view(board)
    assert ready(one, SYMBOL, TF)
    plain = market.select(two, SYMBOL, TF)
    bands = market.select(one, SYMBOL, TF, ('WONBI',))
    for _ in range(8):
        assert market.select(two, SYMBOL, TF, ('EMA',)) is plain
        assert ready(one, SYMBOL, TF)
        assert market.select(two, SYMBOL, TF, ('WONBI',)) is bands
    assert plain is not bands and plain.snapshot is bands.snapshot is snap
    assert np.shares_memory(plain.values, snap.values)
    assert not plain.values.flags.writeable and 'wonbi_upper' in bands.features


@pytest.mark.parametrize('when,allowed', [(OBS, True), (OBS + 30000, True),
                                        (OBS + 30001, False), (OBS - 100, True)])
def test_observation_age_is_rechecked_with_a_warm_array_cache(when, allowed):
    board = board_with(snapshot())
    assert ready(view(board), SYMBOL, TF)
    assert market.select(view(board), SYMBOL, TF) is not None
    later = view(board, when)
    assert bool(ready(later, SYMBOL, TF)) == allowed
    assert (market.select(later, SYMBOL, TF) is not None) == allowed


def test_none_source_time_retains_market_availability_contract():
    board = board_with(snapshot())
    assert market.select(view(board, None), SYMBOL, TF) is not None


@pytest.mark.parametrize('status', ['STALE', 'UNAVAILABLE', 'RECONNECT'])
def test_health_rejection_recovery_and_reconnect_do_not_reuse_readiness(status):
    snap = snapshot()
    board = board_with(snap)
    assert ready(view(board), SYMBOL, TF)
    previous_cache = board._frame_cache
    board.set_health(event(kind=Kind.FEED_HEALTH, status=status))
    assert board._frame_cache is not previous_cache
    assert market.select(view(board), SYMBOL, TF) is None
    assert not ready(view(board), SYMBOL, TF)
    if status == 'RECONNECT':
        assert not board._feeds
    board.commit(event(snap, when=OBS + 1000, seq=2))
    assert ready(view(board, OBS + 1000), SYMBOL, TF)
    assert market.select(view(board, OBS + 1000), SYMBOL, TF) is not None


@pytest.mark.parametrize('rows', [1, 2, 3, 650])
def test_minimum_rows_is_not_combined_with_shared_finite_flag(rows):
    board = board_with(snapshot(rows=rows))
    assert market.select(view(board), SYMBOL, TF) is not None
    assert bool(ready(view(board), SYMBOL, TF)) == (rows >= 3)


def test_missing_feeds_are_checked_before_any_array_access():
    board = Board(EventFacts())
    b = view(board)
    assert market.select(b, SYMBOL, TF) is None and not ready(b, SYMBOL, TF)


def test_each_publication_including_same_object_clears_finite_results(monkeypatch):
    snap = snapshot()
    board = board_with(snap)
    scans = count_scans(monkeypatch)
    for i in range(5):
        assert ready(view(board, OBS + i), SYMBOL, TF)
        before = board._frame_cache
        board.commit(event(snap, seq=i+2, when=OBS+i+1))
        assert board._frame_cache is not before and not board._frame_cache
    assert scans == [(650, 4)] * 5


def test_different_snapshot_identity_not_confused_by_same_sequence_or_epoch():
    first = snapshot()
    second = snapshot(changes=[(-1, 'close', np.nan)])
    cache_board = SimpleNamespace(_frame_cache={})
    assert market.finite_values(cache_board, SYMBOL, TF, first)
    assert not market.finite_values(cache_board, SYMBOL, TF, second)
    assert market.finite_values(cache_board, SYMBOL, TF, first)
    assert len(cache_board._frame_cache) == 1


def test_lightweight_board_without_cache_runs_full_validation(monkeypatch):
    snap = snapshot()
    b = SimpleNamespace(feeds={(SYMBOL, TF): snap}, health={}, source_time=OBS,
                        observed={(SYMBOL, TF): OBS}, snapshot=lambda *a: snap)
    scans = count_scans(monkeypatch)
    assert ready(b, SYMBOL, TF) and ready(b, SYMBOL, TF)
    assert scans == [(650, 4), (650, 4)]
    assert not hasattr(b, '_frame_cache')


def test_symbol_timeframe_and_requirement_keys_are_separate(monkeypatch):
    snap = snapshot()
    b = SimpleNamespace(_frame_cache={})
    scans = count_scans(monkeypatch)
    cols = tuple(COLUMNS[c] for c in MT5_REQUIRED_BY_INDICATOR['EMA'])
    for symbol, tf, columns in [(SYMBOL, TF, None), ('OTHER', TF, None),
                                 (SYMBOL, '5m', None), (SYMBOL, TF, cols)]:
        assert market.finite_values(b, symbol, tf, snap, columns)
        assert market.finite_values(b, symbol, tf, snap, columns)
    assert len(scans) == 4 and len(b._frame_cache) == 4


def test_old_view_new_publication_and_restore_keep_independent_caches():
    snap = snapshot()
    board = board_with(snap)
    saved = board.checkpoint()
    old = view(board)
    assert ready(old, SYMBOL, TF)
    invalid = snapshot(changes=[(4, 'low', np.inf)], seq=2)
    board.commit(event(invalid, seq=2))
    assert not ready(view(board), SYMBOL, TF)
    assert ready(old, SYMBOL, TF)
    board.restore(saved)
    assert not board._frame_cache
    assert ready(view(board), SYMBOL, TF)


def test_cross_symbol_publication_does_not_keep_old_finite_cache():
    snap = snapshot()
    board = board_with(snap)
    assert ready(view(board), SYMBOL, TF)
    previous = board._frame_cache
    board.commit(event(snapshot(), symbol='OTHER', seq=2))
    assert board._frame_cache is not previous and not board._frame_cache
    assert ready(view(board), SYMBOL, TF)


def test_mutating_original_constructor_input_cannot_change_cached_predicate():
    values = snapshot().values.copy()
    snap = FeedSnapshot(snapshot().time, snapshot().volume, values)
    b = view(board_with(snap))
    assert ready(b, SYMBOL, TF)
    values[0, 0] = np.nan
    assert ready(b, SYMBOL, TF) and np.isfinite(snap.values[:, :4]).all()
    with pytest.raises(ValueError):
        snap.values.setflags(write=True)


def test_required_order_unknown_and_duplicate_names_match_full_predicate():
    snap = snapshot(changes=[(-1, 'RSI_val', np.nan)])
    b = view(board_with(snap))
    for required in [(), ('UNKNOWN',), ('EMA',), ('EMA', 'EMA'), ('RSI',),
                     ('EMA', 'RSI'), ('RSI', 'EMA')]:
        columns = [COLUMNS[c] for name in required for c in MT5_REQUIRED_BY_INDICATOR.get(name, ())]
        expected = not columns or np.isfinite(snap.values[-1, columns]).all()
        for _ in range(2):
            assert (market.select(b, SYMBOL, TF, required) is not None) == expected


def test_random_publications_match_uncached_full_predicates():
    rng = np.random.default_rng(4301)
    board = Board(EventFacts())
    groups = [(), ('EMA',), ('WONBI',), ('RSI', 'PRICE'), ('HMA',)]
    for i in range(80):
        changes = []
        if i % 3:
            column = rng.choice(['open', 'close', 'ema_50', 'wonbi_upper', 'price_hma_6', 'RSI_val'])
            changes = [(int(rng.choice([0, -2, -1])), str(column), rng.choice([np.nan, np.inf, -np.inf]))]
        snap = snapshot(changes=changes, seq=i, epoch=str(i // 10), shift=(i // 7) * 60)
        board.commit(event(snap, seq=i+1))
        b = view(board)
        full = np.isfinite(snap.values[:, :4]).all()
        for required in groups * 2:
            columns = [COLUMNS[c] for name in required for c in MT5_REQUIRED_BY_INDICATOR.get(name, ())]
            expected = full and (not columns or np.isfinite(snap.values[-1, columns]).all())
            assert (market.select(b, SYMBOL, TF, required) is not None) == expected
            assert bool(ready(b, SYMBOL, TF)) == bool(full)
        assert len(board._frame_cache) <= 7
