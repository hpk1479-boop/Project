"""A base change refills the recipe on that frame.

수정본144: a base frame always starts again from the recipe, as if it had been written on that
frame; values edited before are dropped (143 moved the edited policy instead).
수정본146: frames come only from the base frame; a policy that sets a frame of its own is refused.
"""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, manager  # noqa: E402
from event_backtest.base_frames import moved_plugins  # noqa: E402
from event_backtest.virtual_contract import required_timeframes  # noqa: E402
from event_backtest.virtual_defaults import recipe_on_base, strategy_profile, target_policy  # noqa: E402
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS as C  # noqa: E402
from strategy_recipe import registry  # noqa: E402
from strategy_recipe.contract import execution_plan  # noqa: E402


def test_special9_policy_follows_each_base_change_and_returns_to_its_recipe():
    profile = strategy_profile('SPECIAL9')
    original = deepcopy(profile)
    two = recipe_on_base(profile, '2m')
    assert two['tf'] == '2m' and two['filters'][1]['tf'] == '20m'
    three = recipe_on_base(profile, '3m')
    assert three['tf'] == '3m' and three['filters'][1]['tf'] == '30m'
    assert recipe_on_base(profile, 'SIGNAL') == recipe_on_base(profile, '1m') == profile['default']
    assert profile == original
    assert two['tf'] == '2m' and two['filters'][1]['tf'] == '20m'


def test_a_policy_with_a_frame_of_its_own_is_refused():
    # Only the base frame chooses frames; the refill gives the recipe's frames on it.
    profile = strategy_profile('SPECIAL9')
    for row in ('filter', 'stop'):
        policy = recipe_on_base(profile, '2m')
        (policy['filters'][1] if row == 'filter' else policy['stop'])['tf'] = '15m'
        with pytest.raises(ValueError, match='기준 프레임으로만'):
            target_policy(['SPECIAL9'], policy=policy)
    refilled = recipe_on_base(profile, '3m')
    assert refilled['filters'][1]['tf'] == '30m' and refilled['stop']['tf'] == 'SIGNAL'
    assert target_policy(['SPECIAL9'], policy=refilled) == refilled


def test_special8_on_4h_is_its_recipe_and_its_stop_frame_is_the_base_frame():
    profile = strategy_profile('SPECIAL8')
    changed = recipe_on_base(profile, '4h')
    assert changed['tf'] == '4h' and changed['stop']['tf'] == 'SIGNAL'
    assert target_policy(['SPECIAL8'], policy=changed) == changed
    changed['stop']['tf'] = '15m'
    with pytest.raises(ValueError, match='기준 프레임으로만'):
        target_policy(['SPECIAL8'], policy=changed)


def test_a_base_frame_returns_every_value_to_the_recipe():
    profile = strategy_profile('SPECIAL9')
    expected = recipe_on_base(profile, '2m')
    assert expected == {**deepcopy(profile['default']), 'tf': '2m', 'filters': [
        profile['default']['filters'][0], {**profile['default']['filters'][1], 'tf': '20m'}, profile['default']['filters'][2]]}
    # Nothing edited before reaches it: it is built from the recipe alone, as a fresh copy.
    expected['stop']['multiplier'] = 2.0
    assert recipe_on_base(profile, '2m')['stop']['multiplier'] == 1.0
    assert profile['default']['stop']['multiplier'] == 1.0


def test_returning_from_4h_does_not_add_frame_fields_to_frameless_conditions():
    profile = strategy_profile('SPECIAL8')
    assert 'tf' not in recipe_on_base(profile, '4h')['conditions'][0]
    assert recipe_on_base(profile, 'SIGNAL') == profile['default']


@pytest.mark.parametrize('name,target', [('SPECIAL1', '2m'), ('SPECIAL4', '2m'),
                                        ('SPECIAL9', '4h'), ('SPECIAL9', '8h')])
def test_an_unavailable_base_change_is_refused(name, target):
    profile = strategy_profile(name)
    with pytest.raises(ValueError):
        recipe_on_base(profile, target)


