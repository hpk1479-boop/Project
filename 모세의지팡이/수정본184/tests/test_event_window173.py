"""수정본173: 사건 뒤 기간 — 연쇄 기간(within), 최근 사건(recent), 반대 방향(OPPOSITE).

기간은 {"seconds": S} 또는 {"bars": N, "tf": TF}이다.
- 시간: 사건 시각 + S초까지(끝 포함, within_sec와 같음).
- 봉 수: 사건이 판정된 봉이 0번째, 그다음 실제 봉 N개. N+1번째 봉이 열리면 끝난다.
  마감봉 판정은 판정한 봉으로, 진행 중 판정·사건(올존 완성 등)은 그 순간 진행 중인 봉으로 센다.
- 올존의 시작점은 올존 엔진이 완성한 순간이다(B0·전송 시각이 아님). 알림 뒤의 무효는 보지 않는다.
- 순차 연쇄의 첫 사건이 기다리는 중에 새로 나오면 새 사건부터 다시 잰다(사용자 결정 1 가).
"""
import copy
import json
import math

import numpy as np
import pytest

from recipe_harness114 import Harness, SYMBOL, feed
from strategy_recipe import windows
from strategy_recipe.alerts import condition_text, render_strategy_alert, window_text
from strategy_recipe.contract import execution_plan
from strategy_recipe.runtime import IntentMachine
from oz_engine.checkpoint import decode, encode

H = 1_790_002_800                      # an hour boundary, real seconds ("10:00" below)
MIN, HOUR = 60, 3600


def bars(start, count, step=MIN):
    return np.arange(count, dtype=np.int64) * step + start


# ----------------------------------------------------------------- the window rule

def test_window_objects_are_checked():
    labels = ('1m', '3m', '1h')
    windows.check({'seconds': 1800}, labels, '기간')
    windows.check({'bars': 10, 'tf': '1m'}, labels, '기간')
    for bad in ({}, {'seconds': 0}, {'seconds': 1.5}, {'seconds': True}, {'seconds': 60, 'tf': '1m'},
                {'bars': 0, 'tf': '1m'}, {'bars': 501, 'tf': '1m'}, {'bars': 3}, {'bars': 3, 'tf': 'SOURCE'},
                {'seconds': 60, 'bars': 2, 'tf': '1m'}, {'bars': 2, 'tf': '1m', 'from': 'B0'}, [60]):
        with pytest.raises(ValueError):
            windows.check(bad, labels, '기간')


@pytest.mark.parametrize('at,end,expected', [
    (H + 3 * MIN + 20, None, H + 14 * MIN),     # an OZ completed during 10:03: bars 10:04..10:13, over when 10:14 opens
    (H + 3 * MIN, None, H + 14 * MIN),          # completed as 10:03 opened: still that bar is bar 0
    (H + 3 * MIN, H + 3 * MIN, H + 13 * MIN),   # a closed judgment of the 10:02 bar: bars 10:03..10:12
    (H + 25 * MIN + 5, None, None),             # bar 11 has not opened yet
    (H - HOUR, None, float(H)),                 # older than every bar held: far behind any bar window
])
def test_a_bar_window_counts_the_bars_after_the_events_own_bar(at, end, expected):
    assert windows.close_time(bars(H, 31), at, end, 10) == expected


@pytest.mark.parametrize('at,end,expected_hour', [
    (H + 3 * HOUR + .3, None, 10),        # 1h OZ completed as 13:00 opened: 14..19, over at 20:00
    (H + 3 * HOUR + 40 * MIN, None, 10),  # completed at 13:40: the same six bars
    (H + 3 * HOUR, H + 3 * HOUR, 9),      # closed judgment of the 12:00 bar (golden cross): 13..18, over at 19:00
])
def test_a_window_frame_of_its_own(at, end, expected_hour):
    assert windows.close_time(bars(H, 24, HOUR), at, end, 6) == H + expected_hour * HOUR


def test_a_bar_window_counts_only_bars_that_exist():
    saturday = bars(H, 6)                               # 10:00..10:05, then the market closes
    monday = bars(H + 2 * 86400 + HOUR, 10)             # reopens 11:00
    times = np.concatenate([saturday, monday])
    # An OZ during 10:01: 10:02..10:05 (4 bars) and 11:00..11:05 (6 bars); over when 11:06 opens.
    assert windows.close_time(times, H + 90, None, 10) == monday[6]
    # Thirty minutes are thirty minutes, closed market or not.
    assert windows.chain_over({'seconds': 1800}, H + 90, None, H + 90 + 1801)
    assert not windows.chain_over({'seconds': 1800}, H + 90, None, H + 90 + 1800)


def test_a_closed_bar_ends_at_its_length():
    assert windows.bar_end(H, '1m') == H + 60 and windows.bar_end(H, '1h') == H + 3600
    assert windows.bar_end(H, '1d') == H + 86400


