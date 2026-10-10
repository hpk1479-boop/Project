"""OZ intended logic, input ownership, persistent state and event dependencies."""
import ast,json,sys,logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from staff_schema import PIPE_VALUE_COLUMNS,legacy_frame
from event_engine import FeedSnapshot,EventEngine,IngressSequencer,Kind
from event_engine.facts import EventFacts
from oz_engine.market import OZMarketView,COLUMNS
from oz_engine.profile import OZProfile
from oz_engine.controllers import ExternalLiquidityController,OZWatchController
from oz_engine.runtime import OZRuntime,SourceClock,Collector
from oz_engine.common import *
from indicator_facts import rma,rma_array,true_range_array,standalone_frame


def view(n=30,**columns):
    a=np.full((n,len(COLUMNS)),100.,dtype=float)
    defaults=dict(open=100,close=100,high=101,low=99,hma_6=101,hma_17=100,open_band_4_mid=100,wonbi_lower=90,wonbi_upper=110,
        price_hma_6=100,price_band_lower=99,price_band_upper=101,price_regime_basis=99,price_regime_lower=90,price_regime_upper=110)
    for family in ('RSI','STO','DI'):
        defaults.update({family+'_val':100,family+'_db':99,family+'_ub':101,family+'_basis':99,
                         family+'_regime_lower':90,family+'_regime_upper':110})
    for key,val in (defaults|columns).items():a[:,COLUMNS[key]]=val
    native(a)
    times=np.arange(n,dtype='int64')*60+1790380000
    s=FeedSnapshot(times,np.ones(n,dtype='int64'),a,1,'test',{})
    return OZMarketView(s,atr_provider=lambda:np.full(n,2.))


def edit(v,**columns):
    a=v.values.copy()
    for key,values in columns.items():a[:,COLUMNS[key]]=values
    native(a)
    return OZMarketView(FeedSnapshot(v.time,v.snapshot.volume,a,v.snapshot.seq+1,v.snapshot.source_epoch,{}),atr_provider=v.atr)


def native(a):
    # Test inputs now explicitly supply the native schema contract. Predicates remain unchanged.
    for p in ('price','RSI','STO','DI'):
        value,lo,hi,basis=('price_hma_6','price_band_lower','price_band_upper','price_regime_basis') if p=='price' else (p+'_val',p+'_db',p+'_ub',p+'_basis')
        v=a[:,COLUMNS[value]]
        a[:,COLUMNS[p+'_lower_out']]=np.where(v<a[:,COLUMNS[lo]],v,np.nan)
        a[:,COLUMNS[p+'_upper_out']]=np.where(v>a[:,COLUMNS[hi]],v,np.nan)
        a[:,COLUMNS[p+'_regime_slope']]=np.r_[np.nan,np.diff(a[:,COLUMNS[basis]])]


def profile(vm='BLIND',tm='OZ'):
    clock=SourceClock({});external=ExternalLiquidityController(clock,sweep_registry={})
    sender=SimpleNamespace(send=lambda *a,**k:True)
    watch=OZWatchController(sender,external,clock)
    return OZProfile('XAUUSD+',{},sender,watch,vm,tm,source_time=1790382000.)


def seed(p,v,direction='LONG',count=4):
    t=int(v.time[-3]);price=99. if direction=='LONG' else 101.
    for family in PERCENTILES[:count]:p._register_percentile_candidate('1m',direction,family,price,t,t)
    c=p.candidates['1m',direction];c.hma_b0_price=price;c.hma_b0_time=t;c.hma_cross_time=t
    p._refresh_true_b0(c);return c


def test_view_zero_copy_full_history_and_registered_memo():
    v=view(650);assert len(v)==650 and np.shares_memory(v.values,v.snapshot.values)
    assert isinstance(v.live.get('time'),int)
    with pytest.raises(ValueError):v.values[-1,0]=0
    assert v.memo('percentile_states',lambda:{'x':1}) is v.memo('percentile_states',lambda:None)
    with pytest.raises(KeyError):v.memo('anonymous',lambda:1)


