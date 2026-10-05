from pathlib import Path
import datetime as dt
import hashlib
import json
import sys
import numpy as np
import pytest
import contextlib
import shutil
import struct

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_backtest.delta import DeltaCodec,write_delta,read_delta,verify_delta
from event_backtest.build_plan import make_plan,require_approval,ConfirmationRequired
from event_backtest.history_check import omissions,require_complete,HistoryMissing
from event_backtest.settings import scenario
from event_selection import resolve
import staff_schema as wire

def bundle(times,values,seq=1,kind=1):
    frame=wire.pack_v2('XAUUSD+','1m',times,np.arange(len(times)),values,seq=seq,kind=kind)
    return wire.pack_bundle('XAUUSD+',[frame],seq=seq,sent_at_ms=seq*1000)

def test_delta_bits_and_row_lifecycle(tmp_path):
    x=np.arange(4*50,dtype=float).reshape(4,50);x.view('<u8')[0,:4]=[0,2**63,0x7ff8000000000001,0x7ff8000000000002]
    y=x.copy();y[1,4]=np.finfo(float).max;y.view('<u8')[0,0]=2**63
    frames=[bundle([1,2,3,4],x),bundle([1,2,3,4],y,2),bundle([2,3,4,5],y,3),
        bundle([5],y[-1:],4,2),bundle([],np.empty((0,50)),5,3),bundle([3,4,5,6],x,6)]
    expected=write_delta(enumerate(frames),tmp_path/'a.gz')
    assert [v for t,v in read_delta(tmp_path/'a.gz')]==frames
    assert verify_delta(tmp_path/'a.gz',expected)==expected
    with pytest.raises(ValueError):verify_delta(tmp_path/'a.gz',{**expected,'bundle_sha256':'bad'})

class Catalog:
    def __init__(self,root,items=()):self.root=root;self.items=list(items)
    def available(self,*a):return self.items
    def find_capture(self,key):return next((c for c in self.items if c['capture_id']==key),None)

def test_plan_schema_build_confirmation_rebuild(tmp_path,monkeypatch):
    import event_backtest.build_plan as plans
    monkeypatch.setattr(plans,'current_build',lambda root:'ea')
    monkeypatch.setattr(plans,'schema_id',lambda:50)
    s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-10-01',strategies=['SPECIAL1'],overlap_trading_days=0)
    catalog=Catalog(tmp_path);plan=make_plan(s,catalog,today=dt.date(2026,9,27))
    assert len(plan['record'])==1
    with pytest.raises(ConfirmationRequired):require_approval(plan,None)
    require_approval(plan,plan['approval_token'])
    item={**plan['record'][0],'capture_id':'a','ea_build_hash':'ea','schema_id':50,'storage':'MSD1','reconstruction_verified':True}
    catalog.items=[item];assert make_plan(s,catalog)['record']==[]
    for key,value in [('schema_id',48),('ea_build_hash','old')]:
        catalog.items=[{**item,key:value}];assert len(make_plan(s,catalog)['record'])==1
    catalog.items=[item];assert len(make_plan(s,catalog,rebuild=True)['record'])==1
    catalog.items=[{k:v for k,v in item.items() if k not in ('storage','reconstruction_verified')}]
    converted=make_plan(s,catalog)
    assert converted['record']==[] and len(converted['convert'])==1

def test_history_gap_stops_replay():
    gaps=omissions(['XAUUSD+,M1 : 2025.09.01 00:00 - 2025.10.01 00:00 history data missing'],
                   '2025-09-01','2025-10-01','XAUUSD+')
    assert gaps[0]['start'].startswith('2025-09-01')
    with pytest.raises(HistoryMissing):require_complete([{'history_missing':gaps}])
    assert not omissions(['XAUUSD+ : real ticks begin from 2023.10.18'], '2025-09-01','2025-10-01','XAUUSD+')

