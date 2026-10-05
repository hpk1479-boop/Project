"""SPECIAL5 BREAKER+EMA lifecycle and removed-regime regression tests."""
from __future__ import annotations
import copy,threading
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from test_oz_profiles48 import (ALL_TFS,CONFIG,view,edit,data_for,drive,observed_fixture,event_signature,prohibit_network)
from event_application import create_event_engine,load_strategy_inputs
from event_composer_domain import SpecialPluginAPI
from oz_profile_loader import load_saved_oz, LegacyUnsupportedProfile
from oz_engine.common import PERCENTILES

@pytest.fixture
def sp5():return load_strategy_inputs({})[2]['SPECIAL5']

class Host:
    """Transport-free host around the actual SPECIAL Plugin API and strategy."""
    def __init__(self):
        self.chat_id='offline';self.config={'MAX_BARS_AFTER_B0':'2'};self._lock=threading.RLock()
        self._active_children={};self.pushes=[];self.saved=[];self.data={};self.delivered=True
        self.session=True;self.notices=[];self.handlers={};self.oz_handlers={}
        self._time_policy=SimpleNamespace(allows=lambda filters:self.session)
        self.staff=self._special_event_staff=SimpleNamespace(request=lambda *a,**kw:self.data)
        self.special_api=SpecialPluginAPI(self)
    def _save_active_children_state_locked(self):self.saved.append(copy.deepcopy(self._active_children))
    def _push(self,item):self.pushes.append(copy.deepcopy(item))
    def register_special_watch_handler(self,key,handler):self.handlers[key]=handler
    def register_special_oz_event_handler(self,key,handler):self.oz_handlers[key]=handler
    def _render_official_oz_alert(self,spec,event,fallback):return spec.alert_template+event.get('trigger_name','')
    def _handle_oz_event_core(self,event):
        self.notices.append(copy.deepcopy(event));return {'ok':self.delivered,'delivered':self.delivered}


def frame(times=None,*,h6=None,fast=101,slow=100):
    times=times if times is not None else [90.,100.,110.,120.]
    return pd.DataFrame({'time':pd.to_datetime(times,unit='s',utc=True),
        'hma_6':h6 if h6 is not None else [101.]*len(times),'hma_17':[100.]*len(times),
        'ema_50':fast,'ema_200':slow})


def test_one_parent_watch_per_symbol_tf_and_idempotent_restore(sp5):
    h=Host();sp5.register(h)
    assert len(h._active_children)==len(sp5.SYMBOLS)*len(sp5.SOURCE_TFS)==16
    assert all(w['trigger_mode']=='BREAKER' and w['validation_mode']=='NORMAL' for w in h._active_children.values())
    assert all(w.startswith(sp5.HIGH_PREFIX) for w in h._active_children)
    assert not hasattr(sp5,'REMOVED_SOURCE_PREFIX')
    before=copy.deepcopy(h._active_children);pushes=len(h.pushes)
    sp5.register(h)
    assert h._active_children==before and len(h.pushes)==pushes

@pytest.mark.parametrize('legacy',['BREAKER'])
def test_breaker_children_restore_without_rearming_and_old_parent_cleanup(sp5,legacy):
    h=Host();api=h.special_api;r=sp5.Special5Runtime(api,'XAUUSD+')
    children=r.arm_final('LONG','5m');before=copy.deepcopy(h._active_children)
    saved=copy.deepcopy(h._active_children)
    for wid in children:saved[wid]['special5_source_trigger_mode']=legacy
    old='OZARM:SP5:HIGH_BREAKER_REGIME:old'
    saved[old]=dict(before[children[0]],watch_id=old,trigger_mode='BREAKER_REGIME')
    restored={}
    for wid,row in saved.items():
        try:restored[wid]=load_saved_oz(row)
        except LegacyUnsupportedProfile:pass
    h._active_children=restored
    sp5.register(h)
    for wid in children:
        item=h._active_children[wid]
        assert item['special5_source_trigger_mode']=='BREAKER' and 'special5_source_gate' not in item
        assert (item['issued_at'],item['special5_setup_id'],item['watch_id'])==(before[wid]['issued_at'],before[wid]['special5_setup_id'],wid)
    assert old not in h._active_children
    assert not any(p.get('watch_id')==old for p in h.pushes)
    assert not hasattr(sp5,'_cleanup_removed_source_watches')

