"""Immutable BAR snapshots vs the supplied current implementation, bit-exact."""
from dataclasses import fields, replace
import hashlib
import math
import random
import struct

import pytest

from generic_backtest.calendar import CalendarRegistry
from generic_backtest.canonical import bits, encode
from generic_backtest.contracts import InstrumentSpec, TIMEFRAMES
from generic_backtest.evaluation import TimeframeCloseGate, close_evaluation_view
from generic_backtest.market import GenericMarketCore, GenericCandleBook
from pit.models import BarState, TickRecord
from conditional_validation.optimization_reference import BaselineCandleBook

CAL={'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}


def raw(ms, ordinal, price, flags=10):
    def binary(value):return struct.unpack('<Q',struct.pack('<d',value))[0]
    return TickRecord('bar-optimization-exact',ordinal,ms//1000,binary(price),
                      binary(price+.1),binary(price),ordinal,ms,flags,binary(1.))


def core_pair(mode, lookbacks, start=0):
    instrument=InstrumentSpec('TEST','SYNTHETIC_BAR_EXACT',chart_mode=mode)
    calendar=CalendarRegistry(CAL)
    left=GenericMarketCore(instrument,calendar,start,lookbacks,'SAME_REFERENCE_NAMESPACE')
    left.book=BaselineCandleBook('TEST',calendar,start,lookbacks,mode)
    right=GenericMarketCore(instrument,calendar,start,lookbacks,'SAME_REFERENCE_NAMESPACE')
    return left,right


def signature(value):return encode(bits(value))


@pytest.mark.parametrize('mode',['BID','LAST'])
@pytest.mark.parametrize('lookback',[0,1,8,681])
def test_bar_every_field_all_tfs_boundaries_and_duplicates(mode,lookback):
    left,right=core_pair(mode,{tf:lookback for tf in TIMEFRAMES})
    # Intrabar, 5/15/30/60 min, midnight and large session gaps. Every TF in
    # the SDK is covered; completed and forming rows use the same old rule.
    times=sorted([0,0,1,59_999,60_000,60_000,60_001,119_999,120_000,
                  179_999,180_000,299_999,300_000,300_000,359_999,360_000,
                  899_999,900_000,1_799_999,1_800_000,3_599_999,3_600_000,
                  3_600_000,3_600_001,86_399_999,86_400_000,86_400_001,
                  3*86_400_000,3*86_400_000+1])
    snapshots=[]
    for i,ms in enumerate(times,1):
        t=raw(ms,i,100.+math.sin(i)*5.)
        a=left.step(t);b=right.step(t)
        assert signature(a)==signature(b)
        assert left.book.interval_cache_hits==right.book.interval_cache_hits
        snapshots.append((b,signature(b)))
    assert all(signature(view)==saved for view,saved in snapshots)


@pytest.mark.parametrize('mode',['BID','LAST'])
def test_bar_gap_bad_prices_flags_and_partial_start(mode):
    left,right=core_pair(mode,{'1m':4,'5m':4,'15m':4,'30m':4,'1h':4},start=13_000_000)
    prices=[100.,101.,math.nan,math.inf,-math.inf,0.,-0.,-1.,102.,99.,103.]
    times=[13,14,59_999,60_000,60_000,60_001,90_000,120_000,300_000,3_600_000,3_600_001]
    for i,(ms,price) in enumerate(zip(times,prices),1):
        t=raw(ms,i,price,flags=(0,2,4,8,10)[i%5])
        gap=i in (2,3,4,8)
        assert signature(left.step(t,gap))==signature(right.step(t,gap))


@pytest.mark.parametrize('mode',['BID','LAST'])
def test_bar_seed_completion_metadata_preserved(mode):
    left,right=core_pair(mode,{'1m':3})
    for core in (left,right):
        core.step(raw(0,1,100.))
        core.book.forming['1m']=replace(core.book.forming['1m'],quality='CUSTOM_TEST_QUALITY',
            seed_quality='CUSTOM_TEST_SEED',complete_at_order=999,revision=71,gap_before=True)
    assert signature(left.step(raw(1,2,101.)))==signature(right.step(raw(1,2,101.)))
    bar=right.current.bars('1m')[-1]
    assert (bar.seed_quality,bar.complete_at_order,bar.quality,bar.revision,bar.gap_before)==(
        'CUSTOM_TEST_SEED',999,'CUSTOM_TEST_QUALITY',72,True)


def test_bar_dataclass_shape_guard_for_future_model_edits():
    # A future added field must trigger review of the explicit constructor.
    assert tuple(f.name for f in fields(BarState))==(
        'bar_id','symbol','timeframe','open_ns','nominal_end_ns','open','high','low','close',
        'tick_volume','first_ordinal','last_ordinal','prefix','revision','state','quality',
        'gap_before','complete_at_order','seed_quality')


def test_five_minute_close_uses_current_hour_not_future_extreme():
    instrument=InstrumentSpec('TEST','SYNTHETIC_BAR_EXACT')
    left,right=core_pair('BID',{'5m':8,'1h':8})
    gates=[TimeframeCloseGate(instrument,CalendarRegistry(CAL),'5m') for _ in range(2)]
    observed=[[],[]]
    times=[0,300_000,600_000,900_000,1_200_000,1_500_000,1_800_000,1_920_000,2_100_000,2_400_000,2_700_000,3_000_000,3_300_000,3_600_000]
    for i,ms in enumerate(times,1):
        price=9999. if ms==3_000_000 else 100.+i
        t=raw(ms,i,price)
        for index,(core,gate) in enumerate(zip((left,right),gates)):
            v,_=core.step(t);close=gate.on_tick(t)
            if close:observed[index].append(close_evaluation_view(v,'5m',close))
    assert signature(observed[0])==signature(observed[1])
    before=[v for v in observed[1] if v.token.now_ns<3_000_000*1_000_000]
    assert before and all(v.bars('1h')[-1].high<9999. for v in before)
    assert any(v.bars('1h')[-1].high==9999. for v in observed[1])
    assert len(observed[1])==12  # Every 5m close; not one 1h callback.
    assert all(v.bars('5m')[-1].state=='COMPLETED' for v in observed[1])


def test_bar_randomized_prices_gaps_and_flags_bit_exact():
    rng=random.Random(248713)
    left,right=core_pair('BID',{tf:5 for tf in TIMEFRAMES})
    ms=0
    for i in range(1,2001):
        ms+=rng.choice((0,0,1,10,1000,60_000,3_600_000))
        price=rng.choice((rng.uniform(.01,5000.),100.,100.,math.nan,0.,-0.,-1.,math.inf))
        t=raw(ms,i,price,rng.choice((0,2,4,8,10)))
        g=rng.randrange(20)==0
        assert signature(left.step(t,g))==signature(right.step(t,g))
