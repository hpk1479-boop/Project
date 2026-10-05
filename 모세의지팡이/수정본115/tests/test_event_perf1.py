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


def test_bundle_cache_copies_features_attrs_and_invalidates(modules,monkeypatch):
    oz,compat,_=modules;s=snap()
    b=SimpleNamespace(feeds={('XAUUSD+','1m'):s},snapshot=lambda *args:s,_frame_cache={},_calculations={})
    a=BoardFrames(b,compat,oz);c=BoardFrames(b,compat,oz)
    calls=[];original=compat.StaffCompat._compose
    def track(self,*args,**kwargs):calls.append(1);return original(self,*args,**kwargs)
    monkeypatch.setattr(compat.StaffCompat,'_compose',track)
    one=a.select('XAUUSD+',['1m'],['PRICE'])['1m'];expected=one.copy(deep=True)
    one.iloc[-1,one.columns.get_loc('close')]=-999
    one['injected']=True;one.attrs['nested']={'bad':[]}
    two=c.select('XAUUSD+',['1m'],['PRICE'])['1m']
    pd.testing.assert_frame_equal(two,expected);assert 'nested' not in two.attrs
    assert len(calls)==1 and len(two)==96
    b._frame_cache={}
    c.select('XAUUSD+',['1m'],['PRICE']);assert len(calls)==2








def test_special5_setup_history_is_not_cut_to_a_recent_hma_window():
    f=raw()
    assert required_rows(f,'1m',('HMA',),role='composer')==650
    assert required_rows(f,'1m',('EMA','HMA'),role='composer')==96






@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_pre_cross_vectorized_extreme_preserves_breaks_and_latest_tie(modules,direction):
    oz,_,_=modules;f=raw();sign=-1 if direction=='LONG' else 1
    f['hma_6']=100+sign;f['hma_17']=100
    col='low' if direction=='LONG' else 'high'
    f[col]=100;f.loc[600,col]=50 if direction=='LONG' else 150
    f.loc[640,col]=f.loc[600,col]
    assert oz.OZMonitor._reconstruct_pre_cross_extreme(f,direction)==(f.loc[640,col],f.loc[640,'time'])
    f.loc[645,'hma_6']=np.nan
    result=oz.OZMonitor._reconstruct_pre_cross_extreme(f,direction)
    assert result==(100.,f.time.iloc[-2])
    f.loc[len(f)-2,'hma_6']=100-sign
    assert oz.OZMonitor._reconstruct_pre_cross_extreme(f,direction) is None






@pytest.mark.parametrize('direction,col,sign',[('LONG','low',-1),('SHORT','high',1)])
def test_oz_breaker_requires_strict_forming_break(modules,direction,col,sign):
    oz,_,_=modules;f=raw();f[col]=100
    cross=f.time.iloc[-3]
    assert not oz.OZMonitor._breaker_bo_break_trigger(f,direction,100,cross)
    f.loc[len(f)-1,col]=100+sign
    assert oz.OZMonitor._breaker_bo_break_trigger(f,direction,100,cross)
    assert not oz.OZMonitor._breaker_bo_break_trigger(f,direction,100,f.time.iloc[-1])
    f.loc[len(f)-2,col]=100+sign
    assert not oz.OZMonitor._breaker_bo_break_trigger(f,direction,100,cross)




@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['B0','HMA17','WONBI'])
def test_oz_price_triggers_use_forming_range_and_inclusive_edges(modules,direction,kind):
    oz,_,_=modules;f=raw();f.loc[len(f)-1,['low','high']]=[99,101]
    for level in (99,100,101):assert oz.OZMonitor._trigger_state(f,direction,level,kind)=='TOUCH'
    for level in (98.999,101.001,np.nan):assert oz.OZMonitor._trigger_state(f,direction,level,kind)=='MISS'
    f.loc[len(f)-2,['low','high']]=[1,1000]
    assert oz.OZMonitor._trigger_state(f,direction,500,kind)=='MISS'


@pytest.mark.parametrize('direction,sign',[('LONG',1),('SHORT',-1)])
def test_oz_closed_candle_and_hull_turn_positive_negative(modules,direction,sign):
    oz,_,_=modules;f=raw().tail(6).reset_index(drop=True)
    f['open']=100.;f['close']=100.-sign
    cross=f.time.iloc[0]
    assert oz.OZMonitor._candle_pullback_trigger(f,cross,direction)==('THREE_BEAR' if sign==1 else 'THREE_BULL')
    assert oz.OZMonitor._candle_pullback_trigger(f,f.time.iloc[-2],direction) is None
    f.loc[len(f)-2,'close']=100.+sign
    assert oz.OZMonitor._candle_pullback_trigger(f,cross,direction) is None
    f['hma_6']=100.
    f.loc[len(f)-3,'hma_6']=100.+sign;f.loc[len(f)-2,'hma_6']=100.-sign
    assert oz.OZMonitor._hma6_turn_pullback_trigger(f,cross,direction)
    assert not oz.OZMonitor._hma6_turn_pullback_trigger(f,f.time.iloc[-2],direction)
    f.loc[len(f)-1,'hma_6']=999
    assert oz.OZMonitor._hma6_turn_pullback_trigger(f,cross,direction)
    f.loc[len(f)-2,'hma_6']=100.+sign
    assert not oz.OZMonitor._hma6_turn_pullback_trigger(f,cross,direction)


@pytest.mark.parametrize('direction,sign',[('LONG',1),('SHORT',-1)])
def test_oz_alignment_and_higher_tf_gate(modules,direction,sign):
    oz,_,_=modules;f=raw();f.loc[len(f)-1,['open','hma_6','hma_17']]=[100+2*sign,100+sign,100]
    assert oz.OZMonitor._hma_aligned(f,direction)
    assert oz.OZMonitor._higher_tf_open_vs_hma6(f,direction)
    f.loc[len(f)-1,['open','hma_6','hma_17']]=100
    assert not oz.OZMonitor._hma_aligned(f,direction)
    assert not oz.OZMonitor._higher_tf_open_vs_hma6(f,direction)


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


def test_removed_oz_gate_helpers_are_not_available(modules):
    oz, _, _ = modules
    for name in ('REGIME_COLUMNS', 'SUPER_COLUMNS', 'regime_filter', 'super_filter'):
        assert not hasattr(oz, name)
    for name in ('use_regime', 'use_super', '_super_fact'):
        assert not hasattr(oz.OZMonitor, name)
