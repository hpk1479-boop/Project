"""SPECIAL4 as shipped (30-minute close, snapshot ATR, 6-minute life, 1x ATR favourable move).

Every boundary is exercised with the first publication of the new bar arriving
on time and late, because live data never arrives exactly on the boundary.
"""
import numpy as np
import pytest

from recipe_harness114 import Harness, feed

T0 = 1790001000          # a 30-minute boundary (multiple of 1800)
ANCHOR = 100.            # close of the 30m bar that ended at T0
ATR_1M = np.array([.1, .2, .3, .4, .5, .6])   # ATR14 per 1m bar; index -2 is the last closed bar
LIMIT = .5               # 1.0 x the ATR of the last closed 1m bar (0.5)
WONBI = {'wonbi_mid': 100., 'wonbi_upper': 110., 'wonbi_lower': 100.}
BANDS = {tf: WONBI for tf in ('30m', '15m')}


def quiet(now):
    """A publication before the boundary: nothing touches, the 30m bar is still forming."""
    return {'30m': feed('30m', now - now % 1800, close=ANCHOR, low=101., high=102.),
            '15m': feed('15m', now - now % 900, close=ANCHOR, low=101., high=102.),
            '1m': feed('1m', now - now % 60, close=ANCHOR, low=101., high=102.)}


def after(open_1m, highs, *, touch15=99.5, touch30=101., seq=1):
    """The board once the 30m bar that opened at T0 exists; `highs` are the 1m highs up to open_1m.

    The closed 15m bar touches the lower band by default (the LONG setup) and the
    closed 30m bar does not. Bars before the boundary all reach 101, far above the
    window, so only the window itself can trigger the favourable-move check.
    """
    return {'30m': feed('30m', T0, close=ANCHOR, low=[101., 101., 101., 101., touch30, 101.], high=102., seq=seq),
            '15m': feed('15m', T0, close=ANCHOR, low=[101., 101., 101., 101., touch15, 101.], high=102., seq=seq),
            '1m': feed('1m', open_1m, close=ANCHOR, low=99.9, high=np.asarray(highs, dtype=float), seq=seq)}


def highs_until_boundary(last):
    """Six 1m highs ending with the boundary bar (opened at T0)."""
    return [101., 101., 101., 101., 101., last]


def long_machine(h): return next(m for m in h.machines if m.direction == 'LONG')


def started(delay, *, high=100.2, **kwargs):
    h = Harness.special('SPECIAL4')
    h.publish(T0 - 30, quiet(T0 - 30), atr={'1m': ATR_1M}, wonbi=BANDS)
    h.publish(T0 + delay, after(T0, highs_until_boundary(high), **kwargs), atr={'1m': ATR_1M}, wonbi=BANDS)
    return h


@pytest.mark.parametrize('delay', [0, 0.3, 2, 59])
def test_setup_starts_on_the_new_30m_bar_however_late_it_is_first_seen(delay):
    h = started(delay)
    assert long_machine(h).active, f'first seen {delay}s after the 30m close'
    assert [c['direction'] for c in h.watch_commands()] == ['LONG']


def test_no_setup_without_the_15m_touch_or_with_the_30m_touch():
    assert not long_machine(started(0, touch15=101.)).active
    assert not long_machine(started(0, touch30=99.)).active


def test_snapshots_are_the_closed_bars_before_the_boundary():
    machine = long_machine(started(0))
    assert machine.snapshots == {'anchor': ANCHOR, 'atr': 0.5}


@pytest.mark.parametrize('delay', [0, 2])
@pytest.mark.parametrize('seconds,alive', [(359, True), (359.9, True), (360, False), (361, False)])
def test_six_minutes_are_counted_from_the_30m_close(delay, seconds, alive):
    h = started(delay)
    machine = long_machine(h)
    h.publish(T0 + seconds, after(T0, highs_until_boundary(100.2), seq=2), atr={'1m': ATR_1M}, wonbi=BANDS)
    assert machine.active is alive, f'{seconds}s after the close, first seen {delay}s late'


@pytest.mark.parametrize('delay', [0, 2])
@pytest.mark.parametrize('extreme,cancelled', [(100.49, False), (100.5, True), (101., True)])
def test_one_atr_favourable_move_counts_from_the_boundary_bar(delay, extreme, cancelled):
    h = started(delay, high=extreme)
    h.port.poll()           # the next evaluation, as a final OZ event's own poll would be
    assert long_machine(h).active is (not cancelled), f'extreme {extreme}, first seen {delay}s late'


def test_later_and_still_forming_bars_count_toward_the_move():
    h = started(0)
    machine = long_machine(h)
    highs = [101., 101., 101., 101., 100.2, 100.49]            # bars at T0 and T0+60 (forming)
    h.publish(T0 + 60, after(T0 + 60, highs), atr={'1m': ATR_1M}, wonbi=BANDS)
    assert machine.active
    highs[-1] = 100.5
    h.publish(T0 + 61, after(T0 + 60, highs, seq=2), atr={'1m': ATR_1M}, wonbi=BANDS)
    assert not machine.active
