"""151: closed numerical Facts, and real alert boundaries on synthetic input only.

The expected MA values come from the existing full-window formula. Alert tests
choose crosses, touches and cancellations explicitly; no previous run is a golden.
"""
from pathlib import Path
import socket
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'tests')]

from event_engine.market import COLUMNS, MarketView
from event_engine.model import FeedSnapshot, Input, Kind, Resolution
from indicator_facts import ma_array
from staff_schema import PIPE_VALUE_COLUMNS
from watch_array_facts import WatchMAStore
from watch_ma import MACondition, feature_source_rows, parse_ma_name
from recipe_harness114 import Harness, feed

START = 1_790_002_800
NAMES = ('SMA20', 'WMA23', 'HMA18', 'EMA21', 'EMA37')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('synthetic tests must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def values(rows=700):
    x = np.arange(rows, dtype=float)
    result = np.full((rows, len(PIPE_VALUE_COLUMNS)), 100.)
    result[:, COLUMNS['open']] = 100. + x * .025 + np.sin(x / 13.)
    result[:, COLUMNS['close']] = result[:, COLUMNS['open']] + np.cos(x / 7.) * .4
    result[:, COLUMNS['high']] = np.maximum(result[:, COLUMNS['open']], result[:, COLUMNS['close']]) + 1.
    result[:, COLUMNS['low']] = np.minimum(result[:, COLUMNS['open']], result[:, COLUMNS['close']]) - 1.
    return result


def view(data, seq=1, *, shift=0, epoch='closed151', times=None):
    times = START + (np.arange(len(data), dtype=np.int64) + shift) * 3600 if times is None else times
    return MarketView(FeedSnapshot(times, np.ones(len(data), dtype=np.int64), data, seq, epoch, {}))


def full(data, name):
    family, period = parse_ma_name(name)
    column = COLUMNS['close' if family == 'EMA' else 'open']
    return ma_array(np.array(data[-feature_source_rows(name):, column]), family, period)


def assert_closed(got, data, names=NAMES):
    for name in names:
        np.testing.assert_array_equal(got[name][:-1], full(data, name)[:-1], err_msg=name)
        assert np.isnan(got[name][-1])


@pytest.mark.parametrize('name', NAMES)
def test_closed_values_equal_full_formula_and_forming_updates_do_not_recalculate(name):
    store = WatchMAStore(); data = values()
    assert_closed(store.get('TEST', '1h', view(data), [name], closed=True), data, (name,))
    count = store.computations
    for seq, price in enumerate((110., 150., 80.), 2):
        updated = data.copy()
        updated[-1, COLUMNS['open']] = price
        updated[-1, COLUMNS['close']] = price + 3.
        assert_closed(store.get('TEST', '1h', view(updated, seq), [name], closed=True), updated, (name,))
        assert store.computations == count
        # A later LIVE request must read the changing forming price too.
        actual = store.get('TEST', '1h', view(updated, seq), [name])[name]
        np.testing.assert_allclose(actual, full(updated, name), rtol=0., atol=1e-12, equal_nan=True)
        count = store.computations  # LIVE work is distinct from CLOSED reuse.


@pytest.mark.parametrize('name', NAMES)
def test_full_correction_same_timestamp_recomputes_closed_values(name):
    store = WatchMAStore(); data = values()
    before = store.get('TEST', '1h', view(data), [name], closed=True)[name]
    corrected = data.copy()
    corrected[-2, COLUMNS['open']] += 25.
    corrected[-2, COLUMNS['close']] += 25.
    after = store.get('TEST', '1h', view(corrected, 2), [name], closed=True)[name]
    assert_closed({name: after}, corrected, (name,))
    assert after[-2] != before[-2]


def test_window_move_period_change_and_epoch_use_current_full_inputs():
    store = WatchMAStore(); data = values()
    assert_closed(store.get('TEST', '1h', view(data), NAMES, closed=True), data)
    moved = values(701)[1:]
    assert_closed(store.get('TEST', '1h', view(moved, 2, shift=1), NAMES, closed=True), moved)
    periods = ('SMA27', 'EMA91', 'HMA31')
    assert_closed(store.get('TEST', '1h', view(moved, 3, shift=1), periods, closed=True), moved, periods)
    reconnected = moved.copy(); reconnected[:, COLUMNS['close']] += 7.
    assert_closed(store.get('TEST', '1h', view(reconnected, 4, shift=1, epoch='reconnected'), periods,
                            closed=True), reconnected, periods)


@pytest.mark.parametrize('closed', (False, True))
def test_native_full_correction_is_retained_before_short_window_and_variable_request(closed):
    store = WatchMAStore(); data = values()
    store.get('TEST', '1h', view(data), ['HMA17', 'HMA50'])
    corrected = data.copy()
    # The old correction is outside the next small window, but inside EMA's seed.
    corrected[-100, COLUMNS['open']] += 100.
    corrected[-100, COLUMNS['close']] += 100.
    corrected[:, COLUMNS['hma_17']] = 101.
    native = store.get('TEST', '1h', view(corrected, 2), ['HMA17', 'HMA50'])
    assert native['HMA17'][-2] == 101.
    history = np.vstack((corrected, values(701)[-1:]))
    small = history[-20:]
    small_view = view(small, 3, shift=681)
    store.get('TEST', '1h', small_view, ['HMA17', 'HMA50'])
    actual = store.get('TEST', '1h', small_view, ['HMA17', 'EMA37', 'SMA20'], closed=closed)
    for name in ('EMA37', 'SMA20'):
        expected = full(history, name)
        np.testing.assert_allclose(actual[name][:-1] if closed else actual[name],
                                   expected[:-1] if closed else expected,
                                   rtol=0., atol=1e-12, equal_nan=True)
    assert actual['HMA17'][-1] == small[-1, COLUMNS['hma_17']]


def test_native_and_variable_same_publication_observe_full_correction():
    store = WatchMAStore(); data = values()
    store.get('TEST', '1h', view(data), ['HMA17', 'EMA37'], closed=True)
    corrected = data.copy(); corrected[-2, COLUMNS['close']] += 10.
    corrected_view = view(corrected, 2)
    store.get('TEST', '1h', corrected_view, ['HMA17'])
    actual = store.get('TEST', '1h', corrected_view, ['EMA37'], closed=True)
    assert_closed(actual, corrected, ('EMA37',))


def test_native_pending_history_cannot_seed_short_reconnected_epoch():
    store = WatchMAStore(); data = values()
    store.get('TEST', '1h', view(data), ['HMA17'])
    corrected = data.copy(); corrected[-100, COLUMNS['close']] += 100.
    store.get('TEST', '1h', view(corrected, 2), ['HMA17'])
    restarted = view(data[-20:], 3, shift=680, epoch='new-epoch')
    actual = store.get('TEST', '1h', restarted, ['EMA37'], closed=True)
    assert_closed(actual, data[-20:], ('EMA37',))
    assert not np.isfinite(actual['EMA37']).any()


def test_readiness_and_numeric_comparison_follow_current_and_previous_values():
    store = WatchMAStore(); condition = MACondition('GOLDEN', ('SMA20', 'EMA21'))
    got = store.get('TEST', '1h', view(values(8)), ['SMA20', 'EMA21'], closed=True)
    assert condition.evaluate(got, -2) is None
    features = {'SMA20': np.array([99., 101., 500.]), 'EMA21': np.array([100., 100., -500.])}
    assert condition.evaluate(features, -2) is True
    features['SMA20'][-1] = -1000.
    assert condition.evaluate(features, -2) is True
    features['SMA20'][-2] = 99.
    assert condition.evaluate(features, -2) is False
    features['SMA20'][-2] = 101.; features['SMA20'][-3] = 102.
    assert condition.evaluate(features, -2) is False
    features['SMA20'][-3] = float('nan')
    assert condition.evaluate(features, -2) is None


def hourly(meaning):
    for step in (*meaning['steps'], *meaning.get('cancel_conditions', ())):
        step['tfs'] = ['1h']


def variable_hourly(meaning):
    hourly(meaning)
    for step in (meaning['steps'][0], *meaning.get('cancel_conditions', ())):
        step.update(ma_left='SMA2', ma_right='SMA3')
    meaning['steps'][1].update(ma_family='SMA', slow_period=3)


def alert_feed(bar, *, seq=1, forming_touch=False, outcome='touch', epoch='epoch'):
    """bar0 closes the chosen golden; bar1 either touches, cancels, or misses."""
    n = 6; indices = np.arange(bar - n + 1, bar + 1)
    left = np.where(indices >= 0, 101., 99.)
    low = np.full(n, 101.)
    if outcome == 'cancel': left[indices >= 1] = 99.
    if outcome == 'touch': low[indices == 1] = 99.5
    if not forming_touch: low[-1] = 101.
    result = feed('1h', START + bar * 3600, n, seq=seq,
                  open=101., close=102., low=low, high=103., hma_17=left, hma_50=100.)
    return FeedSnapshot(result.time, result.volume, result.values, seq, epoch, {})


def variable_feed(bar, *, seq=1, forming_touch=False, outcome='touch'):
    indices = np.arange(bar - 5, bar + 1)
    opened = np.where(indices >= 0, 103., 100.)
    if outcome == 'cancel': opened[indices >= 1] = 90.
    low = opened + .5
    if outcome == 'touch': low[indices == 1] = 101.
    if not forming_touch: low[-1] = opened[-1] + .5
    return feed('1h', START + bar * 3600, seq=seq, open=opened, close=opened + 1.,
                low=low, high=opened + 2.)


def publications(outcome='touch', variable=False):
    make = variable_feed if variable else alert_feed
    return [
        (START, make(0, outcome=outcome)),
        (START + 1800, make(0, seq=2, forming_touch=True, outcome=outcome)),
        (START + 3600, make(1, seq=3, outcome=outcome)),
        (START + 4800, make(1, seq=4, forming_touch=True, outcome=outcome)),
        (START + 7200, make(2, seq=5, outcome=outcome)),
        (START + 7260, make(2, seq=6, outcome=outcome)),
        (START + 10800, make(3, seq=7, outcome=outcome)),
    ]


@pytest.mark.parametrize('variable', (False, True))
@pytest.mark.parametrize('outcome, expected', (('touch', 1), ('cancel', 0), ('miss', 0)))
def test_special8_closed_cross_touch_cancel_and_repeated_numeric_match(outcome, expected, variable):
    h = Harness.special('SPECIAL8', mutate=variable_hourly if variable else hourly)
    for index, (stamp, snapshot) in enumerate(publications(outcome, variable)):
        h.publish(stamp, {'1h': snapshot})
        if index < 4: assert not h.messages
        if index == 2: assert h.machines[0].stage == 1
        if index == 4 and outcome == 'cancel': assert h.machines[0].stage == 0
    assert len(h.messages) == expected
    if expected:
        event = h.kernel.messages[0]
        assert event['event_time'] == START + 7200
        assert event['signal_tf'] == '1h'
        assert event['direction'] == 'LONG'


def test_epoch_and_health_do_not_reissue_initial_historical_cross():
    h = Harness.special('SPECIAL8', mutate=hourly)
    h.publish(START, {'1h': alert_feed(0)})
    h.board.health['TEST'] = {'status': 'STALE'}
    h.publish(START + 3600, {'1h': alert_feed(1)})
    assert h.machines[0].stage == 0
    assert all(not observation['ready'] for observation in h.port.observations[0])
    h.board.health['TEST'] = {'status': 'HEALTHY'}
    h.publish(START + 3660, {'1h': alert_feed(1, epoch='reconnected')})
    assert h.machines[0].stage == 0
    h.publish(START + 7200, {'1h': alert_feed(2, epoch='reconnected')})
    assert not h.messages


def test_same_closed_timestamp_full_correction_changes_values_without_reissuing_event():
    h = Harness.special('SPECIAL8', mutate=hourly)
    # Register while this closed bar has no cross.
    initial = alert_feed(1); data = initial.values.copy(); data[:, COLUMNS['hma_17']] = 99.
    h.publish(START + 3600, {'1h': FeedSnapshot(initial.time, initial.volume, data, 1, 'epoch', {})})
    # FULL corrects exactly the same closed timestamp into a golden.
    h.publish(START + 3660, {'1h': alert_feed(1, seq=2)})
    assert h.machines[0].stage == 0
    h.publish(START + 7200, {'1h': alert_feed(2, seq=3)})
    assert not h.messages


def test_forming_touch_keeps_intrabar_false_to_true_edges():
    meaning = {'direction': 'LONG', 'symbols': ['TEST'], 'steps': [
        {'kind': 'MA_PRICE_TOUCH', 'tfs': ['1h'], 'ma_family': 'HMA', 'slow_period': 50,
         'direction': 'LONG', 'bar_state': 'FORMING'}], 'order_mode': 'SEQUENTIAL',
        'persistent': True, 'final': {'kind': 'NOTIFY', 'direction': 'LONG'}}
    from strategy_recipe.contract import execution_plan
    h = Harness(execution_plan(meaning)['meaning'])
    initial = alert_feed(0)
    h.publish(START, {'1h': initial})
    touching = initial.values.copy(); touching[-1, COLUMNS['low']] = 99.5
    changed = FeedSnapshot(initial.time, initial.volume, touching, 2, 'epoch', {})
    h.publish(START + 60, {'1h': changed})
    h.publish(START + 120, {'1h': changed})
    assert len(h.messages) == 1
    next_bar = alert_feed(1, forming_touch=True)
    h.publish(START + 3600, {'1h': next_bar})
    assert len(h.messages) == 2


@pytest.mark.parametrize('closed', (False, True))
def test_price_cross_preserves_each_edge_and_its_actual_boundary(closed):
    from strategy_recipe.contract import execution_plan
    meaning = {'direction': 'LONG', 'symbols': ['TEST'], 'steps': [
        {'kind': 'MA_PRICE_CROSS', 'tfs': ['1h'], 'ma_family': 'HMA', 'slow_period': 50,
         'relation': 'BREAK_UP', 'direction': 'LONG', 'bar_state': 'CLOSED' if closed else 'FORMING'}],
        'order_mode': 'SEQUENTIAL', 'persistent': True, 'final': {'kind': 'NOTIFY', 'direction': 'LONG'}}
    h = Harness(execution_plan(meaning)['meaning'])
    if closed:
        observations = [(0, 0, 99.), (0, 60, 101.), (1, 0, 101.), (1, 60, 90.), (2, 0, 99.), (3, 0, 101.)]
        expected = [START + 3600, START + 10800]
    else:
        observations = [(0, 0, 99.), (0, 60, 101.), (0, 120, 101.), (0, 180, 99.), (0, 240, 101.)]
        expected = [START + 60, START + 240]
    for seq, (bar, offset, live_price) in enumerate(observations, 1):
        indices = np.arange(bar - 5, bar + 1)
        closes = np.where(indices == 0, 101., np.where(indices == 2, 101., 99.)) if closed else np.full(6, 99.)
        closes[-1] = live_price
        snapshot = feed('1h', START + bar * 3600, seq=seq, open=99., close=closes,
                        low=89., high=110., hma_50=100.)
        h.publish(START + bar * 3600 + offset, {'1h': snapshot})
    assert [event['event_time'] for event in h.kernel.messages] == expected


@pytest.mark.parametrize('variable', (False, True))
@pytest.mark.parametrize('outcome, expected', (('touch', 1), ('cancel', 0), ('miss', 0)))
def test_real_live_and_replay_emit_the_intended_hourly_alert(outcome, expected, variable):
    from event_application import create_event_engine
    from event_engine.replay import replay
    from engine_harness114 import CONFIG, SYMBOL, plugin
    def make(live):
        return create_event_engine(dict(CONFIG), symbols=(SYMBOL,), selection=['SPECIAL8'],
            plugins={'SPECIAL8': plugin('SPECIAL8', variable_hourly if variable else hourly)}, backtest=not live)
    events = []
    for seq, (stamp, snapshot) in enumerate(publications(outcome, variable), 1):
        minute = feed('1m', stamp - stamp % 60, open=102., close=102., high=103., low=101.)
        events.append(Input('synthetic151', seq, stamp * 1000, Kind.MARKET_BUNDLE,
                            {'symbol': SYMBOL, 'feeds': {'1h': snapshot, '1m': minute}}, 0))
    live = make(True)
    for item in events:
        live.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                          source_time=item.source_time, payload=item.payload)
        live.run()
    replayed = make(False)
    replay(replayed, events, Resolution.TICK)
    assert not live.error_log and not replayed.error_log
    def notices(engine):
        result = []
        for signal in engine.signals:
            content = signal.payload.get('content', {})
            if content.get('type') != 'NOTIFICATION': continue
            event = content
            if event.get('signal_strategy') == 'SPECIAL8':
                result.append((event['event_time'], event['direction'], event['signal_tf'], event['signal_price']))
        return result
    actual = notices(live)
    assert actual == notices(replayed)
    assert len(actual) == expected
    if expected: assert actual == [(START + 7200, 'LONG', '1h', 102.)]