@pytest.mark.parametrize('missing',[False,True])
def test_atr_full_history_same_values_without_dataframe(monkeypatch,missing):
    v=view(650,high=np.arange(650)*.123+103)
    a=v.values.copy()
    if missing:a[15:18,:4]=np.nan
    s=FeedSnapshot(v.time,v.snapshot.volume,a,2,'test',{})
    expected=standalone_frame(legacy_frame('XAUUSD+','1m',v.snapshot),'1m').get('ATR14_GENERAL').to_numpy() if not missing else rma(pd.Series(true_range_array(a[:,1],a[:,2],a[:,3])),14).to_numpy()
    facts=EventFacts();facts.invalidate({('XAUUSD+','1m'):s})
    def deny(*a,**k):raise AssertionError('OZ Fact must not build DataFrames')
    monkeypatch.setattr(pd,'DataFrame',deny)
    # np-only path must also avoid isinstance(..., pd.DataFrame).
    actual=facts.get('XAUUSD+','1m',s,'ATR14_GENERAL','oz_engine')
    np.testing.assert_array_equal(actual,expected,strict=True)


@pytest.mark.parametrize('direction,sign',[('LONG',-1),('SHORT',1)])
def test_cross_extreme_ties_and_out_in(direction,sign):
    p=profile();v=view(hma_6=100+sign)
    a=v.values.copy();col='low' if direction=='LONG' else 'high'
    a[5,COLUMNS[col]]=a[25,COLUMNS[col]]=100+10*sign
    s=FeedSnapshot(v.time,v.snapshot.volume,a,1,'test',{});v=OZMarketView(s)
    assert v.pre_cross_extreme(direction)==(100+10*sign,int(v.time[25]))
    a[-2,COLUMNS['hma_6']]=np.nan
    assert OZMarketView(FeedSnapshot(v.time,v.snapshot.volume,a,2,'test',{})).pre_cross_extreme(direction) is None
    outside=edit(v,price_hma_6=100+2*sign)
    p._process_out_in('1m',outside)
    assert not p._active_percentile_candidates('1m',direction)
    p._process_out_in('1m',v)
    assert len(p._active_percentile_candidates('1m',direction))==1
    fresh=profile();fresh._process_out_in('1m',v)
    assert not fresh._active_percentile_candidates('1m',direction)
    crossed=v.values.copy();crossed[-1,COLUMNS['hma_6']]=100-sign
    crossed=OZMarketView(FeedSnapshot(v.time,v.snapshot.volume,crossed,3,'test',{}))
    p._process_hma_cross('1m',crossed)
    assert p.hma_cross_extremes['1m',direction]==(100+10*sign,int(v.time[25]),int(v.time[-1]))
    c=p.candidates['1m',direction];p._refresh_true_b0(c)
    assert c.true_b0_price==100+10*sign


@pytest.mark.parametrize('field,limit',[('true_b0_time',10),('hma_cross_time',7),('trigger_time',7)])
@pytest.mark.parametrize('extra',[0,1])
def test_three_timer_boundaries(field,limit,extra):
    p=profile();v=view();c=seed(p,v);t=int(v.time[-1-limit-extra])
    if field=='trigger_time':
        reg=p.percentile_candidates['1m','LONG','RSI'];reg.trigger_time=t
        p._maintain_base_candidate('1m','LONG',v);assert reg.invalidated==bool(extra)
    else:
        setattr(c,field,t);cancelled,*_=p._check_base_invalidation('1m','LONG',v,c)
        assert cancelled==bool(extra)


