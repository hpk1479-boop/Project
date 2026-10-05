"""SPECIAL5 upstream parent/child ALLZONE, current Part1 comparison."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,close_time_ns
from live_replay.synthetic_specials import special5_trace,NativeTrace
from live_replay.synthetic import SYMBOL
from oracle import original_replay,assert_decision_state_equal

def finals(r):return {w:p for w,p in r.composer._active_children.items() if p.get('special5_stage')=='LOW_BLIND_OZ'}

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('source_mode',['BREAKER','BREAKER_REGIME'])
def test_special5_positive_upstream_part1(direction,source_mode):
    a=Replay(specials=(5,),synthetic=True);e=original_replay(specials=(5,));armed=False
    for row in special5_trace(direction,source_mode,mismatch_ema=source_mode=='BREAKER_REGIME'):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        if finals(a):
            assert len(finals(a))==3 and not a.delivered
            assert {p['special5_source_trigger_mode'] for p in finals(a).values()}=={source_mode}
            armed=True
    assert armed and len(a.delivered)==1 and not finals(a)
    x=a.delivered[0]
    assert (x['strategy'],x['direction'],x['source_tf'])==('SPECIAL5',direction,'1m')
    assert x['session']==['MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK']
    assert x['alert_time_ns']==pd.Timestamp('2026-08-03 '+('01' if direction=='LONG' else '13')+':45:32.100',tz='UTC').value
    assert x['source_spec_id']=='PIPELINE_5' and x['event_order']==1
    assert len(a.composer._active_children)==32  # Persistent upper watches survive internal delivery.

@pytest.mark.parametrize('final_tf',['2m','3m'])
@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special5_positive_lower_tf_close(final_tf,direction):
    a=Replay(specials=(5,),synthetic=True);b=Replay(specials=(5,),synthetic=True,mode='CLOSE')
    rows=special5_trace(direction,final_tf=final_tf);a.run(rows);b.run(rows)
    assert a.delivered==b.delivered and len(a.delivered)==1
    assert a.delivered[0]['source_tf']==final_tf
    assert b.alerts[0]['display_time_ns']==close_time_ns(a.delivered[0]['alert_time_ns'],final_tf)

def test_special5_breaker_checks_closed_not_forming_ema():
    a=Replay(specials=(5,),synthetic=True);e=original_replay(specials=(5,))
    for row in special5_trace(mismatch_ema=True):a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert not a.delivered and not finals(a)
    assert any(x['kind']=='DELIVERY_REPLY' and x['reply'].get('special5_stage') for x in a.audit)

def armed_parent():
    a=Replay(specials=(5,),synthetic=True);e=original_replay(specials=(5,))
    cutoff=pd.Timestamp('2026-08-03 01:45:30.200',tz='UTC').value
    for row in special5_trace():
        if row['timestamp_ns']>cutoff:break
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert len(finals(a))==3 and not a.delivered
    return a,e

@pytest.mark.parametrize('cancel_tf',['5m','1m'])
def test_special5_opposite_cross_parent_group_or_only_own_child(cancel_tf):
    a,e=armed_parent();t=NativeTrace();t.sequence=a.last_key[1];t.snapshot=10000
    def opposite(frame,tf,direction,phase):
        frame['hma_17']=100.;frame['hma_6']=101.
        if tf==cancel_tf:frame.loc[len(frame)-1,'hma_6']=99.
        return frame
    t.publish('2026-08-03 01:50:01','2026-08-03 01:50:00','LONG',0,opposite)
    t.event('SPECIAL_POLL','2026-08-03 01:50:01.100');t.event('COMMAND_DRAIN','2026-08-03 01:50:01.200')
    for row in t.rows:a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert len(finals(a))==(0 if cancel_tf=='5m' else 2)
    if cancel_tf=='1m':assert {p['special5_final_tf'] for p in finals(a).values()}=={'2m','3m'}
    assert not a.delivered

def test_special5_independent_closed_bar_expiry():
    a,e=armed_parent();a.config['MAX_BARS_AFTER_B0']='2';e.config['MAX_BARS_AFTER_B0']='2'
    t=NativeTrace();t.sequence=a.last_key[1];t.snapshot=10000
    def aligned(frame,tf,direction,phase):frame['hma_17']=100.;frame['hma_6']=101.;return frame
    t.publish('2026-08-03 01:49:01','2026-08-03 01:49:00','LONG',0,aligned)
    t.event('SPECIAL_POLL','2026-08-03 01:49:01.100')
    for row in t.rows:a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert {p['special5_final_tf'] for p in finals(a).values()}=={'2m','3m'}

def test_special5_retry_original_event_time_and_sibling_consumption():
    rows=special5_trace();idx=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and r['timestamp_ns']==pd.Timestamp('2026-08-03 01:45:32.100',tz='UTC').value)
    rows[idx]['delivery_results']=[False]
    a=Replay(specials=(5,),synthetic=True);e=original_replay(specials=(5,))
    for i,row in enumerate(rows):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        if i==idx:assert len(finals(a))==3 and not a.delivered
    assert len(a.delivered)==1 and not finals(a)
    assert a.delivered[0]['alert_time_ns']==rows[idx]['timestamp_ns']
    assert a.delivered[0]['delivery_time_ns']>rows[idx]['timestamp_ns']


def test_allzone_profiles_share_part1_staff_snapshot_ttl():
    a=Replay(specials=(5,),synthetic=True);t=NativeTrace()
    t.publish('2026-08-03 01:40:05','2026-08-03 01:40:00','LONG',0)
    t.event('OZ_POLL','2026-08-03 01:40:05.100',symbol=SYMBOL)
    t.publish('2026-08-03 01:40:05.200','2026-08-03 01:40:00','LONG',1)
    t.event('OZ_POLL','2026-08-03 01:40:05.300',symbol=SYMBOL,validation_mode='BLIND',trigger_mode='OZ')
    a.run(t.rows)
    clients=[m.client for (symbol,vm,tm),m in a.profile_monitors.items() if symbol==SYMBOL]
    assert all(c is clients[0] for c in clients)
    assert clients[0]._cache['1m'].iloc[-1]['RSI_val']==10.
    a.step(dict(kind='OZ_POLL',timestamp_ns=pd.Timestamp('2026-08-03 01:40:05.701',tz='UTC').value,sequence=10000,symbol=SYMBOL,validation_mode='BLIND',trigger_mode='OZ'))
    assert clients[0]._cache['1m'].iloc[-1]['RSI_val']==50.
