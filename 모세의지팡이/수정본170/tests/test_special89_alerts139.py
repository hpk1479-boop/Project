"""수정본139: SPECIAL8은 50헐 터치 순간에 알림.

레시피의 '알림 순간 조건'(final_conditions)은 올존 최종처럼 일반 알림(NOTIFY)에서도 알림 순간에 확인한다.
맞지 않으면 그 알림은 버리고 기다리지 않는다(나중에 추세가 바뀌어도 늦은 알림이 오지 않음).
같은 상승추세 안의 다음 골크·터치도 다시 알린다. 알림은 SPECIAL8 알림 중 그 순간 조건이 맞는 것과 같다.
수정본140부터 SPECIAL9는 15분 조건을 터치 순간 조건(단계)으로 쓰므로, 이 공통 경로는 SPECIAL8에
15분 상승추세를 알림 순간 조건으로 붙인 시험용 레시피(GATED)로 확인한다. SPECIAL9: test_special9_hammer140.py.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed   # noqa: E402

T0 = 1_790_000_000 - 1_790_000_000 % 900     # a 15-minute boundary
CROSS, TOUCH = 30, 31                         # bar indexes of the golden cross and the HMA50 touch


def minute_bars(count, events):
    """1m bars: HMA17 under HMA50 except where `events` says, price above HMA50 except on a touch."""
    rows = []
    hma17 = 99.
    for i in range(count):
        kind = events.get(i)
        if kind == 'golden': hma17 = 101.
        if kind == 'dead': hma17 = 99.
        low = 99.5 if kind == 'touch' else 101.5
        rows.append((102., 102.5, low, 102., hma17, 100.))
    return rows


def minute_feed(rows, upto):
    """The board after bar `upto` opened: bars 0..upto, the last one forming."""
    part = rows[:upto + 1]
    values = {name: np.array([r[i] for r in part]) for i, name in
              enumerate(('open', 'high', 'low', 'close', 'hma_17', 'hma_50'))}
    return feed('1m', T0 + upto * 60, n=len(part), **values)


def quarter_feed(now, rising):
    """40 fifteen-minute bars whose opens and native HMA50 rise (or fall) steadily."""
    n = 40; step = 1. if rising else -1.
    opens = 2400. + step * np.arange(n)
    return feed('15m', now - now % 900, n=n, open=opens, close=opens, high=opens + 1, low=opens - 1,
                hma_50=opens - 5)


GATED = 'SPECIAL8 + 알림 순간 15분 상승추세'


def gated():
    return Harness.special('SPECIAL8', mutate=lambda m: m.update(final_conditions=[{'kind': 'TREND', 'tfs': ['15m']}]))


def run(special, events, upto, *, rising=True, harness=None):
    h = harness or (gated() if special == GATED else Harness.special(special))
    rows = minute_bars(upto + 1, events)
    for bar in range(CROSS - 1, upto + 1):
        now = T0 + bar * 60 + 1
        h.publish(now, {'1m': minute_feed(rows, bar), '15m': quarter_feed(now, rising(bar) if callable(rising) else rising)})
    return h


EVENTS = {CROSS: 'golden', TOUCH: 'touch'}


def test_special8_alerts_at_the_touch_bar_without_waiting_for_a_confirmation_candle():
    before = run('SPECIAL8', EVENTS, TOUCH)            # the touch bar is still forming: no alert yet
    assert before.messages == []
    h = run('SPECIAL8', EVENTS, TOUCH + 1)             # the touch bar closed: alert at once
    assert len(h.messages) == 1
    message = h.kernel.messages[0]
    assert message['signal_source'] == 'SIGNAL' and message['direction'] == 'LONG'
    assert message['signal_tf'] == '1m' and message['env_tf'] == '1m'


def test_special8_needs_the_golden_cross_first():
    h = run('SPECIAL8', {TOUCH: 'touch'}, TOUCH + 1)
    assert h.messages == []


@pytest.mark.parametrize('rising,alerts', [(True, 1), (False, 0)])
def test_an_alert_moment_condition_alerts_only_while_it_holds_at_that_moment(rising, alerts):
    h = run(GATED, EVENTS, TOUCH + 1, rising=rising)
    assert len(h.messages) == alerts
    if alerts:
        assert h.kernel.messages[0]['env_tf'] == '1m'


def test_a_dropped_alert_never_arrives_late_when_the_trend_turns_up():
    # Down at the touch: dropped. Up a few bars later without a new cross and touch: still nothing.
    h = run(GATED, EVENTS, TOUCH + 4, rising=lambda bar: bar > TOUCH + 1)
    assert h.messages == []


def test_an_alert_moment_condition_alerts_again_on_the_next_cross_and_touch():
    events = {**EVENTS, TOUCH + 2: 'dead', TOUCH + 4: 'golden', TOUCH + 5: 'touch'}
    h = run(GATED, events, TOUCH + 6)
    assert len(h.messages) == 2


TWO_TOUCHES = {CROSS: 'golden', TOUCH: 'touch', TOUCH + 2: 'touch'}
NEXT_CROSS = {**EVENTS, TOUCH + 2: 'dead', TOUCH + 4: 'golden', TOUCH + 5: 'touch'}


@pytest.mark.parametrize('events,rising', [
    (TWO_TOUCHES, True),
    (TWO_TOUCHES, lambda bar: bar > TOUCH + 1),    # down at the first touch's alert, up at the second touch
    (NEXT_CROSS, lambda bar: bar > TOUCH + 1),
])
def test_alert_moment_alerts_are_the_special8_alerts_made_in_a_15m_uptrend(events, rising):
    # SPECIAL8 alerts once per golden cross, at its first touch. A first touch dropped at the alert
    # moment is not replaced by a later touch of the same cross; the next golden cross is watched again.
    upto = max(events) + 1
    eight, nine = (run(name, events, upto, rising=rising) for name in ('SPECIAL8', GATED))
    up = rising if callable(rising) else (lambda bar: rising)
    assert eight.kernel.messages
    expected = [m['event_time'] for m in eight.kernel.messages if up((m['event_time'] - T0) // 60)]
    assert [m['event_time'] for m in nine.kernel.messages] == expected


def test_an_oz_recipe_records_its_first_conditions_frame_as_the_environment_frame():
    # SPECIAL7: 15분 추세(첫 조건) → 1분 브레이커 올존. The alert's env_tf is that 15m.
    h = Harness.special('SPECIAL7')
    now = T0 + 3600 + 1
    h.publish(now, {'1m': minute_feed(minute_bars(40, {}), 39), '15m': quarter_feed(now, True)})
    armed = [m for m in h.machines if m.active]
    assert [m.direction for m in armed] == ['LONG']
    h.final_oz(armed[0], now)
    assert len(h.kernel.messages) == 1
    message = h.kernel.messages[0]
    assert message['signal_source'] == 'OZ' and message['env_tf'] == '15m'


def test_alert_moment_conditions_are_the_same_rule_for_ozs_and_notifications():
    source = (ROOT / 'Part1/program/strategy_recipe/port.py').read_text(encoding='utf-8')
    assert source.count("m.meaning.get('final_conditions', [])") >= 2   # watched, and checked at a NOTIFY
