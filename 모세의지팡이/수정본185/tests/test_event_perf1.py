"""PERF1 correctness, mutation isolation and closed/forming boundary cases."""
import sys
from pathlib import Path
from types import SimpleNamespace,MappingProxyType
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from test_event_e2_domains import snap
from staff_schema import legacy_frame
from event_engine.history import required_rows
from event_engine.levels import ExternalLevelCache
from event_engine.model import freeze,Kind
from event_engine.frames import BoardFrames
from oz_engine.profile import OZProfile
from test_oz_rewrite import view,edit


@pytest.fixture
def modules():
    import monitor_OZ,staff_compat,strategy_SWEEP
    return monitor_OZ,staff_compat,strategy_SWEEP


def raw():return legacy_frame('XAUUSD+','1m',snap())


def test_sealed_values_do_not_trust_mutable_backing():
    owner={'nested':[1,2]};sealed=freeze(MappingProxyType(owner))
    owner['nested'][0]=9
    assert sealed['nested'][0]==1 and freeze(sealed) is sealed
    array=np.arange(10.);view=array.view();view.flags.writeable=False
    sealed_array=freeze(view);array[0]=99
    assert sealed_array[0]==0
    reused=freeze(sealed_array)
    assert np.shares_memory(reused,sealed_array) and reused is not sealed_array
    with pytest.raises(ValueError):reused.flags.writeable=True








# Pre-cross extreme, breaker break, candle pullback and HMA6 turn boundaries are
# checked on the live OZMarketView in test_oz_rewrite.py.


@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['B0','HMA17','WONBI'])
def test_oz_price_triggers_use_forming_range_and_inclusive_edges(direction,kind):
    low=np.full(30,99.);high=np.full(30,101.);v=view(low=low,high=high)
    for level in (99,100,101):assert OZProfile._trigger_state(v,direction,level,kind)=='TOUCH'
    for level in (98.999,101.001,np.nan):assert OZProfile._trigger_state(v,direction,level,kind)=='MISS'
    low[-2],high[-2]=1,1000
    assert OZProfile._trigger_state(edit(v,low=low,high=high),direction,500,kind)=='MISS'


@pytest.mark.parametrize('direction,sign',[('LONG',1),('SHORT',-1)])
def test_oz_alignment_and_higher_tf_gate(direction,sign):
    last=lambda value:np.r_[np.full(29,100.),value]
    v=view(open=last(100+2*sign),hma_6=last(100+sign),hma_17=100)
    assert OZProfile._hma_aligned(v,direction)
    assert OZProfile._higher_tf_open_vs_hma6(v,direction)
    v=view(open=100,hma_6=100,hma_17=100)
    assert not OZProfile._hma_aligned(v,direction)
    assert not OZProfile._higher_tf_open_vs_hma6(v,direction)


@pytest.mark.parametrize('direction,column,value,miss',[('SHORT','high',105,104.9),('LONG','low',95,95.1)])
def test_sweep_closed_boundary_touch_and_not_forming(modules,direction,column,value,miss):
    _,_,sw=modules;f=raw().tail(6).reset_index(drop=True)
    f[['high','low','close']]=[101.,99.,100.]
    spec=sw.SweepSpec.from_payload({'watch_id':'test','symbol':'XAUUSD+','source_tf':'1m','levels':['PDH']})
    lv=[{'id':'test','direction':direction,'level_code':'PDH','level_name':'test','price':value}]
    f.loc[len(f)-1,column]=value
    assert not sw.ExternalLiquidityDetector(spec).process(f,lv)
    f.loc[len(f)-2,column]=miss
    assert not sw.ExternalLiquidityDetector(spec).process(f,lv)
    f.loc[len(f)-2,column]=value
    detector=sw.ExternalLiquidityDetector(spec);events=detector.process(f,lv)
    assert len(events)==1 and events[0]['kind']=='SWEEP_TOUCH'
    assert events[0]['event_time']==f.time.iloc[-2].timestamp()
    assert not detector.process(f,lv)


def test_sweep_cached_levels_full650_and_short_windows(modules):
    _,_,sw=modules;frames=[]
    for tf,freq in [('1d','1D'),('5m','5min'),('4h','4h'),('8h','8h')]:
        f=raw();f['time']=pd.date_range(end='2026-09-25 17:00',periods=650,freq=freq);frames.append(f)
    args={'session_london':'16:00-21:00','session_newyork':'21:00-05:00'}
    full=sw.build_external_levels(*frames,**args)
    small=[f.tail(required_rows(f,tf,(),role='sweep')).reset_index(drop=True) for f,tf in zip(frames,('1d','5m','4h','8h'))]
    assert sw.build_external_levels(*small,**args)==full
    cache=ExternalLevelCache();assert cache.build(sw.build_external_levels,'XAUUSD+',*small,**args)==full
    for f in small:f.loc[len(f)-1,['high','low']]=[999,-999]
    assert cache.build(sw.build_external_levels,'XAUUSD+',*small,**args)==full
    assert cache.computations==1
    small[0].loc[len(small[0])-2,'high']+=500
    assert cache.build(sw.build_external_levels,'XAUUSD+',*small,**args)==sw.build_external_levels(*small,**args)
    assert cache.computations==2


def test_unrelated_signals_rejected_by_domains(modules):
    oz,compat,sw=modules
    import durable_protocol,strategy_FVG,strategy_INDICATOR
    from event_engine.oz_processor import OZProcessor
    from event_engine.sweep_state import SweepProcessor
    from event_engine.fvg_state import FVGProcessor
    from event_engine.indicator_consumer import IndicatorConsumer
    from event_engine.watch_consumer import WatchConditionConsumer
    domains=[OZProcessor(oz,compat,{}),SweepProcessor(sw,durable_protocol,compat,oz),
        FVGProcessor(strategy_FVG,durable_protocol),IndicatorConsumer(strategy_INDICATOR,durable_protocol,compat,oz),WatchConditionConsumer(oz,compat,{})]
    for consumer in domains:
        assert not consumer.accepts_event(SimpleNamespace(kind=Kind.SIGNAL,payload={'content':{'type':'NOTIFICATION'}}))
        assert consumer.accepts_event(SimpleNamespace(kind=Kind.MARKET_BUNDLE,payload={}))


# 수정본162: the check that only looked for the removed OZ gate helpers was deleted.
