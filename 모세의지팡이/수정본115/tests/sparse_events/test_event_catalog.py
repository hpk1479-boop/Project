"""SYNTHETIC tests. SQLite is an explicitly injected SQL test double, NOT DuckDB."""
import copy
import base64
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
from live_replay import event_catalog as C
from live_replay import allzone_events as E
from live_replay.synthetic import synthetic_trace,native_frame,SYMBOL
from live_replay.synthetic_specials import special3_trace,special5_trace,special7_trace
from live_replay.trace_io import write_trace,publication


def sql_store(path=':memory:'):
    return C.EventStore('EXPLICIT_SQL_TEST_DOUBLE',_connection=sqlite3.connect(str(path),isolation_level=None))


def header():
    return {'kind':'HEADER','schema':'special1-observation-trace/v1','source_fingerprint':C.source_fingerprint(),
            'origin':'SYNTHETIC','health_session':'EVENT_CATALOG_TEST','initial_state':'COLD',
            'scope':'SPECIALS','specials':[1]}


def write(path,rows):
    write_trace(path,header(),rows);return path


def bars(count=100,tf='15m',start='2026-08-01'):
    width=C.tf_seconds(tf)*C.NS;stamp=int(pd.Timestamp(start,tz='UTC').value)
    rows=[]
    for i in range(count):
        price=100.+(i%7)*2
        rows.append(dict(timestamp=stamp+i*width,open=price,high=200. if i%9==5 else price+.2,
                         low=1. if i%11==7 else price-.2,close=price,volume=i+1.))
    return rows


def build_raw_bb(db,*args,**kwargs):
    # Explicit S7 scope: generic BB storage mechanics still run unchanged.
    kwargs.setdefault('event_types',('BB_OPEN_4_3_TOUCH',))
    return C.build_ohlcv(db,*args,**kwargs)


def events(db):
    return db.db.execute('SELECT * FROM events ORDER BY symbol,timeframe,timestamp,event_type').fetchall()


def test_requested_defaults_only():
    assert C.GROUP_A==('1m','2m','3m','4m','5m','6m')
    assert C.GROUP_B==('15m','30m','1h')
    assert len(C.A_TYPES)==13 and len(C.B_TYPES)==2
    assert C.ALLZONE_TYPES['BREAKER_BLIND_ALLZONE']=='BLIND_BREAKER'
    assert 'MACD' not in C.EVENT_TYPES and 'REGIME' not in C.EVENT_TYPES


def test_primary_key_and_same_timestamp_two_sides():
    key=('TEST','3m','BB_OPEN_4_3_TOUCH');fp=C.definition_hash(key[2])
    one=C.occurrence_event(key,300,[{'side':'LOWER','direction':'LONG'}],fp)
    two=C.occurrence_event(key,300,[{'side':'UPPER','direction':'SHORT'}],fp)
    with sql_store() as db:
        for incoming in ([one],[one],[two],[two,one]):
            db.commit([],incoming,source_key='TEST',input_kind='NATIVE')
        out=events(db)
        assert len(out)==1 and out[0][5]=='BOTH'
        assert len(json.loads(out[0][6])['occurrences'])==2
        info=db.db.execute('PRAGMA table_info(events)').fetchall()
        assert [r[1] for r in info if r[5]]==['symbol','timeframe','timestamp','event_type']


def test_sparse_zero_events_still_has_independent_coverage():
    rows=bars(40)
    for i,r in enumerate(rows):
        r.update(open=100+i,close=100+i,high=100+i+.001,low=100+i-.001)
    with sql_store() as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test')
        first=build_raw_bb(db,'TEST',('15m',),event_types=('BB_OPEN_4_3_TOUCH',))
        assert events(db)==[] and first['common_bb_calculations']>0
        again=build_raw_bb(db,'TEST',('15m',),event_types=('BB_OPEN_4_3_TOUCH',))
        assert again['common_bb_calculations']==0 and again['raw_rows_loaded']==0
        assert db.state(('TEST','15m','BB_OPEN_4_3_TOUCH'))['calculated_to']==rows[-1]['timestamp']+900*C.NS+1
        assert db.state(('TEST','15m','WONBI')) is None


