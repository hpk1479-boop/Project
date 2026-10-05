"""SPECIAL6 native MA + actual FVG, branches never cross TF boundaries."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest,pandas as pd
from live_replay.runtime import Replay,close_time_ns
from live_replay.synthetic_specials import special6_trace
from live_replay.synthetic import SYMBOL
from oracle import original_replay,assert_decision_state_equal

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('setup_tf',['15m','30m'])
def test_special6_positive_upstream_part1(direction,setup_tf):
    a=Replay(specials=(6,),synthetic=True);e=original_replay(specials=(6,));armed=False
    for row in special6_trace(direction,setup_tf):
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
        armed |= bool(a.composer._active_children)
    assert armed and len(a.delivered)==1 and a.fvg._seen_created
    x=a.delivered[0]
    assert (x['strategy'],x['direction'],x['source_tf'])==('SPECIAL6',direction,'1m')
    assert x['source_spec_id']==f'PIPELINE_6@{setup_tf}@{direction}'
    assert x['session']==['MAIN_ASIA' if direction=='LONG' else 'MAIN_NEWYORK']
    assert x['alert_time_ns']==pd.Timestamp('2026-08-03 '+('02' if direction=='LONG' else '13')+':01:30',tz='UTC').value
    assert x['event_order']==1

@pytest.mark.parametrize('wrong',['split_tf','wrong_forming_slope'])
@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_special6_rejects_cross_tf_pair_or_wrong_current_hma(wrong,direction):
    a=Replay(specials=(6,),synthetic=True);e=original_replay(specials=(6,))
    for row in special6_trace(direction,**{wrong:True}):a.step(row);e.step(row);assert_decision_state_equal(a,e)
    assert a.fvg._seen_created and not a.delivered and not a.composer._active_children

def test_special6_stale_ma_blocks_new_setup_but_keeps_armed_watch_as_part1():
    a=Replay(specials=(6,),synthetic=True);e=original_replay(specials=(6,))
    cutoff=pd.Timestamp('2026-08-03 02:00:06.300',tz='UTC').value
    for row in special6_trace():
        if row['timestamp_ns']>cutoff:break
        a.step(row);e.step(row);assert_decision_state_equal(a,e)
    before=dict(a.composer._active_children)
    assert before
    row=dict(kind='EVALUATE',timestamp_ns=pd.Timestamp('2026-08-03 02:00:12',tz='UTC').value,sequence=10000,symbols=[SYMBOL])
    a.step(row);e.step(row);assert_decision_state_equal(a,e)
    # LIVE _evaluate_symbol_locked skips a false signature; it does not cancel
    # an already armed persistent child merely because its MA setup becomes stale.
    assert a.composer._active_children==before and not a.delivered
    from live_replay import clock,storage
    with clock.at(row['timestamp_ns']),storage.at(a.state_files):
        spec=a.composer.official_specs['PIPELINE_6@15m@LONG']
        assert not a.composer._evaluate_spec_direction_locked(spec,'LONG')

def test_special6_retry_and_close_keep_identical_signal():
    rows=special6_trace();trigger=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and r['timestamp_ns']==pd.Timestamp('2026-08-03 02:01:30',tz='UTC').value)
    rows[trigger]['delivery_results']=[False]
    a=Replay(specials=(6,),synthetic=True);e=original_replay(specials=(6,));b=Replay(specials=(6,),synthetic=True,mode='CLOSE')
    for row in rows:a.step(row);e.step(row);b.step(row);assert_decision_state_equal(a,e)
    assert len(a.delivered)==1 and a.delivered==b.delivered
    x=a.delivered[0]
    assert x['alert_time_ns']==rows[trigger]['timestamp_ns'] and x['delivery_time_ns']>x['alert_time_ns']
    assert b.alerts[0]['display_time_ns']==close_time_ns(x['alert_time_ns'],x['source_tf'])

def test_special6_registered_tf_and_required_dependencies():
    a=Replay(specials=(6,),synthetic=True)
    assert len(a.composer.official_specs)==8
    for s in a.composer.official_specs.values():
        assert s.oz_tfs==('1m','2m','3m','4m','5m','6m') and s.trigger_mode=='BREAKER'
        assert len({c.tf for c in s.conditions})==1
        assert {c.kind for c in s.conditions}=={'MA_STATE','MA_SLOPE_STATE','FVG'}
