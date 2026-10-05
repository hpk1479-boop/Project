"""Current Part1 SPECIAL4: synthetic upstream state, not recorded LIVE evidence."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,close_time_ns
from live_replay.synthetic_specials import special4_trace
from oracle import original_replay,assert_decision_state_equal

@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special4_positive_upstream_part1(direction):
    a=Replay(specials=(4,),synthetic=True);e=original_replay(specials=(4,))
    armed=False
    for row in special4_trace(direction):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        armed |= bool(a.composer._active_children)
    assert armed and len(a.delivered)==1
    x=a.delivered[0]
    assert (x['strategy'],x['direction'],x['source_tf'])==('SPECIAL4',direction,'1m')
    assert x['session']==['MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK']
    assert x['alert_time_ns']==pd.Timestamp('2026-08-03 '+('01' if direction=='LONG' else '13')+':30:06.100',tz='UTC').value
    assert x['source_spec_id']=='PIPELINE_4' and x['event_order']==1

@pytest.mark.parametrize('gate',['reject_trend','exceed_atr'])
@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special4_final_gate_invalidation(gate,direction):
    a=Replay(specials=(4,),synthetic=True);e=original_replay(specials=(4,));armed=False
    for row in special4_trace(direction,**{gate:True}):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        armed |= bool(a.composer._active_children)
    assert armed and not a.delivered and not a.composer._active_children
    replies=[x['reply'] for x in a.audit if x['kind']=='DELIVERY_REPLY']
    assert any(x.get('special4_gate')==('trend_filter' if gate=='reject_trend' else 'atr_exceeded') for x in replies)

def test_special4_close_and_retry():
    rows=special4_trace();trigger=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and r['timestamp_ns']==pd.Timestamp('2026-08-03 01:30:06.100',tz='UTC').value)
    rows[trigger]['delivery_results']=[False]
    a=Replay(specials=(4,),synthetic=True);b=Replay(specials=(4,),synthetic=True,mode='CLOSE')
    a.run(rows);b.run(rows)
    assert len(a.delivered)==1 and a.delivered==b.delivered
    assert a.delivered[0]['alert_time_ns']==rows[trigger]['timestamp_ns']
    assert a.delivered[0]['delivery_time_ns']>a.delivered[0]['alert_time_ns']
    assert b.alerts[0]['display_time_ns']==close_time_ns(a.delivered[0]['alert_time_ns'],'1m')

def test_special4_cold_boot_no_retroactive_setup_and_six_minute_expiry():
    a=Replay(specials=(4,),synthetic=True);rows=special4_trace()
    first=next(i for i,r in enumerate(rows) if r['kind']=='SPECIAL_POLL')
    a.run(rows[:first+1]);assert not a.composer._active_children
    arm=next(i for i in range(first+1,len(rows)) if rows[i]['kind']=='SPECIAL_POLL')
    a.run(rows[first+1:arm+1]);assert len(a.composer._active_children)==1
    a.step(dict(kind='SPECIAL_POLL',timestamp_ns=pd.Timestamp('2026-08-03 01:36:00',tz='UTC').value,sequence=10000))
    assert not a.composer._active_children and not a.delivered