def test_generated_ticks_warn_and_allow_replay(tmp_path,monkeypatch):
    from event_backtest.history_check import tick_warning
    from event_backtest import workflow,recording,runner
    lines=['XAUUSD+ : 2025.09.01 00:00 - 2025.10.01 00:00 real ticks absent for 30146 minutes of 30146 total minute bars, every tick generation used',
           'XAUUSD+ : real ticks absent for 22 whole days',
           'XAUUSD+ : history ticks unavailable',
           'XAUUSD+,M1 : real ticks absent, generated ticks used',
           'XAUUSD+,H4 : history data missing']
    assert not omissions(lines,'2025-09-01','2025-10-01','XAUUSD+')
    assert omissions(['XAUUSD+,M1 : minute bars missing; generated ticks used'], '2025-09-01','2025-10-01','XAUUSD+')
    assert not omissions(['XAUUSD+,M1 : 2025.08.01 history missing'], '2025-09-01','2025-10-01','XAUUSD+')
    c={'start':'2025-09-01','end':'2025-10-01','history_missing':[], 'tick_evidence':{'actual':'MIXED_OR_GENERATED'}}
    require_complete([c]);assert '생성 틱' in tick_warning(c,'BAR')
    monkeypatch.setattr(runner,'runtime_config',lambda s:{})
    monkeypatch.setattr(workflow,'proposal',lambda *a,**k:{'record':[],'approval_token':'same'})
    monkeypatch.setattr(recording,'prepare',lambda *a,**k:[c])
    calls=[]
    monkeypatch.setattr(runner,'run',lambda *a,**k:(calls.append(k['captures']) or 'complete'))
    assert workflow.execute({'strategies':['SPECIAL1']},tmp_path)=='complete'
    assert calls==[[c]]

def test_journal_keeps_minute_bar_omissions_and_generated_tick_evidence(tmp_path,monkeypatch):
    from event_backtest import recording
    journal=tmp_path/'tester.log'
    journal.write_text('XAUUSD+ : 2025.09.02 minute bars missing\nXAUUSD+ : 2025.09.03 real ticks absent for 1440 minutes of 1440 total minute bars, every tick generation used\n',encoding='utf-16-le')
    monkeypatch.setattr(recording,'journal_positions',lambda profile:{str(journal):journal.stat().st_size})
    evidence=recording.tick_evidence({}, {},tmp_path)
    assert evidence['actual']=='MIXED_OR_GENERATED'
    gaps=omissions(evidence['journal_lines'],'2025-09-01','2025-10-01','XAUUSD+')
    assert len(gaps)==1 and gaps[0]['start'].startswith('2025-09-02')

def test_result_csv_destination_and_rows_are_not_query_parameters(tmp_path,monkeypatch):
    import csv
    from event_backtest.warehouse import Warehouse,FIELDS
    monkeypatch.chdir(tmp_path)
    w=Warehouse(tmp_path/'warehouse',results=True)
    run_id='approved_run';other='other_run'
    row={name:'' for name in FIELDS}
    row.update(run_id=run_id,time_ms=20,strategy='SPECIAL1',message='따옴표 "와, 쉼표\n줄바꿈',recipient='BACKTEST',signal_id='two')
    w.db.execute('INSERT INTO alerts VALUES ('+','.join('?' for _ in FIELDS)+')',[row[f] for f in FIELDS])
    row.update(time_ms=10,signal_id='one')
    w.db.execute('INSERT INTO alerts VALUES ('+','.join('?' for _ in FIELDS)+')',[row[f] for f in FIELDS])
    row.update(run_id=other,time_ms=1)
    w.db.execute('INSERT INTO alerts VALUES ('+','.join('?' for _ in FIELDS)+')',[row[f] for f in FIELDS])
    out=tmp_path/'warehouse'/'알림 결과.csv'
    assert w.export_results(run_id,out)==2
    with out.open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    assert [r['time_ms'] for r in rows]==['10','20']
    assert all(r['run_id']==run_id and r['message']==row['message'] for r in rows)
    empty=out.with_name('empty.csv');assert w.export_results('absent',empty)==0
    with empty.open(encoding='utf-8',newline='') as f:assert list(csv.reader(f))==[list(FIELDS)]
    w.close()
    assert sorted(p.name for p in tmp_path.iterdir())==['warehouse']

