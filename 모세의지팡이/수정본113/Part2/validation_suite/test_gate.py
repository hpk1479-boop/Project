import math
import struct
from dataclasses import replace
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.contracts import InstrumentSpec, GenericError
from generic_backtest.evaluation import (evaluation_base_tf, TimeframeCloseGate,
    OneMinuteCloseGate, close_evaluation_view)
from generic_backtest.market import GenericMarketCore
from pit.models import TickRecord

CAL={'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}
INST=InstrumentSpec('TEST','synthetic-validation-only')

def tick(sec,i,price=100.,flags=2):
    bits=lambda x:struct.unpack('<Q',struct.pack('<d',x))[0]
    return TickRecord('synthetic',i,int(sec),bits(price),bits(price+.1),bits(price),1,int(sec*1000),flags,bits(1.))

@pytest.mark.parametrize('tfs,base,count',[
    (['1m','3m','15m'],'1m',59),(['3m','6m','15m'],'3m',19),
    (['6m','15m'],'6m',9),(['15m'],'15m',3)])
def test_gate_and_actual_callback_count(tfs,base,count):
    assert evaluation_base_tf(tfs)==base
    assert evaluation_base_tf(list(reversed(tfs)))==base
    calendar=CalendarRegistry(CAL)
    gate=TimeframeCloseGate(INST,calendar,base)
    core=GenericMarketCore(INST,calendar,0,{tf:5 for tf in tfs},'gate')
    observed=[]
    def callback(view):observed.append(view)
    for i,sec in enumerate(range(0,3600,10),1):
        t=tick(sec,i,100+i)
        view,_=core.step(t)
        closed=gate.on_tick(t)
        if closed is not None:callback(close_evaluation_view(view,base,closed))
    assert len(observed)==gate.count==count
    for v in observed:
        basebars=v.bars(base)
        assert basebars[-1].state=='COMPLETED'
        assert basebars[-1].last_ordinal<v.token.source_ordinal
        assert basebars[-1].nominal_end_ns<=v.token.now_ns
        for tf in tfs:
            if tf!=base:
                current=v.bars(tf)[-1]
                assert current.state=='FORMING'
                assert current.last_ordinal==v.token.source_ordinal
                assert current.close==100+v.token.source_ordinal
                assert current.high==current.close
    # Earlier immutable snapshots must not change after many future ticks.
    assert observed[0].bars(base)[-1].high<observed[-1].bars(base)[-1].high


def test_higher_ohlc_exact_prefix_and_future_spike():
    calendar=CalendarRegistry(CAL);base='3m';tfs=['3m','6m','15m']
    core=GenericMarketCore(INST,calendar,0,{tf:8 for tf in tfs},'prefix')
    gate=TimeframeCloseGate(INST,calendar,base)
    records=[];snapshots=[]
    for i,sec in enumerate(range(0,1200,10),1):
        price=90000. if sec==190 else 100+math.sin(i)*10
        t=tick(sec,i,price);records.append((sec,price))
        view,_=core.step(t);event=gate.on_tick(t)
        if not event:continue
        v=close_evaluation_view(view,base,event);snapshots.append(v)
        for tf in tfs:
            bar=v.bars(tf)[-1]
            selected=[p for s,p in records if bar.open_ns<=s*10**9 and
                      (s*10**9<bar.nominal_end_ns if tf==base else s*10**9<=v.token.now_ns)]
            assert (bar.open,bar.high,bar.low,bar.close)==(selected[0],max(selected),min(selected),selected[-1])
            assert bar.tick_volume==len(selected)
    assert snapshots[0].bars('15m')[-1].high<90000.
    assert snapshots[1].bars('15m')[-1].high==90000.


def test_gap_no_synthetic_closes_or_eof():
    gate=TimeframeCloseGate(INST,CalendarRegistry(CAL),'3m')
    assert gate.on_tick(tick(1,1)) is None
    assert gate.on_tick(tick(180,2,flags=4)) is None # quote-only does not close
    assert gate.on_tick(tick(181,3,float('nan'))) is None
    event=gate.on_tick(tick(901,4))
    assert (event.bar_open_ns,event.nominal_end_ns,event.observed_at_ns)==(0,180*10**9,901*10**9)
    assert gate.count==1 # skipped calendar buckets are not invented
    assert gate.on_tick(tick(902,5)) is None
    assert gate.count==1


def test_last_price_policy_and_order():
    gate=TimeframeCloseGate(replace(INST,chart_mode='LAST'),CalendarRegistry(CAL),'6m')
    assert gate.on_tick(tick(0,1,flags=2)) is None
    assert gate.on_tick(tick(0,2,flags=8)) is None
    assert gate.on_tick(tick(360,3,flags=8)) is not None
    with pytest.raises(GenericError):gate.on_tick(tick(360,3,flags=8))


def test_closed_base_frame_adapter():
    from generic_backtest.fast.frame_cache import PITFrameCache
    from generic_backtest.watch.engines.frames import frame_from_bars
    core=GenericMarketCore(INST,CalendarRegistry(CAL),0,{'3m':5,'15m':5},'frame')
    gate=TimeframeCloseGate(INST,CalendarRegistry(CAL),'3m')
    for i,sec in enumerate(range(0,600,10),1):
        t=tick(sec,i);v,_=core.step(t);e=gate.on_tick(t)
        if e:
            v=close_evaluation_view(v,'3m',e)
            a=PITFrameCache().frame('TEST','3m',v.bars('3m'),v.token)
            b=frame_from_bars('TEST','3m',v.bars('3m'),v.token)
            import pandas as pd
            pd.testing.assert_frame_equal(a,b,check_exact=True)


def test_zero_lookback_retains_closed_evaluation_row_without_new_dependency():
    from generic_backtest.evaluation import close_gate_lookbacks
    requested={'3m':0,'15m':0};retained=close_gate_lookbacks(requested,'3m')
    assert requested=={'3m':0,'15m':0}
    assert retained=={'3m':1,'15m':0}
    assert close_gate_lookbacks({'15m':0},'3m')=={'15m':0}
    core=GenericMarketCore(INST,CalendarRegistry(CAL),0,retained,'zero')
    gate=TimeframeCloseGate(INST,CalendarRegistry(CAL),'3m')
    for i,sec in enumerate((0,90,180),1):
        t=tick(sec,i);view,_=core.step(t);close=gate.on_tick(t)
    result=close_evaluation_view(view,'3m',close)
    assert len(result.bars('3m'))==1 and result.bars('3m')[-1].state=='COMPLETED'
    assert len(result.bars('15m'))==1 and result.bars('15m')[-1].state=='FORMING'


def test_result_schedule_validates_lowest_tf_and_reads_legacy():
    from generic_backtest.results import validate_close_schedule
    from generic_backtest.evaluation import SCHEDULE_VERSION
    common={'mode':'ONE_MINUTE_CLOSE','all_requested_timeframes_evaluated_together':True,
            'intrabar_touch_latch':False,'eof_forced_evaluation':False}
    for tfs,base in [(['1m','3m','15m'],'1m'),(['3m','6m','15m'],'3m'),(['6m','15m'],'6m'),(['15m'],'15m')]:
        req={'SIGNAL':{'required_timeframes':tfs}}
        schedule=dict(common,version=SCHEDULE_VERSION,base_timeframe=base,evaluation_base_tf=base,
            boundary='FIRST_ELIGIBLE_TICK_OF_NEXT_REAL_BASE_TF_BAR',
            feature_view='JUST_CLOSED_BASE_AND_POST_TICK_FORMING_HIGHER_TF')
        validate_close_schedule(schedule,req)
        with pytest.raises(GenericError):validate_close_schedule(dict(schedule,evaluation_base_tf='2m'),req)
    legacy=dict(common,version='ONE_MINUTE_CLOSE_V1',base_timeframe='1m',
        boundary='FIRST_ELIGIBLE_TICK_OF_NEXT_REAL_M1_BAR',feature_view='POST_TICK_EXISTING_PIT_VIEW')
    validate_close_schedule(legacy,{})
    with pytest.raises(GenericError):validate_close_schedule(dict(legacy,version='INVENTED'),{})