def test_raw_shared_open_bb_and_two_nonempty_types():
    rows=bars()
    with sql_store() as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test')
        r=build_raw_bb(db,'TEST',('15m',))
        assert r['common_bb_calculations']==len(rows)-3
        a=db.db.execute("SELECT timestamp,side,payload_json FROM events WHERE event_type='WONBI' ORDER BY timestamp").fetchall()
        b=db.db.execute("SELECT timestamp,side,payload_json FROM events WHERE event_type='BB_OPEN_4_3_TOUCH' ORDER BY timestamp").fetchall()
        assert a==[] and len(b)>2
        with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):
            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',))
        assert {'LOWER','UPPER'}<={side for _,_,p in b for side in [o['side'] for o in json.loads(p)['occurrences']]}
        assert all(r[4]==r[2] for r in events(db)) # known-at completed-bar timestamp
        old=events(db)
        assert db.put_ohlcv('TEST','15m',rows,source_key='test')==0
        assert build_raw_bb(db,'TEST',('15m',))['event_evaluations']=={}
        assert events(db)==old


@pytest.mark.parametrize('addition',('APPEND','PREPEND','BOTH','GAP'))
def test_raw_incremental_equals_full_and_bounded_lookback(addition):
    rows=bars(100)
    initial={'APPEND':rows[:60],'PREPEND':rows[40:],'BOTH':rows[30:60],
             'GAP':rows[:30]+rows[60:]}[addition]
    remaining=[r for r in rows if r not in initial]
    with sql_store() as db,sql_store() as full:
        db.put_ohlcv('TEST','15m',initial,source_key='test');build_raw_bb(db,'TEST',('15m',))
        before=events(db)
        db.put_ohlcv('TEST','15m',remaining,source_key='test')
        r=build_raw_bb(db,'TEST',('15m',))
        full.put_ohlcv('TEST','15m',rows,source_key='test');build_raw_bb(full,'TEST',('15m',))
        assert events(db)==events(full)
        assert r['raw_rows_loaded']<=len(remaining)+20
        assert before and r['event_evaluations']
        assert len(db.state(('TEST','15m','BB_OPEN_4_3_TOUCH'))['coverage'])==1


def test_add_new_event_only_and_keep_old_bytes():
    rows=bars()
    with sql_store() as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test')
        build_raw_bb(db,'TEST',('15m',))
        old=events(db);state=copy.deepcopy(db.state(('TEST','15m','BB_OPEN_4_3_TOUCH')))
        with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):
            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',))
        assert events(db)==old and old
        assert db.state(('TEST','15m','BB_OPEN_4_3_TOUCH'))==state
        assert db.state(('TEST','15m','WONBI')) is None
        assert build_raw_bb(db,'TEST',('15m',))['event_evaluations']=={}

def test_new_timeframe_only_and_m1_aggregation_not_repeated():
    rows=bars(120,'1m')
    with sql_store() as db:
        db.put_ohlcv('TEST','1m',rows,source_key='test')
        assert C.aggregate_missing_ohlcv(db,'TEST',('5m',))=={'5m':24}
        build_raw_bb(db,'TEST',('5m',))
        old=events(db)
        assert C.aggregate_missing_ohlcv(db,'TEST',('5m','8m'))=={'5m':0,'8m':15}
        r=build_raw_bb(db,'TEST',('5m','8m'))
        assert all(j['key'][1]=='8m' and j['kind']=='NEW_TIMEFRAME' for j in r['jobs'])
        assert [r for r in events(db) if r[1]=='5m']==old
        assert C.aggregate_missing_ohlcv(db,'TEST',('5m','8m'))=={'5m':0,'8m':0}


