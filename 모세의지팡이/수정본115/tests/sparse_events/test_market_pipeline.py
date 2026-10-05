"""Current-upload raw pipeline checks. Synthetic ticks; SQL is an explicit SQLite test double.

No assertion in this module is evidence of broker history or LIVE parity.
The native cold-start viability test intentionally fails while source state is undefined.
"""
import copy
import json
from pathlib import Path
import sqlite3
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
sys.path.insert(1,str(ROOT/'DataManager'))
from live_replay import event_catalog as C
from data_warehouse.store import Store, dumps
from data_warehouse.ticks import TICK_DTYPE
from data_warehouse.legacy import identity
from manager.tick_collection import collect_ticks, collect_market_data
from pit.models import TickRecord

BASE=1_780_000_000//3600*3600
SYMBOL='XAUUSD+'

class FakeMT5:
    broker='SYNTHETIC';server='SYNTHETIC_SERVER';build=(5,1,'test')
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def instrument(self,symbol):
        return {'broker_symbol':symbol,'server_fingerprint':identity(self.server),'chart_mode':'BID',
                'digits':2,'point':.01,'trade_tick_size':.01,'description':'SYNTHETIC','metadata_revision':'TEST'}
    def ticks(self,symbol,a,b):
        self.calls.append((a,b));return self.rows[(self.rows['time_msc']>=a)&(self.rows['time_msc']<b)]


def ticks(n=100,offset=0):
    rows=[]
    for i in range(offset,offset+n):
        p=100+(i%7)*.3
        for sec,value in ((0,p),(20,200. if i%9==5 else 20. if i%11==7 else p+.05)):
            t=BASE+i*60+sec;rows.append((t,value,value+.01,value,1,t*1000,2,1.))
    return np.array(rows,dtype=TICK_DTYPE)


def warehouse(path=':memory:'):
    s=Store.__new__(Store);s.connection=sqlite3.connect(str(path),isolation_level=None);s.read_only=False
    s.path=Path(str(path));s.db.executescript((ROOT/'Part2/data_warehouse/schema.sql').read_text())
    return s


def publish(w,rows,start,end):
    fake=FakeMT5(rows);r=collect_ticks(w,fake,SYMBOL,start,end,chunk_ms=300000)
    aid=r['archive_id'];m=json.loads(w.db.execute('SELECT manifest_json FROM tick_archives WHERE archive_id=?',[aid]).fetchone()[0])
    m['origin']='SYNTHETIC';m.pop('archive_identity');m['archive_identity']=identity(m)
    w.db.execute('UPDATE tick_archives SET manifest_json=? WHERE archive_id=?',[dumps(m),aid])
    return fake


def catalog(w):return C.EventStore('EXPLICIT_SQLITE_TEST_DOUBLE',_connection=w.db)
def all_events(c):return c.db.execute('SELECT * FROM events ORDER BY symbol,timeframe,timestamp,event_type').fetchall()
def bkeys(*tfs):return [(SYMBOL,tf,et) for tf in tfs for et in C.B_TYPES]


def test_collect_original_only_and_no_redownload():
    w=warehouse();raw=ticks(20);source=FakeMT5(raw);a=BASE*C.NS;b=(BASE+20*60)*C.NS
    r=collect_market_data(w,source,SYMBOL,a,b);calls=list(source.calls)
    assert r['status']=='PASS' and calls
    assert collect_market_data(w,source,SYMBOL,a,b)['status']=='UP_TO_DATE'
    assert source.calls==calls
    assert w.db.execute('SELECT count(*) FROM raw_m1').fetchone()[0]==0
    assert w.db.execute('SELECT count(*) FROM timeframe_bars').fetchone()[0]==0
    assert w.db.execute('SELECT count(*) FROM feature_values').fetchone()[0]==0


def test_tick_bytes_order_multi_tf_and_checkpoint():
    w=warehouse();raw=ticks(25);a=BASE*C.NS;b=(BASE+25*60)*C.NS;publish(w,raw,a,b)
    c=catalog(w);archive=C.MarketArchive(c,SYMBOL);records=list(archive.iter_ticks(a,b))
    assert b''.join(t.to_bytes() for _,_,t in records)==raw.tobytes()
    feed=C.MarketReplay(archive,bkeys('1m','3m','6m'),a)
    assert feed.tfs==('1m','3m','6m')
    for _,_,t in records[:21]:feed.advance(t)
    cp=feed.checkpoint();restored=C.MarketReplay(archive,bkeys('1m','3m','6m'),a);restored.restore(cp)
    for stamp,_,t in records[21:]:
        feed.advance(t);restored.advance(t)
        assert feed.core.book.view()==restored.core.book.view()
        assert all(x.open_ns<=stamp<x.nominal_end_ns for x in feed.core.book.forming.values())
    assert feed.checkpoint()==restored.checkpoint()


