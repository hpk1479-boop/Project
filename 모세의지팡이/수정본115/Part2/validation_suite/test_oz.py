"""Exact OZ observation/state/event parity; public Pandas boundary is retained."""
import sys,math,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pytest
from oz_fixtures import Sequence,PROFILES,SPECS,full_state
from generic_backtest.watch.engines.oz import HistoricalOZEngine,OZEnvironment
from generic_backtest.watch.engines.oz_rules import _OZRules, Candidate
from generic_backtest.watch.engines.inputs import exact_tree,frame_signature
from generic_backtest.watch.engines.array_frame import ArrayFrame,from_validated_frame
from generic_backtest.contracts import GenericError,TIMEFRAMES
from pit.models import AsOfToken


def assert_pair(a,b,data,token,env=None,delivery=True):
    ea=a.observe(data,token,environments=env,delivery_succeeded=delivery)
    eb=b.observe(data,token,environments=env,delivery_succeeded=delivery)
    assert exact_tree(ea)==exact_tree(eb)
    assert exact_tree(full_state(a))==exact_tree(full_state(b))
    return ea

@pytest.mark.parametrize('vm,tm',PROFILES)
@pytest.mark.parametrize('case',['oscillating','nan','equal'])
def test_observation_order_full_state(vm,tm,case):
    a=HistoricalOZEngine('TEST',vm,tm,use_numpy=False,max_outin_bars=3)
    b=HistoricalOZEngine('TEST',vm,tm,use_numpy=True,max_outin_bars=3)
    for i,(data,token,env,delivery) in enumerate(Sequence(count=70,rows=32,scenario=case)):
        if i==41:a.mark_observation_gap();b.mark_observation_gap()
        assert_pair(a,b,data,token,env,delivery)
    assert b.numpy_stats['array_frames']==210


def targeted_frames(direction,step=0,*,one_way=False,opposite=False,break_b0=True,mid_valid=True):
    # Step 0 OUT/alignment-before-cross, step 1 IN+cross in same bar,
    # step 2+ advancing bars; deliberately enable nonempty final decisions.
    now=pd.Timestamp('2025-01-13').value+max(step-1,0)*60_000_000_000+(1 if step==1 else 0)
    result={}
    long=direction=='LONG';h_before=99. if long else 101.;h_after=101. if long else 99.
    for tf in ('1m','3m','6m'):
        period=TIMEFRAMES[tf]*1_000_000_000;bucket=now//period
        times=pd.to_datetime((bucket+np.arange(-11,1,dtype='int64'))*period)
        df=pd.DataFrame({'time':times,'open':np.full(12,100.),'close':np.full(12,100.),
            'high':np.full(12,110.),'low':np.full(12,90.),'volume':np.ones(12,dtype='int64'),
            'hma_6':np.full(12,h_before),'hma_17':np.full(12,100.),'atr_14':np.full(12,4.),
            'wonbi_lower':np.full(12,99.),'wonbi_upper':np.full(12,101.)})
        if tf=='1m':
            # All bars since the cross retain alignment; preceding opposite
            # region has its exact equal-price extreme tie at its newest row.
            if step>=1:
                cross=pd.Timestamp('2025-01-13')
                mask=df['time']>=cross
                df.loc[mask,'hma_6']=h_after
                df.loc[mask,'low']=95.;df.loc[mask,'high']=105.
                if one_way:
                    after=df['time']>cross
                    df.loc[after,'close']=101. if long else 99.
            df.iat[-1,df.columns.get_loc('low')]=89. if step>=2 and break_b0 and long else 95.
            df.iat[-1,df.columns.get_loc('high')]=111. if step>=2 and break_b0 and not long else 105.
            if opposite and step>=2:df.iat[-1,df.columns.get_loc('hma_6')]=h_before
        else:
            df['hma_6']=h_after;df['open']=102. if long else 98.
        for val,lo,hi,slope,zone in SPECS.values():
            value=(30. if long else 70.) if ((tf=='1m' and step==0) or (tf=='3m' and mid_valid)) else 50.
            df[val]=value;df[lo]=40.;df[hi]=60.;df[slope]=1. if long else -1.;df[zone]=0.
        df.attrs.update(symbol='TEST',timeframe=tf,available_at_ns=now,source_ordinal=step,input_prefix=f'target-{step}')
        result[tf]=df
    token=AsOfToken('targeted',0,now,step,f'target-{step}','B','F','V')
    return result,token