def test_raw_boundary_and_sigma_definition_rejection():
    rows=bars()
    with sql_store() as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test');build_raw_bb(db,'TEST',('15m',))
        old=events(db)
        with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):
            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',),config={'WONBI_SIGMA':4})
        assert events(db)==old
        with pytest.raises(C.ReplayError,match='EXPLICIT_REBUILD'):
            db.rebuild([('TEST','15m','BB_OPEN_4_3_TOUCH')])
        bad=copy.deepcopy(rows[:1]);bad[0]['high']+=1
        with pytest.raises(C.ReplayError,match='CORRECTION'):
            db.put_ohlcv('TEST','15m',bad,source_key='test')
        assert events(db)==old
        build_raw_bb(db,'TEST',('15m',),event_types=('BB_OPEN_4_3_TOUCH',),config={'WONBI_SIGMA':4},rebuild=True)
        assert [e for e in events(db) if e[3]=='BB_OPEN_4_3_TOUCH']==[e for e in old if e[3]=='BB_OPEN_4_3_TOUCH']


def test_raw_daily_commit_failure_and_resume(tmp_path):
    rows=bars(80,'1h');day=rows[24]['timestamp']//C.DAY_NS
    with sql_store(tmp_path/'double.sqlite') as db,sql_store() as full:
        db.put_ohlcv('TEST','1h',rows,source_key='test')
        with pytest.raises(RuntimeError,match='COMMIT_FAILURE'):
            build_raw_bb(db,'TEST',('1h',),fail_day=day)
        assert events(db) and db.state(('TEST','1h','BB_OPEN_4_3_TOUCH'))['calculated_to']<rows[-1]['timestamp']
        r=build_raw_bb(db,'TEST',('1h',))
        full.put_ohlcv('TEST','1h',rows,source_key='test');build_raw_bb(full,'TEST',('1h',))
        assert events(db)==events(full) and r['raw_rows_loaded']<len(rows)


def test_raw_pause_is_daily_safe():
    rows=bars(80,'1h')
    with sql_store() as db,sql_store() as full:
        db.put_ohlcv('TEST','1h',rows,source_key='test')
        assert build_raw_bb(db,'TEST',('1h',),pause=lambda:True)['status']=='PAUSED'
        build_raw_bb(db,'TEST',('1h',))
        full.put_ohlcv('TEST','1h',rows,source_key='test');build_raw_bb(full,'TEST',('1h',))
        assert events(db)==events(full)


@pytest.mark.parametrize('et',tuple(C.ALLZONE_TYPES))
@pytest.mark.parametrize('direction',('LONG','SHORT'))
def test_native_positive_all_five_types_equal_current_direct(tmp_path,et,direction):
    if et=='ALLZONE':rows=special3_trace(direction)
    elif et=='BREAKER_REGIME_ALLZONE':rows=special7_trace(direction)
    elif et=='BLIND_ALLZONE':rows=special5_trace(direction)
    else:rows=synthetic_trace() if direction=='SHORT' else synthetic_trace(False)
    profile=C.ALLZONE_TYPES[et];vm,tm=E.PROFILES[profile]
    if et=='BREAKER_BLIND_ALLZONE':
        rows=[dict(r,validation_mode=vm,trigger_mode=tm) if r['kind']=='OZ_POLL' else r for r in rows]
    path=write(tmp_path/'native.jsonl',rows)
    direct=E.EventCollector(header());want=[]
    for r in rows:want.extend(e for e in direct.step(r) if e['allzone_type']==profile and e['timeframe']=='1m')
    assert any(e['direction']==direction for e in want)
    with sql_store() as db:
        result=C.build_native(path,db,[(SYMBOL,'1m',et)])
        got=db.read_events(SYMBOL,rows[0]['timestamp_ns'],rows[-1]['timestamp_ns']+1,timeframes=('1m',),event_types=(et,))
        actual=[(e['timestamp'],o['direction'],o['event_id']) for e in got for o in e['payload']['occurrences']]
        assert actual==[(e['event_time'],e['direction'],e['event_id']) for e in want]
        assert result['event_evaluations'][f'{SYMBOL}|1m|{et}']>0