def market_view(tf, rows, names):
    values = np.ones((len(rows), len(C)))
    for i, row in enumerate(rows):
        for name, value in zip(names, row[1:]):
            values[i, C.index(name)] = value
    return SimpleNamespace(time=np.asarray([row[0] for row in rows], dtype=np.int64),
                           values=values, columns=C)


def trend_view(tf, rising):
    seconds = {'15m': 900, '20m': 1200}[tf]
    step = 1. if rising else -1.
    opens = 2400. + step * np.arange(40)
    rows = [(seconds * (i - 39), value, value + 1, value - 1, value, value - 5 * step)
            for i, value in enumerate(opens)]
    return market_view(tf, rows, ('open', 'high', 'low', 'close', 'hma_50'))


def virtual_trade(policy, *, fifteen_rising, twenty_rising):
    alert = dict(signal_id='s', strategy='SPECIAL9', symbol='TEST', tf='2m', direction='LONG',
                 time_ms=0, signal_source='SIGNAL', signal_tf='2m', env_tf='2m', signal_price=100.5)
    calculator = VirtualEntry([alert], 0, policy)
    # A valid two-minute touch closes bullish above HMA50 at 120 seconds.
    rows = [(-120 * k, 101.8, 102.5, 101.5, 102., 101., 100.) for k in range(30, 0, -1)]
    rows += [(0, 101.5, 102., 99.8, 101.9, 101., 100.),
             (120, 101.9, 102.4, 101.6, 102.2, 101., 100.)]
    minute_rows = []
    for start, op, hi, lo, close, *_ in rows:
        middle = (op + close) / 2
        minute_rows.extend(row for row in [(start, op, hi, lo, middle),
                                           (start + 60, middle, hi, lo, close)] if row[0] <= 120)
    feeds = {'2m': market_view('2m', rows, ('open', 'high', 'low', 'close', 'hma_17', 'hma_50')),
             '1m': market_view('1m', minute_rows, ('open', 'high', 'low', 'close')),
             '15m': trend_view('15m', fifteen_rising), '20m': trend_view('20m', twenty_rising)}
    calculator.observe(120000, feeds, end_ms=10 ** 9)
    return calculator.trades[0], alert


@pytest.mark.parametrize('fifteen,twenty,status', [(False, True, 'ENTERED'), (True, False, 'PASS_ENV')])
def test_a_base_change_uses_20m_and_a_policy_keeping_15m_on_2m_is_refused(fifteen, twenty, status):
    profile = strategy_profile('SPECIAL9')
    changed = recipe_on_base(profile, '2m')
    trade, alert = virtual_trade(changed, fifteen_rising=fifteen, twenty_rising=twenty)
    assert required_timeframes(changed, alert) == {'1m', '2m', '20m'}
    assert trade['status'] == status
    if status == 'ENTERED':
        assert trade['entry_time'] == 120000 and trade['entry_price'] == 101.9
    explicit = deepcopy(profile['default'])
    explicit['tf'] = '2m'                       # the 1m recipe's 15m trend kept on a 2m base frame
    with pytest.raises(ValueError, match='기준 프레임으로만'):
        target_policy(['SPECIAL9'], policy=explicit)