def test_membership_at_the_end_of_a_bar_window():
    w, close = {'bars': 10, 'tf': '1m'}, H + 14 * MIN
    assert windows.within_chain(w, H, close, close - .001, None)           # on bar 10, in progress
    assert not windows.within_chain(w, H, close, close, None)              # bar 11 opened
    assert windows.within_chain(w, H, close, close, close)                 # closed judgment of bar 10, made as bar 11 opens
    assert not windows.within_chain(w, H, close, close + MIN, close + MIN)  # closed judgment of bar 11
    assert windows.within_chain(w, H, None, H + 10 ** 6, None)             # bar 11 not opened yet
    s = {'seconds': 1800}
    assert windows.within_chain(s, H, None, H + 1800, None) and not windows.within_chain(s, H, None, H + 1801, None)


def test_a_recent_occurrence_counts_from_its_own_moment():
    w, mark, close = {'bars': 6, 'tf': '1h'}, (H + 3 * HOUR + .3, None), H + 10 * HOUR
    assert windows.recent_holds(w, mark, close, mark[0], False)               # its own moment
    assert not windows.recent_holds(w, mark, close, mark[0] - 1, False)        # before it
    assert windows.recent_holds(w, mark, close, close - 1, False)
    assert not windows.recent_holds(w, mark, close, close, False)              # bar 7 opened
    assert windows.recent_holds(w, mark, close, close, True)                   # closed judgment of bar 6
    assert not windows.recent_holds(w, mark, close, H + 3 * HOUR, True)        # closed bar before the OZ
    assert not windows.recent_holds(w, None, None, H, False)                  # nothing occurred
    s = {'seconds': 6 * HOUR}
    assert windows.recent_holds(s, mark, None, mark[0] + 6 * HOUR, False)
    assert not windows.recent_holds(s, mark, None, mark[0] + 6 * HOUR + 1, False)


# ----------------------------------------------------------------- ① a chain within a window

