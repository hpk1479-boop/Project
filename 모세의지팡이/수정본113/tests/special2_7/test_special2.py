from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,M1_NS
from live_replay.synthetic_specials import special2_trace
from live_replay import clock,storage
from live_replay.reference import sweep as W
from oracle import original_replay,assert_decision_state_equal
@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('mode',['REALTIME','CLOSE'])
def test_special2_positive_native_sweep_against_current_part1(direction,mode):
    a=Replay(specials=(2,),synthetic=True,mode=mode);e=original_replay(specials=(2,),mode=mode)
    pending=active=candidate=False
    for row in special2_trace(direction):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        pending|=any(x.status=='PENDING_ATR' for x in a.watch.external._states.values())
        active|=any(x.status in {'ACTIVE','CONFIRMED'} for x in a.watch.external._states.values())
        candidate|=any(x is not None for x in a.monitors['XAUUSD+'].candidates.values())
    assert pending and active and candidate and len(a.delivered)==1
    x=a.delivered[0];assert x['direction']==direction and x['strategy']=='SPECIAL2'
    assert x['source_tf']=='1m' and x['source_spec_id']=='PIPELINE_2@1m'
    stamp='2026-08-03 '+('10:41:30' if direction=='LONG' else '22:17:30')
    assert x['alert_time_ns']==pd.Timestamp(stamp,tz='Asia/Seoul').value
    assert x['session']==[('MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK')] and x['event_order']==1
    assert a.alerts[0]['display_time_ns']==(x['alert_time_ns'] if mode=='REALTIME' else (x['alert_time_ns']//M1_NS+1)*M1_NS)
def test_special2_registration_and_source_tf_requirements():
    r=Replay(specials=(2,));assert len(r.composer.official_specs)==24
    for spec in r.composer.official_specs.values():
        assert len(spec.conditions)==1 and spec.conditions[0].kind=='SWEEP';assert spec.oz_tfs==(spec.conditions[0].tf,)
        assert (spec.validation_mode,spec.trigger_mode)==('NORMAL','BREAKER')
        p=r.composer._sweep_subscription_payload(spec,spec.conditions[0]);s=W.SweepSpec.from_payload(p)
        assert W.SweepEngine._required_tfs(s)==list(dict.fromkeys([s.source_tf,'1d','4h','8h','5m']))
def test_special2_always_on_and_retry():
    rows=special2_trace();idx=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and str(pd.Timestamp(x['timestamp_ns']))=='2026-08-03 01:41:30')
    rows[idx]['delivery_results']=[False];rows[idx+1]['delivery_results']=[True]
    a=Replay(specials=(2,),synthetic=True);e=original_replay(specials=(2,));before=False
    for row in rows:
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        if row['kind']=='OZ_POLL' and not a.watch._watches and not a.delivered:
            before=True;assert a.monitors['XAUUSD+'].prev_states
            assert any(ep.active for ep in a.monitors['XAUUSD+'].episodes.values())
    assert before and len(a.delivered)==1
    assert a.delivered[0]['alert_time_ns']==rows[idx]['timestamp_ns'];assert a.delivered[0]['delivery_time_ns']==rows[idx+1]['timestamp_ns']
    assert not a.watch._watches and not a.composer._active_children
    event=next(iter(a.sender._events.all().values()))
    with clock.at(a.last_ns+1),storage.at(a.state_files):reply=a.sender._request(event)
    assert reply['duplicate'] and len(a.delivered)==1
def test_special2_invalidation_tombstone():
    a=Replay(specials=(2,),synthetic=True);e=original_replay(specials=(2,))
    for row in special2_trace():
        a.step(row);e.step(row)
        if row['kind']=='SWEEP_EVENT_DRAIN':break
    assert a.watch.external._states;touch=next(iter(a.composer.sweep_touches.values()));invalid=dict(touch,kind='SWEEP_INVALIDATED',event_time=touch['event_time']+60)
    with clock.at(a.last_ns+1),storage.at(a.state_files):a.watch.apply_external_event(invalid);a.watch.apply_external_event(touch)
    with clock.at(e.last_ns+1),storage.at(e.state_files):e.watch.apply_external_event(invalid);e.watch.apply_external_event(touch)
    assert not a.watch.external._states;assert_decision_state_equal(a,e)
def test_special2_final_session_gate():
    rows=special2_trace();a=Replay(specials=(2,),synthetic=True)
    idx=next(i for i,x in enumerate(rows) if x['kind']=='STAFF_PUBLISH' and x['timestamp_ns']>pd.Timestamp('2026-08-03 01:41:00',tz='UTC').value)
    for row in rows[:idx]:a.step(row)
    assert a.composer._active_children
    for row in rows[idx:]:a.step(dict(row,timestamp_ns=row['timestamp_ns']+pd.Timedelta(hours=3).value))
    assert not a.delivered and a.sender._events.all() and a.composer._active_children

@pytest.mark.parametrize('tf,expected', [('2m','10:44:00'),('3m','10:45:00'),('5m','10:45:00'),('1h','11:00:00')])
def test_close_uses_actual_triggering_tf_not_m1(tf,expected):
    from live_replay.runtime import close_time_ns
    signal=pd.Timestamp('2026-08-03 10:42:30',tz='Asia/Seoul').value
    assert close_time_ns(signal,tf)==pd.Timestamp('2026-08-03 '+expected,tz='Asia/Seoul').value

def test_other_strategy_checkpoint_cannot_silently_restore_as_special1():
    from live_replay.checkpoint import dump
    from live_replay.runtime import ReplayError
    with pytest.raises(ReplayError,match='silently omitted'):
        dump(Replay(specials=(2,)))

@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special2_m2_positive_and_close_are_not_m1(direction):
    rt=Replay(specials=(2,),synthetic=True);cl=Replay(specials=(2,),synthetic=True,mode='CLOSE')
    for row in special2_trace(direction,source_tf='2m'):
        rt.step(row);cl.step(row)
    assert len(rt.delivered)==len(cl.alerts)==1
    assert rt.delivered==cl.delivered
    assert rt.delivered[0]['source_tf']=='2m'
    signal=rt.delivered[0]['alert_time_ns']
    assert cl.alerts[0]['display_time_ns']==(signal//(2*M1_NS)+1)*(2*M1_NS)
    assert cl.alerts[0]['display_time_ns']!=(signal//M1_NS+1)*M1_NS