@pytest.mark.parametrize('tf,seconds', [('2m', 120), ('5m', 300)])
def test_special8_runs_as_a_strategy_written_on_the_new_frame_with_its_own_atr_stop(tf, seconds):
    # This independent recipe is written directly on the selected frame rather
    # than obtained by the rebasing implementation under test.
    native = {'direction': 'LONG', 'symbols': ['TEST'], 'order_mode': 'SEQUENTIAL', 'persistent': True,
              'steps': [{'kind': 'MA_CROSS', 'tfs': [tf], 'ma_left': 'HMA17', 'ma_right': 'HMA50',
                         'direction': 'LONG', 'bar_state': 'CLOSED'},
                        {'kind': 'MA_PRICE_TOUCH', 'tfs': [tf], 'ma_family': 'HMA', 'slow_period': 50,
                         'direction': 'LONG', 'bar_state': 'CLOSED'}],
              'cancel_conditions': [{'kind': 'MA_CROSS', 'tfs': [tf], 'ma_left': 'HMA17', 'ma_right': 'HMA50',
                                     'direction': 'SHORT', 'bar_state': 'CLOSED'}],
              'final': {'kind': 'NOTIFY', 'direction': 'LONG'}}
    expected = Harness(execution_plan(native)['meaning'], namespace='SPECIAL8')
    plugin = moved_plugins(registry.load_plugins({'SYMBOLS': 'TEST'}, ['SPECIAL8'], part='Part2'),
                           {'SPECIAL8': tf})['SPECIAL8']
    actual = Harness.__new__(Harness)
    actual.manager, actual.kernel = manager()
    actual.board = actual.kernel.board
    plugin.register(actual.manager)
    actual.port = actual.manager._special_watch_handlers['SPECIAL8']
    actual.manager.commands.clear()
    start = 1_800_000_000
    rows = []
    for i in range(35):
        # The bearish touch at 31 is the preparation signal; the following
        # bullish candle at 32 confirms entry at the opening of candle 33.
        candle = ((101.5, 101.8, 99.5, 100.5) if i == 31 else
                  (101.0, 102., 100.2, 101.9) if i == 32 else
                  (101.9, 102.5, 101.5, 102.))
        rows.append((start + i * seconds, *candle, 101. if i >= 30 else 99., 100.))
    names = ('open', 'high', 'low', 'close', 'hma_17', 'hma_50')
    for current in range(29, 34):
        shown = rows[:current + 1]
        snapshot = feed(tf, shown[-1][0], n=len(shown),
                        **{name: np.array([row[index + 1] for row in shown]) for index, name in enumerate(names)})
        for harness in (expected, actual):
            # No 1m feed is present: all strategy conditions must read tf.
            harness.publish(start + current * seconds, {tf: snapshot})
            assert [int(m['event_time']) for m in harness.kernel.messages] == (
                [] if current < 32 else [start + 32 * seconds])
    assert actual.kernel.messages[0]['signal_tf'] == expected.kernel.messages[0]['signal_tf'] == tf
    profile = strategy_profile('SPECIAL8')
    policy = recipe_on_base(profile, tf)
    alert = dict(signal_id='s8', strategy='SPECIAL8', symbol='TEST', tf=tf, direction='LONG',
                 time_ms=(start + 32 * seconds) * 1000, signal_source='SIGNAL', signal_tf=tf,
                 env_tf=tf, signal_price=101.)
    assert required_timeframes(policy, alert) == {'1m', tf}
    calculator = VirtualEntry([alert], 0, policy)
    for current in (32, 33):
        shown = rows[:current + 1]
        # Deliberately incompatible M1 history: its ATR is 20, its HMA order is
        # bearish and its closed candles are bearish. None may decide entry.
        now = start + current * seconds
        one_minute = [(now - 60 * k, 110., 120., 100., 100., 99., 101.) for k in range(70, -1, -1)]
        calculator.observe(now * 1000, {tf: market_view(tf, shown, names),
                                       '1m': market_view('1m', one_minute, names)}, end_ms=10 ** 15)
        assert calculator.trades[0]['status'] == ('WAITING' if current == 32 else 'ENTERED')
    trade = calculator.trades[0]
    assert trade['entry_time'] == (start + 33 * seconds) * 1000
    assert trade['entry_price'] == 101.9
    # Independent Wilder ATR14 recurrence over the native frame's completed
    # candles. M1's 20-point ATR would give a distinctly different stop.
    atr = rows[0][2] - rows[0][3]
    for before, row in zip(rows[:32], rows[1:33]):
        tr = max(row[2] - row[3], abs(row[2] - before[4]), abs(row[3] - before[4]))
        atr = atr * (13 / 14) + tr / 14
    assert trade['risk'] == pytest.approx(atr)
    assert trade['stop_price'] == pytest.approx(101.9 - atr)