OZ_STEP = {'kind': 'OZ_ALERT', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ'}
OUT_IN = {'kind': 'PERCENTILE_OUT_IN', 'tfs': ['3m'], 'families': ['RSI']}
CLOSE_5M = {'kind': 'BAR_CLOSE', 'tfs': ['5m']}
MINUTES = bars(H - HOUR, 200)
OZ_AT = H + 3 * MIN + 20                # 10:03:20, during the 10:03 bar
END = H + 14 * MIN                      # 10:14 opens: ten 1m bars after it are over


def machine(within, steps=(OZ_STEP, OUT_IN), order='SEQUENTIAL', **extra):
    raw = {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': order, 'steps': [copy.deepcopy(s) for s in steps],
           'final': {'kind': 'NOTIFY'}, **extra}
    if within is not None: raw['within'] = within
    return IntentMachine(execution_plan(raw)['meaning'], SYMBOL, 'LONG')


def ev(token, at, end=None, matched=True):
    o = {'ready': True, 'matched': matched, 'event': True, 'token': token, 'at': at, 'source_tf': '1m'}
    if end is not None: o['bar_end'] = end
    return o


def closer(count=10):
    return lambda at, end: windows.close_time(MINUTES, at, end, count)


def step(m, now, *observations, count=10):
    return m.advance(now, list(observations), window_close=closer(count))


@pytest.mark.parametrize('second,end,alerted', [
    (END - 1, None, True),           # on the tenth bar (10:13), in progress
    (END, None, False),              # 10:14 opened
    (END, END, True),                # a closed judgment of the 10:13 bar, made as 10:14 opens
    (END + MIN, END + MIN, False),   # a closed judgment of the 10:14 bar
])
def test_a_follow_up_within_ten_bars_of_an_oz(second, end, alerted):
    m = machine({'bars': 10, 'tf': '1m'})
    assert step(m, OZ_AT, ev('oz', OZ_AT), ev('x', 0, matched=False)) == []
    assert (step(m, second, ev('oz', OZ_AT), ev('in', second, end)) == ['NOTIFY']) is alerted


def test_a_follow_up_at_the_same_instant_or_before_is_not_after():
    m = machine({'bars': 10, 'tf': '1m'})
    assert step(m, OZ_AT, ev('oz', OZ_AT), ev('in', OZ_AT)) == []          # one instant does not make "then"
    assert step(m, OZ_AT + 30, ev('oz', OZ_AT), ev('in', OZ_AT)) == []     # the same follow-up is used up
    early = machine({'bars': 10, 'tf': '1m'})
    assert step(early, H, ev('oz', 0, matched=False), ev('in', H)) == []    # a follow-up before the start
    assert step(early, OZ_AT, ev('oz', OZ_AT), ev('in', H, matched=False)) == []
    assert step(early, OZ_AT + 30, ev('oz', OZ_AT), ev('in', H, matched=False)) == []


def test_the_wait_ends_when_the_window_does_and_its_start_is_never_used_again():
    m = machine({'bars': 10, 'tf': '1m'})
    step(m, OZ_AT, ev('oz', OZ_AT), ev('x', 0, matched=False))
    step(m, END - 30, ev('oz', OZ_AT), ev('x', 0, matched=False))
    assert m.stage == 1 and m.window_close == END
    step(m, END, ev('oz', OZ_AT), ev('x', 0, matched=False))                # 10:14: discarded
    assert m.stage == 0 and m.started is None
    assert step(m, END + 30, ev('oz', OZ_AT), ev('in', END + 30)) == []     # the old OZ is not reused


def test_a_closed_start_counts_from_the_bar_after_the_one_it_judged():
    # A closed judgment of the 10:02 bar (a cross at its close): bars 10:03..10:12, over when 10:13 opens.
    start = H + 3 * MIN
    m = machine({'bars': 10, 'tf': '1m'}, steps=(CLOSE_5M, OUT_IN))
    step(m, start, ev('close', start, start), ev('x', 0, matched=False))
    assert m.window_close == H + 13 * MIN
    assert step(m, H + 13 * MIN - 1, ev('close', start, start, matched=False), ev('in', H + 13 * MIN - 1)) == ['NOTIFY']
    late = machine({'bars': 10, 'tf': '1m'}, steps=(CLOSE_5M, OUT_IN))
    step(late, start, ev('close', start, start), ev('x', 0, matched=False))
    assert step(late, H + 13 * MIN, ev('close', start, start, matched=False), ev('in', H + 13 * MIN)) == []


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
@pytest.mark.parametrize('gap', [.5, 60, 1799, 1800, 1801, 3600])
def test_within_seconds_is_within_sec(order, gap):
    results = []
    for key in ({'within_sec': 1800}, {'within': {'seconds': 1800}}):
        raw = {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': order, 'steps': [copy.deepcopy(OZ_STEP), copy.deepcopy(OUT_IN)],
               'final': {'kind': 'NOTIFY'}, **key}
        m = IntentMachine(execution_plan(raw)['meaning'], SYMBOL, 'LONG')
        first = m.advance(H, [ev('a', H), ev('b', 0, matched=False)])
        second = m.advance(H + gap, [ev('a', H), ev('b', H + gap)])
        later = m.advance(H + gap + 1, [ev('a', H), ev('b', H + gap)])
        results.append((first, second, later, m.stage, m.started, sorted(m.hits.items())))
    assert results[0] == results[1]
    assert (results[0][1] == ['NOTIFY']) is (gap <= 1800 and gap > 0)


def test_a_newer_start_restarts_the_wait():
    m = machine({'seconds': 1800})
    step(m, H, ev('oz1', H), ev('x', 0, matched=False))
    step(m, H + 1200, ev('oz2', H + 1200), ev('x', 0, matched=False))      # a newer OZ while waiting
    assert m.started == H + 1200
    # 35 minutes after the first OZ but 15 after the newer one.
    assert step(m, H + 2100, ev('oz2', H + 1200), ev('in', H + 2100)) == ['NOTIFY']


def test_a_newer_start_restarts_a_bar_window_too():
    m = machine({'bars': 10, 'tf': '1m'})
    step(m, H + 20, ev('oz1', H + 20), ev('x', 0, matched=False))           # window until 10:11
    step(m, H + 8 * MIN + 30, ev('oz2', H + 8 * MIN + 30), ev('x', 0, matched=False))  # until 10:19
    assert m.window_close == H + 19 * MIN
    assert step(m, H + 15 * MIN, ev('oz2', H + 8 * MIN + 30), ev('in', H + 15 * MIN)) == ['NOTIFY']


def test_a_restart_drops_what_was_gathered_before_it():
    m = machine({'seconds': 3600}, steps=(OZ_STEP, OUT_IN, CLOSE_5M))
    step(m, H, ev('a1', H), ev('x', 0, matched=False), ev('y', 0, matched=False))
    step(m, H + 60, ev('a1', H), ev('b1', H + 60), ev('y', 0, matched=False))
    assert m.stage == 2
    step(m, H + 120, ev('a2', H + 120), ev('b1', H + 60, matched=False), ev('y', 0, matched=False))
    assert m.stage == 1 and m.started == H + 120                            # b1 came before a2
    assert step(m, H + 180, ev('a2', H + 120), ev('x', 0, matched=False), ev('c1', H + 180)) == []
    assert step(m, H + 240, ev('a2', H + 120), ev('b2', H + 240), ev('y', 0, matched=False)) == []
    assert step(m, H + 300, ev('a2', H + 120), ev('x', 0, matched=False), ev('c2', H + 300)) == ['NOTIFY']


def test_an_older_start_delivered_late_does_not_restart():
    m = machine({'seconds': 3600}, steps=(OZ_STEP, OUT_IN, CLOSE_5M))
    step(m, H, ev('a1', H), ev('x', 0, matched=False), ev('y', 0, matched=False))
    step(m, H + 120, ev('a1', H), ev('b1', H + 120), ev('y', 0, matched=False))
    step(m, H + 130, ev('a2', H + 60), ev('x', 0, matched=False), ev('y', 0, matched=False))   # before b1
    assert m.stage == 2 and m.started == H
    assert step(m, H + 180, ev('a2', H + 60), ev('x', 0, matched=False), ev('c1', H + 180)) == ['NOTIFY']


def test_a_follow_up_in_the_same_poll_completes_the_current_chain_first():
    m = machine({'seconds': 1800})
    step(m, H, ev('oz1', H), ev('x', 0, matched=False))
    assert step(m, H + 600, ev('oz2', H + 600), ev('in', H + 600)) == ['NOTIFY']


def test_without_a_window_nothing_restarts():
    m = machine(None)
    step(m, H, ev('oz1', H), ev('x', 0, matched=False))
    step(m, H + 1200, ev('oz2', H + 1200), ev('x', 0, matched=False))
    assert m.started == H and m.stage == 1


def test_unordered_hits_keep_the_latest_inside_their_windows():
    m = machine({'bars': 10, 'tf': '1m'}, order='UNORDERED')
    step(m, OZ_AT, ev('oz', OZ_AT), ev('x', 0, matched=False))
    # The out-in at 10:16 is past the OZ's window (10:14): the OZ is dropped and the out-in waits alone.
    assert step(m, H + 16 * MIN, ev('oz', OZ_AT), ev('in', H + 16 * MIN)) == []
    assert list(m.hits) == [1]
    # A newer OZ at 10:20 is inside the out-in's window (10:17..10:26, over at 10:27).
    assert step(m, H + 20 * MIN, ev('oz2', H + 20 * MIN), ev('in', H + 16 * MIN)) == ['NOTIFY']


def test_a_bar_wait_survives_a_restart():
    def run(restart):
        m = machine({'bars': 10, 'tf': '1m'})
        step(m, OZ_AT, ev('oz', OZ_AT), ev('x', 0, matched=False))
        step(m, OZ_AT + 30, ev('oz', OZ_AT), ev('x', 0, matched=False))
        if restart:
            saved = decode(json.loads(json.dumps(encode(m.checkpoint()))))
            m = machine({'bars': 10, 'tf': '1m'})
            m.restore(saved)
        close = m.window_close
        return close, step(m, END - 1, ev('oz', OZ_AT), ev('in', END - 1))
    assert run(False) == run(True) == (END, ['NOTIFY'])


# ----------------------------------------------------------------- ① on the real port: examples 1 and 2

RSI = {'RSI_val': 50., 'RSI_db': 30., 'RSI_ub': 70., 'RSI_upper_out': math.nan}


def minutes_board(h, now, out=False):
    """1m and 3m bars up to now; the forming 3m bar is below its RSI band when `out`."""
    feeds = {}
    for tf, size in (('1m', MIN), ('3m', 3 * MIN)):
        lower = np.full(40, math.nan)
        if tf == '3m' and out: lower[-1] = 1.
        feeds[tf] = feed(tf, now - now % size, n=40, RSI_lower_out=lower, **RSI)
    h.publish(now, feeds)


def follow_alerts(within, ozs, out_at, in_at, until=None):
    """A 1m BLIND OZ, then a 3m RSI out-in within `within`; OZ completions at `ozs`. Alert times."""
    meaning = execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': 'SEQUENTIAL', 'within': within,
        'time_filters': [], 'final_time_filters': 0,
        'steps': [copy.deepcopy(OZ_STEP), copy.deepcopy(OUT_IN)], 'final': {'kind': 'NOTIFY', 'direction': 'LONG'}})['meaning']
    h = Harness(meaning)
    times = sorted({*range(H, (until or in_at) + 1, 30), *ozs, out_at, in_at})
    for now in times:
        minutes_board(h, now, out=now == out_at)
        if now in ozs:
            h.parent_oz(meaning['steps'][0], '1m', now, direction='LONG', validation='BLIND', trigger='OZ')
    return [m['event_time'] for m in h.kernel.messages]