@pytest.mark.parametrize('direction,sign',[('LONG',1),('SHORT',-1)])
def test_closed_candle_turn_one_way_and_live_touch(direction,sign):
    v=view(6,close=100-sign);cross=int(v.time[0])
    assert v.candle_pullback(direction,cross)==('THREE_BEAR' if sign==1 else 'THREE_BULL')
    assert v.candle_pullback(direction,int(v.time[-4])) is None
    assert edit(v,close=100+sign).one_way(direction,cross)==('ONE_WAY_UP' if sign==1 else 'ONE_WAY_DOWN')
    assert edit(v,close=100+sign).one_way(direction,int(v.time[-4])) is None
    h=np.full(6,100.);h[-3]=100+sign;h[-2]=100-sign
    assert edit(v,hma_6=h).hma6_turn(direction,cross)
    assert not edit(v,hma_6=h).hma6_turn(direction,int(v.time[-2]))
    for level in (99.,100.,101.):assert v.touch(level)
    for level in (98.999,101.001,np.nan):assert not v.touch(level)
    changed=v.values.copy();changed[-1,COLUMNS['close']]=100+sign
    assert OZMarketView(FeedSnapshot(v.time,v.snapshot.volume,changed,2,'test',{})).candle_pullback(direction,cross)==v.candle_pullback(direction,cross)


def test_trigger_five_kinds_accumulate_and_b0_or_two_pass():
    p=profile();v=view(6);c=seed(p,v)
    assert p._final_trigger_decision(v,'LONG',c).trigger_name=='B0'
    c.true_b0_price=90;c.outin_b0_price=90;c.hma_b0_price=90
    quiet=edit(v,low=98,high=99,hma_17=99,wonbi_lower=90)
    p._accumulate_trigger_hits('1m',quiet,'LONG',c,2)
    assert set(c.trigger_hits)=={'HMA17'}
    assert p._final_trigger_decision(quiet,'LONG',c) is None
    second=edit(quiet,hma_17=95,wonbi_lower=98)
    p._accumulate_trigger_hits('1m',second,'LONG',c,2)
    assert set(c.trigger_hits)=={'HMA17','WONBI'}
    assert p._final_trigger_decision(second,'LONG',c) is not None
    v=edit(v,close=99,hma_6=[100,100,100,101,99,101],wonbi_lower=99)
    hits=p._current_trigger_hits(v,'LONG',99,int(v.time[0]),True)
    assert set(hits)=={'B0','HMA17','WONBI','CANDLE','HMA6_TURN'}
    assert 'B0' not in p._current_trigger_hits(v,'LONG',99,int(v.time[0]),False)


@pytest.mark.parametrize('vm,tm',PROFILE_KEYS)
def test_all4_completion_and_failed_filters(vm,tm):
    p=profile(vm,tm);v=view();c=seed(p,v)
    # Candidate history stays fixed; BREAKER is the original strict price break.
    vals=np.full(len(v),100.);vals[-3]=98.
    basis=np.arange(len(v))*.01+99
    v=edit(v,price_hma_6=vals,**{f+'_val':vals for f in ('RSI','STO','DI')})
    if p.use_breaker:
        low=np.full(len(v),99.);low[-1]=98.9;v=edit(v,low=low)
    middle=edit(v,price_hma_6=98,open=103,**{f+'_val':98 for f in ('RSI','STO','DI')})
    upper=edit(v,price_hma_6=100,price_regime_basis=basis,**{f+'_val':100 for f in ('RSI','STO','DI')},**{f+'_basis':basis for f in ('RSI','STO','DI')})
    data={'1m':v,'3m':middle,'6m':upper}
    decision=p._candidate_completion_decision(data,'1m','LONG',require_external=False,commit_validation=True)
    assert decision is not None and decision.grade=='S'
    if vm=='NORMAL':
        assert p._candidate_completion_decision(data|{'3m':upper},'1m','LONG',require_external=False,commit_validation=True) is None
        assert not p._active_percentile_candidates('1m','LONG')


@pytest.mark.parametrize('direction,col,sign',[('LONG','low',-1),('SHORT','high',1)])
def test_breaker_strict_boundary(direction,col,sign):
    v=view(**{col:100});cross=int(v.time[-3])
    assert not v.bo_break(direction,100,cross)
    values=np.full(len(v),100.);values[-1]+=sign;v=edit(v,**{col:values})
    assert v.bo_break(direction,100,cross)
    assert not v.bo_break(direction,100,int(v.time[-1]))
    values[-2]+=sign;assert not edit(v,**{col:values}).bo_break(direction,100,cross)