@pytest.mark.parametrize('vm,tm',PROFILES)
@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_nonempty_alerts_all_profiles_and_true_b0(vm,tm,direction):
    a=HistoricalOZEngine('TEST',vm,tm,use_numpy=False)
    b=HistoricalOZEngine('TEST',vm,tm,use_numpy=True)
    env={('1m',direction):OZEnvironment(frozenset({'armed'}))}
    events=[]
    for i in range(3):
        data,token=targeted_frames(direction,i)
        events.extend(assert_pair(a,b,data,token,env))
    alerts=[e for e in events if e['kind']=='OZ_LOCAL_ALERT' and e['timeframe']=='1m']
    assert len(alerts)==1 and alerts[0]['direction']==direction
    decision=alerts[0]['payload']['decision']
    assert decision.true_b0_price==(90. if direction=='LONG' else 110.)
    # Part1 LIVE touch rule: B0 immediate, otherwise >=2 distinct accumulated touches (HMA17 + WONBI here).
    assert decision.trigger.trigger_name==('BO_BREAK' if 'BREAKER' in tm else 'HMA17, WONBI')
    assert any(e['kind']=='OZ_FAMILY_REGISTERED' for e in events)

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('reason',['ONE_WAY','OPPOSITE_HMA','TIMER_TRUE_B0','TIMER_HMA_CROSS','FAMILY_EXPIRED'])
def test_cancel_expire_timing(direction,reason):
    kwargs=dict(max_bars=100,max_cross_bars=100,max_outin_bars=100)
    if reason=='TIMER_TRUE_B0':kwargs['max_bars']=1
    if reason=='TIMER_HMA_CROSS':kwargs['max_cross_bars']=0
    if reason=='FAMILY_EXPIRED':kwargs['max_outin_bars']=1
    a=HistoricalOZEngine('TEST','NORMAL','OZ',use_numpy=False,**kwargs)
    b=HistoricalOZEngine('TEST','NORMAL','OZ',use_numpy=True,**kwargs)
    env={('1m',direction):OZEnvironment(frozenset({'blocked-delivery'}),external_required=True)}
    events=[]
    for i in range(6 if reason=='ONE_WAY' else 4):
        data,token=targeted_frames(direction,i,one_way=reason=='ONE_WAY',opposite=reason=='OPPOSITE_HMA',break_b0=False)
        events.extend(assert_pair(a,b,data,token,env))
    relevant=[e for e in events if e['timeframe']=='1m' and e['direction']==direction]
    if reason=='FAMILY_EXPIRED':assert sum(e['kind']=='OZ_FAMILY_EXPIRED' for e in relevant)==4
    else:assert any(e['kind']=='OZ_BASE_CANCELLED' and reason in e['payload']['reason'] for e in relevant)

@pytest.mark.parametrize('kind',['object','string-time','timezone','nullable'])
def test_non_native_dtype_fallback(kind):
    a=HistoricalOZEngine('TEST',use_numpy=False);b=HistoricalOZEngine('TEST',use_numpy=True)
    for i,(data,token,env,delivery) in enumerate(Sequence(count=8,rows=12)):
        data={tf:df.copy(deep=True) for tf,df in data.items()}
        for df in data.values():
            if kind=='object':df['extra']='unused label'
            elif kind=='string-time':df['time']=df['time'].astype(str)
            elif kind=='timezone':df['time']=df['time'].dt.tz_localize('UTC')
            elif kind=='nullable':df['volume']=df['volume'].astype('Int64')
        assert_pair(a,b,data,token,env,delivery)
    assert b.numpy_stats['reference_fallback_frames']==24


