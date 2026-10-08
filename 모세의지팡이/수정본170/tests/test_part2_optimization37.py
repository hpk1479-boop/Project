"""Revision 37 regression contracts: no changes to strategy predicates."""
from pathlib import Path
from types import SimpleNamespace
import ast,csv,datetime as dt,hashlib,importlib.util,json,os,sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2'),str(ROOT/'build/optimization37')]
from support import load_warehouse
from step3_check import loop_code
from event_engine.model import Event,Kind
from event_engine import domain_support
from event_backtest.settings import milliseconds
from event_backtest.cancellation import requested,Cancelled

@pytest.fixture
def warehouse_module():
    return load_warehouse(ROOT,'event_backtest.optimization37_warehouse')

@pytest.mark.parametrize('typ',[None,'DOMAIN_FACT','NOTIFICATION','EXTERNAL_REQUEST','DEGRADED'])
@pytest.mark.parametrize('status',[None,'OK','DEGRADED'])
def test_writer_predicate_and_external_precedence(tmp_path,warehouse_module,typ,status):
    content={'message':'notice','text':'unresolved','recipients':['A','B']}
    if typ is not None:content['type']=typ
    if status is not None:content['status']=status
    w=warehouse_module.ResultWriter(tmp_path/'alerts.csv',{'run_id':'test'},100,200)
    w.context['id']={'b0_price':12.5,'b0_time':90}
    event=Event(1,0,'test',1,100,Kind.SIGNAL,{'strategy':'S','symbol':'XAUUSD+','signal_id':'id','content':content})
    w.accept(event);w.close()
    rows=list(csv.DictReader(w.path.open(encoding='utf-8',newline='')))
    if typ=='EXTERNAL_REQUEST':
        assert not rows and w.external_error=='결정적으로 해석되지 않는 명령: unresolved'
        assert 'id' in w.context
    elif typ=='NOTIFICATION' or status=='DEGRADED':
        assert len(rows)==2 and not w.context
        assert [r['recipient'] for r in rows]==['A','B']
        assert all(r['b0_price']=='12.5' and r['b0_time']=='90' for r in rows)
    else:assert not rows and 'id' in w.context

@pytest.mark.parametrize('stamp,expected_rows',[(99,0),(100,1),(199,1),(200,0)])
def test_writer_warmup_context_and_half_open_range(tmp_path,warehouse_module,stamp,expected_rows):
    w=warehouse_module.ResultWriter(tmp_path/'alerts.csv',{'run_id':'test'},100,200);w.context['id']={'b0_price':10}
    w.accept(Event(1,0,'test',1,stamp,Kind.SIGNAL,{'strategy':'S','symbol':'XAUUSD+','signal_id':'id','content':{'type':'NOTIFICATION','message':'x'}}))
    w.close();assert not w.context
    assert len(list(csv.DictReader(w.path.open(encoding='utf-8',newline=''))))==expected_rows

def test_discarded_fact_never_calls_plain(tmp_path,warehouse_module,monkeypatch):
    w=warehouse_module.ResultWriter(tmp_path/'alerts.csv',{'run_id':'test'},0,2)
    def forbidden(value):raise AssertionError('plain called for a discarded fact')
    monkeypatch.setattr(domain_support,'plain',forbidden)
    w.accept(Event(1,0,'test',1,1,Kind.SIGNAL,{'content':{'type':'DOMAIN_FACT','event':{'nested':[1,2,3]}}}));w.close()

@pytest.fixture
def catalog_fixture(tmp_path,warehouse_module):
    root=tmp_path/'warehouse';p=root/'capture';p.mkdir(parents=True)
    (p/'complete.txt').write_text('complete');target=p/'data';target.write_bytes(b'original')
    metadata={'path':'capture','files':{'data':hashlib.sha256(b'original').hexdigest()}}
    class DB:
        def execute(self,sql,parameters):return SimpleNamespace(fetchone=lambda:('capture',json.dumps(metadata)))
    def catalog(where=root):
        w=warehouse_module.Warehouse.__new__(warehouse_module.Warehouse);w.root=where;w.db=DB();return w
    return catalog,target,metadata

