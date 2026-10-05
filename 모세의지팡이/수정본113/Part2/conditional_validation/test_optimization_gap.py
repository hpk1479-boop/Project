"""Literal pre-optimization any() is the STEP 1 oracle; no strategy shortcut."""
import copy
import random
import pytest
from generic_backtest.runner import _GapCursor


def original_gap(gaps, previous_ns, now):
    return any(g['status'] in ('KNOWN_MISSING','ACQUISITION_ERROR')
               and previous_ns < g['end_ns'] and now >= g['start_ns']
               for g in gaps)


def gap(start, end, status='KNOWN_MISSING'):
    return {'start_ns':start, 'end_ns':end, 'status':status}


CASES = [
    ('none', [], [0,10,20,30]),
    ('unverified_only', [gap(10,20,'EMPTY_UNVERIFIED')], [0,10,15,20,30]),
    ('one_known', [gap(10,20)], [0,10,15,20,30]),
    ('many_known', [gap(50,70),gap(10,20),gap(30,40)], [0,10,25,45,60,80]),
    ('one_acquisition', [gap(10,20,'ACQUISITION_ERROR')], [0,10,15,20,30]),
    ('mixed_status', [gap(10,20),gap(30,40,'ACQUISITION_ERROR'),gap(20,30,'EMPTY_UNVERIFIED')], [0,10,20,30,40,50]),
    ('overlap', [gap(100,200),gap(150,300)], [0,100,150,200,250,300,350]),
    ('nested', [gap(100,400),gap(150,200),gap(160,180),gap(300,350)], [0,100,160,200,300,400,450]),
    ('adjacent', [gap(100,200),gap(200,300)], [0,100,200,200,300,300,400]),
    ('exact_start', [gap(100,200)], [0,99,100,100,101]),
    ('previous_equals_end', [gap(100,200)], [0,199,200,200,201]),
    ('jump_entire_interval', [gap(100,200)], [0,99,301,400]),
    ('same_timestamp', [gap(100,200)], [0,100,100,150,150,200,200]),
    ('very_long', [gap(1,10**18)], [0,1,1,10**9,10**18,10**18,10**18+1]),
    ('many_unverified', [gap(i,i+2,'EMPTY_UNVERIFIED') for i in range(5000)]+[gap(100,200),gap(300,400,'ACQUISITION_ERROR')], [0,100,150,200,300,400,500,6000]),
    ('all_past', [gap(-300,-200),gap(-200,-100)], [0,10,100]),
    ('all_future', [gap(1000,2000),gap(3000,4000)], [0,1,10,100]),
    ('short_outer_order', [gap(100,300),gap(100,200),gap(100,400)], [0,100,200,250,300,350,400,400]),
    ('point_and_reverse_preserved', [gap(100,100),gap(200,150)], [0,100,100,120,200,300]),
]


@pytest.mark.parametrize('name,gaps,ticks', CASES, ids=[c[0] for c in CASES])
def test_gap_matrix(name, gaps, ticks):
    before = copy.deepcopy(gaps)
    cursor = _GapCursor(gaps)
    previous = ticks[0]
    for now in ticks:
        assert cursor.intersects(previous,now) == original_gap(gaps,previous,now), (name,previous,now)
        previous = now
    assert gaps == before
    assert all(s in ('KNOWN_MISSING','ACQUISITION_ERROR') for s in
               [g['status'] for g in gaps if (g['start_ns'],g['end_ns']) in cursor.intervals])


def test_gap_randomized_tick_by_tick():
    rng = random.Random(20260923)
    for _ in range(1000):
        gaps = [gap(rng.randrange(-100,800),rng.randrange(-100,1000),
                    rng.choice(('KNOWN_MISSING','ACQUISITION_ERROR','EMPTY_UNVERIFIED','OTHER')))
                for _ in range(rng.randrange(70))]
        rng.shuffle(gaps)
        ticks = sorted(rng.randrange(1000) for _ in range(100))
        cursor = _GapCursor(gaps)
        previous = 0
        indices = []
        for now in ticks:
            assert cursor.intersects(previous,now) == original_gap(gaps,previous,now)
            indices.append(cursor.index)
            previous = now
        assert indices == sorted(indices)
        assert cursor.index <= len(cursor.intervals)