@pytest.mark.parametrize('in_at,alerted', [(END - 30, True), (END - 1, True), (END, False), (END + 30, False)])
def test_example_2_out_in_within_ten_1m_bars_of_a_1m_oz(in_at, alerted):
    assert follow_alerts({'bars': 10, 'tf': '1m'}, [OZ_AT], in_at - 15, in_at) == ([in_at] if alerted else [])


@pytest.mark.parametrize('in_at,alerted', [(OZ_AT + 1800, True), (OZ_AT + 1801, False)])
def test_example_1_out_in_within_thirty_minutes_of_a_1m_oz(in_at, alerted):
    assert follow_alerts({'seconds': 1800}, [OZ_AT], in_at - 10, in_at) == ([in_at] if alerted else [])


def test_example_1_counts_from_the_newest_oz():
    # 35 minutes after the first OZ, 15 after the second.
    assert follow_alerts({'seconds': 1800}, [H + 20, H + 20 * MIN + 20], H + 35 * MIN + 10, H + 35 * MIN + 20) == [H + 35 * MIN + 20]
    assert follow_alerts({'seconds': 1800}, [H + 20], H + 35 * MIN + 10, H + 35 * MIN + 20) == []


def test_an_out_in_before_the_oz_does_not_count():
    assert follow_alerts({'bars': 10, 'tf': '1m'}, [OZ_AT], OZ_AT - 60, OZ_AT - 30, until=END + 60) == []