def test_find_capture_rehashes_across_catalog_instances(warehouse_module,catalog_fixture,monkeypatch):
    catalog,target,metadata=catalog_fixture;count=[];original=warehouse_module.file_hash
    def hashed(p):count.append(p);return original(p)
    monkeypatch.setattr(warehouse_module,'file_hash',hashed)
    assert catalog().find_capture('a')==metadata==catalog().find_capture('a')
    assert len(count)==2
    metadata['files']['data']='0'*64
    assert catalog().find_capture('a') is None and len(count)==3

def test_find_capture_rejects_same_size_same_mtime_replacement(warehouse_module,catalog_fixture):
    catalog,target,metadata=catalog_fixture
    assert catalog().find_capture('a')==metadata
    before=target.stat()
    target.write_bytes(b'CHANGED!')
    os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert target.stat().st_size==before.st_size
    assert target.stat().st_mtime_ns==before.st_mtime_ns
    assert catalog().find_capture('a') is None
    assert catalog().find_capture('a') is None

@pytest.mark.parametrize('mutation',['size','mtime','delete','complete'])
def test_find_capture_rechecks_changed_file(warehouse_module,catalog_fixture,mutation):
    catalog,target,metadata=catalog_fixture;assert catalog().find_capture('a')==metadata
    before=target.stat()
    if mutation=='size':
        target.write_bytes(b'much longer content');os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns))
    elif mutation=='mtime':
        target.write_bytes(b'changed!');os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000))
    elif mutation=='delete':target.unlink()
    else:(target.parent/'complete.txt').unlink()
    assert catalog().find_capture('a') is None

def test_find_capture_change_during_hash_not_cached(warehouse_module,catalog_fixture,monkeypatch):
    catalog,target,metadata=catalog_fixture;old=warehouse_module.file_hash
    def racing(path):
        result=old(path);target.write_bytes(b'changed and larger');return result
    monkeypatch.setattr(warehouse_module,'file_hash',racing)
    assert catalog().find_capture('a') is None
    monkeypatch.setattr(warehouse_module,'file_hash',old)
    metadata['files']['data']=hashlib.sha256(target.read_bytes()).hexdigest()
    assert catalog().find_capture('a')==metadata


def execute_loop(tmp_path,stamps,walls,stop_at=None,kinds=None):
    clock=SimpleNamespace(value=0.);checks=[];cpu=[];posts=[]
    def check_factory(path):
        def check():
            checks.append(clock.value)
            if stop_at is not None and clock.value>=stop_at:raise Cancelled()
        return check
    def stream(*args):
        for index,(stamp,wall) in enumerate(zip(stamps,walls)):
            clock.value=wall
            yield SimpleNamespace(kind=kinds[index] if kinds else Kind.MARKET_BUNDLE,source='test',source_seq=index+1,source_time=stamp,payload={})
    def sample():cpu.append(clock.value);return 0
    from itertools import islice
    from event_engine.model import Resolution
    # 수정본161: the loop drives lanes (one here), each with its own engine, writer and counters.
    lane=SimpleNamespace(engine=SimpleNamespace(ingress=SimpleNamespace(post=lambda *a,**k:posts.append(k['source_time'])),run=lambda:None,
                                                error_log=[],board=SimpleNamespace(timeframe_misses=set())),
                         writer=SimpleNamespace(external_error=None),subs=None,previous={},out=tmp_path,
                         count=0,total_ns=0,hist={},first_bundle_seconds=None,warmup_bundles=0,
                         processed_start=None,processed_end=None,month_counts={},join=None)
    namespace={'Path':Path,'dt':dt,'time':SimpleNamespace(perf_counter=lambda:clock.value,process_time=lambda:clock.value,perf_counter_ns=lambda:int(clock.value*1e9)),
      'file_check':check_factory,'requested':requested,'milliseconds':milliseconds,'task':{'out':str(tmp_path/'worker'),'start':'2026-01-01'},'task_started':0.,'out':tmp_path,
      'outs':[tmp_path/'worker'],'states':[lane],'islice':islice,'Resolution':Resolution,'_select_batch':None,
      'inputs':None,'source':stream(),'timeframes':None,'cache':SimpleNamespace(shadow_fallbacks=[]),'misses':lambda:[],
      'resolution':Resolution.TICK,
      'Kind':Kind,'current_processor':sample,'write_progress':lambda *a:None,'peak_memory':lambda:0,'os':os,
      'start_ms':0,'end_ms':2**63-1,'own_end_ms':2**63-1,'join':None,'_STOP_CHECK_SECONDS':.5,'_CPU_SAMPLE_SECONDS':.5}
    exec(loop_code(ROOT),namespace)
    namespace['count']=lane.count
    return namespace,checks,cpu,posts