@pytest.mark.parametrize('direction,fast,slow,expected',[
    ('LONG',101,100,True),('LONG',99,100,False),('LONG',100,100,False),
    ('SHORT',99,100,True),('SHORT',101,100,False),('SHORT',100,100,False),('LONG',float('nan'),100,False)])
def test_ema_uses_last_closed_row_not_live(sp5,direction,fast,slow,expected):
    h=Host();r=sp5.Special5Runtime(h.special_api,'XAUUSD+')
    h.data={'5m':frame(fast=[100,100,fast,1000 if direction=='SHORT' else -1000],slow=slow)}
    assert r._source_ema_trend_allowed(direction,'5m') is expected

@pytest.mark.parametrize('band,ema,session,expected',[(False,True,True,True),(False,False,True,False),(True,False,True,False),(True,True,True,True),(True,True,False,False)])
def test_only_ema_source_ignores_unrecognized_event_metadata(sp5,band,ema,session,expected):
    h=Host();sp5.register(h);h.session=session
    h.data={'5m':frame(fast=101 if ema else 99)}
    handler=h.oz_handlers[sp5.SPEC_ID];wid=next(w for w,x in h._active_children.items() if x['symbol']=='XAUUSD+' and x['timeframes']==['5m'])
    event={'kind':'FINAL_ALERT','symbol':'XAUUSD+','tf':'5m','direction':'LONG','watch_ids':[wid]}
    if band:event['unrelated_metadata']='not_a_condition'
    result=handler.handle_oz_event(event)
    children=[x for w,x in h._active_children.items() if w.startswith(sp5.FINAL_PREFIX)]
    assert len(children)==3*int(expected) and not h.notices
    assert result['suppressed'] and wid in h._active_children
    if expected:assert all('special5_source_gate' not in x for x in children)


def test_unknown_parent_event_never_rearms(sp5):
    h=Host();sp5.register(h);h.data={'5m':frame()}
    h.oz_handlers[sp5.SPEC_ID].handle_oz_event({'kind':'FINAL_ALERT','symbol':'XAUUSD+','tf':'5m','direction':'LONG','watch_ids':['OZARM:UNKNOWN:parent']})
    assert not any(w.startswith(sp5.FINAL_PREFIX) for w in h._active_children)


def test_same_direction_replaced_other_direction_preserved(sp5):
    h=Host();r=sp5.Special5Runtime(h.special_api,'XAUUSD+')
    first=r.arm_final('LONG','5m');short=r.arm_final('SHORT','6m')
    second=r.arm_final('LONG','10m')
    assert not set(first)&set(h._active_children)
    assert set(h._active_children)==set(short+second)
    assert {tuple(x['timeframes']) for x in h._active_children.values()}=={('1m',),('2m',),('3m',)}

@pytest.mark.parametrize('source_cross,child_cross,expected',[(True,False,0),(False,True,2),(False,False,3)])
def test_parent_and_child_opposite_cross_cancellation(sp5,source_cross,child_cross,expected):
    h=Host();r=sp5.Special5Runtime(h.special_api,'XAUUSD+');ids=r.arm_final('LONG','5m')
    for item in h._active_children.values():item['issued_at']=100.
    h.data={tf:frame() for tf in ('5m','1m','2m','3m')}
    if source_cross:h.data['5m']=frame(h6=[101,101,99,99])
    if child_cross:h.data['2m']=frame(h6=[101,101,99,99])
    r._maintain_final_children()
    assert len(h._active_children)==expected


def test_own_tf_bar_expiry_at_n_plus_one_only(sp5):
    h=Host();r=sp5.Special5Runtime(h.special_api,'XAUUSD+');r.arm_final('LONG','5m')
    for x in h._active_children.values():x['issued_at']=100.
    # max2: 1m has3 new closed bars, 2m has2, 3m has1; live row excluded.
    h.data={'5m':frame(),'1m':frame([90,100,101,102,103,104]),'2m':frame([90,100,102,104,106]),'3m':frame([90,100,103,106])}
    r._maintain_final_children()
    assert {x['special5_final_tf'] for x in h._active_children.values()}=={'2m','3m'}

