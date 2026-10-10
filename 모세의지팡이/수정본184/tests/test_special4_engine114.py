"""SPECIAL4's cycle in a real event engine: LIVE and backtest on the same input, first seen late.

The values come from the engine's own Facts (ATR14 of the 1m bars, WONBI bands), not from the
test: 1m bars have a true range of 2.0, so 1x ATR is 2.0 above the 30m close of 100.
"""
import numpy as np
import pytest

from engine_harness114 import COL, Engine, FeedSnapshot, quiet

T0 = 1790001000              # a 30-minute boundary
ANCHOR = 100.                # close of every quiet bar
LIMIT = 2.0                  # 1.0 x ATR14 of the 1m bars


def bars(tf, now, **columns):
    """Quiet bars of `tf` ending with the bar open at `now`; columns may be per-bar arrays."""
    base = quiet(now, tf=tf)
    values = base.values.copy()
    for name, value in columns.items():
        values[:, COL[name]] = value
    return FeedSnapshot(base.time, base.volume, values, base.seq, base.source_epoch, {})


def publication(now, *, boundary, high=101., touch=True):
    """The board at source time `now`; `boundary` says whether the 30m bar opened at T0 is already there."""
    t30 = T0 if boundary else T0 - 1800
    low15 = np.full(250, 99.)
    if boundary and touch: low15[-2] = 96.          # the closed 15m bar touches the lower WONBI band
    highs = np.full(250, 101.)
    highs[-1] = high
    return {'30m': bars('30m', t30),
            '15m': bars('15m', t30, low=low15),
            '1m': bars('1m', now - now % 60, high=highs),
            '3m': bars('3m', now - now % 180)}


def timeline(live, *, delay, high=101., until=T0 + 400):
    """(source time, LONG machine active) at each publication; the first sees the new bar `delay` late."""
    engine = Engine('SPECIAL4', live=live)
    try:
        engine.send(T0 - 30, publication(T0 - 30, boundary=False))
        rows = []
        times = sorted({T0 + delay, T0 + delay + 1, T0 + 120, T0 + 358, T0 + 359, T0 + 360, T0 + 361, until})
        for now in times:
            port = engine.send(now, publication(now, boundary=True, high=high))
            long = next(m for m in port.machines if m.direction == 'LONG')
            rows.append((now - T0, long.active))
        return rows
    finally:
        engine.close()


def both(**kwargs):
    live, backtest = timeline(True, **kwargs), timeline(False, **kwargs)
    assert live == backtest, 'LIVE and backtest disagree on the same input'
    return dict(backtest)


@pytest.mark.parametrize('delay', [1, 2, 45])
def test_the_cycle_starts_however_late_the_new_bar_is_first_seen_and_ends_six_minutes_after_the_close(delay):
    alive = both(delay=delay)
    assert alive[delay] and alive[358] and alive[359]          # armed on first sight, alive to the last second
    assert not alive[360] and not alive[361]                   # 6 minutes after the 30m close, not after first sight


@pytest.mark.parametrize('high,cancelled', [(ANCHOR + LIMIT - .01, False), (ANCHOR + LIMIT, True), (ANCHOR + LIMIT + 1, True)])
def test_one_atr_favourable_move_inside_the_boundary_bar_cancels_from_the_next_look(high, cancelled):
    alive = both(delay=2, high=high, until=T0 + 100)
    assert alive[2] is True                                    # armed when first seen
    assert alive[120] is (not cancelled)                       # evaluated on the following publication


def test_without_the_lower_band_touch_no_cycle_starts():
    engine = Engine('SPECIAL4')
    try:
        engine.send(T0 - 30, publication(T0 - 30, boundary=False))
        port = engine.send(T0 + 2, publication(T0 + 2, boundary=True, touch=False))
        assert not any(m.active for m in port.machines)
    finally:
        engine.close()