@pytest.mark.parametrize('values,counts',[
    (['2026-01-31T23:59:59.999','2026-02-01'],{'2026-01':1,'2026-02':1}),
    (['2024-02-29T23:59:59.999','2024-03-01'],{'2024-02':1,'2024-03':1}),
    (['2026-12-31T23:59:59.999','2027-01-01'],{'2026-12':1,'2027-01':1}),
    (['2026-01-01','2026-04-01','2026-02-01'],{'2026-01':1,'2026-04':1,'2026-02':1}),
    ([],{}),
])
def test_month_boundaries(tmp_path,values,counts):
    stamps=[milliseconds(v) for v in values]
    ns,_,_,posts=execute_loop(tmp_path,stamps,[i*.1 for i in range(len(stamps))])
    assert {k:v['bundles'][0] for k,v in ns['month_metrics'].items()}==counts
    assert posts==stamps

def test_worker_stop_and_cpu_sample_intervals(tmp_path):
    walls=[0.,.1,.49,.5,.51,.99,1.,1.49,1.5];stamps=[milliseconds('2026-01-01')+i*60000 for i in range(len(walls))]
    ns,checks,cpu,posts=execute_loop(tmp_path,stamps,walls)
    assert checks==cpu==[0.,.5,1.,1.5] and posts==stamps
    ns,checks,_,posts=execute_loop(tmp_path,stamps,walls,stop_at=.51)
    assert ns['interrupted'] and checks==[0.,.5,1.] and len(posts)==6
    ns,checks,_,posts=execute_loop(tmp_path,stamps,walls,stop_at=0.)
    assert ns['interrupted'] and not posts

def test_cpu_samples_only_market_bundles(tmp_path):
    stamps=[milliseconds('2026-01-01')+i*60000 for i in range(4)]
    ns,_,cpu,_=execute_loop(tmp_path,stamps,[0.,.1,.5,.6],kinds=[Kind.TIMER,Kind.MARKET_BUNDLE,Kind.TIMER,Kind.MARKET_BUNDLE])
    assert cpu==[.1,.6] and ns['count']==2

@pytest.mark.parametrize('bad',[np.nan,np.inf,-np.inf,np.finfo(float).max])
def test_staff_precomputed_indices_keep_validity_and_full_row_rules(tmp_path,bad):
    from event_host import load_staff
    staff=load_staff();n=10;columns=staff.PIPE_VALUE_COLUMNS
    expected={ind:tuple(columns.index(c) if c in columns else None for c in names) for ind,names in staff.MT5_REQUIRED_BY_INDICATOR.items()}
    assert staff._MT5_REQUIRED_COLUMN_INDICES==expected
    values=np.full((n,len(columns)),100.);times=np.arange(n,dtype='<i8')*60+1788184800;volume=np.ones(n,dtype='<i8')
    cache=staff.StaffPipeCache('',health_session='TEST37',monotonic=lambda:0.,gap_journal=tmp_path/'gaps')
    cache._publish_arrays('XAUUSD+','1m',1,times,volume,values)
    first,_=cache.snapshot_with_age('XAUUSD+','1m')
    values[-1,columns.index('ema_20')]=bad
    with pytest.raises(staff.wire.WireError,match='ROW validity transition requires FULL'):
        cache._publish_arrays('XAUUSD+','1m',2,times,volume,values,advance_epoch=False)
    cache._publish_arrays('XAUUSD+','1m',2,times,volume,values)
    second,_=cache.snapshot_with_age('XAUUSD+','1m')
    assert second.indicator_validity['EMA'] is False and first.indicator_validity['EMA'] is True
    assert second.source_epoch!=first.source_epoch
    assert first.values[-1,columns.index('ema_20')]==100.
    with pytest.raises(ValueError):second.values.setflags(write=True)