def test_public_runner_final_export_stays_in_run_directory(tmp_path,monkeypatch):
    import csv,concurrent.futures
    from event_backtest import runner
    from event_backtest.warehouse import FIELDS
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner,'runtime_config',lambda s:{})
    monkeypatch.setattr(runner,'code_hash',lambda:'test-code')
    monkeypatch.setattr(runner.concurrent.futures,'ProcessPoolExecutor',concurrent.futures.ThreadPoolExecutor)
    def worker(task):
        out=Path(task['out']);out.mkdir(parents=True)
        path=out/'alerts.csv'
        with path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
            writer.writerow({'run_id':task['run_id'],'time_ms':1,'strategy':'SPECIAL1','message':'검증 알림','recipient':'OFFLINE','signal_id':'one'})
        return {'bundles':1,'processor_timings':{},'max_memory_bytes':1,'approximate':False,
                'alerts_csv':path.relative_to(task['warehouse']).as_posix(),
                'task_start':task['start'],'task_end':task['end'],
                'warmup_bundles':0,'elapsed_seconds':0.0,
                'processed_start_ms':None,'processed_end_ms':None}
    monkeypatch.setattr(runner,'run_chunk',worker)
    s=scenario(start='2025-09-01',end='2025-09-08',strategies=['SPECIAL1'],overlap_trading_days=0)
    root=tmp_path/'warehouse';result=runner.run(s,root,sequential=True,captures=[])
    export=root/result['alerts_csv']
    assert export.parent==root/'runs'/result['run_id']
    with export.open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    assert len(rows)==1 and rows[0]['message']=='검증 알림'
    assert sorted(p.name for p in tmp_path.iterdir())==['warehouse']

def test_selection_dependency_closure_and_no_implicit_all():
    plan=resolve(['SPECIAL1'],{})
    assert plan.specials==('SPECIAL1',)
    assert plan.processors=={'OZ_STATE','SWEEP_STATE'}
    assert plan.consumers=={'OZ','SWEEP','INDICATOR'}
    assert 'FVG' not in plan.families and 'WATCH' not in plan.families
    assert resolve(['SPECIAL3'],{}).processors=={'OZ_STATE','SWEEP_STATE','FVG_STATE','WATCH_CONDITIONS'}
    assert 'FVG_STATE' in resolve(['SPECIAL6'],{}).processors
    with pytest.raises(ValueError):resolve([],{})
    assert resolve(None,{})==resolve(['ALL'],{})

def test_official_definitions_only_selected():
    config={'OFFICIAL_SPECS':json.dumps([{'spec_id':'one','conditions':['TREND@1m']},{'spec_id':'two','conditions':['FVG@1m']}])}
    plan=resolve(['OFFICIAL:one'],config)
    assert len(plan.official)==1 and 'FVG_STATE' not in plan.processors
    assert not resolve(['SPECIAL1'],config).official

def test_commands_require_declared_selection():
    with pytest.raises(ValueError):scenario(strategies=['SPECIAL1'],commands=[{'text':'골드 올존 계속','chat_id':'test','strategy':'OZ'}])

def test_construct_selected_engine(tmp_path,monkeypatch):
    monkeypatch.setenv('MOSES_LOG_DIRECTORY',str(tmp_path/'logs'))
    from event_application import create_event_engine
    from event_engine.model import Kind
    engine=create_event_engine({'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TELEGRAM_CHAT_ID':'OFFLINE'},selection=['SPECIAL1'])
    assert {p.name for p in engine.processors}=={'OZ_STATE','SWEEP_STATE'}
    assert {p.name for p in engine.strategies}=={'OZ','SWEEP','INDICATOR','COMPOSER'}
    assert not any('SPECIAL2' in k for k in engine.strategy_state)

