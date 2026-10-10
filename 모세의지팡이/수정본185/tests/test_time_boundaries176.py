"""176: SWEEP event windows start at confirmation; session levels exist at their boundary."""
import datetime as dt

import numpy as np
import pytest

from recipe_harness114 import Harness, SYMBOL, feed
from strategy_recipe.contract import execution_plan
from event_engine.market import MarketView, COLUMNS
from event_engine.model import FeedSnapshot
from event_engine.sweep_levels import external_levels, LevelStore
from event_engine.sweep_runtime import LiquidityDetector
from strategy_SWEEP import SweepSpec

H = 1_790_002_800
HOUR = 3600
LEVEL = dict(id='PDH:test', direction='SHORT', level_code='PDH', level_name='PDH', price=2405.)


def view(times, *, high=2410., low=2390., seq=1):
    times = np.asarray(times, dtype=np.int64)
    values = np.full((len(times), len(COLUMNS)), 2400.)
    values[:, COLUMNS['high']] = high
    values[:, COLUMNS['low']] = low
    return MarketView(FeedSnapshot(times, np.ones(len(times), dtype=np.int64), values, seq, 'epoch', {}))


def recent_harness(window):
    return Harness(execution_plan({'symbols': [SYMBOL], 'direction': 'SHORT',
        'steps': [{'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['1h'], 'level': 'PDH', 'recent': window}],
        'final': {'kind': 'NOTIFY'}})['meaning'])


def touch(h, observed, source=None):
    step = h.port.meaning['steps'][0]
    wid = h.port._dependency_id(SYMBOL, '1h', 'SWEEP', step)
    detector = LiquidityDetector(SweepSpec(wid, SYMBOL, '1h', ('PDH',)))
    source = source or MarketView(feed('1h', H + HOUR, n=20, high=2410., low=2390.))
    event, = detector.process(source, [LEVEL], observed_at=observed)
    return detector, event


def publish_fact(h, observed, event, *, source=None):
    source = source or feed('1h', H + HOUR, n=20, high=2410., low=2390.)
    h.publish(observed, {'1h': source, '1m': feed('1m', int(observed // 60) * 60, n=100)})
    step = h.port.meaning['steps'][0]
    wid = h.port._dependency_id(SYMBOL, '1h', 'SWEEP', step)
    h.port.handle_fact(dict(kind='FACT_SNAPSHOT', strategy='SWEEP', symbol=SYMBOL, source_tf='1h',
        watch_id=wid, source_health={'sources': {'1h': 'epoch'}}, facts=[event]))
    return h.port.observations[0][0]


@pytest.mark.parametrize('delay', [0, 5.5])
@pytest.mark.parametrize('elapsed,expected', [(0, True), (1799, True), (1800, True), (1800.001, False)])
def test_recent_seconds_start_at_the_actual_closed_bar_judgment(delay, elapsed, expected):
    h = recent_harness({'seconds': 1800})
    observed = H + HOUR + delay
    _, event = touch(h, observed)
    assert event['touch_time'] == event['event_time'] == H
    assert event['touch_confirmed_at'] == observed
    assert publish_fact(h, observed, event)['matched']
    assert publish_fact(h, observed + elapsed, event)['matched'] is expected


@pytest.mark.parametrize('elapsed,expected', [(0, True), (114.999, True), (115, False)])
def test_a_closed_hour_touch_counts_the_following_real_minutes(elapsed, expected):
    h = recent_harness({'bars': 2, 'tf': '1m'})
    observed = H + HOUR + 5
    _, event = touch(h, observed)
    assert publish_fact(h, observed + elapsed, event)['matched'] is expected


def test_forming_touch_is_not_an_event_and_confirmation_is_not_refreshed():
    detector = LiquidityDetector(SweepSpec('w', SYMBOL, '1h', ('PDH',)))
    forming = view([H - HOUR, H, H + HOUR], high=[2400., 2400., 2410.])
    assert detector.process(forming, [LEVEL], observed_at=H + HOUR + 10) == []
    closed = view([H, H + HOUR, H + 2 * HOUR], high=[2400., 2410., 2400.])
    event, = detector.process(closed, [LEVEL], observed_at=H + 2 * HOUR + 5)
    assert event['touch_time'] == H + HOUR
    assert event['touch_confirmed_at'] == H + 2 * HOUR + 5
    restored = LiquidityDetector(detector.spec, detector.export_state())
    assert restored.process(closed, [LEVEL], observed_at=H + 3 * HOUR) == []
    assert restored.active_touch_events()[0]['touch_confirmed_at'] == event['touch_confirmed_at']


def test_a_weekend_confirmation_and_restart_do_not_rejuvenate_a_retained_fact():
    friday = H
    monday = H + 3 * 86400
    source = view([friday - HOUR, friday, monday])
    h = recent_harness({'seconds': 1800})
    detector, event = touch(h, monday + 5, source)
    assert publish_fact(h, monday + 5, event, source=source.snapshot)['matched']
    restored = LiquidityDetector(detector.spec, detector.export_state())
    h2 = recent_harness({'seconds': 1800})
    assert h2.port.restore(h.port.checkpoint())
    old, = restored.active_touch_events(restored=True)
    assert not publish_fact(h2, monday + 1806, old, source=source.snapshot)['matched']
    # An older state has no observation timestamp: the known following bar is a stable
    # fallback, never the current poll. No implementation or missing historical bar is invented.
    legacy = {k: v for k, v in old.items() if k != 'touch_confirmed_at'}
    assert h2.port._touch_occurrence(legacy, source, '1h') == (monday, friday + HOUR)
    assert h2.port._touch_occurrence(legacy, view([monday, monday + HOUR, monday + 2 * HOUR]), '1h')[0] == friday


def test_the_actual_startup_serializer_preserves_confirmation_time():
    import json
    import durable_protocol
    import strategy_SWEEP
    from event_engine.restoration import sweep_state
    from event_engine.sweep_runtime import SweepRuntime
    h = recent_harness({'seconds': 1800})
    detector, event = touch(h, H + HOUR + 5.5)
    saved = {'version': 1, 'detectors': {detector.spec.watch_id: {
        'spec': {'symbol': SYMBOL, 'source_tf': '1h', 'levels': ['PDH']}, **detector.export_state()}}}
    state = {}
    sweep_state(strategy_SWEEP, durable_protocol, state, {'sweep_detector_state.json': json.dumps(saved)})
    restored = SweepRuntime(state['core']).detectors[detector.spec.watch_id]
    old, = restored.active_touch_events(restored=True)
    assert old['touch_confirmed_at'] == event['touch_confirmed_at']
    assert not publish_fact(h, H + HOUR + 1806, old)['matched']


def test_a_sequential_window_can_follow_the_newly_confirmed_touch():
    h = Harness(execution_plan({'symbols': [SYMBOL], 'direction': 'SHORT', 'order_mode': 'SEQUENTIAL',
        'within': {'seconds': 1800}, 'steps': [
            {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['1h'], 'level': 'PDH'},
            {'kind': 'BAR_CLOSE', 'tfs': ['1m']}], 'final': {'kind': 'NOTIFY'}})['meaning'])
    _, event = touch(h, H + HOUR + 5)
    publish_fact(h, H + HOUR + 5, event)
    assert not h.messages  # the simultaneous/earlier minute close is not a follow-up
    publish_fact(h, H + HOUR + 60, event)
    assert len(h.messages) == 1


KST = dt.timezone(dt.timedelta(hours=9))


def local(hour, minute=0, day=15):
    return int(dt.datetime(2026, 7, day, hour, minute, tzinfo=KST).timestamp())


def session_view(now, forming_high=9999.):
    times = np.arange(local(0, day=14), now + 1, 300, dtype=np.int64)
    highs = np.full(len(times), 110.)
    highs[-1] = forming_high
    return view(times, high=highs, low=90.)


@pytest.mark.parametrize('hour,minute,session', [(15, 55, None), (16, 0, 'LONDON'), (16, 5, 'LONDON'),
    (20, 55, 'LONDON'), (21, 0, 'NY'), (21, 5, 'NY'), (0, 0, 'NY'), (5, 55, 'NY'), (6, 0, None)])
def test_session_level_changes_on_the_boundary_and_uses_only_closed_prices(hour, minute, session):
    now = local(hour, minute, day=16 if hour < 6 else 15)
    levels = {x['level_code']: x for x in external_levels({'5m': session_view(now)}, '1600-0100', '2100-0600')}
    if session is None:
        assert 'PREV_SESSION_HIGH' not in levels
    else:
        assert f':{session}:' in levels['PREV_SESSION_HIGH']['id']
        assert levels['PREV_SESSION_HIGH']['price'] == 110.


def test_session_cache_reuses_prices_but_not_a_changed_current_bar_boundary():
    before = session_view(local(15, 55))
    # A gap/revised forming timestamp can advance the session with identical closed history.
    times = before.time.copy(); times[-1] = local(16)
    boundary = view(times, high=before.column('high'), low=before.column('low'))
    store = LevelStore()
    assert not store.get({'5m': before}, '1600-0100', '2100-0600')
    expected = store.get({'5m': boundary}, '1600-0100', '2100-0600')
    assert expected and store.computations == 2
    changed_prices = view(times, high=[*boundary.column('high')[:-1], 123456.], low=boundary.column('low'))
    assert store.get({'5m': changed_prices}, '1600-0100', '2100-0600') == expected
    assert store.computations == 2


def test_the_first_minute_of_a_new_session_can_touch_its_previous_session_level():
    detector = LiquidityDetector(SweepSpec('w', SYMBOL, '1m', ('PREV_SESSION_HIGH',)))
    levels = [x for x in external_levels({'5m': session_view(local(16))}, '1600-0100', '2100-0600')
              if x['level_code'] == 'PREV_SESSION_HIGH']
    at = local(16, 1)
    source = view([at - 120, at - 60, at], high=[100., 111., 100.], low=99.)
    event, = detector.process(source, levels, observed_at=at)
    assert event['touch_time'] == local(16)
    assert event['level_price'] == 110. and event['touch_confirmed_at'] == at


def test_live_and_replay_confirm_the_same_touch_and_expire_at_the_same_moment():
    from engine_harness114 import Engine, quiet, SYMBOL as ENGINE_SYMBOL

    def recipe(raw):
        raw.clear()
        raw.update({'symbols': [ENGINE_SYMBOL], 'direction': 'SHORT', 'time_filters': [], 'final_time_filters': 0,
            'steps': [{'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['1h'], 'level': 'PDH', 'recent': {'seconds': 1800}}],
            'final': {'kind': 'NOTIFY'}})

    def run(live):
        engine = Engine('SPECIAL2', mutate=recipe, live=live)
        observations = []
        try:
            # The first publication installs the strategy's SWEEP subscription.
            engine.send(H, {tf: quiet(H, tf=tf) for tf in ('1h', '1d', '4h', '8h', '5m')})
            for now in (H + HOUR + 5, H + HOUR + 1805, H + HOUR + 1806):
                feeds = {tf: quiet(H + HOUR, tf=tf) for tf in ('1h', '1d', '4h', '8h', '5m')}
                port = engine.send(now, feeds)
                observations.append(port.observations[0][0]['matched'])
            return observations, [(x['event_time'], x['direction']) for x in engine.alerts()]
        finally:
            engine.close()

    live, replay = run(True), run(False)
    assert live == replay
    assert live[0] == [True, True, False]
    assert live[1] == [(float(H + HOUR + 5), 'SHORT')]
