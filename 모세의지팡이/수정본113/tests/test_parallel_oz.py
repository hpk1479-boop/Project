"""Revision26 dependency, determinism, seek and incremental-calculation contracts."""
from pathlib import Path
import sys,socket
from types import SimpleNamespace
import pytest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)
    monkeypatch.setattr(socket,'create_connection',deny)

@pytest.mark.parametrize('special',[f'SPECIAL{i}' for i in range(1,8)])
def test_special_registered_dependencies_and_local_poll(special,monkeypatch):
    from event_application import create_event_engine
    from event_engine import Kind
    from test_engine_optimization import snapshot
    engine=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
        'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'OFFLINE'},symbols=('XAUUSD+',),selection=[special],backtest=True)
    def market(seq):
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=seq,source_time=1756684800000+seq*60000,
            payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot(seq)}})
        engine.run();assert not engine.error_log,engine.error_log
    market(1)
    kernel=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+'];m=kernel.manager
    family={'TREND':'INDICATOR','TREND_METRIC':'INDICATOR','WONBI':'WONBI','FVG':'FVG',
            'SWEEP':'SWEEP','PERCENTILE':'PERCENTILE','MA_STATE':'MA','MA_PRICE_STATE':'MA','MA_SLOPE_STATE':'MA'}
    for spec in m.official_specs.values():
        for condition in spec.conditions:assert family[condition.kind] in engine.selection.capabilities
    if m.official_chain_specs:assert {'CHAINS','WATCH','FVG'}<=engine.selection.capabilities
    handlers=[h for h in m._special_watch_handlers.values() if callable(getattr(h,'poll',None))]
    if handlers:
        assert 'CHAINS' in engine.selection.capabilities
        calls=[]
        for h in handlers:monkeypatch.setattr(h,'poll',lambda targets:calls.append(targets))
        market(2);assert len(calls)==len(handlers)
    assert bool(handlers)==(special in ('SPECIAL4','SPECIAL5'))

def test_unordered_condition_scans_are_sorted():
    # These sets allocate touch sequence IDs; iteration must have total order.
    import inspect
    from event_composer_domain import ComposerManager
    for method in ('_update_wonbi','_update_percentile'):
        source=inspect.getsource(getattr(ComposerManager,method))
        assert 'for tf in sorted(tf_set, key=tf_seconds)' in source
    source=inspect.getsource(ComposerManager._update_ma_state)
    for name in ('symbols','pair_reqs','price_reqs','slope_reqs'):assert f'in sorted({name})' in source


@pytest.mark.parametrize('number',[4,5])
def test_chain_dependency_runs_cycle_and_maintenance_from_market_boundary(number,monkeypatch):
    from event_application import create_event_engine
    from event_engine import Kind,FeedSnapshot
    from test_engine_optimization import snapshot
    engine=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
        'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'OFFLINE'},symbols=('XAUUSD+',),selection=[f'SPECIAL{number}'],backtest=True)
    base=snapshot()
    def publish(seq):
        item=FeedSnapshot(base.time+(seq-1)*1800,base.volume,base.values,seq,base.source_epoch,base.indicator_validity)
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=seq,source_time=int(item.time[-1])*1000,
                            payload={'symbol':'XAUUSD+','feeds':{'30m':item}})
        engine.run();assert not engine.error_log,engine.error_log
        return int(item.time[-1])
    publish(1)
    m=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager
    runtime=next(x for x in m._special_watch_handlers.values() if callable(getattr(x,'poll',None)))
    calls=[]
    if number==4:
        monkeypatch.setattr(runtime,'_setup_from_boundary',calls.append)
        boundary=publish(2);assert calls==[boundary]
    else:
        monkeypatch.setattr(runtime,'ensure_high_watches',lambda **kw:calls.append('parents'))
        monkeypatch.setattr(runtime,'_maintain_final_children',lambda:calls.append('children'))
        publish(2);assert calls==['parents','children']

