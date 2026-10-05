"""Current Part1-source-derived acceptance tests. No old tests/reports imported."""
from __future__ import annotations
import ast,base64,copy,hashlib,json,sys,types
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
from live_replay import clock
from live_replay.runtime import Replay,ReplayError,mode_name,M1_NS,Composer,Monitor
from live_replay.reference import oz as O,trend as T,staff as S,composer as C
from live_replay.trace_io import pack_snapshot,publication
from live_replay.synthetic import synthetic_trace,native_frame,ORDER,SYMBOL
from live_replay.checkpoint import dump,restore,source_fingerprint
from live_replay.compare import compare


def ledger(result):
    return {'origin':'SYNTHETIC_ORACLE','timestamp_policy':'PART1_EVENT_TIME','alerts':result['delivered']}


def original_source_class(filename,class_name,generated_module,base):
    """Execute original Part1 method AST in a separate namespace; never import its IO.

    Shared service-boundary constructors mean this is a decision-code differential
    test, NOT a substitute for an actual Windows/MT5/concurrent LIVE trace.
    """
    tree=ast.parse((ROOT/'Part1/program'/filename).read_text(encoding='utf-8-sig'))
    original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
    names=set(vars(getattr(generated_module,class_name)))-{'__init__','__new__'}-(set(vars(base)) if base is not getattr(generated_module,class_name) else set())  # adapter keeps its service-boundary overrides
    selected=copy.deepcopy(original)
    selected.bases=[]  # Thread/process constructors are external service boundaries.
    selected.body=[n for n in selected.body if
        isinstance(n,ast.FunctionDef) and n.name in names or
        isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    namespace=dict(vars(generated_module));namespace['__name__']='source_oracle_'+class_name
    exec(compile(ast.fix_missing_locations(ast.Module(body=[selected],type_ignores=[])),
                 str(ROOT/'Part1/program'/filename),'exec'),namespace)
    return type('Original_'+class_name,(namespace[class_name],base),{})


def original_replay(mode='REALTIME'):
    r=Replay(mode=mode,synthetic=True)
    cm=original_source_class('manager_KIM.py','ComposerManager',C,Composer)
    om=original_source_class('monitor_OZ.py','OZMonitor',O,Monitor)
    r.composer=cm(r)
    r.monitors={symbol:om(r,symbol) for symbol in r.monitors}
    tc=original_source_class('strategy_INDICATOR.py','IndicatorEngine',T,T.IndicatorEngine)
    trend=tc.__new__(tc);trend.__dict__.update(r.trend.__dict__);r.trend=trend
    return r


@pytest.mark.parametrize('mode',['실시간','봉마감'])
def test_nonzero_end_to_end_against_original_part1_decision_code(mode):
    expected=original_replay(mode);actual=Replay(mode=mode,synthetic=True)
    for rec in synthetic_trace():
        expected.step(rec);actual.step(rec)
        for symbol in actual.monitors:
            for field in O.OZMonitor._checkpoint_fields:
                assert getattr(actual.monitors[symbol],field)==getattr(expected.monitors[symbol],field),field
        assert actual.composer._last_signatures==expected.composer._last_signatures
        assert actual.composer._active_children==expected.composer._active_children
    assert actual.delivered==expected.delivered
    assert actual.alerts==expected.alerts
    assert len(actual.delivered)==2
    assert [x['direction'] for x in actual.delivered]==['LONG','SHORT']
    assert [x['session'] for x in actual.delivered]==[['MAIN_ASIA'],['MAIN_NEWYORK']]
    assert [x['alert_time_ns'] for x in actual.delivered]==[
        pd.Timestamp('2026-08-03 10:41:30',tz='Asia/Seoul').value,
        pd.Timestamp('2026-08-03 22:17:30',tz='Asia/Seoul').value]


def test_close_is_only_presentation_no_signal_state_change():
    a=Replay(mode='실시간',synthetic=True);b=Replay(mode='봉마감',synthetic=True)
    rows=synthetic_trace()
    for row in rows:a.step(row);b.step(row)
    assert a.delivered==b.delivered
    for rt,cl in zip(a.alerts,b.alerts):
        assert cl['display_time_ns']==(rt['alert_time_ns']//M1_NS+1)*M1_NS
        assert cl['session']==rt['session']
    assert [x['display_time_ns'] for x in b.alerts]==[
        pd.Timestamp('2026-08-03 10:42',tz='Asia/Seoul').value,
        pd.Timestamp('2026-08-03 22:18',tz='Asia/Seoul').value]


def test_eof_does_not_invent_future_bar_close():
    rows=synthetic_trace(False)
    r=Replay(mode='봉마감',synthetic=True);r.run(rows[:-1])
    assert len(r.delivered)==1 and len(r.alerts)==0 and len(r.pending)==1
    r.step(rows[-1]);assert len(r.alerts)==1


def test_retry_keeps_first_part1_event_time_and_preserves_watch_until_cancel_drain():
    rows=synthetic_trace(False)
    first=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and x['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    rows[first]['delivery_results']=[False];rows[first+1]['delivery_results']=[True]
    r=Replay(synthetic=True)
    for row in rows[:first+1]:r.step(row)
    assert not r.delivered
    assert r.composer._active_children and r.watch._watches
    r.step(rows[first+1]);assert len(r.delivered)==1
    signal=r.delivered[0]
    assert signal['alert_time_ns']==rows[first]['timestamp_ns']
    assert signal['delivery_time_ns']==rows[first+1]['timestamp_ns']
    assert not r.composer._active_children
    assert r.watch._watches  # Cancel is an asynchronous command, not an inline mutation.
    r.step(rows[first+2]);assert not r.watch._watches


def test_duplicate_receipt_does_not_deliver_twice():
    r=Replay(synthetic=True);r.run(synthetic_trace(False))
    event=next(iter(r.sender._events.all().values()))
    with clock.at(r.last_ns+1):reply=r.sender._request(event)
    assert reply['duplicate'] is True and len(r.delivered)==1


def test_final_session_gate_acknowledges_without_real_delivery_or_child_cancellation():
    r=Replay(synthetic=True);rows=synthetic_trace(False)
    trigger=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and x['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    for rec in rows[:trigger]:r.step(rec)
    # Invoke the real SPECIAL1 handler outside all final delivery sessions.
    wid=next(iter(r.composer._active_children))
    outside=pd.Timestamp('2026-08-03 13:00',tz='Asia/Seoul').value
    with clock.at(outside):
        reply=r.sender._request({'kind':'FINAL_ALERT','strategy':'OZ','direction':'LONG',
            'symbol':SYMBOL,'event_id':'session-test','event_time':outside/1e9,'source_tf':'1m',
            'source_spec_id':'PIPELINE_1@1h','watch_ids':[wid],'message':'unit-test'})
    assert reply['delivered'] and reply['suppressed']
    assert len(r.delivered)==0 and wid in r.composer._active_children


@pytest.mark.parametrize('hm,expected', [('08:00:00',True),('12:00:59',True),('12:01:00',False),('20:59:59',False),('21:00:00',True)])
def test_exact_part1_minute_inclusive_session_policy(hm,expected):
    r=Replay(synthetic=True)
    with clock.at(pd.Timestamp('2026-08-03 '+hm,tz='Asia/Seoul').value):
        assert r.composer._time_policy.allows(('MAIN_ASIA','MAIN_LONDON','MAIN_NEWYORK')) is expected


@pytest.mark.parametrize('mode',['REALTIME','CLOSE'])
def test_checkpoint_continuation_preserves_state_and_pending_presentation(mode):
    rows=synthetic_trace();split=next(i for i,x in enumerate(rows) if x['kind']=='CLOCK')
    original=Replay(mode=mode,synthetic=True);original.run(rows[:split])
    payload=json.loads(json.dumps(dump(original),allow_nan=False))
    resumed=restore(payload)
    original.run(rows[split:]);resumed.run(rows[split:])
    assert resumed.result()==original.result()
    assert resumed.composer._last_signatures==original.composer._last_signatures


def test_checkpoint_refuses_wrong_source_or_dependencies():
    p=dump(Replay(synthetic=True));p['source_fingerprint']='wrong'
    with pytest.raises(ReplayError,match='mismatch'):restore(p)
    p=dump(Replay(synthetic=True));p['dependencies']['pandas']='other'
    with pytest.raises(ReplayError,match='version'):restore(p)


def test_forming_native_buffers_not_replaced_with_completed_bar():
    r=Replay(synthetic=True);frame=native_frame()
    frame.loc[len(frame)-1,'RSI_val']=13.25
    frame.loc[len(frame)-2,'RSI_val']=66.75
    rec=publication(frame,SYMBOL,'1m',1,timestamp_ns=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value,sequence=1)
    r.step(rec)
    with clock.at(rec['timestamp_ns']):
        got=r.staff.request(SYMBOL,['1m'],['RSI','STO','DI','PRICE'])['1m']
    assert got.iloc[-1]['RSI_val']==13.25
    assert got.iloc[-2]['RSI_val']==66.75
    for field in ('RSI_val','RSI_db','RSI_ub','STO_val','STO_db','STO_ub','DI_val','DI_db','DI_ub','price_hma_6','price_band_lower','price_band_upper'):
        np.testing.assert_array_equal(got[field].to_numpy(),frame[field].to_numpy())
    assert O.percentile_states(got.iloc[-1])['RSI']=='LOWER_OUT'


def test_invalid_native_requested_family_fails_instead_of_using_previous_row():
    r=Replay(synthetic=True);frame=native_frame();frame.loc[len(frame)-1,'RSI_val']=np.nan
    rec=publication(frame,SYMBOL,'1m',1,timestamp_ns=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value,sequence=1)
    r.step(rec)
    with clock.at(rec['timestamp_ns']):
        assert r.staff.request(SYMBOL,['1m'],['RSI']) is None
        assert r.staff.request(SYMBOL,['1m'],['WONBI']) is not None


def test_native_copy_exactly_preserves_duplicate_snapshot_policy():
    r=Replay(synthetic=True);now=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value
    f=native_frame();first=publication(f,SYMBOL,'1m',10,timestamp_ns=now,sequence=1)
    r.step(first);f.loc[len(f)-1,'close']=999.
    r.step(publication(f,SYMBOL,'1m',10,timestamp_ns=now+1,sequence=2))
    from staff_schema import legacy_frame
    assert legacy_frame(SYMBOL,'1m',r.cache.snapshot(SYMBOL,'1m'),r.cache.max_bars).iloc[-1]['close']!=999.


def test_native_staleness_and_health_epoch():
    r=Replay(synthetic=True);now=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value;f=native_frame()
    r.step(publication(f,SYMBOL,'1m',1,timestamp_ns=now,sequence=1))
    from staff_schema import legacy_frame
    epoch=legacy_frame(SYMBOL,'1m',r.cache.snapshot(SYMBOL,'1m'),r.cache.max_bars).attrs['source_epoch']
    with clock.at(now+31_000_000_000):
        assert r.staff.health(SYMBOL,['1m'])['1m']['status']=='STALE'
        assert r.staff.request(SYMBOL,['1m'],[]) is None
    r.step(publication(f,SYMBOL,'1m',2,timestamp_ns=now+31_000_000_000,sequence=2))
    assert legacy_frame(SYMBOL,'1m',r.cache.snapshot(SYMBOL,'1m'),r.cache.max_bars).attrs['source_epoch']!=epoch


def test_shared_oz_snapshot_ttl_is_preserved():
    r=Replay(synthetic=True);now=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value;f=native_frame()
    r.step(publication(f,SYMBOL,'1m',1,timestamp_ns=now,sequence=1));client=r.monitors[SYMBOL].client
    with clock.at(now):before=client.request(SYMBOL,['1m'])['1m'].iloc[-1]['close']
    f.loc[len(f)-1,'close']=before+1
    r.step(publication(f,SYMBOL,'1m',2,timestamp_ns=now+100_000_000,sequence=2))
    with clock.at(now+500_000_000):assert client.request(SYMBOL,['1m'])['1m'].iloc[-1]['close']==before
    with clock.at(now+501_000_000):assert client.request(SYMBOL,['1m'])['1m'].iloc[-1]['close']==before+1


def test_multi_timeframe_request_fails_as_a_whole():
    r=Replay(synthetic=True);now=pd.Timestamp('2026-08-03 01:40:05',tz='UTC').value
    r.step(publication(native_frame(),SYMBOL,'1m',1,timestamp_ns=now,sequence=1))
    with clock.at(now):assert r.staff.request(SYMBOL,['1m','2m'],[]) is None


def test_source_order_is_not_sorted_or_deduplicated_away():
    r=Replay();rec={'kind':'CLOCK','timestamp_ns':100,'sequence':1};r.step(rec)
    with pytest.raises(ReplayError,match='strictly ordered'):r.step(rec)
    with pytest.raises(ReplayError,match='strictly ordered'):r.step(dict(rec,timestamp_ns=99,sequence=2))


@pytest.mark.parametrize('alias',['실시간','REALTIME','TICK','LIVE_PARITY'])
def test_only_one_realtime_behavior_for_legacy_aliases(alias):assert mode_name(alias)=='REALTIME'


@pytest.mark.parametrize('alias',['봉마감','CLOSE','ONE_MINUTE_CLOSE'])
def test_close_aliases(alias):assert mode_name(alias)=='CLOSE'


def test_no_wall_clock_fallback():
    with pytest.raises(RuntimeError,match='not bound'):clock.now_ns()


def test_comparator_detects_timestamp_direction_session_count_and_order_errors():
    result=Replay(synthetic=True).run(synthetic_trace());base=ledger(result)
    assert compare(base,result)['status']=='MATCH_NONEMPTY'
    for field,value in [('alert_time_ns',1),('direction','SHORT'),('session',['MAIN_LONDON'])]:
        bad=copy.deepcopy(result);bad['alerts'][0][field]=value
        assert compare(base,bad)['status']=='MISMATCH'
    bad=copy.deepcopy(result);bad['alerts'].reverse();assert not compare(base,bad)['event_ordering_same']
    bad=copy.deepcopy(result);bad['alerts'].pop();assert not compare(base,bad)['count_same']
    with pytest.raises(ReplayError):compare({},result)
    assert compare(dict(base,alerts=[]),dict(result,alerts=[],pending_close_count=0))['status']=='INCONCLUSIVE_EMPTY'
    # Same timestamp/direction/session does not make different branch events interchangeable.
    tied=copy.deepcopy(result);tied['alerts']=[copy.deepcopy(result['alerts'][0]) for _ in range(2)]
    tied['alerts'][1]['source_spec_id']='PIPELINE_1@2h'
    tied['alerts'][1]['source_spec_ids']=['PIPELINE_1@2h']
    tied_ledger=dict(base,alerts=copy.deepcopy(tied['alerts']))
    tied['alerts'].reverse()
    assert compare(tied_ledger,tied)['status']=='MISMATCH'
    assert not compare(tied_ledger,tied)['event_ordering_same']
    incomplete=copy.deepcopy(base);incomplete['alerts'][0].pop('source_tf')
    with pytest.raises(ReplayError,match='source_tf'):compare(incomplete,result)



def test_close_comparator_uses_same_signal_with_m1_presentation_policy():
    realtime=Replay(synthetic=True).run(synthetic_trace());closed=Replay(mode='CLOSE',synthetic=True).run(synthetic_trace())
    assert compare(ledger(realtime),closed,mode='CLOSE')['status']=='MATCH_NONEMPTY'


def test_duckdb_on_is_blocked_not_silently_fallback():
    from live_replay.__main__ import run_file
    with pytest.raises(ReplayError,match='disabled'):run_file('does-not-exist.jsonl',duckdb='ON')


def test_duckdb_off_on_live_comparison_environment_gate():
    pytest.skip('ENVIRONMENT_LIMITATION: no DuckDB package; actual LIVE oracle absent and acceleration deliberately gated')


def test_windows_mt5_live_capture_environment_gate():
    pytest.skip('ENVIRONMENT_LIMITATION: Linux, no MT5/Windows runtime and no native LIVE capture in uploaded ZIP')


def test_late_delivery_keeps_original_timestamp_without_publishing_in_the_past():
    r=Replay(mode='CLOSE',synthetic=True)
    signal=pd.Timestamp('2026-08-03 10:41:30',tz='Asia/Seoul').value
    delivered=pd.Timestamp('2026-08-03 10:42:10',tz='Asia/Seoul').value
    event={'kind':'FINAL_ALERT','event_time':signal/1e9,'event_id':'late-unit',
           'symbol':SYMBOL,'direction':'LONG','source_tf':'1m','source_spec_id':'PIPELINE_1@1h'}
    with clock.at(delivered):
        r.current_delivery=event;r.deliver('unit test','OFFICIAL');r.current_delivery=None
        r.flush(delivered)
    row=r.alerts[0]
    assert row['alert_time_ns']==signal
    assert row['display_time_ns']==pd.Timestamp('2026-08-03 10:42',tz='Asia/Seoul').value
    assert row['presentation_emitted_ns']==delivered and row['late_delivery']


def test_environment_absent_consumes_completed_cycle_silently():
    r=Replay(synthetic=True)
    # Native computation continues although no Composer/TREND/Watch setup exists.
    rows=[x for x in synthetic_trace(False) if x['kind'] in {'STAFF_PUBLISH','OZ_POLL','CLOCK'}]
    r.run(rows)
    candidate=r.monitors[SYMBOL].candidates[('1m','LONG')]
    assert candidate.completed_outside_window
    assert len(r.monitors[SYMBOL].alert_keys)==1 and not r.delivered


def test_new_environment_does_not_backdate_preexisting_complete_candidate():
    rows=synthetic_trace(False)
    r=Replay(synthetic=True)
    # Arm is generated but command delivery is deferred until the final-completion snapshot.
    rows=[x for x in rows if x['kind']!='COMMAND_DRAIN']
    trigger=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and x['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    for rec in rows[:trigger]:r.step(rec)
    with clock.at(rows[trigger]['timestamp_ns']):r.drain_commands()
    r.step(rows[trigger])
    assert not r.delivered
    assert r.monitors[SYMBOL].candidates[('1m','LONG')].completed_outside_window


def test_two_deliveries_before_async_cancel_drain_are_not_collapsed_to_first_only():
    r=Replay(synthetic=True);rows=synthetic_trace(False)
    trigger=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and x['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    for rec in rows[:trigger]:r.step(rec)
    now=rows[trigger]['timestamp_ns']
    with clock.at(now):
        for tf,identity in [('1m','unit-b0-one'),('2m','unit-b0-two')]:
            assert r.watch.try_fire(SYMBOL,tf,'LONG','S','NORMAL','BREAKER',
                alert_identity=identity,completion_time=now/1e9,current_price=100.)
    assert [x['source_tf'] for x in r.delivered]==['1m','2m']
    assert r.watch._watches and not r.composer._active_children


def test_mid_tf_invalidates_only_its_percentile_family_in_actual_source_state_machine():
    r=Replay(synthetic=True);rows=synthetic_trace(False)
    trigger=next(i for i,x in enumerate(rows) if x['kind']=='OZ_POLL' and x['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    for rec in rows[:trigger]:r.step(rec)
    # Same native snapshot except one mapped middle family no longer OUT.
    with clock.at(rows[trigger]['timestamp_ns']):
        data=r.staff.request(SYMBOL,list(T.MT5_TIMEFRAMES))
        data['3m'].loc[len(data['3m'])-1,'RSI_val']=50.
        decision=r.monitors[SYMBOL]._candidate_completion_decision(data,'1m','LONG',require_external=True,commit_validation=True)
    assert decision is not None and decision.consensus_count==3
    assert r.monitors[SYMBOL].percentile_candidates[('1m','LONG','RSI')].invalidated


def test_default_nan_and_exact_boundary_state_use_strict_out_comparisons():
    row=pd.Series({'RSI_val':20.,'RSI_db':20.,'RSI_ub':80.,'STO_val':float('nan'),
                  'STO_db':20.,'STO_ub':80.,'DI_val':80.,'DI_db':20.,'DI_ub':80.,
                  'price_hma_6':95.,'price_band_lower':95.,'price_band_upper':105.})
    assert O.percentile_states(row)=={'RSI':'IN','STO':'NA','DI':'IN','PRICE':'IN'}