def test_skip_only_checkpoint_saves_and_never_runtime_records():
    from domain_memory import memory_scope,read,write
    store={}
    with memory_scope(store):write('composer_private_watches.json',{'restored':True})
    with memory_scope(store,skip_checkpoint_only=True):
        write('composer_private_watches.json',{'updated':True})
        write('composer_signatures.json',{'active':'must remain readable'})
        assert read('composer_signatures.json')['active']=='must remain readable'
        with pytest.raises(RuntimeError):read('composer_private_watches.json')
    with memory_scope(store):assert read('composer_private_watches.json')=={'restored':True}

def tiny_capture(root):
    root.mkdir(parents=True)
    values=np.arange(150,dtype=float).reshape(3,50)
    child=wire.pack_v2('XAUUSD+','1m',[1756684680,1756684740,1756684800],[1,2,3],values,seq=1)
    (root/'pipe_000.bin').write_bytes(struct.pack('<IIII',0x4D535033,2,50,650)+struct.pack('<qiI',1756684800,31,len(child))+child)
    (root/'manifest.tsv').write_text('MSP3\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\npipe_feed\t0\t1m\tpipe_000.bin\t1\n')
    (root/'complete.txt').write_text('complete')

def test_build_replace_only_after_verification_and_restore(tmp_path_factory,monkeypatch):
    # This exercises successful recording/replacement, so use a short real
    # warehouse. Unsupported final path lengths have separate release tests.
    tmp_path=tmp_path_factory.mktemp('capture')
    from event_backtest import recording,build_plan,storage
    from generic_backtest.history import live_status
    monkeypatch.setattr(live_status,'running_processes',lambda:[])
    from event_backtest.warehouse import Warehouse
    warehouse=tmp_path/'warehouse';build=warehouse/'builds'/'test';build.mkdir(parents=True)
    (build/'ready.json').write_text('{}')
    monkeypatch.setattr(recording,'source_hash',lambda:'test');monkeypatch.setattr(build_plan,'current_build',lambda root:'ea')
    common=tmp_path/'common';monkeypatch.setattr(recording.native,'common_files_root',lambda:common)
    calls=[]
    @contextlib.contextmanager
    def installed(*a,**k):
        calls.append('stop')
        try:yield 'ea'
        finally:calls.append('restore')
    monkeypatch.setattr(recording,'installed_build',installed)
    monkeypatch.setattr(recording,'journal_positions',lambda p:{})
    monkeypatch.setattr(recording,'tick_evidence',lambda *a:{'actual':'REAL_TICKS','journal_lines':['XAUUSD+ real ticks used']})
    generation=[0]
    def launch(*a,**k):
        generation[0]+=1;session=str(generation[0]);path=common/'MosesDataBuild'/session;tiny_capture(path)
        return {'session':session,'export':str(path),'records':1,'elapsed_seconds':1}
    monkeypatch.setattr(recording.native,'run_native_tester',launch)
    s=scenario(start='2025-09-01',end='2025-10-01',strategies=['SPECIAL1'],overlap_trading_days=0)
    with pytest.raises(ConfirmationRequired):recording.prepare(s,warehouse,profile={})
    assert not calls
    def plan(rebuild=False):
        c=Warehouse(warehouse)
        try:return build_plan.make_plan(s,c,rebuild=rebuild)
        finally:c.close()
    p=plan();first=recording.prepare(s,warehouse,profile={},plan=p,approved_token=p['approval_token'])[0]
    old=warehouse/first['path'];assert old.is_dir() and calls==['stop','restore']
    assert not (common/'MosesDataBuild'/'1').exists()
    assert recording.prepare(s,warehouse,profile={})==[first] and len(calls)==2
    p=plan(True);original=storage.verify_indexed
    monkeypatch.setattr(storage,'verify_indexed',lambda *a,**kw:(_ for _ in ()).throw(ValueError('corrupt candidate')))
    with pytest.raises(ValueError,match='corrupt candidate'):recording.prepare(s,warehouse,profile={},plan=p,approved_token=p['approval_token'],rebuild=True)
    c=Warehouse(warehouse);assert c.find_capture(first['capture_id'])==first;c.close()
    assert old.exists() and calls[-1]=='restore'
    monkeypatch.setattr(storage,'verify_indexed',original)
    p=plan(True);second=recording.prepare(s,warehouse,profile={},plan=p,approved_token=p['approval_token'],rebuild=True)[0]
    assert second['path']!=first['path'] and old.exists()
    c=Warehouse(warehouse);assert c.find_capture(first['capture_id'])==second;c.close()
    moved=tmp_path/'moved';shutil.copytree(warehouse,moved)
    c=Warehouse(moved);restored=c.find_capture(second['capture_id']);c.close()
    assert restored==second
    assert list(storage.bundles(moved/second['path']))==list(storage.bundles(warehouse/second['path']))