def keyframe_fixture(tmp_path):
    import staff_schema as wire
    from event_host import load_staff
    from event_backtest.keyframes import write_indexed,DAY_MS
    from test_engine_optimization import snapshot
    snap=snapshot();rows=[];times=snap.time
    for i in range(9):
        stamp=1756684800000+(i//3)*DAY_MS+(i%3)*1000
        children=[]
        for tf in ('1m','5m'):
            values=snap.values.copy();values[-1,3]+=i
            if i==0:values[-1,wire.PIPE_VALUE_COLUMNS.index('RSI_val')]=np.nan
            full=i in (0,1,3,6);heartbeat=i in (2,5)
            children.append(wire.pack_v2('XAUUSD+',tf,times if full else times[-1:],snap.volume if full else snap.volume[-1:],
                values if full else values[-1:],seq=i+7,
                kind=wire.WIRE_FULL if full else wire.WIRE_HEARTBEAT if heartbeat else wire.WIRE_ROW))
        rows.append((stamp,wire.pack_bundle('XAUUSD+',children,seq=i+1,sent_at_ms=stamp)))
    root=tmp_path/'capture';root.mkdir();clock=[0.]
    staff=load_staff();cache=staff.StaffPipeCache('',health_session='BACKTEST',monotonic=lambda:clock[0],gap_journal=tmp_path/'build_gaps.jsonl')
    index=write_indexed(rows,root/'capture.delta2',staff_cache=cache,receive_clock=clock)
    return rows,index,root

def test_keyframe_full_and_suffix_bytes_and_unknown_format(tmp_path):
    from event_backtest.keyframes import read_indexed,verify_indexed,read_index
    rows,index,root=keyframe_fixture(tmp_path);path=root/'capture.delta2'
    assert list(read_indexed(path))==rows
    assert verify_indexed(path,index)['bundle_sha256']==index['bundle_sha256']
    assert len(index['days'])==3
    for offset in (0,3,6):
        seen=[]
        assert list(read_indexed(path,start_ms=rows[offset][0],bootstrap=lambda *a:seen.append(a)))==rows[offset:]
        assert len(seen)==int(offset>0)
    with path.open('r+b') as f:f.write(b'BAD!')
    with pytest.raises(ValueError,match='magic'):read_index(path)

@pytest.mark.parametrize('transport',['live','replay'])
def test_keyframe_bootstrap_uses_staff_and_preserves_epoch_sequence(tmp_path,transport):
    from event_backtest.bridge import CaptureInputs
    from event_host import load_staff
    rows,index,root=keyframe_fixture(tmp_path)
    staff=load_staff()
    results=[]
    for mode in ('beginning','keyframe'):
        clock=[0.];cache=staff.StaffPipeCache('',health_session='TEST',monotonic=lambda:clock[0],gap_journal=tmp_path/(mode+'.jsonl'))
        source=CaptureInputs(staff,cache,[root],transport=transport,clock=clock,start_ms=rows[3][0],capture_start=mode)
        items=list(source);results.append(items)
        if mode=='keyframe':assert source.seek_info['prefix_bundles_skipped']==3
        assert not cache.wire_diagnostics()['gaps']
    for a,b in zip(*results):
        assert (a.kind,a.source_seq,a.source_time)==(b.kind,b.source_seq,b.source_time)
        assert a.payload['symbol']==b.payload['symbol']
        for tf,sa in a.payload['feeds'].items():
            sb=b.payload['feeds'][tf]
            assert sa.source_epoch==sb.source_epoch and sa.seq==sb.seq
            assert sa.indicator_validity==sb.indicator_validity
            for key in ('time','volume','values'):assert getattr(sa,key).tobytes()==getattr(sb,key).tobytes()
    assert len(results[0])==len(results[1])==6

@pytest.mark.parametrize('alpha,period',[(1./(1.+((1.-1./14)/(1./14))),14),(2/18,17),(1.,1)])
def test_incremental_ewm_is_bit_identical_with_forming_close_correction_and_reconnect(alpha,period):
    from event_engine.recurrence import EWMPrefix
    from indicator_facts_numpy import _ewm_array
    cache=EWMPrefix();times=np.arange(650,dtype='<i8')*60
    values=np.sin(np.arange(650)/13)*13;values[[0,17,18,31]]=np.nan;values[9]=-0.
    def check(t,v,epoch):
        got=cache.evaluate(t,v,epoch,alpha,period)
        assert got.tobytes()==_ewm_array(v,alpha,period).tobytes()
        return got.copy()
    check(times,values,'1');assert cache.steps==650
    for i in range(7):
        values[-1]=i/11.;check(times,values,'1')
    assert cache.steps==657 and cache.full_rebuilds==1
    # Closing the forming row then extending history retains its true final value.
    values=np.r_[values[:-1],3.5,9.];times=np.r_[times,times[-1]+60]
    check(times,values,'1');assert cache.steps==659
    for t,v,epoch in ((times[1:],values[1:],'1'),(times,values,'2')):
        check(t,v,epoch)
    before=cache.full_rebuilds;values[300]+=1.;check(times,values,'2');assert cache.full_rebuilds==before+1
    values[9]=0.;check(times,values,'2')

def test_general_atr_cache_matches_canonical_full_calculation():
    from event_engine.facts import EventFacts
    from event_engine import FeedSnapshot
    from indicator_facts import true_range_array,rma_array
    from test_engine_optimization import snapshot
    facts=EventFacts();s=snapshot()
    for seq in range(12):
        values=s.values.copy();values[-1,1]+=seq;values[-1,2]-=seq/3
        if seq>=8:values[10,1]+=3  # closed-bar correction
        item=FeedSnapshot(s.time,s.volume,values,seq,'epoch:2' if seq>=10 else 'epoch:1',s.indicator_validity)
        facts.invalidate({('XAUUSD+','1m'):item})
        got=facts.get('XAUUSD+','1m',item,'ATR14_GENERAL','probe')
        expected=rma_array(true_range_array(values[:,1],values[:,2],values[:,3]),14)
        assert got.tobytes()==expected.tobytes()
    cache=facts._recurrences['XAUUSD+','1m','ATR14_GENERAL']
    assert cache.full_rebuilds==3 and cache.steps<12*len(s.time)

def test_unchanged_market_values_still_reset_recursive_state_on_new_epoch():
    from event_engine.facts import EventFacts
    from event_engine import FeedSnapshot
    from test_engine_optimization import snapshot
    facts=EventFacts();base=snapshot();results=[]
    for epoch in ('before','after'):
        item=FeedSnapshot(base.time,base.volume,base.values,1,epoch,base.indicator_validity)
        facts.invalidate({('XAUUSD+','1m'):item})
        results.append(facts.get('XAUUSD+','1m',item,'ATR14_GENERAL','probe'))
    assert results[0].tobytes()==results[1].tobytes()
    cache=facts._recurrences['XAUUSD+','1m','ATR14_GENERAL']
    assert cache.full_rebuilds==2 and cache.key[0]=='after'

def test_oz_selection_static_parent_child_and_confirmation_superset():
    from event_application import load_strategy_inputs
    from event_selection import resolve
    from event_oz_selection import declare
    from oz_engine.common import TF_MAP
    _,_,plugins=load_strategy_inputs({})
    for i in range(1,8):
        name=f'SPECIAL{i}';plan=declare(resolve([name],{}),plugins)
        assert plan.triples
        for tf,vm,tm in plan.triples:
            assert tf in plan.feeds
            if vm=='NORMAL':assert set(TF_MAP[tf])<=plan.feeds
    p5=plugins['SPECIAL5'];five=declare(resolve(['SPECIAL5'],{}),plugins)
    for tf in p5.SOURCE_TFS:
        for trigger in (p5.SOURCE_TRIGGER_MODE,):
            five.require(tf,p5.SOURCE_VALIDATION_MODE,trigger)
    for tf in p5.FINAL_TFS:five.require(tf,p5.FINAL_VALIDATION_MODE,p5.FINAL_TRIGGER_MODE)
    with pytest.raises(ValueError,match='undeclared OZ'):five.require('1h','BLIND','SUPER')
    assert declare(resolve(['ALL'],{}),plugins) is None
    assert declare(resolve(['OZ'],{}),plugins) is None

def test_work_partition_has_no_holes_or_output_overlap():
    from event_backtest.settings import work_periods,scenario
    for size in ('MONTH','FORTNIGHT'):
        jobs=list(work_periods('2024-10-01','2025-10-01',size))
        assert jobs[0][0]=='2024-10-01' and jobs[-1][1]=='2025-10-01'
        assert all(a[1]==b[0] for a,b in zip(jobs,jobs[1:]))
        assert len(jobs)==(12 if size=='MONTH' else 27)
    with pytest.raises(ValueError):scenario(strategies=['ALL'],work_size='invalid')


def test_measured_worker_default_and_explicit_override(monkeypatch):
    from event_backtest import runner
    monkeypatch.setattr(runner,'physical_cores',lambda:14)
    assert runner.worker_count({})==14
    assert runner.worker_count({'cores':6})==6
    assert runner.worker_count({'cores':6},14)==14
    assert runner.worker_count({'cores':6},14,True)==1
    monkeypatch.setattr(runner,'physical_cores',lambda:4)
    assert runner.worker_count({})==4

def test_work_settings_scenario_and_cli_precedence(tmp_path):
    import json
    from event_backtest.settings import scenario
    path=tmp_path/'scenario.json';path.write_text(json.dumps({'strategies':['SPECIAL1']}))
    defaults={'work_size':'FORTNIGHT','cores':6,'capture_start':'beginning'}
    assert scenario(path,defaults=defaults)['work_size']=='FORTNIGHT'
    path.write_text(json.dumps({'strategies':['SPECIAL1'],'work_size':'MONTH','cores':12}))
    result=scenario(path,defaults=defaults)
    assert (result['work_size'],result['cores'],result['capture_start'])==('MONTH',12,'beginning')
    result=scenario(path,defaults=defaults,cores=14,work_size='FORTNIGHT',capture_start='keyframe')
    assert (result['work_size'],result['cores'],result['capture_start'])==('FORTNIGHT',14,'keyframe')

def test_oz_runtime_rejects_an_unannounced_profile_and_checkpoint_keeps_selection():
    from event_application import create_event_engine
    from event_engine import Kind
    from test_engine_optimization import snapshot
    from event_engine.model import Event
    from copy import deepcopy
    engine=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
        'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'OFFLINE'},symbols=('XAUUSD+',),selection=['SPECIAL4'],backtest=True)
    engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=1,source_time=1756684800000,
                        payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot()}})
    engine.run();runtime=engine.processor_state['OZ_STATE']['runtime']
    assert set(runtime.profiles)=={('XAUUSD+','NORMAL','OZ')}
    assert runtime.profiles['XAUUSD+','NORMAL','OZ'].base_tfs==['1m']
    restored=deepcopy(runtime)
    assert restored.selection==runtime.selection and restored.profiles['XAUUSD+','NORMAL','OZ'].base_tfs==['1m']
    # Any undeclared child is an explicit failure, rather than a lost alert.
    restored.watch._watches['invalid']=SimpleNamespace(timeframes=('5m',),validation_mode='BLIND',trigger_mode='SUPER')
    # The same post-command guard is run before the next market, including state restores.
    with pytest.raises(ValueError,match='undeclared OZ'):restored.validate_selection()