def test_scalar_search_and_one_way_match_special_values():
    data,_=targeted_frames('LONG',5,one_way=True,break_b0=False)
    df=data['1m'];arr=from_validated_frame(df)
    assert isinstance(arr,ArrayFrame)
    assert frame_signature(df)==frame_signature(df,column_arrays=arr.arrays,dtype_names=arr.dtype_names)
    for target in [None,pd.NaT,*list(df['time']),pd.Timestamp('1900'),pd.Timestamp('9999'),df['time'].iat[-1].tz_localize('UTC')]:
        assert arr.bars_since(target)==_OZRules._bars_since_event(df,target)
    for value in [float('nan'),float('inf'),float('-inf'),100.,-0.,0.]:
        df.iat[-2,df.columns.get_loc('close')]=value
        arr=from_validated_frame(df)
        for direction in ['LONG','SHORT']:
            for cross in [pd.Timestamp('1900'),pd.NaT,df['time'].iat[-1]]:
                assert arr.one_way_reason(direction,cross)==_OZRules._one_way_reason(df,direction,cross)
            assert _OZRules._reconstruct_pre_cross_extreme(arr,direction)==_OZRules._reconstruct_pre_cross_extreme(df,direction)


def test_retry_collision_and_future_boundary_still_enforced():
    a=HistoricalOZEngine('TEST',use_numpy=True)
    data,token,env,delivery=next(iter(Sequence(count=1)))
    a.observe(data,token,environments=env)
    state=exact_tree(full_state(a))
    assert a.observe(data,token,environments=env)==()
    assert exact_tree(full_state(a))==state
    data['1m'].iat[-1,data['1m'].columns.get_loc('close')]+=1.
    with pytest.raises(GenericError,match='E_ENGINE_RETRY_COLLISION'):a.observe(data,token,environments=env)
    data['1m'].iat[-1,data['1m'].columns.get_loc('time')]=pd.Timestamp(token.now_ns+1)
    with pytest.raises(GenericError,match='E_FUTURE_READ'):a.observe(data,token,environments=env)

@pytest.mark.parametrize('cow',[False,True])
def test_readonly_columns_and_reacquisition_after_cow(cow):
    with pd.option_context('mode.copy_on_write',cow):
        data,_=targeted_frames('LONG',1);df=data['1m'];old=df.copy(deep=True)
        arr=from_validated_frame(df)
        with pytest.raises(ValueError):arr.arrays['close'][0]=123.
        pd.testing.assert_frame_equal(df,old,check_exact=True)
        df.iat[-1,df.columns.get_loc('close')]=123.
        df['open']=np.arange(len(df),dtype='float64')
        new=from_validated_frame(df)
        assert new.iloc[-1].get('close')==123.
        assert new.iloc[-1].get('open')==len(df)-1
        assert frame_signature(df)==frame_signature(df,column_arrays=new.arrays,dtype_names=new.dtype_names)


def test_public_export_fallback_when_private_hook_missing(monkeypatch):
    data,_=targeted_frames('LONG',1);df=data['1m']
    monkeypatch.setattr(pd.DataFrame,'_get_column_array',None)
    arr=from_validated_frame(df)
    assert isinstance(arr,ArrayFrame)
    assert frame_signature(df)==frame_signature(df,column_arrays=arr.arrays,dtype_names=arr.dtype_names)


def test_dataframe_subclass_retains_original_access_semantics():
    class CustomFrame(pd.DataFrame):pass
    data,_=targeted_frames('LONG',1)
    frame=CustomFrame(data['1m'])
    assert from_validated_frame(frame) is frame