@pytest.mark.parametrize('direction,sign',[('LONG',-1),('SHORT',1)])
@pytest.mark.parametrize('extra',[0,.001])
def test_external_first_survival_second_boundaries(direction,sign,extra):
    p=profile();ext=p.watch.external;v=view();when=int(v.time[-2]);side='low' if sign<0 else 'high'
    ext._specs['w']=ExternalLiquiditySpec('w','XAUUSD+','1m')
    st=ExternalLiquidityState('w','PENDING_ATR',direction,'level',level_price=100,event_time=when)
    ext._states['w']=st
    v=edit(v,**{side:100+sign*(3+extra)})
    ext.update_market('XAUUSD+',{'1m':v},['w'])
    assert st.status==('ACTIVE' if extra==0 else 'INVALID')
    if extra:assert st.reason=='ATR_1P5_FIRST_CHECK'
    st.status='ACTIVE';st.reason=None;st.max_distance=3;st.atr_snapshot=2
    assert ext.validate_true_b0('w',direction,100+sign*(3+extra))==(extra==0)
    st.status='ACTIVE';ext.update_market('XAUUSD+',{'1m':v},['w'])
    assert st.status==('ACTIVE' if extra==0 else 'INVALID')
    if extra:assert st.reason=='ATR_1P5_SURVIVAL'


def test_manual_touch_never_backdates_fractional_registration():
    p=profile();v=view();ext=p.watch.external
    spec=ExternalLiquiditySpec('w','XAUUSD+','1m','MANUAL_LEVEL',100,'LONG',int(v.time[-1])+.1)
    assert not ext._register_manual_touch_locked(spec,v)
    spec.registered_at=int(v.time[-1]);assert ext._register_manual_touch_locked(spec,v)


def test_restart_no_inferred_transition_and_checkpoint_continuity():
    p=profile();outside=view(price_hma_6=98);p._process_out_in('1m',outside)
    encoded=p.export_event_state();q=profile();q.restore_event_state(encoded)
    inside=view();p._process_out_in('1m',inside);q._process_out_in('1m',inside)
    assert p.export_event_state()==q.export_event_state()
    r=profile();r.restore_observed_checkpoint({'version':1,'observed':encoded})
    r._process_out_in('1m',inside);assert not r._active_percentile_candidates('1m','LONG')
    # Old Timestamp envelope accepted as epoch seconds.
    from oz_engine.checkpoint import decode
    assert decode({'type':'timestamp','value':'2026-01-01T00:00:00'})==1767225600


def test_static_numpy_path_has_no_dataframe_or_io():
    violations=[]
    for path in (ROOT/'Part1/program/oz_engine').glob('*.py'):
        tree=ast.parse(path.read_text('utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):assert not any(n.name.split('.')[0] in ('pandas','socket','requests','time','random') for n in node.names)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                if node.func.attr in ('now','read_text','read_bytes','write_text','write_bytes','open','to_datetime','DataFrame'):
                    violations.append((path.name,node.lineno))
    assert not violations


def factory():
    from event_engine.oz_processor import OZProcessor,OZConsumer
    from event_engine.model import Subscriptions
    import monitor_OZ
    class Sweep:
        name='SWEEP_STATE'
        def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,))
        def on_event(self,event,board,state):state['__board__']={'registry':{},'publication':event.engine_seq,'external_events':[]}
    return EventEngine(IngressSequencer(),[OZConsumer()],[Sweep(),OZProcessor(monitor_OZ,None,{})])


def drive(e,feeds,seq,symbol='XAUUSD+'):
    e.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=seq,source_time=1790381800000+seq*1000,
        payload={'symbol':symbol,'feeds':feeds});e.run()
    return e.processor_state['OZ_STATE']['runtime']


