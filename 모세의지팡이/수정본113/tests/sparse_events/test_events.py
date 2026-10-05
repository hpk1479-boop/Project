"""Synthetic verification. sqlite is an explicit SQL test double, NOT DuckDB.

The real DuckDB test below is separately skipped when the package is absent.
No application code imports sqlite or silently substitutes a database backend.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import zipfile
import pandas as pd
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
sys.path.insert(0,str(ROOT/'tests/special2_7'))
from live_replay import allzone_events as E
from live_replay.runtime import Replay, Monitor, ReplayError
from live_replay.synthetic import synthetic_trace
from live_replay.synthetic_specials import (special2_trace,special3_trace,special4_trace,
    special5_trace,special6_trace,special7_trace)
from live_replay.trace_io import write_trace
from live_replay.checkpoint import source_fingerprint
from oracle import original_replay


def header(n=1):
    return {'kind':'HEADER','schema':'special1-observation-trace/v1','origin':'SYNTHETIC',
        'scope':'SPECIALS','specials':[n],'initial_state':'COLD','health_session':'SPARSE_TEST',
        'source_fingerprint':source_fingerprint()}


def scenario(n,d='LONG',**kwargs):
    return synthetic_trace() if n==1 else globals()[f'special{n}_trace'](d,**kwargs)


def collect(rows,n=1):
    c=E.EventCollector(header(n));out=[]
    for row in rows: out.extend(c.step(row))
    return out,c


def sql_double(path=':memory:'):
    return E.SparseStore('TEST_DOUBLE_NOT_DUCKDB',_connection=sqlite3.connect(str(path),isolation_level=None))


@pytest.mark.parametrize('n',range(1,8))
@pytest.mark.parametrize('direction',('LONG','SHORT'))
@pytest.mark.parametrize('mode',('REALTIME','CLOSE'))
def test_nonempty_direct_event_and_current_part1(n,direction,mode):
    rows=scenario(n,direction);events,_=collect(rows,n)
    assert events
    off=Replay(specials=(n,),synthetic=True,mode=mode)
    on=Replay(specials=(n,),synthetic=True,mode=mode,allzone_events=events)
    original=original_replay(specials=(n,),mode=mode)
    for row in rows:
        off.step(row);on.step(row);original.step(row)
        assert on.delivered==off.delivered==original.delivered
        assert on.alerts==off.alerts==original.alerts
    assert any(a['direction']==direction for a in off.delivered)
    assert on.event_playback.stats()['allzone_calculation_calls']==0
    assert all(m is None for m in on.profile_monitors.values())


@pytest.mark.parametrize('n,kwargs',[(2,{'source_tf':'2m'}),(3,{'cross_tf':'2m'}),
    (5,{'final_tf':'3m'}),(5,{'source_mode':'BREAKER_REGIME'}),(6,{'setup_tf':'30m'})])
def test_additional_trigger_tf_and_branch(n,kwargs):
    rows=scenario(n,'LONG',**kwargs);events,_=collect(rows,n)
    off=Replay(specials=(n,),synthetic=True,mode='CLOSE')
    on=Replay(specials=(n,),synthetic=True,mode='CLOSE',allzone_events=events)
    for row in rows:off.step(row);on.step(row)
    assert len(on.delivered)==len(off.delivered)==1
    assert on.alerts==off.alerts


@pytest.mark.parametrize('n',range(1,8))
def test_batch_sql_roundtrip_once_and_no_future(tmp_path,n):
    rows=scenario(n);expected,_=collect(rows,n)
    path=tmp_path/f'special{n}.jsonl';write_trace(path,header(n),rows)
    with sql_double() as db:
        result=E.build_trace(path,db);saved=db.build(result['build_id'])
        loaded=db.read_events(result['build_id'],saved['start_ns'],saved['end_ns'])
        assert loaded==expected and db.event_queries==1
        on=Replay(specials=(n,),synthetic=True,allzone_events=loaded)
        for row in rows:
            on.step(row)
            assert all(a['alert_time_ns']<=row['timestamp_ns'] for a in on.delivered)
            assert all(e['event_time']<=row['timestamp_ns'] for e in on.event_playback.events[:on.event_playback.position])
        assert db.event_queries==1
        tables={r[0] for r in db.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert tables=={'allzone_events','allzone_builds','allzone_days'}
        assert db.db.execute('SELECT count(*) FROM allzone_events').fetchone()[0]==len(expected)


def test_on_does_not_construct_or_execute_allzone(monkeypatch):
    rows=scenario(1);events,_=collect(rows)
    def forbidden(*args,**kwargs):raise AssertionError('ALLZONE runtime was executed ON')
    monkeypatch.setattr(Monitor,'__init__',forbidden)
    for name in ('_process_out_in','_process_hma_cross','_maintain_base_candidate','_candidate_completion_decision'):
        monkeypatch.setattr(E.O.OZMonitor,name,forbidden)
    on=Replay(synthetic=True,allzone_events=events).run(rows)
    assert on['delivered_count']==2


def test_period_query_no_future_and_no_fake_empty_rows(tmp_path):
    rows=scenario(1);p=tmp_path/'all.jsonl';write_trace(p,header(),rows)
    with sql_double() as db:
        r=E.build_trace(p,db);saved=db.build(r['build_id'])
        first,_=collect(rows);stamp=first[0]['event_time']
        assert not db.read_events(r['build_id'],saved['start_ns'],stamp-1)
        selected=db.read_events(r['build_id'],stamp,stamp)
        assert len(selected)==1 and selected[0]['event_time']==stamp
        with pytest.raises(ReplayError,match='COVERAGE'):
            db.read_events(r['build_id'],saved['start_ns'],saved['end_ns']+1)


def shift_day(rows,days):
    # Only clock/publication age moves; the native bar-time axis is independent
    # in Part1. This scenario tests storage boundaries, not new market prices.
    return [dict(r,timestamp_ns=r['timestamp_ns']+days*E.DAY_NS,
                 sequence=r['sequence']+days*10000) for r in rows]


def test_daily_failure_rollback_resume_and_incremental(tmp_path):
    base=scenario(1);rows=base+shift_day(base,1)+shift_day(base,2)
    p=tmp_path/'multi.jsonl';write_trace(p,header(),rows)
    expected,_=collect(rows)
    with sql_double(tmp_path/'test_double.sqlite') as db:
        with pytest.raises(RuntimeError,match='INJECTED_RECORD_FAILURE'):
            E.build_trace(p,db,fail_at_record=len(base)+12)
        bid=E.trace_build_id(p,header());saved=db.build(bid)
        assert saved['status']=='FAILED' and saved['last_complete_day']==E.day_name(base[-1]['timestamp_ns'])
        committed=saved['committed_offset'];count=saved['event_count']
        final=E.build_trace(p,db)
        assert final['resumed'] and final['processed_records']==len(rows)-len(base)
        assert db.build(bid)['committed_offset']>committed
        actual=db.read_events(bid,db.build(bid)['start_ns'],db.build(bid)['end_ns'])
        assert actual==expected
        assert E.build_trace(p,db)['processed_records']==0
        extra=shift_day(base,3)
        with p.open('a') as f:
            for row in extra:f.write(json.dumps(row)+'\n')
        before=db.build(bid)['event_count'];r=E.build_trace(p,db)
        assert r['processed_records']==len(extra)
        assert r['events']>=before>=count
        all_expected,_=collect(rows+extra)
        assert db.read_events(bid,db.build(bid)['start_ns'],db.build(bid)['end_ns'])==all_expected


def test_transaction_failure_retains_prior_day(tmp_path):
    base=scenario(1);rows=base+shift_day(base,1)+shift_day(base,2)
    p=tmp_path/'rollback.jsonl';write_trace(p,header(),rows)
    with sql_double() as db:
        fail=E.day_name(rows[len(base)]['timestamp_ns'])
        with pytest.raises(RuntimeError,match='COMMIT_FAILURE'):E.build_trace(p,db,fail_day=fail)
        saved=db.build(E.trace_build_id(p,header()))
        assert saved['last_complete_day']==E.day_name(base[-1]['timestamp_ns'])
        assert db.db.execute('SELECT count(*) FROM allzone_days').fetchone()[0]==1
        r=E.build_trace(p,db);assert r['resumed']


def test_pause_checkpoint_preserves_inflight_candidate(tmp_path):
    rows=scenario(1);p=tmp_path/'pause.jsonl';write_trace(p,header(),rows)
    count=0
    def pause():
        nonlocal count
        count+=1
        return count==48
    with sql_double() as db:
        a=E.build_trace(p,db,pause=pause);assert a['status']=='PAUSED'
        saved=db.build(a['build_id']);restored=E.EventCollector.restore(saved['checkpoint'])
        assert restored.last_key==tuple((rows[47]['timestamp_ns'],rows[47]['sequence']))
        b=E.build_trace(p,db);assert b['processed_records']==len(rows)-48
        saved=db.build(a['build_id'])
        assert db.read_events(a['build_id'],saved['start_ns'],saved['end_ns'])==collect(rows)[0]


def test_mutated_history_corrupt_checkpoint_and_version_are_rejected(tmp_path):
    p=tmp_path/'guard.jsonl';rows=scenario(1);write_trace(p,header(),rows)
    with sql_double() as db:
        r=E.build_trace(p,db);saved=db.build(r['build_id'])
        raw=p.read_bytes();p.write_bytes(raw.replace(b'STAFF_PUBLISH',b'STAFF_PUBLISX',1))
        with pytest.raises(ReplayError,match='PREFIX_CHANGED'):E.build_trace(p,db)
        p.write_bytes(raw)
        db.db.execute('UPDATE allzone_builds SET checkpoint=?',[b'bad'])
        with pytest.raises(ReplayError,match='CHECKPOINT_CORRUPT'):E.build_trace(p,db)
        db.db.execute('UPDATE allzone_builds SET checkpoint=?,engine_hash=?',[saved['checkpoint'],'wrong'])
        with pytest.raises(ReplayError,match='VERSION_CHANGED'):E.build_trace(p,db)


def test_incomplete_last_row_is_not_committed_or_called_complete(tmp_path):
    p=tmp_path/'growing.jsonl';write_trace(p,header(),scenario(1))
    valid=p.stat().st_size
    with p.open('ab') as f:f.write(b'{"kind":"STAFF')
    with sql_double() as db:
        r=E.build_trace(p,db);saved=db.build(r['build_id'])
        assert saved['committed_offset']==valid
        assert saved['last_complete_day'] is None
        assert db.db.execute('SELECT status FROM allzone_days').fetchone()[0]=='PARTIAL_DAY'


@pytest.mark.parametrize('tf,expected',[('1m','10:42:00'),('3m','10:45:00'),('15m','10:45:00')])
def test_own_bar_close(tf,expected):
    stamp='10:41:30' if tf=='1m' else ('10:43:17' if tf=='3m' else '10:37:24')
    ns=pd.Timestamp('2024-01-23 '+stamp,tz='Asia/Seoul').value
    assert E.close_time_ns(ns,tf)==pd.Timestamp('2024-01-23 '+expected,tz='Asia/Seoul').value


def test_retry_preserves_original_timestamp():
    rows=scenario(2);index=next(i for i,r in enumerate(rows) if r['kind']=='OZ_POLL' and
        r['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:30',tz='UTC').value)
    rows[index]['delivery_results']=[False];rows[index+1]['delivery_results']=[True]
    events,_=collect(rows,2);off=Replay(specials=(2,),synthetic=True)
    on=Replay(specials=(2,),synthetic=True,allzone_events=events)
    for row in rows:off.step(row);on.step(row)
    assert len(on.delivered)==1 and on.delivered==off.delivered
    assert on.delivered[0]['alert_time_ns']==rows[index]['timestamp_ns']


def test_real_duckdb_roundtrip(tmp_path):
    pytest.importorskip('duckdb',reason='ENVIRONMENT_LIMITATION: actual DuckDB package unavailable')
    p=tmp_path/'real.jsonl';write_trace(p,header(),scenario(1))
    with E.SparseStore(tmp_path/'actual.duckdb') as db:
        r=E.build_trace(p,db);s=db.build(r['build_id'])
        assert len(db.read_events(r['build_id'],s['start_ns'],s['end_ns']))==2
    with E.SparseStore(tmp_path/'actual.duckdb',read_only=True) as db:
        assert db.build(r['build_id'])['event_count']==2


def delayed_external_breach(direction):
    """Natural cross/OUT-IN -> ATR breach -> later strict BO break. No candidate injection."""
    from live_replay.synthetic_specials import NativeTrace,sweep_market
    day='2026-08-03 ';hour='01' if direction=='LONG' else '13';minute=40 if direction=='LONG' else 16
    a=day+hour+f':{minute:02d}';b=day+hour+f':{minute+1:02d}';c=day+hour+f':{minute+2:02d}'
    start=pd.Timestamp(a+':00');up=direction=='LONG';side='low' if up else 'high'
    t=NativeTrace();t.rows=[r for r in special2_trace(direction) if r['timestamp_ns']<=pd.Timestamp(a+':06.200',tz='UTC').value]
    t.sequence=max(r['sequence'] for r in t.rows);t.snapshot=sum(r['kind']=='STAFF_PUBLISH' for r in t.rows)
    def market(frame,tf,d,phase):
        frame=sweep_market(frame,tf,d,phase)
        if tf=='1m':
            frame.loc[frame['time']>=start,'hma_6']=101. if up else 99.
            frame.loc[frame['time']==start-pd.Timedelta(minutes=1),'hma_6']=99. if up else 101.
            frame.loc[frame['time']==start-pd.Timedelta(minutes=1),side]=90. if up else 110.
            frame.loc[frame['time']==start,side]=70. if up else 130.
            frame.loc[frame['time']==start+pd.Timedelta(minutes=1),side]=97. if up else 103.
            frame.loc[frame['time']==start+pd.Timedelta(minutes=2),side]=89. if up else 111.
        return frame
    t.publish(a+':07',a+':00',direction,1,market);t.event('OZ_POLL',a+':07.200',symbol='XAUUSD+')
    t.publish(b+':10',b+':00',direction,2,market);t.event('OZ_POLL',b+':10.200',symbol='XAUUSD+')
    t.publish(c+':30',c+':00',direction,2,market);t.event('OZ_POLL',c+':30.200',symbol='XAUUSD+')
    t.event('COMMAND_DRAIN',c+':30.400');t.close(str(pd.Timestamp(c+':00')+pd.Timedelta(minutes=1)))
    return t.rows


@pytest.mark.parametrize('direction',('LONG','SHORT'))
@pytest.mark.xfail(strict=True,reason='KNOWN_PARITY_FAILURE: pre-completion SPECIAL2 ATR confirmation is not in completed events')
def test_known_special2_early_external_confirmation(direction):
    rows=delayed_external_breach(direction);events,_=collect(rows,2)
    off=Replay(specials=(2,),synthetic=True);original=original_replay(specials=(2,))
    on=Replay(specials=(2,),synthetic=True,allzone_events=events)
    for row in rows:
        off.step(row);original.step(row);on.step(row)
        assert original.delivered==off.delivered
    assert len(off.delivered)==1 and off.delivered[0]['direction']==direction
    assert on.delivered==off.delivered


def modify_publication(record, fn):
    import base64
    from live_replay import clock
    from live_replay.runtime import NativeCache
    from live_replay.reference.staff import PIPE_HEADER
    from live_replay.trace_io import publication
    cache=NativeCache('MOD',30);raw=base64.b64decode(record['payload_b64'])
    with clock.at(record['timestamp_ns']):cache.publish(raw)
    symbol,tf=cache.keys()[0]
    from staff_schema import legacy_frame
    frame=legacy_frame(symbol,tf,cache.snapshot(symbol,tf),cache.max_bars)
    frame=fn(frame.copy(),tf)
    return publication(frame,symbol,tf,PIPE_HEADER.unpack(raw[:PIPE_HEADER.size])[2],
                       timestamp_ns=record['timestamp_ns'],sequence=record['sequence'])


@pytest.mark.xfail(strict=True,reason='KNOWN_PARITY_FAILURE: passive collection cannot preserve environment-conditioned NORMAL family invalidation')
def test_known_mid_confirmation_invalidation_before_completion():
    rows=synthetic_trace(two_sessions=False)
    def patch(frame,tf):
        if tf=='3m':
            for family in ('RSI','STO','DI'):frame.loc[len(frame)-1,f'{family}_val']=50.
            frame.loc[len(frame)-1,'price_hma_6']=100.
        return frame
    rows=[modify_publication(r,patch) if r['kind']=='STAFF_PUBLISH' and
          r['timestamp_ns']==pd.Timestamp('2026-08-03 01:40:06',tz='UTC').value else r for r in rows]
    events,_=collect(rows);off=Replay(synthetic=True);original=original_replay()
    on=Replay(synthetic=True,allzone_events=events)
    for row in rows:off.step(row);original.step(row);on.step(row)
    assert off.delivered==original.delivered==[]
    assert on.delivered==off.delivered


@pytest.mark.parametrize('profile',tuple(E.PROFILES))
def test_actual_profile_axes_with_nonempty_native_trigger(profile):
    rows=special7_trace('LONG')
    vm,tm=E.PROFILES[profile]
    rows=[dict(r,validation_mode=vm,trigger_mode=tm) if r['kind']=='OZ_POLL' else r for r in rows]
    events,collector=collect(rows,7)
    assert any(e['allzone_type']==profile and e['timeframe']=='1m' and e['direction']=='LONG' for e in events)
    assert collector.polled_profiles=={('XAUUSD+',vm,tm)}
    assert all(collector.monitors[('XAUUSD+',a,b)].client is collector.oz_staff['XAUUSD+'] for a,b in E.PROFILES.values())


@pytest.mark.parametrize('tf',E.DEFAULT_TFS)
def test_each_requested_base_tf_uses_its_own_middle_upper_mapping(tf):
    from live_replay.synthetic_specials import NativeTrace
    from live_replay.synthetic import native_frame
    from live_replay.reference.language import tf_seconds
    t=NativeTrace();a=pd.Timestamp('2026-08-03 00:00:00');b=a+pd.Timedelta(seconds=tf_seconds(tf))
    def market(frame,current,direction,phase):
        if current==tf:
            source=native_frame('1m',direction=direction,phase=phase)
            source['time']=frame['time'];frame=source
        if current==E.O.TF_MAP[tf][0]:
            for family in ('RSI','STO','DI'):frame.loc[len(frame)-1,f'{family}_val']=10.
            frame.loc[len(frame)-1,'price_hma_6']=94.
        return frame
    for stamp,bar,phase in ((a+pd.Timedelta(seconds=5),a,0),(a+pd.Timedelta(seconds=6),a,1),(b+pd.Timedelta(seconds=29),b,2)):
        t.publish(str(stamp),str(bar),'LONG',phase,market)
        t.event('OZ_POLL',str(stamp+pd.Timedelta(milliseconds=200)),symbol='XAUUSD+',trigger_mode='BREAKER')
    events,_=collect(t.rows)
    assert any(e['timeframe']==tf and e['allzone_type']=='BREAKER' for e in events)


def test_midnight_checkpoint_has_unfinished_candidate_then_one_event(tmp_path, record_property):
    rows=synthetic_trace(two_sessions=False)
    # Shift the observation clock so OUT->IN is before UTC midnight and TRUE_B0
    # after it; native bar order remains unchanged. Storage must not reset state.
    delta=pd.Timestamp('2026-08-03 23:59:00',tz='UTC').value-pd.Timestamp('2026-08-03 01:40:00',tz='UTC').value
    rows=[dict(r,timestamp_ns=r['timestamp_ns']+delta) for r in rows]
    expected,full=collect(rows)
    assert len(expected)==1
    boundary=next(i for i,r in enumerate(rows) if E.day_name(r['timestamp_ns'])!=E.day_name(rows[0]['timestamp_ns']))
    p=tmp_path/'midnight.jsonl';write_trace(p,header(),rows)
    with sql_double() as db:
        with pytest.raises(RuntimeError):E.build_trace(p,db,fail_at_record=boundary)
        saved=db.build(E.trace_build_id(p,header()))
        recovered=E.EventCollector.restore(saved['checkpoint'])
        assert any(c is not None and not c.alerted and not c.completed_outside_window
                   for m in recovered.monitors.values() for c in m.candidates.values())
        assert saved['event_count']==0
        E.build_trace(p,db);saved=db.build(E.trace_build_id(p,header()))
        assert db.read_events(saved['build_id'],saved['start_ns'],saved['end_ns'])==expected
        restored=E.EventCollector.restore(saved['checkpoint'])
        restored_blob, full_blob = restored.checkpoint(), full.checkpoint()
        # S6+ policy: storage bytes and unordered-container serialization order
        # are diagnostics. Keep every typed value/field, ordered sequence, and
        # all preceding unfinished-candidate/final-event assertions as gates.
        record_property('checkpoint_raw_bytes_equal', restored_blob == full_blob)
        record_property('checkpoint_restored_sha256', hashlib.sha256(restored_blob).hexdigest())
        record_property('checkpoint_continuous_sha256', hashlib.sha256(full_blob).hexdigest())
        (tmp_path/'checkpoint_restored.bin').write_bytes(restored_blob)
        (tmp_path/'checkpoint_continuous.bin').write_bytes(full_blob)
        import zlib
        def semantic(value):
            if isinstance(value, list):
                return [semantic(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: semantic(item) for key, item in value.items()}
            if result.get('$') in ('set', 'frozenset'):
                result['value'] = sorted(result['value'], key=lambda item: json.dumps(item, sort_keys=True))
            elif result.get('$') == 'dict':
                result['value'] = sorted(result['value'], key=lambda pair: json.dumps(pair[0], sort_keys=True))
            return result
        assert semantic(json.loads(zlib.decompress(restored_blob))) == semantic(json.loads(zlib.decompress(full_blob)))


def test_cli_on_batch_reads_once_and_auto_off_without_db(tmp_path,monkeypatch):
    from live_replay.__main__ import run_file
    rows=scenario(1);trace=tmp_path/'cli.jsonl';write_trace(trace,header(),rows)
    file=tmp_path/'SQL_TEST_DOUBLE.sqlite'
    with sql_double(file) as db:E.build_trace(trace,db)
    stores=[];original_store=E.SparseStore
    def connect(path,**kwargs):
        store=original_store('SQL_TEST_DOUBLE',_connection=sqlite3.connect(str(path),isolation_level=None))
        stores.append(store);return store
    monkeypatch.setattr(E,'SparseStore',connect)
    off=run_file(trace,duckdb='AUTO',duckdb_path=tmp_path/'missing.duckdb')
    on=run_file(trace,duckdb='ON',duckdb_path=file)
    assert off['alerts']==on['alerts'] and len(on['alerts'])==2
    assert off['duckdb']=='OFF' and on['duckdb']=='ON'
    assert on['duckdb_parity']=='KNOWN_PARITY_FAILURES'
    assert on['duckdb_cache']['event_queries']==1 and stores[0].event_queries==1
    with trace.open('a') as out:out.write(json.dumps(dict(rows[-1],timestamp_ns=rows[-1]['timestamp_ns']+1,sequence=rows[-1]['sequence']+1))+'\n')
    with pytest.raises(ReplayError,match='NEEDS_UPDATE'):run_file(trace,duckdb='ON',duckdb_path=file)


def test_data_manager_archive_error_does_not_cold_start_next_file(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('dm_sparse_test',ROOT/'DataManager/manager/allzone_events.py')
    dm=importlib.util.module_from_spec(spec);spec.loader.exec_module(dm)
    for name in ('a.jsonl','b.jsonl'):(tmp_path/name).write_text('{}\n')
    calls=[]
    class Process:
        def __init__(self,args,**kwargs):
            calls.append(args);self.code=1 if len(calls)==1 else 0
            self.stdout=iter([json.dumps({'status':'FAILED','error':'INJECTED_INPUT_ERROR'} if self.code else {'status':'READY','events':1,'progress':1})+'\n'])
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def wait(self):return self.code
    monkeypatch.setattr(dm.subprocess,'Popen',Process)
    result=dm.run_build(tmp_path,tmp_path/'test.duckdb',part2=ROOT/'Part2',
                        symbol='XAUUSD+',source_mode='NATIVE')
    # OBSOLETE_TEST replaced: starting each file cold breaks cross-file state.
    assert len(calls)==1 and result['status']=='FAILED'
    assert str(tmp_path.resolve()) in calls[0] and 'INJECTED_INPUT_ERROR' in result['error']
    assert 'live_replay.event_catalog' in calls[0]


def test_no_poll_data_is_not_reported_as_built(tmp_path):
    rows=[r for r in scenario(1) if r['kind']=='CLOCK']
    p=tmp_path/'no_polls.jsonl';write_trace(p,header(),rows)
    with sql_double() as db:
        with pytest.raises(ReplayError,match='NO_ALLZONE_OBSERVATIONS'):E.build_trace(p,db)


@pytest.mark.parametrize('program',('DataManager','Part2'))
def test_virtual_display_gui_controls(tmp_path,program):
    import os,subprocess
    if sys.platform!='win32' and not os.environ.get('DISPLAY'):
        pytest.skip('Virtual display is required for a real Tk window')
    body='''
import tkinter as tk
from tkinter import ttk
root=tk.Tk()
def widgets(node):
    result=[node]
    for child in node.winfo_children():result.extend(widgets(child))
    return result
'''
    if program=='DataManager':
        body+='''
from manager.gui import DataManagerApp
app=DataManagerApp(root)
app.submit=lambda action:None
app.open_allzone_events()
root.update()
controls=widgets(root)
texts=[str(w.cget('text')) for w in controls if 'text' in w.keys()]
assert all(x in texts for x in ('EVENT 저장·증분','원본 데이터 수집','MT5 연결','일시정지','재개','전체 최신화','전체 재구축','현재 날짜 (UTC)','현재 EVENT'))
assert len([w for w in controls if isinstance(w,tk.Toplevel)])==1
'''
    else:
        body+='''
from live_replay import gui
original=tk.Tk
tk.Tk=lambda:root
root.mainloop=lambda:None
gui.main()
root.update()
choices=[tuple(w.cget('values')) for w in widgets(root) if isinstance(w,ttk.Combobox)]
assert ('실시간','봉마감') in choices and ('OFF','ON') in choices
'''
    body+='\nroot.destroy()\nprint("VIRTUAL_GUI_CONTROLS_PASS")\n'
    env=dict(os.environ,PYTHONPATH=str(ROOT/program),PYTHONDONTWRITEBYTECODE='1',LOCALAPPDATA=str(tmp_path))
    run=subprocess.run([sys.executable,'-B','-c',body],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'VIRTUAL_GUI_CONTROLS_PASS' in run.stdout


@pytest.mark.parametrize('blocked',('3m_band_out','3m_open_hma6','6m_band_in'))
def test_m1_requires_full_middle_upper_confirmation(blocked):
    rows=synthetic_trace(two_sessions=False)
    def patch(frame,tf):
        if blocked=='3m_band_out' and tf=='3m':
            for family in ('RSI','STO','DI'):frame.loc[len(frame)-1,f'{family}_val']=50.
            frame.loc[len(frame)-1,'price_hma_6']=100.
        if blocked=='3m_open_hma6' and tf=='3m':
            frame.loc[len(frame)-1,'open']=frame.iloc[-1]['hma_6']-1.
        if blocked=='6m_band_in' and tf=='6m':
            for family in ('RSI','STO','DI'):frame.loc[len(frame)-1,f'{family}_val']=10.
            frame.loc[len(frame)-1,'price_hma_6']=94.
        return frame
    rows=[modify_publication(r,patch) if r['kind']=='STAFF_PUBLISH' and
          r['timestamp_ns']==pd.Timestamp('2026-08-03 01:41:29.900',tz='UTC').value else r for r in rows]
    assert collect(synthetic_trace(two_sessions=False))[0]  # Positive control.
    events,_=collect(rows);off=Replay(synthetic=True);original=original_replay()
    for row in rows:off.step(row);original.step(row)
    assert events==[] and off.delivered==original.delivered==[]


def test_regime_slope_and_max_bar_filter_are_not_removed_by_collector():
    assert collect(special7_trace('LONG'),7)[0]
    assert not collect(special7_trace('LONG',wrong_regime=True),7)[0]
    h=header();h['config']={'MAX_BARS_AFTER_HMA_CROSS':'0'}
    c=E.EventCollector(h);result=[];direct=Replay(synthetic=True,config=h['config'])
    rows=synthetic_trace(two_sessions=False)
    assert collect(rows)[0]
    for row in rows:result.extend(c.step(row));direct.step(row)
    assert not result and not direct.delivered


def test_data_manager_parent_close_requests_safe_pause(tmp_path):
    import os,subprocess
    if sys.platform!='win32' and not os.environ.get('DISPLAY'):
        pytest.skip('Virtual display is required for a real Tk window')
    body=r'''
import tkinter as tk,time
from tkinter import ttk,messagebox
from pathlib import Path
from manager.gui import DataManagerApp
from manager import allzone_events as module
root=tk.Tk();app=DataManagerApp(root);app.submit=lambda action:None;seen=[]
def fake(source,db_path,*,pause_file,emit,**kwargs):
    limit=time.monotonic()+2
    while not Path(pause_file).exists() and time.monotonic()<limit:time.sleep(.01)
    seen.append(Path(pause_file).exists())
    emit({'status':'PAUSED' if seen[-1] else 'FAILED','events':0})
    return {'status':'PAUSED'}
module.run_build=fake
app.open_allzone_events();root.update()
window=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
def widgets(node):
    result=[node]
    for child in node.winfo_children():result.extend(widgets(child))
    return result
app.symbol.set('XAUUSD+')
# The event window inherited an empty symbol; only the symbol control is set.
entries=[w for w in widgets(window) if isinstance(w,ttk.Entry)]
entries[1].delete(0,'end');entries[1].insert(0,'XAUUSD+')
window.event_start('EVENT 저장·증분')
assert app.busy
module.close_choice=lambda parent:'저장 후 종료'
app.close()
destroyed=False;limit=time.monotonic()+3
while time.monotonic()<limit:
    try:root.update();alive=root.winfo_exists()
    except tk.TclError:destroyed=True;break
    if not alive:destroyed=True;break
    time.sleep(.01)
if not destroyed:root.destroy()
assert seen==[True],repr(seen)
assert destroyed,'parent window did not close after safe pause'
print('VIRTUAL_PARENT_CLOSE_PASS')
'''
    env=dict(os.environ,PYTHONPATH=str(ROOT/'DataManager'),PYTHONDONTWRITEBYTECODE='1',LOCALAPPDATA=str(tmp_path))
    result=subprocess.run([sys.executable,'-B','-c',body],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