def test_market_nonempty_group_b_sparse_no_duplicate_and_actual_progress():
    w=warehouse();raw=ticks(120);a=BASE*C.NS;b=(BASE+120*60)*C.NS;publish(w,raw,a,b)
    c=catalog(w);updates=[];r=C.build_market(c,SYMBOL,bkeys('5m'),emit=lambda x:updates.append(copy.deepcopy(x)),batch_ticks=8)
    assert r['status']=='READY',r
    before=all_events(c);assert before
    assert set(x[3] for x in before)==set(C.B_TYPES)
    assert all(x[4]==C.close_time_ns(x[2],x[1]) for x in before)
    assert any(0<x.get('progress',0)<1 for x in updates)
    assert r['processed_ticks']==len(raw)
    again=C.build_market(c,SYMBOL,bkeys('5m'));assert again['status']=='UP_TO_DATE' and again['processed_ticks']==0
    assert all_events(c)==before
    assert c.db.execute('SELECT count(*) FROM ohlcv').fetchone()[0]==0


def test_market_new_event_and_new_tf_only():
    w=warehouse();raw=ticks(100);a=BASE*C.NS;b=(BASE+100*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    key=(SYMBOL,'5m','WONBI');first=C.build_market(c,SYMBOL,[key]);assert first['status']=='READY',first
    before=copy.deepcopy(c.state(key));events=all_events(c)
    r=C.build_market(c,SYMBOL,bkeys('5m'));assert all(j['key'][2]=='BB_OPEN_4_3_TOUCH' for j in r['jobs'])
    assert c.state(key)==before
    assert [x for x in all_events(c) if x[3]=='WONBI']==events
    before=all_events(c);r=C.build_market(c,SYMBOL,bkeys('5m','8m'))
    assert r['status']=='READY',r
    assert all(j['key'][1]=='8m' for j in r['jobs'])
    assert [x for x in all_events(c) if x[1]=='5m']==before


def test_market_append_and_pause_resume_equal_full():
    w=warehouse();full=warehouse();raw=ticks(120);a=BASE*C.NS;mid=(BASE+60*60)*C.NS;b=(BASE+120*60)*C.NS
    publish(w,raw,a,mid);c=catalog(w);r=C.build_market(c,SYMBOL,bkeys('5m'));assert r['status']=='READY',r
    publish(w,raw,mid,b);calls=[]
    def pause():calls.append(1);return len(calls)==7
    paused=C.build_market(c,SYMBOL,bkeys('5m'),pause=pause,batch_ticks=3);assert paused['status']=='PAUSED',paused
    assert paused['processed_ticks']==7
    checkpoint_to=c.state(bkeys('5m')[0])['calculated_to'];assert mid<checkpoint_to<b
    resume=C.build_market(c,SYMBOL,bkeys('5m'));assert resume['status']=='READY',resume
    assert resume['processed_ticks']==len(raw[raw['time_msc']*1000000>=checkpoint_to])
    publish(full,raw,a,b);f=catalog(full);assert C.build_market(f,SYMBOL,bkeys('5m'))['status']=='READY'
    assert all_events(c)==all_events(f)


def test_market_prepend_equals_full_with_bounded_join():
    w=warehouse();full=warehouse();raw=ticks(160);a=BASE*C.NS;mid=(BASE+60*60)*C.NS;b=(BASE+160*60)*C.NS
    publish(w,raw,mid,b);c=catalog(w);assert C.build_market(c,SYMBOL,bkeys('5m'))['status']=='READY'
    old=all_events(c);publish(w,raw,a,mid);r=C.build_market(c,SYMBOL,bkeys('5m'))
    assert r['status']=='READY',r
    publish(full,raw,a,b);f=catalog(full);C.build_market(f,SYMBOL,bkeys('5m'))
    assert all_events(c)==all_events(f)
    edge=mid+5*300*C.NS
    assert [x for x in old if x[2]>=edge]==[x for x in all_events(c) if x[2]>=edge]
    assert r['processed_ticks']<len(raw)


def test_market_native_four_percentiles_must_be_finite_after_warmup():
    """Viability, not a pass merely because an undefined buffer was preserved."""
    # Current Part1 PublishFeed rejects fewer than 250 bars. This is the
    # source's publication floor, NOT an invented recurrence seed/warmup.
    w=warehouse();raw=ticks(250);a=BASE*C.NS;b=(BASE+250*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    archive=C.MarketArchive(c,SYMBOL);keys=[(SYMBOL,'1m',e) for e in C.BAND_TYPES]
    feed=C.MarketReplay(archive,keys,a)
    for _,_,t in archive.iter_ticks(a,b):feed.advance(t)
    assert all(feed.ready_counts.get(('1m',f),0)>0 for f in C.FAMILIES), {'ready':feed.ready_counts,'undefined':feed.unavailable}


def test_unavailable_source_state_is_not_reported_up_to_date():
    w=warehouse();raw=ticks(42);a=BASE*C.NS;b=(BASE+42*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    keys=[(SYMBOL,'1m','PERCENTILE_PRICE_OUT')]
    r=C.build_market(c,SYMBOL,keys);assert r['status']=='PARTIAL',r
    again=C.build_market(c,SYMBOL,keys);assert again['status']=='PARTIAL',again
    with pytest.raises(C.ReplayError,match='COVERAGE'):
        c.read_events(SYMBOL,a,b,timeframes=('1m',),event_types=('PERCENTILE_PRICE_OUT',))


def test_gui_paths_shutdown_labels_and_countdown():
    from manager.gui import default_database_path
    from manager.allzone_events import shutdown_text
    assert Path(default_database_path())==ROOT/'DataManager/data/backtest.duckdb'
    assert shutdown_text(31,1)=='종료 예상: 30초'
    assert shutdown_text(31,31)=='종료 준비 중...'


def test_paused_undefined_a_is_not_on_readable():
    w=warehouse();raw=ticks(10);a=BASE*C.NS;b=(BASE+10*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    key=(SYMBOL,'1m','PERCENTILE_PRICE_OUT')
    result=C.build_market(c,SYMBOL,[key],pause=lambda:True)
    assert result['status']=='PAUSED'
    state=c.state(key);assert state['status']=='PARTIAL'
    with pytest.raises(C.ReplayError,match='COVERAGE'):
        c.read_events(SYMBOL,a,state['calculated_to'],timeframes=('1m',),event_types=(key[2],))


@pytest.mark.parametrize('special',range(1,8))
def test_market_on_wiring_no_percentile_and_one_event_batch(special,monkeypatch):
    """Synthetic empty coverage is injected ONLY to inspect the ON adapter.

    This is not a builder / nonempty alert parity pass. Those tests are separate.
    """
    from live_replay.__main__ import run_market
    from calculations import percentile_band
    w=warehouse();raw=ticks(6);a=BASE*C.NS;b=(BASE+6*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    archive=C.MarketArchive(c,SYMBOL)
    updates=[{'key':k,'from':a,'to':b,'definition_hash':C.definition_hash(k[2],input_kind='MARKET_TICKS',config={})}
             for k in C.replay_requirements((special,),[SYMBOL])]
    c.commit(updates,[],checkpoint={'SYNTHETIC_WIRING_ONLY':True},source_key=archive.source_key,input_kind='MARKET_TICKS')
    def forbidden(*args,**kwargs):raise AssertionError('ON must not construct a Percentile kernel')
    monkeypatch.setattr(percentile_band,'NativeSequence',forbidden)
    result=run_market('TEST_DOUBLE',SYMBOL,a,b,specials=(special,),duckdb='ON',_store=c)
    assert c.event_queries==1
    assert result['duckdb']=='ON' and result['processed_ticks']==len(raw)
    assert result['actual_live_comparison']=='NOT_PERFORMED'
    assert not any(k.split('|')[-1] in C.FAMILIES for k in result['common_calculations'])


def test_raw_off_fails_explicitly_for_undefined_native_state():
    from live_replay.__main__ import run_market
    w=warehouse();raw=ticks(5);a=BASE*C.NS;b=(BASE+5*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    with pytest.raises(C.ReplayError,match='SOURCE_DEFINED_WARMUP_OR_NATIVE_STATE_UNAVAILABLE'):
        run_market('TEST_DOUBLE',SYMBOL,a,b,specials=(7,),duckdb='OFF',_store=c)


def test_mt5_existing_terminal_auto_and_manual_api_mock(tmp_path,monkeypatch):
    from manager import mt5_source as M
    executable=tmp_path/'terminal64.exe';executable.touch();calls=[]
    class API:
        def initialize(self,*args,**kwargs):calls.append((args,kwargs));return True
        def terminal_info(self):return SimpleNamespace(connected=True,path=str(tmp_path),data_path=str(tmp_path/'data'),company='BROKER',maxbars=1000)
        def account_info(self):return SimpleNamespace(login=123,server='SERVER',company='BROKER')
        def version(self):return (5,1,'test')
        def shutdown(self):pass
        def symbols_get(self):return [SimpleNamespace(name='XAUUSD+',visible=True,select=True,description='Gold')]
    monkeypatch.setattr(M,'running_terminal_paths',lambda:[str(executable)])
    with M.MT5Source(api=API(),auto=True) as source:
        assert source.account==123 and source.server=='SERVER'
        assert source.symbols()[0]['symbol']=='XAUUSD+'
    assert calls[0][0]==(str(executable),) and calls[0][1]['timeout']==5000
    monkeypatch.setattr(M,'running_terminal_paths',lambda:[])
    with pytest.raises(RuntimeError):M.MT5Source(api=API(),auto=True)
    with M.MT5Source(str(executable),api=API()) as source:assert source.account==123
    assert len(calls)==2


def test_entrypoint_does_not_launch_gui_when_spawn_imports(monkeypatch):
    import runpy
    from manager import gui
    def forbidden():raise AssertionError('spawn import launched another Tk root')
    monkeypatch.setattr(gui,'main',forbidden)
    runpy.run_path(str(ROOT/'DataManager/DataManager.pyw'),run_name='__mp_main__')


def test_stored_original_integrity_verification():
    from manager.tick_collection import verify_market_data
    w=warehouse();raw=ticks(20);a=BASE*C.NS;b=(BASE+20*60)*C.NS
    source=publish(w,raw,a,b);calls=list(source.calls)
    result=verify_market_data(w,source,SYMBOL,a,b)
    assert result['status']=='PASS',result
    assert result['scope']=='STORED_RAW_INTEGRITY_ONLY'
    assert source.calls==calls


def _virtual_gui(body,tmp_path):
    import os,subprocess
    if sys.platform!='win32' and not os.environ.get('DISPLAY'):pytest.skip('A virtual Tk display is required')
    env=dict(os.environ,PYTHONPATH=str(ROOT/'DataManager')+os.pathsep+str(ROOT/'Part2'),
             PYTHONDONTWRITEBYTECODE='1',LOCALAPPDATA=str(tmp_path),PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'-B','-c',body],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def test_virtual_event_progress_pause_close_choices_countdown_custom_db(tmp_path):
    _virtual_gui(r'''
import tkinter as tk,time
from pathlib import Path
from manager.gui import DataManagerApp
from manager import allzone_events as M
root=tk.Tk();app=DataManagerApp(root);app.submit=lambda action:None
app.symbol.set('XAUUSD+');chosen=str(Path.cwd()/'custom.duckdb');app.db.set(chosen)
root.update();window=M.open_window(app);started=[]
def fake(source,db_path,*,pause_file,emit,**kwargs):
    assert source is None and db_path==chosen
    started.append(1)
    emit({'status':'BUILDING','progress':.25,'date':'2026-06-01T00:00:00Z','timeframe':'5m',
          'event_type':'WONBI','events_inserted':3,'commit_seconds':.01,'unit_seconds':1.2})
    limit=time.monotonic()+5
    while not Path(pause_file).exists() and time.monotonic()<limit:time.sleep(.005)
    assert Path(pause_file).exists()
    time.sleep(1.25)
    emit({'status':'PAUSED','progress':.3,'events_inserted':4})
    return {'status':'PAUSED'}
M.run_build=fake;window.event_start('EVENT 저장·증분')
limit=time.monotonic()+3
while window.event_progress.get()!=25 and time.monotonic()<limit:root.update();time.sleep(.005)
assert window.event_progress.get()==25
assert window.event_variables['events_inserted'].get()=='3'
M.close_choice=lambda parent:'취소';window.event_close();assert window.event_job['busy']
assert not window.event_job['pause'].exists()
M.close_choice=lambda parent:'저장 후 종료';window.event_close()
text1=window.event_shutdown.get();assert text1.startswith('종료 예상:')
limit=time.monotonic()+1.1
while time.monotonic()<limit:root.update();time.sleep(.005)
assert window.event_shutdown.get()!=text1
limit=time.monotonic()+3
while window.winfo_exists() and time.monotonic()<limit:root.update();time.sleep(.005)
assert not window.winfo_exists() and not app.busy and app.db.get()==chosen
root.destroy()
''',tmp_path)


def test_virtual_force_close_terminates_actual_child(tmp_path):
    _virtual_gui(r'''
import tkinter as tk,time,subprocess,sys
from manager.gui import DataManagerApp
from manager import allzone_events as M
root=tk.Tk();app=DataManagerApp(root);app.submit=lambda action:None;app.symbol.set('XAUUSD+')
root.update();window=M.open_window(app);children=[]
def fake(source,db_path,*,on_process,emit,**kwargs):
    proc=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(25)'])
    children.append(proc);on_process(proc);proc.wait()
    return {'status':'FAILED'}
M.run_build=fake;window.event_start('EVENT 저장·증분')
limit=time.monotonic()+3
while not children and time.monotonic()<limit:root.update();time.sleep(.005)
assert children and children[0].poll() is None
M.close_choice=lambda parent:'강제 종료';begin=time.monotonic();window.event_close()
limit=time.monotonic()+3
while window.winfo_exists() and time.monotonic()<limit:root.update();time.sleep(.005)
assert children[0].poll() is not None and not window.winfo_exists()
assert time.monotonic()-begin<2
root.destroy()
''',tmp_path)


def test_virtual_exact_shutdown_dialog_labels(tmp_path):
    _virtual_gui(r'''
import tkinter as tk
from tkinter import ttk
from manager.allzone_events import close_choice
root=tk.Tk();root.update()
def check():
    def widgets(node):
        yield node
        for child in node.winfo_children():yield from widgets(child)
    buttons=[w for w in widgets(root) if isinstance(w,ttk.Button)]
    assert [w.cget('text') for w in buttons]==['저장 후 종료','강제 종료','취소']
    buttons[-1].invoke()
root.after(30,check)
assert close_choice(root)=='취소'
root.destroy()
''',tmp_path)


def test_virtual_part2_exact_period_and_owner_close(tmp_path):
    _virtual_gui(r'''
import tkinter as tk,time
from live_replay.gui import open_window
root=tk.Tk();root.update()
window=open_window(root,symbol='XAUUSD+',start='2026-09-23',end='2026-09-24',
    exact_range=(1000000000000,2000000000000),replay_mode='봉마감')
assert window.replay_exact_range==(1000000000000,2000000000000)
assert window.replay_mode.get()=='봉마감'
window.replay_close(close_owner=True)
try:exists=root.winfo_exists()
except tk.TclError:exists=False
assert not exists
''',tmp_path)


def test_virtual_auto_connection_failure_is_nonmodal_and_manual_available(tmp_path):
    _virtual_gui(r'''
import tkinter as tk,time
from manager.gui import DataManagerApp
root=tk.Tk();app=DataManagerApp(root)
limit=time.monotonic()+5
while time.monotonic()<limit:
    root.update();time.sleep(.01)
    if app.connection_info.get().startswith('자동 연결 실패') and not app.busy:break
assert app.connection_info.get().startswith('자동 연결 실패')
manual=next(b for b in app.buttons if b.cget('text')=='MT5 연결')
assert str(manual.cget('state'))=='normal'
root.destroy()
''',tmp_path)


def test_b_partial_initial_bar_requires_only_its_finite_lookback():
    w=warehouse();raw=ticks(100);a=(BASE+60)*C.NS;b=(BASE+100*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    result=C.build_market(c,SYMBOL,bkeys('5m'))
    assert result['status']=='READY',result
    assert all_events(c),'B must become computable after four complete observed opens, not 682 candles'


def test_progress_after_warmup_pause_excludes_already_committed_work(monkeypatch):
    w=warehouse();raw=ticks(100);a=BASE*C.NS;b=(BASE+100*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    keys=bkeys('5m');begin=(BASE+50*60)*C.NS
    count=[]
    def pause():count.append(1);return len(count)==35
    paused=C.build_market(c,SYMBOL,keys,start=begin,pause=pause,batch_ticks=4)
    assert paused['status']=='PAUSED' and paused['processed_ticks']==35
    resumed=C.build_market(c,SYMBOL,keys,start=begin)
    assert resumed['status']=='READY' and resumed['processed_ticks']==len(raw)-35
    assert c.db.execute("SELECT count(*) FROM event_checkpoints WHERE checkpoint_id LIKE 'work:%'").fetchone()[0]==0


def test_explicit_rebuild_discards_selected_uncommitted_warmup_pointer():
    w=warehouse();raw=ticks(100);a=BASE*C.NS;b=(BASE+100*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    keys=bkeys('5m');begin=(BASE+50*60)*C.NS
    assert C.build_market(c,SYMBOL,keys,start=begin,pause=lambda:True)['status']=='PAUSED'
    assert c.db.execute("SELECT count(*) FROM event_checkpoints WHERE checkpoint_id LIKE 'work:%'").fetchone()[0]==1
    rebuilt=C.build_market(c,SYMBOL,keys,start=begin,rebuild=True)
    assert rebuilt['status']=='READY' and rebuilt['processed_ticks']==len(raw)


def test_force_kill_rollback_and_market_checkpoint_resume_sql_double(tmp_path):
    """Actual OS process kill; SQLite only. Actual DuckDB recovery is unverified."""
    import os,subprocess,time
    path=tmp_path/'EXPLICIT_SQLITE_TEST_DOUBLE.db';w=warehouse(path);raw=ticks(100)
    a=BASE*C.NS;b=(BASE+100*60)*C.NS;publish(w,raw,a,b);c=catalog(w);calls=[]
    def pause():calls.append(1);return len(calls)==31
    assert C.build_market(c,SYMBOL,bkeys('5m'),pause=pause,batch_ticks=8)['status']=='PAUSED'
    expected=all_events(c);states=[c.state(k) for k in bkeys('5m')];w.close()
    pending=tmp_path/'pending'
    code=r'''
import sqlite3,sys,time
from pathlib import Path
from live_replay import event_catalog as C
c=C.EventStore('EXPLICIT_SQLITE_TEST_DOUBLE',_connection=sqlite3.connect(sys.argv[1],isolation_level=None))
original=c._store_events
def blocked(events):
    result=original(events)
    Path(sys.argv[2]).touch()
    time.sleep(30)
    return result
c._store_events=blocked
C.build_market(c,'XAUUSD+',[('XAUUSD+','5m',e) for e in C.B_TYPES],batch_ticks=3)
'''
    p=subprocess.Popen([sys.executable,'-B','-c',code,str(path),str(pending)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env=dict(os.environ,PYTHONPATH=str(ROOT/'Part2'),PYTHONDONTWRITEBYTECODE='1'))
    try:
        limit=time.monotonic()+8
        while not pending.exists() and p.poll() is None and time.monotonic()<limit:time.sleep(.01)
        assert pending.exists(),p.communicate(timeout=1)
        p.kill();p.wait(timeout=3)
    finally:
        if p.poll() is None:p.kill();p.wait()
    w=warehouse(path);c=catalog(w)
    assert all_events(c)==expected and [c.state(k) for k in bkeys('5m')]==states
    assert C.build_market(c,SYMBOL,bkeys('5m'))['status']=='READY'
    f=warehouse();publish(f,raw,a,b);fc=catalog(f);C.build_market(fc,SYMBOL,bkeys('5m'))
    assert all_events(c)==all_events(fc)


def test_actual_duckdb_market_pipeline_and_rollback_when_available(tmp_path):
    pytest.importorskip('duckdb',reason='NOT_RUN: actual DuckDB package is unavailable')
    path=tmp_path/'backtest.duckdb';raw=ticks(100);a=BASE*C.NS;b=(BASE+100*60)*C.NS
    with Store(path,create=True) as w:publish(w,raw,a,b)
    with C.EventStore(path) as c:
        assert C.build_market(c,SYMBOL,bkeys('5m'))['status']=='READY'
        before=all_events(c);assert before
        assert C.build_market(c,SYMBOL,bkeys('5m'))['processed_ticks']==0
        with pytest.raises(RuntimeError):
            c.commit([],[],checkpoint={'test':1},source_key='test',input_kind='MARKET_TICKS',fail_before_commit=True)
        assert all_events(c)==before


def test_prepend_boundary_pause_and_resume_equal_full():
    w=warehouse();raw=ticks(160);a=BASE*C.NS;mid=(BASE+60*60)*C.NS;b=(BASE+160*60)*C.NS
    publish(w,raw,mid,b);c=catalog(w);C.build_market(c,SYMBOL,bkeys('5m'))
    publish(w,raw,a,mid);calls=[]
    def pause():calls.append(1);return len(calls)==125
    first=C.build_market(c,SYMBOL,bkeys('5m'),pause=pause)
    assert first['status']=='PAUSED',first
    assert c.db.execute("SELECT count(*) FROM event_checkpoints WHERE checkpoint_id LIKE 'work:boundary:%'").fetchone()[0]>0
    resumed=C.build_market(c,SYMBOL,bkeys('5m'))
    assert resumed['status'] in ('READY','UP_TO_DATE'),resumed
    full=warehouse();publish(full,raw,a,b);f=catalog(full);C.build_market(f,SYMBOL,bkeys('5m'))
    assert all_events(c)==all_events(f)

# Source-initialization diagnostics and publication transport tests below do
# NOT stand in for the failing raw-market viability assertion above.
def _source_history(n):
    import math
    return [dict(bar_id=str(i),open=100+i*.1,high=103+i*.1,
                 low=98+i*.1,close=101+math.sin(i)*2+i*.1) for i in range(n)]


@pytest.mark.parametrize('n',(110,200,400,7501))
@pytest.mark.parametrize('family',C.FAMILIES)
def test_preloaded_history_reports_exact_unwritten_predecessor(family,n):
    """One full P=0 history call, not a longer tick cold-start illusion.

    This verifies the diagnosis only. Raw Group A READY remains a separate
    mandatory positive test and must not be xfailed or weakened.
    """
    from calculations.percentile_band import NativeSequence
    sequence=NativeSequence(family);result=sequence.advance(_source_history(n))
    gap=result.metadata['initialization_gap']
    index={'PRICE':7,'RSI':24,'STO':36,'DI':36}[family]
    assert result.event.R==n and result.event.P==0 and result.returned==n
    assert gap['buffer']==('ehlers' if family=='PRICE' else 'smooth')
    assert gap['chronological_index']==index and gap['shift']==n-1-index
    assert gap['rates_total']==n and gap['prev_calculated']==0
    assert gap['bar_id']==str(index)
    assert gap['source_reason'].endswith('NEW_STORAGE:'+str(index))
    assert result.cell('raw').source_defined
    assert all(not result.cell(name).available for name in
               ('smooth','lower','upper','basis','regime_lower','regime_upper'))
    if family=='PRICE':assert result.cell('hma').source_defined


@pytest.mark.parametrize('family',C.FAMILIES)
def test_native_preload_forming_completed_checkpoint_lifecycle(family):
    """Source calls / raw values / HMA continuity; not finite Percentile parity."""
    from calculations.percentile_band import NativeSequence
    seq=NativeSequence(family);bars=_source_history(400)
    preload=seq.advance(bars,source_ordinal=1)
    assert preload.event.P==0
    old_raw=preload.cell('raw').bits
    forming=[*bars[:-1],dict(bars[-1],close=bars[-1]['close']+1)]
    preview=seq.advance(forming,source_ordinal=2)
    assert preview.event.P==400 and preview.event.R==400
    assert preload.cell('raw').bits==old_raw  # previous observation immutable
    cp=seq.checkpoint();restored=NativeSequence.restore(cp)
    extended=[*forming,_source_history(401)[-1]]
    commit=seq.advance(extended,source_ordinal=3);resumed=restored.advance(extended,source_ordinal=3)
    assert commit.event.P==400 and commit.event.R==401
    assert commit.buffers==resumed.buffers and seq.checkpoint()==restored.checkpoint()
    assert commit.metadata['initialization_gap']==preload.metadata['initialization_gap']
    if family=='PRICE':assert commit.cell('hma',1).bits==preview.cell('hma').bits
    # Existing checkpoint files without the diagnostic field remain readable.
    legacy=copy.deepcopy(cp);legacy['kernel'].pop('initialization_gap')
    assert NativeSequence.restore(legacy).advance(extended,source_ordinal=3).buffers==commit.buffers


def _publication_result(family,n=750):
    """SYNTHETIC buffer-transport fixture, NEVER a kernel seed or market result."""
    from pit.features.percentile.contracts import NativeCell,KernelResult,CalcEvent
    value=lambda x:NativeCell.from_float(x)
    buffers={name:tuple(value(base+i*.01) for i in range(n)) for name,base in
             {'hma' if family=='PRICE' else 'smooth':100.,'lower':90.,'upper':110.,
              'basis':100.,'regime_lower':95.,'regime_upper':105.}.items()}
    return KernelResult(CalcEvent('transport',n,0),n,buffers)


@pytest.mark.parametrize('family',C.FAMILIES)
def test_all_published_native_history_rows_are_projected_without_recalculation(family):
    """Fix regression: old adapter copied only two of STAFF's up-to-650 rows."""
    result=_publication_result(family);before=copy.deepcopy(dict(result.buffers))
    feed=C.MarketReplay.__new__(C.MarketReplay);feed.native_status={}
    frame=C.pd.DataFrame({'time':C.pd.date_range('2026-01-01',periods=650,freq='min')})
    feed._project_native_family(frame,'1m',family,result)
    field='price_hma_6' if family=='PRICE' else family+'_val'
    assert len(frame)==650 and frame[field].notna().sum()==650
    assert frame[field].iloc[0]==100.+649*.01 and frame[field].iloc[-1]==100.
    assert feed.native_status['1m',family]['published'] and feed.native_status['1m',family]['ready']
    assert dict(result.buffers)==before


@pytest.mark.parametrize('bad',('UNKNOWN','NAN','EMPTY','INFINITY','SHORT_COPY'))
def test_copybuffer_failure_masks_whole_family_without_altering_native_cells(bad):
    from pit.features.percentile.contracts import NativeCell,KernelResult,EMPTY_VALUE
    result=_publication_result('PRICE');buffers=dict(result.buffers)
    cell={'UNKNOWN':NativeCell.unknown('TEST_UNAVAILABLE'),
          'NAN':NativeCell.from_float(float('nan')),'EMPTY':NativeCell.from_float(EMPTY_VALUE),
          'INFINITY':NativeCell.from_float(float('inf'))}.get(bad)
    buffers['basis']=tuple([cell]*750) if cell is not None else buffers['basis'][:681]
    result=KernelResult(result.event,result.returned,buffers)
    feed=C.MarketReplay.__new__(C.MarketReplay);feed.native_status={}
    frame=C.pd.DataFrame({'time':range(650)})
    feed._project_native_family(frame,'1m','PRICE',result)
    assert frame['price_hma_6'].isna().all() and frame['price_band_lower'].isna().all()
    assert not feed.native_status['1m','PRICE']['published']
    assert result.cell('hma').source_defined  # not overwritten to manufacture success/failure
    assert result.buffers['basis']==buffers['basis']


def test_copybuffer_preserves_real_zero_and_requires_staff_250_bars():
    from pit.features.percentile.contracts import NativeCell,KernelResult
    feed=C.MarketReplay.__new__(C.MarketReplay);feed.native_status={}
    for n in (249,250):
        result=_publication_result('DI',n);buffers=dict(result.buffers)
        buffers['basis']=tuple(NativeCell.from_float(0.) for _ in range(n))
        result=KernelResult(result.event,result.returned,buffers)
        frame=C.pd.DataFrame({'time':range(n)})
        feed._project_native_family(frame,'1m','DI',result)
        assert feed.native_status['1m','DI']['published']==(n==250)
        if n==250:assert (frame['DI_basis']==0.).all()
        else:assert frame['DI_basis'].isna().all()


def test_source_diagnostic_reaches_worker_and_health_not_false_ready():
    w=warehouse();raw=ticks(45);a=BASE*C.NS;b=(BASE+45*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    keys=[(SYMBOL,'1m','PERCENTILE_'+f+'_OUT') for f in C.FAMILIES]
    r=C.build_market(c,SYMBOL,keys)
    assert r['status']=='PARTIAL'
    assert 'NATIVE_BUFFER_READ_BEFORE_WRITE' in r['error']
    assert all(r['native_initialization']['1m/'+f]['initialization_gap'] for f in C.FAMILIES)
    archive=C.MarketArchive(c,SYMBOL);feed=C.MarketReplay(archive,keys,a)
    for _,_,tick in archive.iter_ticks(a,b):feed.advance(tick)
    assert not feed.health(SYMBOL,['1m'])['1m']['ready']
    assert feed.health(SYMBOL,['1m'])['1m']['status']=='NATIVE_BUFFER_READ_BEFORE_WRITE'
    assert feed.request(SYMBOL,['1m'],['PRICE']) is None
    assert feed.request(SYMBOL,['1m'],[])  # independent raw OHLC availability
    saved=feed.checkpoint();new=C.MarketReplay(archive,keys,a);new.restore(saved)
    assert new.native_status==feed.native_status


def test_all_six_tf_observation_order_and_single_shared_family_pass(monkeypatch):
    from calculations.percentile_band import NativeSequence
    w=warehouse();raw=ticks(3);a=BASE*C.NS;b=(BASE+3*60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    archive=C.MarketArchive(c,SYMBOL)
    keys=[(SYMBOL,tf,et) for tf in C.GROUP_A for et in C.BAND_TYPES]
    calls=[];original=NativeSequence.advance
    def traced(self,bars,**kwargs):
        calls.append((kwargs['source_ordinal'],kwargs['history_epoch'].rsplit('|',1)[-1],self.family))
        return original(self,bars,**kwargs)
    monkeypatch.setattr(NativeSequence,'advance',traced)
    feed=C.MarketReplay(archive,keys,a)
    for stamp,ordinal,tick in archive.iter_ticks(a,b):
        feed.advance(tick)
        batch=[(tf,f) for source,tf,f in calls if source==tick.source_ordinal]
        assert batch==[(tf,f) for tf in C.GROUP_A for f in sorted(C.FAMILIES)]
        assert all(bar.open_ns<=stamp<bar.nominal_end_ns for bar in feed.core.book.forming.values())
    assert len(calls)==len(raw)*6*4  # OUT and OUT_IN share the one kernel call


def test_group_a_preload_budget_uses_real_part1_confirmation_mapping():
    w=warehouse();raw=ticks(1);a=BASE*C.NS;b=(BASE+60)*C.NS;publish(w,raw,a,b);c=catalog(w)
    feed=C.MarketReplay(C.MarketArchive(c,SYMBOL),[(SYMBOL,tf,'ALLZONE') for tf in C.GROUP_A],a)
    needed=set(C.GROUP_A)|{tf for base in C.GROUP_A for tf in C.O.TF_MAP[base]}
    requirements=feed.history_requirements()
    assert set(requirements)==needed and '30m' in needed
    assert requirements['30m']['aligned_full_m1_bar_budget']==7500
    assert all(v['staff_min_bars']==250 and not v['finite_history_guarantees_ready'] for v in requirements.values())


@pytest.mark.parametrize('a_tf',('1m','5m'))
def test_missing_group_a_family_does_not_suppress_valid_group_b(a_tf):
    raw=ticks(80);a=BASE*C.NS;b=(BASE+80*60)*C.NS
    mixed=warehouse();only_b=warehouse()
    publish(mixed,raw,a,b);publish(only_b,raw,a,b)
    c=catalog(mixed);baseline=catalog(only_b)
    akey=(SYMBOL,a_tf,'PERCENTILE_PRICE_OUT')
    result=C.build_market(c,SYMBOL,[akey,*bkeys('5m')])
    expected=C.build_market(baseline,SYMBOL,bkeys('5m'))
    assert result['status']=='PARTIAL' and expected['status']=='READY'
    assert c.state(akey)['status']=='PARTIAL'
    assert all(c.state(k)['status']=='READY' for k in bkeys('5m'))
    actual_b=[row for row in all_events(c) if row[3] in C.B_TYPES]
    assert actual_b and actual_b==all_events(baseline)


def test_measurement_range_preloads_raw_history_without_storing_prior_events():
    """Nonempty Group B checks the common raw/premeasurement storage boundary.

    This does not certify a native Group A recurrence initialization.
    """
    raw=ticks(80);a=BASE*C.NS;start=(BASE+40*60)*C.NS;end=(BASE+80*60)*C.NS
    measured=warehouse();whole=warehouse()
    publish(measured,raw,a,end);publish(whole,raw,a,end)
    c=catalog(measured);full=catalog(whole)
    result=C.build_market(c,SYMBOL,bkeys('5m'),start=start,end=end)
    C.build_market(full,SYMBOL,bkeys('5m'))
    actual=all_events(c)
    assert actual and actual==[row for row in all_events(full) if row[2]>=start]
    assert all(start<=row[2]<end for row in actual)
    assert result['processed_ticks']==len(raw)  # earlier raw data was calculated, not stored as EVENT
    assert all(c.state(k)['calculated_from']==start for k in bkeys('5m'))