def test_persistent_profiles_dependency_scope_heartbeat_checkpoint_and_all_tf_equivalence():
    all_tfs=tuple(dict.fromkeys((*TF_MAP,*(x for pair in TF_MAP.values() for x in pair))))
    v=view();feeds={tf:v.snapshot for tf in all_tfs}
    e=factory();full=factory();r=drive(e,feeds,1);rf=drive(full,feeds,1);rf.force_all=True
    ids=tuple(map(id,r.profiles.values()));assert len(ids)==4
    count=r.evaluations
    drive(e,feeds,2);drive(full,feeds,2)
    assert r.evaluations==count and tuple(map(id,r.profiles.values()))==ids
    changed=edit(v,close=100.5).snapshot
    drive(e,{'3m':changed},3);drive(full,{'3m':changed},3)
    assert '1m' in r.last_evaluated['XAUUSD+','NORMAL','OZ']
    assert '1m' not in r.last_evaluated['XAUUSD+','BLIND','OZ']
    assert '1m' not in r.last_evaluated['XAUUSD+','BLIND','BREAKER']
    changed2=edit(v,close=100.6).snapshot
    drive(e,{'6m':changed2},4);drive(full,{'6m':changed2},4)
    assert '1m' not in r.last_evaluated['XAUUSD+','BLIND','BREAKER']
    assert '1m' not in r.last_evaluated['XAUUSD+','BLIND','OZ']
    resumed=factory();resumed.restore(e.checkpoint())
    rr=resumed.processor_state['OZ_STATE']['runtime'];assert rr is not r
    assert rr.watch is not r.watch and rr.external is not r.external
    # A Watch only wakes its own symbol/profile/timeframes, including stable feeds.
    for runtime in (r,rf,rr):
        runtime.watch._watches['w']=WatchSpec('w','MANUAL',('1m',),'XAUUSD+',validation_mode='BLIND')
    drive(e,{'6m':changed2},5);drive(full,{'6m':changed2},5);drive(resumed,{'6m':changed2},5)
    assert r.last_evaluated['XAUUSD+','BLIND','OZ']==('1m',)
    assert not r.last_evaluated['XAUUSD+','BLIND','BREAKER']
    for runtime in (rf,rr):
        assert {k:p.export_event_state() for k,p in r.profiles.items()}=={k:p.export_event_state() for k,p in runtime.profiles.items()}
    assert not e.signals and not full.signals and not resumed.signals
    assert not e.error_log


@pytest.mark.parametrize('environment',['none','new','retained','replaced'])
def test_fresh_environment_silent_consume_and_retained_environment_fires(environment):
    p=profile();v=view();seed(p,v);p.base_tfs=['1m']
    p.prev_states['1m']=v.percentile_states()
    p.hma_cross_extremes['1m','LONG']=(99,int(v.time[-3]),int(v.time[-3]))
    messages=[];p.telegram.send=lambda *a,**k:messages.append((a,k)) or True
    if environment!='none':
        p.watch._watches['w']=WatchSpec('w','MANUAL',('1m',),'XAUUSD+','LONG',True,validation_mode='BLIND')
        current=p.watch.allowed_environment_identities('XAUUSD+','1m','LONG','BLIND','OZ')
        p.environment_identities['1m','LONG']=current if environment=='retained' else frozenset({'old'}) if environment=='replaced' else frozenset()
    p.run_once(0,() if environment=='none' else ('1m',),{'1m':v})
    c=p.candidates['1m','LONG']
    assert c is not None
    assert c.alerted==(environment=='retained')
    assert c.completed_outside_window==(environment!='retained')
    assert bool(messages)==(environment=='retained')


def test_profile_state_only_serializes_at_explicit_checkpoint(monkeypatch):
    e=factory();v=view();feeds={tf:v.snapshot for tf in set(TF_MAP)|{x for pair in TF_MAP.values() for x in pair}}
    r=drive(e,feeds,1);calls=[];original=OZProfile.export_event_state
    def tracked(self):calls.append(self);return original(self)
    monkeypatch.setattr(OZProfile,'export_event_state',tracked)
    drive(e,feeds,2);assert not calls
    e.checkpoint();assert len(calls)==4
    calls.clear();from event_startup import export_engine_state
    files=export_engine_state(e);assert len(calls)==4
    assert sum(name.startswith('oz_observed_') for name in files)==4