@pytest.mark.parametrize('delivered,session,left',[(True,True,0),(False,True,3),(True,False,0)])
def test_final_delivery_cancel_siblings_and_failed_delivery_keeps_setup(sp5,delivered,session,left):
    h=Host();h.delivered=delivered;h.session=session;r=sp5.Special5Runtime(h.special_api,'XAUUSD+')
    ids=r.arm_final('LONG','5m')
    result=sp5.Special5EventHandler(h.special_api,{'XAUUSD+':r}).handle_oz_event({'kind':'FINAL_ALERT','symbol':'XAUUSD+','tf':'1m','direction':'LONG','watch_ids':ids[:1]})
    assert len(h._active_children)==left
    assert len(h.notices)==int(session)
    if session:assert '브레이커 올존' in h.notices[0]['trigger_name'] and '레짐' not in h.notices[0]['trigger_name']



def engine_children(e,sp5):
    m=e.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager
    return {w:x for w,x in m.special_api.snapshot_oz_watches(source_spec_id=sp5.SPEC_ID,watch_id_prefix=sp5.FINAL_PREFIX)}

@pytest.mark.parametrize('source_case',['EMA','BAND','BOTH','NEITHER','FAMILY_B0'])
def test_source_live_backtest_no_regime_alternative(sp5,source_case):
    base,mid,upper=data_for(n=240);n=len(base)
    # EMA is explicitly independent of the native upper-regime predicate.
    close=np.linspace(99.5,100.5,n) if source_case in ('EMA','BOTH') else np.full(n,100.)
    base=edit(base,close=close,ema_50=101. if source_case in ('EMA','BOTH') else 99.,ema_200=100.)
    fields={p:np.linspace(98.,99.,n) for p in ('price_regime_basis','RSI_basis','STO_basis','DI_basis')}
    if source_case=='FAMILY_B0':fields['RSI_basis']=99.
    upper=edit(upper,**fields) if source_case in ('BAND','BOTH','FAMILY_B0') else upper
    outputs=[]
    for bt in (False,True):
        e=create_event_engine(CONFIG,symbols=('XAUUSD+',),selection=['SPECIAL5'],backtest=bt,trigger_overrides={})
        feeds={tf:base.snapshot for tf in ALL_TFS};feeds.update({'15m':mid.snapshot,'30m':upper.snapshot})
        r=drive(e,feeds,1);assert not e.error_log,e.error_log
        wid=next(w for w,x in r.watch._watches.items() if w.startswith(sp5.HIGH_PREFIX) and x.symbol=='XAUUSD+' and x.timeframes==('5m',))
        b0={'RSI':97.} if source_case=='FAMILY_B0' else None
        observed_fixture(r,'NORMAL','BREAKER',base,tf='5m',watch_id=wid,family_b0=b0,hma_b0=100.)
        low=base.column('low').copy();low[-1]=98.9
        drive(e,{'5m':edit(base,low=low).snapshot},2)
        assert not e.error_log,e.error_log
        children=engine_children(e,sp5)
        assert len(children)==(3 if source_case in ('EMA','BOTH') else 0),(source_case,children,event_signature(e))
        assert not event_signature(e,notifications=True) # parent is internal, never a Telegram alert
        assert all('special5_source_gate' not in child for child in children.values())
        facts=event_signature(e)
        assert len(facts)==(0 if source_case=='FAMILY_B0' else 1)
        assert all('special5_source_gate' not in event for _,event in facts)
        assert not hasattr(r, 'source_conditions')
        assert not hasattr(r, '_source_condition')
        assert all((vm,tm) in (('NORMAL','OZ'),('BLIND','OZ'),('NORMAL','BREAKER'),('BLIND','BREAKER'))
                   for _symbol,vm,tm in r.profiles)
        if source_case=='FAMILY_B0':
            assert not r.profiles['XAUUSD+','NORMAL','BREAKER'].candidates['5m','LONG'].alerted
        # Explicit engine checkpoint reopens existing setups; no fresh arm or IDs.
        resumed=create_event_engine(CONFIG,symbols=('XAUUSD+',),selection=['SPECIAL5'],backtest=bt,trigger_overrides={})
        resumed.restore(e.checkpoint());drive(resumed,feeds,3)
        assert not resumed.error_log,resumed.error_log
        assert engine_children(resumed,sp5)==children
        outputs.append((facts,children))
    assert outputs[0]==outputs[1]
