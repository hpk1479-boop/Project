"""수정본134: 봉 마지막 순간에 완성된 HMA6/17 크로스도 반대 방향 올존 후보를 취소한다.

진행봉 판정은 '직전 확정봉 vs 진행봉'만 비교하므로, 마지막 순간에 완성된 크로스는 새 봉과 한 묶음으로
들어와 보이지 않았다(매수 후보가 데크 뒤에도 남음). 새 봉이 열리면 방금 확정된 봉과 그 앞 봉을 비교해
놓친 크로스를 진행 중에 본 것처럼 처리한다. 이미 처리한 같은 크로스는 다시 처리하지 않는다.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program')]
from test_oz_rewrite import view, profile, seed, native   # noqa: E402  (OZ 시험 도구)
from event_engine import FeedSnapshot                     # noqa: E402
from oz_engine.market import OZMarketView, COLUMNS        # noqa: E402

H6 = COLUMNS['hma_6']        # HMA17 = 100 everywhere; HMA6 above (101) = golden state


def forming(v, hma6):
    """Same bar, newer tick."""
    a = v.values.copy(); a[-1, H6] = hma6; native(a)
    return OZMarketView(FeedSnapshot(v.time, v.snapshot.volume, a, v.snapshot.seq + 1, v.snapshot.source_epoch, {}),
                        atr_provider=v.atr)


def next_bar(v, closed, new, bars=1):
    """A new bar opens; the previous forming bar closes with value ``closed``."""
    a = np.vstack([v.values[bars:]] + [v.values[-1:]] * bars).copy()
    a[-1 - bars, H6] = closed
    a[-bars:, H6] = new; native(a)
    return OZMarketView(FeedSnapshot(v.time + 60 * bars, v.snapshot.volume, a, v.snapshot.seq + 1,
                                     v.snapshot.source_epoch, {}), atr_provider=v.atr)


def start(direction='LONG', level=101.):
    p = profile(); v = view(hma_6=level)
    seed(p, v, direction)
    p._process_hma_cross('1m', v)
    return p, v


def step(p, v):
    p._process_hma_cross('1m', v)
    return v


def alive(p, direction='LONG'):
    return p.candidates['1m', direction] is not None


def test_cross_seen_while_forming_cancels_as_before():
    p, v = start()
    step(p, forming(v, 99.))
    assert not alive(p)


def test_cross_completed_at_bar_end_now_cancels_on_the_next_bar():
    p, v = start()
    v = step(p, next_bar(v, closed=99., new=98.5))      # 10:05 closed below, seen only with 10:06
    assert not alive(p)
    assert p.hma_cross_extremes['1m', 'SHORT'][2] == int(v.time[-2])   # the cross belongs to the closed bar


def test_golden_cross_at_bar_end_cancels_short_candidate():
    p, v = start('SHORT', level=99.)
    step(p, next_bar(v, closed=101., new=101.5))
    assert not alive(p, 'SHORT')


def test_same_cross_is_not_applied_again_when_its_bar_closes():
    p, v = start()
    v = step(p, forming(v, 99.))                          # seen and applied while forming
    assert not alive(p)
    seed(p, v, 'LONG')                                    # a later LONG structure in the same bar
    step(p, next_bar(v, closed=99., new=98.5))
    assert alive(p)                                       # the closed check does not cancel it again


def test_reverted_cross_does_nothing_at_close_but_a_final_recross_does():
    p, v = start()
    v = step(p, forming(v, 99.)); v = step(p, forming(v, 101.))   # dead, then golden again inside the bar
    seed(p, v, 'LONG')
    step(p, next_bar(v, closed=101., new=101.))           # closed golden: nothing to cancel
    assert alive(p)
    p, v = start()
    v = step(p, forming(v, 99.)); v = step(p, forming(v, 101.))
    seed(p, v, 'LONG')
    step(p, next_bar(v, closed=99., new=98.5))            # back below in the unseen last moment
    assert not alive(p)


def test_skipped_bars_are_not_replayed_as_on_restart():
    # No input for 3 bars (feed gap): only the bar last seen forming is checked, as before.
    p, v = start()
    step(p, next_bar(v, closed=101., new=98.5, bars=3))
    assert alive(p)
    p, v = start()
    step(p, next_bar(v, closed=99., new=98.5, bars=3))     # the watched bar itself closed below
    assert not alive(p)


def test_first_look_after_start_keeps_startup_behaviour():
    p = profile(); v = view()
    a = v.values.copy(); a[-2:, H6] = 99.; native(a)        # the bar before start already crossed below
    v = OZMarketView(FeedSnapshot(v.time, v.snapshot.volume, a, 2, 'test', {}), atr_provider=v.atr)
    seed(p, v, 'LONG')
    p._process_hma_cross('1m', v)
    assert alive(p)


@pytest.mark.parametrize('direction,above,below', [('SHORT', 101., 99.), ('LONG', 99., 101.)])
def test_missed_cross_records_what_a_forming_look_would_have_recorded(direction, above, below):
    # The structure left by the closed check equals a profile that saw the bar's final value while forming.
    missed, v = start('LONG' if direction == 'SHORT' else 'SHORT', level=above)
    after = next_bar(v, closed=below, new=below)
    step(missed, after)
    watched, w = start('LONG' if direction == 'SHORT' else 'SHORT', level=above)
    step(watched, forming(w, below))
    assert missed.hma_cross_extremes['1m', direction] == watched.hma_cross_extremes['1m', direction]
