"""SPECIAL7 15m TREND -> 1m BREAKER_REGIME; actual source, not its comments."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,close_time_ns
from live_replay.synthetic_specials import special7_trace
from live_replay.synthetic import SYMBOL
from live_replay.reference.oz import PROFILE_KEYS
from oracle import original_replay,assert_decision_state_equal

@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special7_positive_upstream_part1(direction):
    a=Replay(specials=(7,),synthetic=True);e=original_replay(specials=(7,));armed=False
    for row in special7_trace(direction):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        armed |= bool(a.composer._active_children)
    assert armed and len(a.delivered)==1
    x=a.delivered[0]
    assert (x['strategy'],x['direction'],x['source_tf'])==('SPECIAL7',direction,'1m')
    assert x['source_spec_id']=='PIPELINE_7'
    assert x['session']==['MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK']
    assert x['alert_time_ns']==pd.Timestamp('2026-08-03 '+('01' if direction=='LONG' else '13')+':41:30',tz='UTC').value
    assert x['event_order']==1

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('wrong',['opposite_trend','wrong_regime'])
def test_special7_rejects_wrong_direction_or_upper_regime(wrong,direction):
    a=Replay(specials=(7,),synthetic=True);e=original_replay(specials=(7,))
    for row in special7_trace(direction,**{wrong:True}):a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert a.composer.trend_facts and a.composer._active_children and not a.delivered

def test_special7_actual_registration_has_main_session_gate_and_no_added_setup():
    a=Replay(specials=(7,),synthetic=True)
    for s in a.composer.official_specs.values():
        assert len(s.conditions)==1 and s.conditions[0].kind=='TREND' and s.conditions[0].tf=='15m'
        assert s.oz_tfs==('1m',) and s.trigger_mode=='BREAKER_REGIME'
        assert s.time_filters==('MAIN_ASIA','MAIN_LONDON','MAIN_NEWYORK')
    a.run(special7_trace(session_hour='04'))
    assert a.composer.trend_facts and not a.composer._active_children and not a.delivered

def test_special7_retry_close_and_receipt_duplicate():
    rows=special7_trace();trigger=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and r['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    rows[trigger]['delivery_results']=[False]
    a=Replay(specials=(7,),synthetic=True);e=original_replay(specials=(7,));b=Replay(specials=(7,),synthetic=True,mode='CLOSE')
    for row in rows:a.step(row);e.step(row);b.step(row);assert_decision_state_equal(a,e)
    assert len(a.delivered)==1 and a.delivered==b.delivered
    x=a.delivered[0];assert x['alert_time_ns']==rows[trigger]['timestamp_ns'] and x['delivery_time_ns']>x['alert_time_ns']
    assert b.alerts[0]['display_time_ns']==close_time_ns(x['alert_time_ns'],'1m')
    from live_replay import clock,storage
    event=next(x for x in a.sender._events.all().values() if x['kind']=='FINAL_ALERT')
    with clock.at(a.last_ns+1),storage.at(a.state_files):reply=a.sender._request(event)
    assert reply['duplicate'] and len(a.delivered)==1

def test_all_part1_profiles_are_available_and_share_symbol_cache():
    a=Replay(specials=tuple(range(1,8)),synthetic=True)
    for symbol in ('XAUUSD+','NAS100'):
        assert {(vm,tm) for s,vm,tm in a.profile_monitors if s==symbol}==set(PROFILE_KEYS)
        assert all(m.client is a.oz_staff[symbol] for (s,vm,tm),m in a.profile_monitors.items() if s==symbol)
    assert a.oz_staff['XAUUSD+'] is not a.oz_staff['NAS100']