def band_trace(direction='LONG'):
    rows=[];seq=0
    for i,phase in enumerate((1,0,1,1)):
        t=pd.Timestamp('2026-08-03 01:40:00',tz='UTC').value+i*C.NS
        frame=native_frame('1m',bar='2026-08-03 01:40:00',direction=direction,phase=phase)
        seq+=1;rows.append(publication(frame,SYMBOL,'1m',i+1,timestamp_ns=t,sequence=seq))
        seq+=1;rows.append(dict(kind='PERCENTILE_POLL',timestamp_ns=t+100000000,sequence=seq,symbol=SYMBOL))
    return rows


@pytest.mark.parametrize('direction',('LONG','SHORT'))
def test_four_families_out_and_outin_shared_once(tmp_path,direction):
    rows=band_trace(direction);path=write(tmp_path/'bands.jsonl',rows)
    keys=[(SYMBOL,'1m',e) for e in C.BAND_TYPES]
    with sql_store() as db:
        result=C.build_native(path,db,keys)
        assert len(events(db))==8
        assert result['common_counts']['percentile_state_rows']==4
        for e in events(db):
            p=json.loads(e[6])['occurrences']
            assert len(p)==1 and p[0]['direction']==direction
        assert C.build_native(path,db,keys)['processed_records']==0


def test_native_add_only_new_event_and_timeframe(tmp_path,monkeypatch):
    path=write(tmp_path/'native.jsonl',synthetic_trace(False))
    first=(SYMBOL,'1m','BREAKER_ALLZONE');new=(SYMBOL,'1m','PERCENTILE_PRICE_OUT_IN')
    with sql_store() as db:
        C.build_native(path,db,[first]);before=events(db);state=copy.deepcopy(db.state(first))
        def forbidden(*args,**kwargs):raise AssertionError('existing ALLZONE recalculated')
        monkeypatch.setattr(E.CollectMonitor,'run_once',forbidden)
        result=C.build_native(path,db,[first,new])
        assert result['event_evaluations'][f'{SYMBOL}|1m|BREAKER_ALLZONE']==0
        assert result['jobs'][0]['kind']=='NEW_EVENT'
        assert [e for e in events(db) if e[3]=='BREAKER_ALLZONE']==before
        assert db.state(first)==state
        newer=(SYMBOL,'2m','PERCENTILE_PRICE_OUT')
        result=C.build_native(path,db,[first,new,newer])
        assert result['jobs'][0]['kind']=='NEW_TIMEFRAME'


def shift(rows,days):
    return [dict(r,timestamp_ns=r['timestamp_ns']+days*C.DAY_NS,sequence=r['sequence']+days*10000) for r in rows]