def test_external_source_dependency_wakes_its_base_tf():
    e=factory();v=view();feeds={tf:v.snapshot for tf in set(TF_MAP)|{x for pair in TF_MAP.values() for x in pair}}
    r=drive(e,feeds,1)
    r.watch._watches['w']=WatchSpec('w','MANUAL',('1m',),'XAUUSD+','LONG',True,external_watch_id='ext',validation_mode='BLIND')
    r.external._specs['ext']=ExternalLiquiditySpec('ext','XAUUSD+','1d','MANUAL_LEVEL',1,'LONG',0)
    drive(e,feeds,2)
    drive(e,{'1d':edit(v,close=100.5).snapshot},3)
    assert r.last_evaluated['XAUUSD+','BLIND','OZ']==('1m',)


def test_changed_tf_schedule_matches_all_tf_over_240_observations_with_candidates():
    e=factory();full=factory();tfs=set(TF_MAP)|{x for pair in TF_MAP.values() for x in pair}
    v=view();created=False;consumed=False
    for second in range(240):
        phase=min(second,59)
        values=v.values.copy();values[:,COLUMNS['hma_6']]=99
        values[-1,COLUMNS['hma_6']]=99 if phase<10 else 101
        for family,col in [('PRICE','price_hma_6'),('RSI','RSI_val'),('STO','STO_val'),('DI','DI_val')]:
            values[-1,COLUMNS[col]]=98 if phase<8 else 100
        native(values)
        times=v.time+(second//60)*60
        s=FeedSnapshot(times,v.snapshot.volume,values,second+1,'test',{})
        feeds={tf:s for tf in tfs} if second%2==0 else {'1m':s}
        r=drive(e,feeds,second+1);rf=drive(full,feeds,second+1);rf.force_all=True
        for key,p in r.profiles.items():
            # Equality here proves a scheduling shortcut, not an old baseline.
            assert p.export_event_state()==rf.profiles[key].export_event_state(),(second,key)
            created |= any(c is not None for c in p.candidates.values())
            consumed |= any(c is not None and c.completed_outside_window for c in p.candidates.values())
    assert created and consumed
    assert r.evaluations<rf.evaluations


def test_late_feed_preparation_initializes_all_required_base_states():
    e=factory();v=view();tfs=set(TF_MAP)|{x for pair in TF_MAP.values() for x in pair}
    feeds={tf:v.snapshot for tf in tfs if tf!='1d'}
    r=drive(e,feeds,1);assert not r.profiles['XAUUSD+','NORMAL','OZ'].prev_states
    drive(e,{'1d':v.snapshot},2)
    assert set(r.profiles['XAUUSD+','NORMAL','OZ'].prev_states)==set(TF_MAP)
    assert r.last_evaluated['XAUUSD+','NORMAL','OZ']==tuple(TF_MAP)


def test_corrupt_watch_restore_keeps_existing_empty_fallback():
    import monitor_OZ
    r=OZRuntime(monitor_OZ,{}, {'oz_external_liquidity_state.json':'{broken','oz_manual_watch_state.json':'{broken'})
    assert not r.external._specs and not r.watch._watches


def test_oz_export_cannot_overwrite_latest_composer_state():
    from event_startup import export_engine_state
    e=factory();r=drive(e,{'1m':view().snapshot},1)
    r.memory['event_composer_memory.json']='{"stale":true}'
    kernel=SimpleNamespace(memory={'fresh':'latest'},export_memory=lambda:{'fresh':'latest'},
                           notifier=SimpleNamespace(sequence=1,deliveries={},delivery_times={},clock=0))
    e.strategy_state['COMPOSER']={'kernels':{'XAUUSD+':kernel}}
    files=export_engine_state(e)
    assert json.loads(files['event_composer_memory.json'])['XAUUSD+']['fresh']=='latest'
    assert 'stale' not in json.loads(files['event_composer_memory.json'])
