"""Synthetic upstream-native observations, independently executing Part1 methods."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,close_time_ns
from live_replay.synthetic_specials import special3_trace
from oracle import original_replay,assert_decision_state_equal

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('cross_tf',['1m','2m'])
@pytest.mark.parametrize('fvg_first',[False,True])
def test_special3_positive_upstream_part1(direction,cross_tf,fvg_first):
    a=Replay(specials=(3,),synthetic=True);e=original_replay(specials=(3,))
    paired=False
    for row in special3_trace(direction,cross_tf,fvg_first):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        paired |= any(s['post_touch_armed'] is not None for s in a.composer._config_chain_state.values())
    assert paired and a.fvg._seen_created and len(a.delivered)==1
    x=a.delivered[0]
    assert (x['strategy'],x['direction'],x['source_tf'])==('SPECIAL3',direction,'1m')
    assert x['source_spec_id']==('PIPELINE_3' if cross_tf=='1m' else 'PIPELINE_3@2m')
    assert x['session']==['MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK']
    assert x['alert_time_ns']==pd.Timestamp('2026-08-03 '+('10' if direction=='LONG' else '22')+':40:12.100',tz='Asia/Seoul').value
    assert x['event_order']==1

def test_special3_close_changes_only_presentation():
    a=Replay(specials=(3,),synthetic=True);b=Replay(specials=(3,),synthetic=True,mode='CLOSE')
    rows=special3_trace();a.run(rows);b.run(rows)
    assert a.delivered==b.delivered and len(b.alerts)==1
    assert b.alerts[0]['display_time_ns']==close_time_ns(a.delivered[0]['alert_time_ns'],'1m')
    assert a.composer._config_chain_state==b.composer._config_chain_state

def test_special3_registration_and_no_initial_historical_cross():
    a=Replay(specials=(3,),synthetic=True)
    assert len(a.composer.official_chain_specs)==4
    for s in a.composer.official_chain_specs.values():
        assert s.fvg_tfs==('5m','6m') and s.oz_tfs==('1m','2m')
        assert s.trigger_mode=='OZ' and s.order_mode=='UNORDERED'
    for row in special3_trace():
        a.step(row)
        if row['kind']=='WATCH_POLL':break
    assert not a.delivered and all(not s['unordered_latch']['groups'] for s in a.composer._config_chain_state.values())

def test_special3_retry_keeps_completion_time_and_duplicate_receipt():
    from live_replay import clock,storage
    rows=special3_trace()
    trigger=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and r['timestamp_ns']==pd.Timestamp('2026-08-03 01:40:12.100',tz='UTC').value)
    rows[trigger]['delivery_results']=[False]
    retry=next(i for i in range(trigger+1,len(rows)) if rows[i]['kind']=='OZ_POLL')
    rows[retry]['delivery_results']=[True]
    a=Replay(specials=(3,),synthetic=True);e=original_replay(specials=(3,))
    for row in rows:
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert len(a.delivered)==1
    assert a.delivered[0]['alert_time_ns']==rows[trigger]['timestamp_ns']
    assert a.delivered[0]['delivery_time_ns']==rows[retry]['timestamp_ns']
    event=next(x for x in a.sender._events.all().values() if x['kind']=='FINAL_ALERT')
    with clock.at(a.last_ns+1),storage.at(a.state_files):reply=a.sender._request(event)
    assert reply['duplicate'] and len(a.delivered)==1

def test_special3_opposite_cross_cancels_existing_pair_from_native_input():
    from live_replay.synthetic_specials import NativeTrace
    from live_replay.synthetic import SYMBOL
    from live_replay import clock,storage
    a=Replay(specials=(3,),synthetic=True);e=original_replay(specials=(3,))
    cut=pd.Timestamp('2026-08-03 01:40:11.100',tz='UTC').value
    for row in special3_trace():
        if row['timestamp_ns']>cut:break
        a.step(row);e.step(row)
    assert a.composer._config_chain_active and not a.delivered
    t=NativeTrace();t.sequence=a.last_key[1];t.snapshot=10000
    def opposite(frame,tf,direction,phase):
        if tf=='1m':
            frame['ema_200']=100.;frame['ema_50']=101.
            frame.loc[len(frame)-2:,'ema_50']=99.
        return frame
    t.publish('2026-08-03 01:41:01','2026-08-03 01:41:00','LONG',0,opposite)
    t.event('WATCH_POLL','2026-08-03 01:41:01.100',symbol=SYMBOL)
    t.event('COMMAND_DRAIN','2026-08-03 01:41:01.200')
    for row in t.rows:a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert not a.composer._config_chain_active and not a.watch._watches and not a.delivered