def test_native_daily_rollback_resume_append(tmp_path):
    base=synthetic_trace(False);rows=base+shift(base,1)+shift(base,2)
    path=write(tmp_path/'native.jsonl',rows);keys=[(SYMBOL,'1m','BREAKER_ALLZONE')]
    with sql_store() as db,sql_store() as full:
        with pytest.raises(RuntimeError,match='COMMIT_FAILURE'):
            C.build_native(path,db,keys,fail_day=rows[len(base)]['timestamp_ns']//C.DAY_NS)
        assert events(db)
        resumed=C.build_native(path,db,keys)
        C.build_native(path,full,keys)
        assert events(db)==events(full)
        assert resumed['processed_records']==2*len(base)
        old=events(db)
        more=shift(base,3);write(path,rows+more)
        result=C.build_native(path,db,keys)
        assert result['processed_records']==len(more)
        assert all(e in events(db) for e in old)
        assert C.build_native(path,db,keys)['processed_records']==0


def test_native_pause_exact_candidate_continuity(tmp_path):
    path=write(tmp_path/'native.jsonl',synthetic_trace(False));keys=[(SYMBOL,'1m','BREAKER_ALLZONE')]
    counter=[0]
    def pause():counter[0]+=1;return counter[0]==6
    with sql_store() as db,sql_store() as full:
        r=C.build_native(path,db,keys,pause=pause);assert r['status']=='PAUSED'
        C.build_native(path,db,keys);C.build_native(path,full,keys)
        assert events(db)==events(full) and len(events(db))==1


def test_native_prepend_reconciles_only_boundary(tmp_path):
    first=band_trace();later=shift(first,1)
    from live_replay.trace_io import PIPE_HEADER
    for row in later:
        if row['kind']=='STAFF_PUBLISH':
            raw=base64.b64decode(row['payload_b64']);fields=list(PIPE_HEADER.unpack(raw[:PIPE_HEADER.size]));fields[2]+=10000
            row['payload_b64']=base64.b64encode(PIPE_HEADER.pack(*fields)+raw[PIPE_HEADER.size:]).decode()
    path=write(tmp_path/'native.jsonl',later)
    keys=[(SYMBOL,'1m',et) for et in C.BAND_TYPES]
    with sql_store() as db,sql_store() as full:
        C.build_native(path,db,keys)
        write(path,first+later)
        result=C.build_native(path,db,keys)
        assert all(r['status']=='VERIFIED' for r in result['boundary_results'])
        assert all(r['records']<=len(later) for r in result['boundary_results'])
        C.build_native(path,full,keys)
        assert events(db)==events(full)
        assert all(len(db.state(k)['coverage'])==1 for k in keys)


def test_native_source_changes_fail_without_destroying_events(tmp_path):
    rows=synthetic_trace(False);path=write(tmp_path/'native.jsonl',rows);keys=[(SYMBOL,'1m','BREAKER_ALLZONE')]
    with sql_store() as db:
        C.build_native(path,db,keys);before=events(db)
        rows[-1]=dict(rows[-1],kind='DIFFERENT');write(path,rows)
        with pytest.raises(C.ReplayError,match='RECORDS_CHANGED'):C.build_native(path,db,keys)
        assert events(db)==before


def test_not_observed_is_not_a_valid_empty_cache(tmp_path):
    rows=synthetic_trace(False);path=write(tmp_path/'native.jsonl',rows)
    with sql_store() as db:
        result=C.build_native(path,db,[(SYMBOL,'1m','BLIND_ALLZONE')])
        assert result['status']=='PARTIAL'
        with pytest.raises(C.ReplayError,match='COVERAGE'):
            db.read_events(SYMBOL,rows[0]['timestamp_ns'],rows[-1]['timestamp_ns']+1,
                           timeframes=['1m'],event_types=['BLIND_ALLZONE'])


def test_raw_does_not_fake_native_allzone_and_unknown_map():
    with sql_store() as db:
        with pytest.raises(C.ReplayError,match='NATIVE_OBSERVATIONS'):
            build_raw_bb(db,'TEST',event_types=['ALLZONE'])
        with pytest.raises(C.ReplayError,match='NO_PART1_ALLZONE_MAPPING'):
            db.plan([('TEST','8m','ALLZONE')],0,100,source_key='t')


def test_real_duckdb_roundtrip_when_installed(tmp_path):
    pytest.importorskip('duckdb',reason='ENVIRONMENT_LIMITATION: actual DuckDB unavailable')
    path=tmp_path/'backtest.duckdb';rows=bars()
    with C.EventStore(path) as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test');build_raw_bb(db,'TEST',('15m',))
        before=events(db);assert before
    with C.EventStore(path) as db:
        assert build_raw_bb(db,'TEST',('15m',))['common_bb_calculations']==0
        assert events(db)==before


def test_append_index_parses_only_new_records(tmp_path):
    first=band_trace();path=write(tmp_path/'trace.jsonl',first)
    with sql_store() as db:
        indexed=C.IndexedTrace(path,db);assert indexed.index_records==len(first)
        assert C.IndexedTrace(path,db).index_records==0
        with path.open('a') as f:
            for row in shift(first,1):f.write(json.dumps(row)+'\n')
        appended=C.IndexedTrace(path,db)
        assert appended.index_records==len(first) and appended.files[0]['index']['count']==2*len(first)
        assert list(appended.iter_range(first[0]['timestamp_ns']+C.DAY_NS,appended.end))==shift(first,1)


def test_new_file_with_interior_observation_is_not_silently_covered(tmp_path):
    first=band_trace();write(tmp_path/'one.jsonl',first)
    key=(SYMBOL,'1m','PERCENTILE_PRICE_OUT')
    with sql_store() as db:
        C.build_native(tmp_path,db,[key]);before=events(db)
        row=dict(kind='CLOCK',timestamp_ns=first[1]['timestamp_ns']+1,sequence=1000)
        write(tmp_path/'new.jsonl',[row])
        with pytest.raises(C.ReplayError,match='INSIDE_COMPLETED_RANGE'):
            C.build_native(tmp_path,db,[key])
        assert events(db)==before


def test_native_unverified_prepend_is_not_reported_up_to_date(tmp_path):
    first=band_trace();later=shift(first,1)
    path=write(tmp_path/'native.jsonl',later);keys=[(SYMBOL,'1m',e) for e in C.BAND_TYPES]
    with sql_store() as db:
        C.build_native(path,db,keys);old=events(db)
        write(path,first+later)
        result=C.build_native(path,db,keys)
        # Deliberately reused snapshot IDs: source cache is NOT the same after
        # prefixing history. Candidate-state-only equality would wrongly pass.
        assert result['status']=='BOUNDARY_UNVERIFIED'
        assert all(e in events(db) for e in old)
        repeat=C.build_native(path,db,keys)
        assert repeat['status']=='BOUNDARY_UNVERIFIED' and repeat['processed_records']==0
        assert repeat['boundary_records']<=len(later)


@pytest.mark.parametrize('tf',C.GROUP_B)
@pytest.mark.parametrize('direction',('LONG','SHORT'))
def test_native_wonbi_bb_shared_value_positive(tmp_path,monkeypatch,tf,direction):
    rows=[];seq=0
    for index,on in enumerate((False,True,True)):
        frame=native_frame(tf,bar='2026-08-03 01:30:00',direction=direction,phase=1)
        # S7: consume the synthetic EA input bands directly; no Python production Wonbi.
        reference=frame
        lower=float(reference.iloc[-1]['wonbi_lower']);upper=float(reference.iloc[-1]['wonbi_upper'])
        frame.loc[frame.index[-1],'low']=lower-1 if on and direction=='LONG' else lower+.01
        frame.loc[frame.index[-1],'high']=upper+1 if on and direction=='SHORT' else upper-.01
        stamp=int(pd.Timestamp('2026-08-03 01:40:00',tz='UTC').value)+index*C.NS
        seq+=1;rows.append(publication(frame,SYMBOL,tf,index+1,timestamp_ns=stamp,sequence=seq))
        seq+=1;rows.append(dict(kind='WONBI_POLL',timestamp_ns=stamp+100000000,sequence=seq,symbol=SYMBOL))
    path=write(tmp_path/'wonbi.jsonl',rows)
    monkeypatch.setattr(C,'bb_open4',lambda *a,**k:(_ for _ in ()).throw(AssertionError('native bands recalculated')))
    with sql_store() as db:
        result=C.build_native(path,db,[(SYMBOL,tf,e) for e in C.B_TYPES])
        assert result['common_counts']['bb_rows']==3
        got=events(db);assert len(got)==2
        assert {e[3] for e in got}==set(C.B_TYPES)
        assert all(json.loads(e[6])['occurrences'][0]['direction']==direction for e in got)
        assert got[0][2]==got[1][2]==rows[3]['timestamp_ns']


def test_native_old_tail_and_new_type_share_input_without_old_replay(tmp_path):
    first=band_trace();path=write(tmp_path/'trace.jsonl',first)
    a=(SYMBOL,'1m','PERCENTILE_PRICE_OUT');b=(SYMBOL,'1m','PERCENTILE_PRICE_OUT_IN')
    with sql_store() as db,sql_store() as full:
        C.build_native(path,db,[a]);old=events(db)
        later=shift(first,1)
        from live_replay.trace_io import PIPE_HEADER
        for row in later:
            if row['kind']=='STAFF_PUBLISH':
                raw=base64.b64decode(row['payload_b64']);fields=list(PIPE_HEADER.unpack(raw[:PIPE_HEADER.size]));fields[2]+=10000
                row['payload_b64']=base64.b64encode(PIPE_HEADER.pack(*fields)+raw[PIPE_HEADER.size:]).decode()
        write(path,first+later)
        r=C.build_native(path,db,[a,b]);C.build_native(path,full,[a,b])
        assert r['processed_records']==len(first)+len(later)
        assert r['event_evaluations']['|'.join(a)]==4
        assert r['event_evaluations']['|'.join(b)]==8
        assert events(db)==events(full) and all(e in events(db) for e in old)


def test_catalog_reader_on_off_and_input_integrity(tmp_path,monkeypatch):
    from live_replay.synthetic_specials import special4_trace
    from live_replay.__main__ import run_file
    from live_replay.runtime import Monitor
    rows=special4_trace('LONG');head=header();head['specials']=[4]
    path=tmp_path/'special4.jsonl';write_trace(path,head,rows)
    database=tmp_path/'SQL_TEST_DOUBLE.sqlite'
    with sql_store(database) as db:
        C.build_native(path,db,C.replay_requirements([4],[SYMBOL]))
    off=run_file(path,duckdb='OFF');assert len(off['alerts'])==1
    original=E.SparseStore
    monkeypatch.setattr(E,'SparseStore',lambda p,**k:original('SQL_TEST_DOUBLE',_connection=sqlite3.connect(str(p),isolation_level=None)))
    def forbidden(*a,**k):raise AssertionError('ON created ALLZONE Monitor')
    monkeypatch.setattr(Monitor,'__init__',forbidden)
    on=run_file(path,duckdb='ON',duckdb_path=database)
    assert on['alerts']==off['alerts'] and on['duckdb_cache']['event_queries']==1
    altered=copy.deepcopy(rows);altered[-1]['sequence']+=1
    write_trace(path,head,altered)
    with pytest.raises(C.ReplayError,match='SOURCE_CHANGED|INPUT_CHANGED'):
        run_file(path,duckdb='ON',duckdb_path=database)


def test_cache_coverage_hole_and_incomplete_default_group_rejected(tmp_path):
    rows=synthetic_trace(False);path=write(tmp_path/'input.jsonl',rows)
    with sql_store() as db:
        C.build_native(path,db,[(SYMBOL,'1m','BREAKER_ALLZONE')])
        with pytest.raises(C.ReplayError,match='COVERAGE'):
            C.read_replay_events(db,header(),rows[0]['timestamp_ns'],rows[-1]['timestamp_ns']+1,symbols=[SYMBOL],specials=[1])
        assert db.event_queries==0
    assert C.missing_ranges(0,100,[{'from':0,'to':30},{'from':60,'to':100}])==[(30,60)]


def test_missing_raw_does_not_report_ready():
    with sql_store() as db:
        assert build_raw_bb(db,'TEST')['status']=='PARTIAL'


def test_no_native_observations_remain_partial_on_repeat(tmp_path):
    path=write(tmp_path/'input.jsonl',synthetic_trace(False));key=(SYMBOL,'1m','BLIND_ALLZONE')
    with sql_store() as db:
        assert C.build_native(path,db,[key])['status']=='PARTIAL'
        repeat=C.build_native(path,db,[key]);assert repeat['status']=='PARTIAL'
        assert repeat['processed_records']==0


def test_existing_manager_m1_import_and_selected_tf_only():
    rows=bars(120,'1m')
    with sql_store() as db:
        db.db.execute('CREATE TABLE sources(source_id TEXT PRIMARY KEY,symbol TEXT)')
        db.db.execute('CREATE TABLE raw_m1(source_id TEXT,timestamp BIGINT,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,tick_volume BIGINT)')
        db.db.execute("INSERT INTO sources VALUES ('broker1','TEST')")
        db.db.executemany('INSERT INTO raw_m1 VALUES (?,?,?,?,?,?,?)',
            [('broker1',r['timestamp']//C.NS,r['open'],r['high'],r['low'],r['close'],r['volume']) for r in rows])
        assert C.import_manager_m1(db,'TEST')['rows']==120
        assert C.import_manager_m1(db,'TEST')['rows']==0
        assert C.aggregate_missing_ohlcv(db,'TEST',('15m',))=={'15m':8}
        build_raw_bb(db,'TEST',('15m',))
        old=events(db);state=copy.deepcopy(db.state(('TEST','15m','BB_OPEN_4_3_TOUCH')))
        assert C.aggregate_missing_ohlcv(db,'TEST',('15m','30m'))=={'15m':0,'30m':4}
        r=build_raw_bb(db,'TEST',('15m','30m'))
        assert all(j['key'][1]=='30m' for j in r['jobs'])
        assert [e for e in events(db) if e[1]=='15m']==old
        assert db.state(('TEST','15m','BB_OPEN_4_3_TOUCH'))==state


@pytest.mark.parametrize('n',range(1,8))
def test_generic_catalog_specials_nonempty_off_on_batch(tmp_path,n):
    from live_replay import synthetic_specials as SS
    from live_replay.runtime import Replay
    base=synthetic_trace(False) if n==1 else getattr(SS,f'special{n}_trace')('LONG')
    required=C.replay_requirements([n],[SYMBOL]);profiles={E.PROFILES[C.ALLZONE_TYPES[k[2]]] for k in required}
    rows=[]
    # A full-cache capture explicitly polls all required profiles. A profile
    # absent from a trace must never be claimed to have a valid empty history.
    for old in base:
        rows.append(dict(old,sequence=len(rows)+1))
        if old['kind']=='OZ_POLL':
            observed=(old.get('validation_mode','NORMAL'),old.get('trigger_mode',C.special1.FINAL_TRIGGER_MODE))
            for vm,tm in sorted(profiles-{observed}):
                rows.append(dict(old,sequence=len(rows)+1,validation_mode=vm,trigger_mode=tm))
    path=tmp_path/f'special{n}.jsonl';head=header();head['specials']=[n];write_trace(path,head,rows)
    with sql_store() as db:
        built=C.build_native(path,db,required);assert built['status']=='READY'
        loaded=C.read_replay_events(db,head,rows[0]['timestamp_ns'],rows[-1]['timestamp_ns']+1,symbols=[SYMBOL],specials=[n])
        off=Replay(specials=(n,),synthetic=True,health_session=head['health_session'])
        on=Replay(specials=(n,),synthetic=True,health_session=head['health_session'],allzone_events=loaded)
        for row in rows:
            off.step(row);on.step(row)
            assert on.delivered==off.delivered
            assert on.alerts==off.alerts
        assert off.delivered and any(a['direction']=='LONG' for a in off.delivered)
        assert db.event_queries==1 and on.event_playback.stats()['allzone_calculation_calls']==0