def test_gui_default_rebuild_off_and_confirmation_before_run(monkeypatch,tmp_path):
    import tkinter as tk
    from event_backtest import gui,ui_model
    from event_backtest.segment_control import Segments
    from modern_widgets import ModernButton
    monkeypatch.setattr(gui,'ROOT',tmp_path);(tmp_path/'Part2').mkdir()
    monkeypatch.setattr(gui,'settings',lambda:{'warehouse':str(tmp_path/'warehouse')})
    monkeypatch.setattr(ui_model,'load',lambda:{'target_mode':'SPECIAL','specials':{'SPECIAL1':{'enabled':True}},'watch':{'text':'','chat_id':'BACKTEST'}})
    monkeypatch.setattr(ui_model,'save',lambda *a,**kw:None)
    root=tk.Tk();root.withdraw()
    monkeypatch.setattr(gui.tk,'Tk',lambda:root)
    calls=[];confirmed=[]
    plan={'record':[{'start':'2025-09-01','end':'2025-10-01'}],
          'estimate':{'recording_seconds':244,'temporary_msp3_bytes':29_000_000_000,'recommended_free_bytes':60_000_000_000},'approval_token':'approved'}
    class Process:
        def __init__(self,args,**kw):
            calls.append(args)
            if args[6]=='run':assert confirmed and '--approved-token' in args
            self.stdout=[json.dumps({'event':'COMPLETE','result':plan if args[6]=='plan' else {'alerts_csv':'runs/test/alerts.csv'}})+'\n']
        def wait(self):return 0
    monkeypatch.setattr(gui.subprocess,'Popen',Process)
    monkeypatch.setattr(gui.messagebox,'askyesno',lambda *a:(confirmed.append(True) or True))
    def descendants(widget):
        for child in widget.winfo_children():yield child;yield from descendants(child)
    def click():
        widgets=list(descendants(root))
        box=next(w for w in widgets if isinstance(w,Segments) and len(w.choices)==3)
        assert box.variable.get()=='ALERT_ONLY'
        next(w for w in widgets if isinstance(w,ModernButton) and '백테스트 실행' in w.cget('text')).invoke()
    # Real Tcl event scheduling tests the actual plan -> dialog -> run callbacks.
    root.after(20,click);root.after(1200,root.destroy)
    gui.main()
    assert [args[6] for args in calls]==['plan','run']

def test_public_workflow_history_gap_never_calls_runner(tmp_path,monkeypatch):
    from event_backtest import workflow,recording,runner
    monkeypatch.setattr(runner,'runtime_config',lambda s:{})
    monkeypatch.setattr(workflow,'proposal',lambda *a,**k:{'record':[],'approval_token':'same'})
    monkeypatch.setattr(recording,'prepare',lambda *a,**k:[{'history_missing':[{'start':'2025-09-01','end':'2025-10-01','reason':'missing'}]}])
    monkeypatch.setattr(runner,'run',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not replay')))
    with pytest.raises(HistoryMissing):workflow.execute({'strategies':['SPECIAL1']},tmp_path)