def test_reused_worker_moves_write_boundary_before_creating_next_job(tmp_path):
    import subprocess,os
    rows,index,capture=keyframe_fixture(tmp_path)
    script='''
import sys
from pathlib import Path
from event_backtest.runner import run_chunk
from event_backtest.settings import scenario
root=Path(sys.argv[1]);capture=Path(sys.argv[2])
s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-09-04',mode='TICK',strategies=['SPECIAL7'],overlap_trading_days=0)
config={'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+','TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'OFFLINE'}
for i in range(2):
    task={'scenario':s,'config':config,'out':str(root/f'job{i}'),'run_id':str(i),'start':s['start'],'end':s['end'],
          'warm_start':s['start'],'captures':[{'path':capture.relative_to(root).as_posix()}],'warehouse':str(root)}
    result=run_chunk(task)
    assert result['bundles']==9
try:(root/'outside.txt').write_text('forbidden')
except PermissionError:pass
else:raise AssertionError('write boundary was lost')
'''
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT/'Part1/program'),str(ROOT/'Part2')))}
    proc=subprocess.run([sys.executable,'-X','utf8','-B','-c',script,str(tmp_path),str(capture)],
                        env=env,capture_output=True,text=True,encoding='utf-8')
    assert proc.returncode==0,proc.stderr
    assert all((tmp_path/f'job{i}'/'result.json').exists() for i in range(2))


