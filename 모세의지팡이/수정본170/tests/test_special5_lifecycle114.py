"""SPECIAL5 as shipped: a parent OZ and confirmed EMA start child OZ watches that live a
configured number of their own bars and end on an opposite HMA cross.

Replaces the checks that used to live in the retired tests of the old Python SPECIAL5.
"""
import numpy as np
import pytest

from recipe_harness114 import Harness, feed, SECONDS, SYMBOL

T = 1790001000          # multiple of 1800: also of every timeframe used here
SETUP = T + 10          # when the parent OZ is seen
TFS = ('1m', '2m', '3m', '5m', '15m')


def bars(tf, now, *, cross_down=False, n=12):
    """tf bars up to `now`: EMA50 above EMA200 (LONG trend) and HMA6 above HMA17 throughout,
    unless the last closed bar crosses HMA6 below HMA17."""
    hma6 = np.full(n, 2402.)
    if cross_down: hma6[-2:] = 2398.
    return feed(tf, now - now % SECONDS[tf], n, ema_50=2401., ema_200=2400., hma_6=hma6, hma_17=2400.)


def board(now, *, down=()):
    return {tf: bars(tf, now, cross_down=tf in down) for tf in TFS}


def started(config=None):
    h = Harness.special('SPECIAL5', config=config)
    h.publish(T + 5, board(T + 5))
    h.publish(SETUP, board(SETUP))
    parent = next(m for m in h.machines if m.direction == 'LONG' and m.meaning['steps'][0]['tfs'] == ['5m'])
    h.parent_oz(parent.meaning['steps'][0], '5m', SETUP)
    return h


def children(h, parent_tf='5m'):
    return {m.final_tf: m for m in h.machines if m.direction == 'LONG' and m.meaning['steps'][0]['tfs'] == [parent_tf]}


def test_parent_oz_and_confirmed_ema_arm_one_child_per_final_timeframe():
    h = started()
    kids = children(h)
    assert set(kids) == {'1m', '2m', '3m'} and all(m.active for m in kids.values())
    orders = sorted((c['timeframes'][0], c['direction'], c['validation_mode'], c['trigger_mode']) for c in h.watch_commands())
    assert orders == [('1m', 'LONG', 'BLIND', 'OZ'), ('2m', 'LONG', 'BLIND', 'OZ'), ('3m', 'LONG', 'BLIND', 'OZ')]
    assert not children(h, '15m')['1m'].active                  # other parents stay idle
    assert not any(m.active for m in h.machines if m.direction == 'SHORT')


def test_an_ema_that_is_not_confirmed_yet_arms_nothing():
    h = Harness.special('SPECIAL5')
    flat = {tf: feed(tf, SETUP - SETUP % SECONDS[tf], 12, ema_50=2400., ema_200=2400., hma_6=2402., hma_17=2400.) for tf in TFS}
    h.publish(T + 5, flat); h.publish(SETUP, flat)
    parent = next(m for m in h.machines if m.direction == 'LONG' and m.meaning['steps'][0]['tfs'] == ['5m'])
    h.parent_oz(parent.meaning['steps'][0], '5m', SETUP)
    assert not any(m.active for m in h.machines) and not h.watch_commands()


@pytest.mark.parametrize('direction,closed_ok,live_ok,armed', [
    ('LONG', 2401., -1000., True),        # the closed bar decides; a live bar that disagrees is ignored
    ('LONG', 2399., 1000., False),        # ... and one that agrees does not arm it
    ('LONG', 2400., 1000., False),        # equal is not above
    ('SHORT', 2399., 1000., True)])
def test_the_ema_is_read_on_the_last_closed_bar_never_the_live_one(direction, closed_ok, live_ok, armed):
    h = Harness.special('SPECIAL5')
    fast = np.full(12, 2400.); fast[-2], fast[-1] = closed_ok, live_ok
    flat = {tf: feed(tf, SETUP - SETUP % SECONDS[tf], 12, ema_50=fast, ema_200=2400., hma_6=2402., hma_17=2400.) for tf in TFS}
    h.publish(T + 5, flat); h.publish(SETUP, flat)
    parent = next(m for m in h.machines if m.direction == direction and m.meaning['steps'][0]['tfs'] == ['5m'])
    h.parent_oz(parent.meaning['steps'][0], '5m', SETUP, direction=direction)
    assert any(m.active for m in h.machines if m.direction == direction and m.meaning['steps'][0]['tfs'] == ['5m']) is armed