def test_closed_judgments_carry_the_end_of_the_bar_they_judged():
    h = Harness(execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'final': {'kind': 'NOTIFY'},
                                'steps': [{'kind': 'BAR_CLOSE', 'tfs': ['3m']}]})['meaning'])
    minutes_board(h, H + 2 * MIN + 30)
    minutes_board(h, H + 3 * MIN + 5)
    board = h.port._board()[0]
    closed = h.port.observations[0][0]                       # what the poll at 10:03:05 saw
    assert closed['matched'] and closed['bar_end'] == H + 3 * MIN and closed['at'] == H + 3 * MIN
    forming = h.port._observe(board, SYMBOL, dict(OUT_IN, _event_mode=True), 'LONG', H + 3 * MIN + 5)
    assert 'bar_end' not in forming


# ----------------------------------------------------------------- ② recent occurrence: examples 3 and 4

BAND = {'1h': {'wonbi_lower': 2390., 'wonbi_upper': 2410., 'wonbi_mid': 2400.}}
SELL_BLIND = {'kind': 'OZ_ALERT', 'tfs': ['1h'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ',
              'direction': 'OPPOSITE', 'recent': {'bars': 6, 'tf': '1h'}}


def wonbi_meaning(placement, bar_state=None, recent=None):
    touch = {'kind': 'WONBI_TOUCH', 'tfs': ['1h']}
    if bar_state: touch['bar_state'] = bar_state
    condition = dict(SELL_BLIND, recent=recent or SELL_BLIND['recent'])
    raw = {'symbols': [SYMBOL], 'direction': 'BOTH', 'time_filters': [], 'final_time_filters': 0,
           'steps': [touch], 'final': {'kind': 'NOTIFY'}}
    if placement == 'cancel': raw['cancel_conditions'] = [condition]
    elif placement == 'steps': raw['steps'] = [touch, dict(condition, negated=True)]
    else: raw['final_conditions'] = [dict(condition, negated=True)]
    return execution_plan(raw)['meaning']


def hourly(h, now, lower=(), upper=()):
    """1h bars up to now; bars opening at `lower` reach the lower Wonbi band, at `upper` the upper one."""
    last = now - now % HOUR
    times = feed('1h', last, n=30).time
    low = np.where(np.isin(times, list(lower)), 2385., 2400.)
    high = np.where(np.isin(times, list(upper)), 2415., 2400.)
    h.publish(now, {'1h': feed('1h', last, n=30, low=low, high=high)}, wonbi=BAND)


def at(hour, seconds=0):
    return H + hour * HOUR + seconds


def wonbi_run(meaning, plan, oz=('SHORT', at(3, .3)), harness=None):
    """plan: (now, lower bar hours, upper bar hours). Returns (time, direction) of each alert."""
    h = harness or Harness(meaning)
    for now, lower, upper in plan:
        hourly(h, now, [at(x) for x in lower], [at(x) for x in upper])
        if oz and now == oz[1]:
            step_ = next(s for key in ('steps', 'cancel_conditions', 'final_conditions')
                         for s in meaning.get(key, ()) if s['kind'] == 'OZ_ALERT')
            h.parent_oz(step_, '1h', now, direction=oz[0], validation='BLIND', trigger='OZ', b0_time=at(1))
    return [(m['event_time'], m['direction']) for m in h.kernel.messages], h


# A sell BLIND OZ completes at 13:00 (hour 3): six 1h bars after it are hours 4..9, over at hour 10.
# Polls are seconds apart in the market; the plans keep one quiet poll right after the window ends, as
# there always is one: a cancel condition lets an alert out only for a touch that holds anew.
SEPARATE_TOUCHES = [(at(2), (), ()), (at(3, .3), (), ()), (at(5, 600), (5,), ()), (at(6, 600), (), ()),
                    (at(9, 600), (9,), ()), (at(9, 1800), (), ()), (at(10, 30), (), ()), (at(10, 600), (10,), ())]


@pytest.mark.parametrize('placement', ['cancel', 'steps', 'final'])
def test_example_3_lower_touches_are_cancelled_for_six_1h_bars_after_a_sell_oz(placement):
    alerts, _ = wonbi_run(wonbi_meaning(placement), SEPARATE_TOUCHES)
    assert alerts == [(at(10, 600), 'LONG')]


def test_example_4_mirror_upper_touches_after_a_buy_oz():
    plan = [(now, upper, lower) for now, lower, upper in SEPARATE_TOUCHES]
    alerts, _ = wonbi_run(wonbi_meaning('cancel'), plan, oz=('LONG', at(3, .3)))
    assert alerts == [(at(10, 600), 'SHORT')]


def test_the_same_direction_oz_cancels_nothing_of_that_direction():
    plan = [(at(2), (), ()), (at(3, .3), (), ()), (at(5, 600), (5,), (5,))]
    alerts, _ = wonbi_run(wonbi_meaning('cancel'), plan, oz=('SHORT', at(3, .3)))
    assert alerts == [(at(5, 600), 'SHORT')]           # the sell OZ cancels only the buy side


def test_without_an_oz_nothing_is_cancelled():
    alerts, _ = wonbi_run(wonbi_meaning('cancel'), SEPARATE_TOUCHES, oz=None)
    assert alerts == [(at(5, 600), 'LONG'), (at(9, 600), 'LONG'), (at(10, 600), 'LONG')]


ONGOING = [(at(2), (), ()), (at(3, .3), (), ()), (at(9, 600), (9,), ()), (at(10, 30), (9, 10), ()),
           (at(10, 1800), (9,), ()), (at(11, 600), (11,), ())]


def test_a_touch_that_began_inside_the_window_alerts_at_its_end_only_as_a_condition():
    # steps + negated: at 20:00 the touch still holds and no recent OZ remains.
    assert wonbi_run(wonbi_meaning('steps'), ONGOING)[0] == [(at(10, 30), 'LONG'), (at(11, 600), 'LONG')]
    # cancel_conditions: the touch has to hold anew after the window.
    assert wonbi_run(wonbi_meaning('cancel'), ONGOING)[0] == [(at(11, 600), 'LONG')]


def test_closed_bar_conditions_are_judged_on_the_bar_they_judge():
    plan = [(at(2), (), ()), (at(3, .3), (), ()), (at(10, 30), (9, 10), ()), (at(11, 30), (9, 10), ())]
    # The 19:00 (hour 9) bar's closed touch, judged as hour 10 opens, is the sixth bar: still cancelled.
    assert wonbi_run(wonbi_meaning('steps', bar_state='CLOSED'), plan)[0] == [(at(11, 30), 'LONG')]
    # A touch on the bar in progress at hour 10 is on the seventh bar.
    assert wonbi_run(wonbi_meaning('steps'), plan)[0] == [(at(10, 30), 'LONG')]


def test_seconds_count_from_the_oz():
    plan = [(at(2), (), ()), (at(3, .3), (), ()), (at(9, 300), (9,), ()), (at(9, 1800), (), ()), (at(9, 1900), (), ()),
            (at(9, 2000), (9,), ())]
    alerts, _ = wonbi_run(wonbi_meaning('cancel', recent={'seconds': 6 * HOUR + 1800}), plan)
    assert alerts == [(at(9, 2000), 'LONG')]            # 13:00:00.3 + 6h30m = 19:30:00.3


def test_a_later_oz_starts_the_window_again():
    # The first sell OZ (hour 3) would end at hour 10; the second (hour 8) keeps hours 9..14, over at hour 15.
    plan = [(at(2), (), ()), (at(3, .3), (), ()), (at(8, .5), (), ()), (at(10, 600), (10,), ()),
            (at(10, 1800), (), ()), (at(15, 30), (), ()), (at(15, 600), (15,), ())]
    h = Harness(wonbi_meaning('cancel'))
    oz_step = h.port.meaning['cancel_conditions'][0]
    for now, lower, upper in plan:
        hourly(h, now, [at(x) for x in lower], [at(x) for x in upper])
        if now in (at(3, .3), at(8, .5)):
            h.parent_oz(oz_step, '1h', now, direction='SHORT', validation='BLIND', trigger='OZ', b0_time=now - HOUR)
    assert [(m['event_time'], m['direction']) for m in h.kernel.messages] == [(at(15, 600), 'LONG')]


def test_what_becomes_of_the_oz_after_it_completed_does_not_matter():
    # The OZ engine replaces or cancels that cycle (an opposite HMA cross, a newer B0): the OZ still occurred.
    meaning = wonbi_meaning('cancel')
    h = Harness(meaning)
    alerts, h = wonbi_run(meaning, SEPARATE_TOUCHES[:2], harness=h)
    h.board.oz_resources[SYMBOL, '1h', 'BLIND', 'OZ', 'SHORT'] = None
    alerts, _ = wonbi_run(meaning, SEPARATE_TOUCHES[2:], oz=None, harness=h)
    assert alerts == [(at(10, 600), 'LONG')]


def test_a_recent_occurrence_survives_a_restart():
    meaning = wonbi_meaning('cancel')
    whole, _ = wonbi_run(meaning, SEPARATE_TOUCHES)
    first, h = wonbi_run(meaning, SEPARATE_TOUCHES[:3])
    saved = json.loads(json.dumps(h.port.checkpoint()))
    restarted = Harness(meaning)
    assert restarted.port.restore(saved)
    rest, _ = wonbi_run(meaning, SEPARATE_TOUCHES[3:], oz=None, harness=restarted)
    assert first + rest == whole == [(at(10, 600), 'LONG')]


def test_a_recent_condition_names_no_frame_and_no_place():
    meaning = wonbi_meaning('steps')
    alerts, h = wonbi_run(meaning, [(at(2), (), ()), (at(3, .3), (), ()), (at(10, 600), (10,), ())])
    assert alerts == [(at(10, 600), 'LONG')]
    message = h.kernel.messages[0]
    # The touch completes the alert; a recent OZ is not an OZ completing now.
    assert message['signal_source'] == 'SIGNAL' and message['signal_tf'] == '1h'
    assert '하단 원비 터치' in message['message'] and '최근' not in message['message']


# ----------------------------------------------------------------- the recipe language

def plan(**raw):
    return execution_plan({'symbols': [SYMBOL], 'direction': 'LONG', 'final': {'kind': 'NOTIFY'}, **raw})['meaning']


TREND = {'kind': 'TREND', 'tfs': ['1h']}
RECENT = {'seconds': 600}


@pytest.mark.parametrize('raw,error', [
    (dict(steps=[OZ_STEP], within={'seconds': 60}), '동시 조건'),
    (dict(order_mode='SEQUENTIAL', steps=[OZ_STEP, OUT_IN], within={'seconds': 60}, within_sec=60), '함께'),
    (dict(order_mode='SEQUENTIAL', steps=[OZ_STEP, OUT_IN], within={'bars': 501, 'tf': '1m'}), '1~500'),
    (dict(order_mode='SEQUENTIAL', steps=[OZ_STEP, OUT_IN], within={'bars': 5, 'tf': 'FINAL'}), '시간봉'),
    (dict(steps=[dict(TREND, recent=RECENT)]), '사건 조건'),
    (dict(steps=[{'kind': 'PRICE_LEVEL', 'tfs': ['1m'], 'level': 2400., 'relation': 'ABOVE', 'recent': RECENT}]), '사건 조건'),
    (dict(order_mode='SEQUENTIAL', steps=[dict(OZ_STEP, capture='oz', recent=RECENT)]), '사건 참조'),
    (dict(steps=[dict(OZ_STEP, tfs=['SOURCE'], recent=RECENT)]), '실제 시간봉'),
    (dict(steps=[dict(OUT_IN, family_combine='MATCHING_FAMILY', recent=RECENT)]), '같은 지표 계열'),
    (dict(steps=[TREND], lifecycle={'restart_on': [dict(OZ_STEP, recent=RECENT)]}), '재시작'),
    (dict(steps=[dict(OZ_STEP, direction='OPPOSITE')], final={'kind': 'NOTIFY', 'direction': 'SAME_AS_PREVIOUS_DIRECTION'}), '반대 방향'),
    (dict(steps=[OZ_STEP], final={'kind': 'NOTIFY', 'direction': 'OPPOSITE'}), '최종 방향'),
])
def test_the_contract_rejects(raw, error):
    with pytest.raises(ValueError, match=error):
        plan(**copy.deepcopy(raw))


def test_the_contract_keeps_what_the_runtime_reads():
    meaning = plan(order_mode='SEQUENTIAL', within={'bars': 10, 'tf': '1m'},
                   steps=[OZ_STEP, OUT_IN], cancel_conditions=[dict(SELL_BLIND, direction='OPPOSITE')])
    assert meaning['within'] == {'bars': 10, 'tf': '1m'}
    cancel = meaning['cancel_conditions'][0]
    assert cancel['recent'] == {'bars': 6, 'tf': '1h'} and cancel['_event_mode']
    opposite = plan(direction='BOTH', steps=[dict(TREND), dict(OZ_STEP, direction='OPPOSITE', recent=RECENT, negated=True)])
    assert opposite['steps'][1]['_resolved_direction'] == 'OPPOSITE'
    # A recent touch or band exit is read as an edge even in a filter.
    touch = plan(steps=[{'kind': 'WONBI_TOUCH', 'tfs': ['1h'], 'recent': RECENT}])
    assert touch['steps'][0]['_event_mode']


def test_shipped_recipes_are_unchanged():
    from strategy_recipe import registry
    for entry in registry.builtin_entries().values():
        text = json.dumps(entry['recipe'])
        assert '"recent"' not in text and '"within"' not in text and 'OPPOSITE' not in text


def test_the_alert_wording():
    assert window_text({'seconds': 1800}) == '30분' and window_text({'seconds': 21600}) == '6시간'
    assert window_text({'seconds': 90}) == '90초' and window_text({'bars': 6, 'tf': '1h'}) == '1시간봉 6개'
    from types import SimpleNamespace as NS
    machine_ = NS(direction='LONG', captures={}, source_tf=None, final_tf=None)
    step_ = dict(SELL_BLIND, negated=True)
    assert condition_text(step_, machine_).startswith('1시간 미성립: 최근 1시간봉 6개 안 ')
    touch = {'kind': 'WONBI_TOUCH', 'tfs': ['1h'], 'direction': 'OPPOSITE'}
    assert condition_text(touch, machine_) == '1시간 상단 원비 터치'      # the opposite of a buy watch


# ----------------------------------------------------------------- backtest base frames and feeds

def test_a_bar_window_moves_with_the_base_frame_and_seconds_keep_it_still():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Part2'))
    from event_backtest import base_frames
    raw = {'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': 'SEQUENTIAL', 'within': {'bars': 10, 'tf': '1m'},
           'steps': [copy.deepcopy(OZ_STEP), copy.deepcopy(OUT_IN)],
           'cancel_conditions': [dict(SELL_BLIND, tfs=['3m'], recent={'bars': 6, 'tf': '3m'})], 'final': {'kind': 'NOTIFY'}}
    assert base_frames.base_frame(raw) == '1m'
    moved = base_frames.move(raw, '1m', '2m')
    assert moved['within'] == {'bars': 10, 'tf': '2m'}
    assert moved['cancel_conditions'][0]['recent'] == {'bars': 6, 'tf': '6m'} and moved['steps'][1]['tfs'] == ['6m']
    timed = dict(raw, within={'seconds': 1800})
    assert base_frames.base_frame(timed) is None and base_frames.base_choices(timed) == []


def test_the_window_frames_are_feeds_a_replay_reads():
    from event_backtest.timeframe_selection import _strings
    raw = {'steps': [dict(OZ_STEP, recent={'bars': 6, 'tf': '4h'})], 'within': {'bars': 2, 'tf': '12h'}}
    found = set(_strings([raw], set()))
    assert {'4h', '12h', '1m'} <= found


# ----------------------------------------------------------------- the real engine, LIVE and replay

def bar_chain(meaning):
    """After each 5m close, a 1m close within two 1m bars; nothing for two minutes after a 15m close."""
    import engine_harness114 as eh
    meaning.clear()
    meaning.update({'symbols': [eh.SYMBOL], 'direction': 'LONG', 'order_mode': 'SEQUENTIAL',
                    'within': {'bars': 2, 'tf': '1m'}, 'time_filters': [], 'final_time_filters': 0,
                    'steps': [{'kind': 'BAR_CLOSE', 'tfs': ['5m']}, {'kind': 'BAR_CLOSE', 'tfs': ['1m']}],
                    'cancel_conditions': [{'kind': 'BAR_CLOSE', 'tfs': ['15m'], 'recent': {'seconds': 120}}],
                    'final': {'kind': 'NOTIFY', 'direction': 'LONG'}})


def engine_alerts(live):
    from command_interpreter import tf_seconds
    from engine_harness114 import Engine, quiet
    engine = Engine('SPECIAL2', mutate=bar_chain, live=live)
    try:
        for now in range(H, H + 40 * MIN + 1, 20):
            engine.send(now, {tf: quiet(now - now % tf_seconds(tf), tf=tf) for tf in ('1m', '5m', '15m')})
        return [(a['event_time'], a['direction']) for a in engine.alerts()]
    finally:
        engine.close()


def test_live_and_replay_agree_on_windows_and_recent_conditions():
    live, replay = engine_alerts(True), engine_alerts(False)
    assert live == replay
    # A 5m close at B starts the window (bar 0: the closed 5m bar; bars 1..2: B and B+1m); the 1m close at
    # B+1m is a closed judgment of the bar B, inside. 15m closes cancel for two minutes.
    expected = [(float(H + b * MIN + MIN), 'LONG') for b in (5, 10, 20, 25, 35)]
    assert live == expected