def test_keyframe_index_is_portable(tmp_path):
    import shutil
    from event_backtest.storage import bundles
    rows,index,root=keyframe_fixture(tmp_path)
    destination=tmp_path/'moved'/'capture'
    shutil.copytree(root,destination)
    assert list(bundles(destination))==rows


def test_watch_ema_prefix_matches_full_after_forming_and_history_correction():
    from watch_array_facts import WatchMAStore
    from event_engine.market import MarketView
    from event_engine import FeedSnapshot
    from indicator_facts import ma_array
    from test_engine_optimization import snapshot
    store=WatchMAStore();base=snapshot()
    for seq in range(9):
        values=base.values.copy();values[-1,3]+=seq*.11
        if seq>=4:values[25,3]+=3.
        item=FeedSnapshot(base.time,base.volume,values,seq,'epoch:2' if seq>=7 else 'epoch:1',base.indicator_validity)
        got=store.get('XAUUSD+','1m',MarketView(item),['EMA17'])['EMA17']
        from watch_ma import feature_source_rows
        expected=ma_array(values[-feature_source_rows('EMA17'):,3],'EMA',17)
        assert got.tobytes()==expected.tobytes()
    cache=store.recurrences['XAUUSD+','1m','EMA17']
    assert cache.full_rebuilds==3