def test_a_newer_parent_of_one_direction_leaves_the_other_direction_alone():
    h = started({'MAX_BARS_AFTER_B0': '10'})
    h.publish(SETUP + 60, board(SETUP + 60))
    short = next(m for m in h.machines if m.direction == 'SHORT' and m.meaning['steps'][0]['tfs'] == ['15m'])
    # Only the SHORT parent's own timeframe turns down; the LONG group's timeframes keep their trend.
    now = SETUP + 61
    h.publish(now, {**board(now), '15m': feed('15m', now - now % 900, 12, ema_50=2399., ema_200=2400., hma_6=2398., hma_17=2400.)})
    h.parent_oz(short.meaning['steps'][0], '15m', now, direction='SHORT')
    assert all(m.active for m in children(h, '5m').values())          # the LONG group lives on
    assert any(m.active for m in h.machines if m.direction == 'SHORT')


def test_a_delivered_final_ends_the_whole_group_a_failed_delivery_keeps_it():
    h = started({'MAX_BARS_AFTER_B0': '10'})
    kids = children(h)
    h.manager.notifier.send = lambda *args, **kwargs: False
    assert not h.final_oz(kids['1m'], SETUP + 30)['ok']                  # nothing was sent: the setup stays
    assert all(m.active for m in kids.values())
    del h.manager.notifier.send                                           # the real sender again
    assert h.final_oz(kids['1m'], SETUP + 31)['ok'] and len(h.messages) == 1
    assert not any(m.active for m in kids.values())                       # first success ends every sibling
    assert {c['watch_id'] for c in h.cancel_commands()} >= {h.port._wid(kids['2m']), h.port._wid(kids['3m'])}


def first_cancel(h, tf, *, start=T + 60, step=60, stop=T + 1500):
    """The first publication time at which the child on `tf` no longer lives, and the one before it."""
    machine = children(h)[tf]
    before = None
    for now in range(int(start) + 5, int(stop), step):
        h.publish(now, board(now))
        if not machine.active: return now, before
        before = now
    return None, before


@pytest.mark.parametrize('config,limit', [({'MAX_BARS_AFTER_B0': '2'}, 2), ({'MAX_BARS_AFTER_B0': '4'}, 4),
    (None, 10), ({'MAX_BARS_AFTER_B0': ''}, 10), ({'MAX_BARS_AFTER_B0': 'abc'}, 10),
    ({'MAX_BARS_AFTER_B0': '0'}, 10), ({'MAX_BARS_AFTER_B0': '-3'}, 10)])
def test_a_child_lives_the_configured_number_of_its_own_closed_bars(config, limit):
    h = started(config)
    cancelled, before = first_cancel(h, '1m')
    # Bars opened after the setup (T+10) are T+60, T+120, ...: the bar that opens at T+60*(limit+2) is
    # the first with limit+1 closed bars behind it, and `closed > limit` ends the child there.
    assert cancelled == T + 60 * (limit + 2) + 5, (config, cancelled)
    assert before == cancelled - 60


def test_the_limit_is_counted_per_child_timeframe():
    h = started({'MAX_BARS_AFTER_B0': '2'})
    kids = children(h)
    alive = {}
    for now in range(T + 65, T + 800, 60):
        h.publish(now, board(now))
        for tf, machine in kids.items():
            alive.setdefault(tf, now) if not machine.active else None
    # 1m: 3 closed 1m bars after the setup; 2m: 3 closed 2m bars; 3m: 3 closed 3m bars.
    assert alive == {'1m': T + 245, '2m': T + 485, '3m': T + 725}


def test_the_parents_opposite_hma_cross_ends_every_child_and_leaves_other_parents():
    h = started({'MAX_BARS_AFTER_B0': '10'})
    other = children(h, '15m')
    h.publish(SETUP + 60, board(SETUP + 60))
    assert all(m.active for m in children(h).values())
    h.publish(SETUP + 360, board(SETUP + 360, down=('5m',)))
    assert not any(m.active for m in children(h).values())
    cancelled = {c['watch_id'] for c in h.cancel_commands()}
    assert len(cancelled) == 3 and not any(m.active for m in other.values())


def test_a_child_timeframes_opposite_cross_ends_only_that_child():
    h = started({'MAX_BARS_AFTER_B0': '10'})
    h.publish(SETUP + 60, board(SETUP + 60))
    h.publish(SETUP + 120, board(SETUP + 120, down=('1m',)))
    kids = children(h)
    assert (kids['1m'].active, kids['2m'].active, kids['3m'].active) == (False, True, True)


def test_a_newer_parent_of_the_same_direction_replaces_the_older_group_only():
    h = started({'MAX_BARS_AFTER_B0': '10'})
    h.publish(SETUP + 60, board(SETUP + 60))
    newer = next(m for m in h.machines if m.direction == 'LONG' and m.meaning['steps'][0]['tfs'] == ['15m'])
    h.parent_oz(newer.meaning['steps'][0], '15m', SETUP + 60)
    assert not any(m.active for m in children(h, '5m').values())
    assert all(m.active for m in children(h, '15m').values())
