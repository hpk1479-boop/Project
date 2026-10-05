from pathlib import Path
from types import SimpleNamespace
import hashlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generic_backtest import gui
from generic_backtest.duckdb_gui import EXECUTION_MT5, EXECUTION_DUCKDB, EXECUTION_BUILD
from generic_backtest.canonical import identity, write_json, read_json
from generic_backtest.history.cache import RawChunkWriter
from pit.archive.reader import TICK_DTYPE


def make_archive(root, *, server='TEST-SERVER', symbol='XAUUSD.TEST'):
    root=Path(root);instrument={'broker_symbol':symbol,'server_fingerprint':identity(server),
        'chart_mode':'BID','digits':2,'point':0.01,'trade_tick_size':0.01,
        'description':'test','metadata_revision':'test'}
    stream=identity(('BROKER_STREAM',instrument['server_fingerprint'],symbol))
    rows=np.zeros(2,dtype=TICK_DTYPE);rows['time_msc']=[1000,1100];rows['time']=1
    rows['bid']=[100.,100.1];rows['ask']=[100.1,100.2];rows['last']=rows['bid']
    rows['volume']=1;rows['volume_real']=1.;rows['flags']=2
    writer=RawChunkWriter(root,stream);desc=writer.write(0,rows,1_000_000_000,1_200_000_000)
    m={'kind':'GENERIC_RAW_ARCHIVE_V1','status':'READY','instrument':instrument,
        'stream_namespace':stream,'coverage_start_ns':1_000_000_000,'coverage_end_ns':1_200_000_000,
        'count':2,'raw_sha256':hashlib.sha256(rows.tobytes()).hexdigest(),'chunks':[desc],'gaps':[],
        'coverage':{'completeness':'UNVERIFIED','broker_history_completeness':'UNVERIFIED'},
        'input_kind':'BROKER_REAL_TICKS','source_build':[5,0,'test']}
    m['archive_identity']=identity(m);write_json(root/'manifest.json',m)
    return m


def test_data_build_rejects_wrong_server_before_database_open(tmp_path):
    from generic_backtest.data_build import import_archive_to_duckdb
    make_archive(tmp_path/'archive',server='SERVER-A')
    with pytest.raises(ValueError,match='DATA_BUILD_SERVER_IDENTITY_MISMATCH'):
        import_archive_to_duckdb(tmp_path/'archive',tmp_path/'x.duckdb',{'broker':'B','server':'SERVER-B'})


def test_data_build_store_contract_without_duckdb_runtime(tmp_path, monkeypatch):
    from generic_backtest.data_build import import_archive_to_duckdb
    manifest=make_archive(tmp_path/'archive')
    calls=[]
    class FakeStore:
        def __init__(self,path,*,create=False):calls.append(('open',Path(path),create))
        def __enter__(self):return self
        def __exit__(self,*args):calls.append(('close',))
        def add_source(self,broker,server,symbol,instrument):
            calls.append(('source',broker,server,symbol,instrument));return 'SID'
        def checkpoint_file(self):calls.append(('checkpoint',))
    import data_warehouse.store as store_mod
    import data_warehouse.ticks as tick_mod
    monkeypatch.setattr(store_mod,'Store',FakeStore)
    monkeypatch.setattr(tick_mod,'import_archive',lambda store,root,sid:
        calls.append(('import',Path(root),sid)) or {'archive_id':'AID','status':'PASS','count':2,'raw_sha256':manifest['raw_sha256']})
    result=import_archive_to_duckdb(tmp_path/'archive',tmp_path/'x.duckdb',{'broker':'BROKER','server':'TEST-SERVER'})
    assert result['status']=='PASS' and result['source_id']=='SID' and result['symbol']=='XAUUSD.TEST'
    assert [c[0] for c in calls]==['open','source','import','checkpoint','close']


@pytest.fixture
def tk_root():
    import tkinter as tk
    try:root=tk.Tk()
    except tk.TclError as exc:pytest.skip(str(exc))
    yield root
    root.destroy()


@pytest.fixture
def panel(tk_root,tmp_path):
    p=gui.GenericPanel(tk_root,auto_connect=False,start_poll=False,log_directory=tmp_path/'log')
    p.frame.pack(fill='both',expand=True);tk_root.update();yield p


def test_three_exclusive_execution_modes_are_visible(panel):
    assert tuple(panel.execution_choice.cget('values'))==(EXECUTION_MT5,EXECUTION_DUCKDB,EXECUTION_BUILD)
    assert panel.execution_mode.get()==EXECUTION_MT5


def test_data_build_run_never_starts_backtest(panel,monkeypatch):
    panel.execution_mode.set(EXECUTION_BUILD);called=[]
    monkeypatch.setattr(panel,'_begin_data_build',lambda:called.append('build') or True)
    monkeypatch.setattr(panel,'_config_from_controls',lambda:pytest.fail('strategy config must not run in data build'))
    panel.run()
    assert called==['build'] and panel.running


def test_mt5_run_uses_original_market_watch_path(panel,monkeypatch):
    panel.execution_mode.set(EXECUTION_MT5);calls=[]
    monkeypatch.setattr(panel,'_config_from_controls',lambda:{'evaluation_mode':'TICK'})
    monkeypatch.setattr(panel,'request_market_watch',lambda reason,after=None:calls.append((reason,after)))
    monkeypatch.setattr(panel,'_begin_duckdb_run',lambda:pytest.fail('DuckDB path must not run'))
    panel.run()
    assert calls and calls[0][0]=='run' and calls[0][1]==panel._run_selected_symbol


def test_mt5_plan_runs_strategy_tester_before_history_prepare(panel,monkeypatch,tmp_path):
    panel.execution_mode.set(EXECUTION_MT5)
    panel.job=tmp_path/'job';panel.job.mkdir()
    panel.run_symbol='XAUUSD.TEST'
    panel.config={'instrument':{'broker_symbol':'XAUUSD.TEST'},'start_ns':17,'end_ns':22}
    panel.profile=tmp_path/'profile.json';panel.profile.write_text('{}',encoding='utf-8')
    launched=[]
    monkeypatch.setattr(panel,'launch',lambda module,args,done,history=False,phase=None:
        launched.append((module,list(map(str,args)),done,history,phase)))
    panel.plan_done({'start_ns':11,'end_ns':22})
    assert len(launched)==1
    module,args,done,history,phase=launched[0]
    assert module=='generic_backtest' and args[0]=='native-backtest'
    assert args[args.index('--symbol')+1]=='XAUUSD.TEST'
    assert args[args.index('--start-ns')+1]=='17' and args[args.index('--end-ns')+1]=='22'
    assert read_json(panel.job/'seed_plan.json')['start_ns']==11
    assert done==panel.mt5_strategy_tester_done and history is False and phase=='MT5_STRATEGY_TESTER'


def test_mt5_strategy_tester_pass_is_recorded_then_history_prepare_starts(panel,monkeypatch,tmp_path):
    panel.execution_mode.set(EXECUTION_MT5)
    panel.job=tmp_path/'job';panel.job.mkdir()
    panel.config={'instrument':{'broker_symbol':'XAUUSD.TEST'},'start_ns':17,'end_ns':22}
    panel.profile=tmp_path/'profile.json';panel.profile.write_text('{}',encoding='utf-8')
    write_json(panel.job/'seed_plan.json',{'start_ns':11,'end_ns':22})
    launched=[]
    monkeypatch.setattr(panel,'launch',lambda module,args,done,history=False,phase=None:
        launched.append((module,list(map(str,args)),done,history,phase)))
    panel.mt5_strategy_tester_done({'status':'PASS','runtime':'MT5_STRATEGY_TESTER','expert':'THE_STAFF_OF_MOSES',
        'symbol':'XAUUSD.TEST','session':'S','percentile_families':['PRICE','RSI','STO','DI'],
        'feeds':19,'rows':123,'payload_sha256':'a'*64,'elapsed_seconds':1.5,'export_removed':False,
        'export':str(tmp_path/'native'),'start_ns':11,'end_ns':22})
    receipt=panel.config['resources']['mt5_strategy_tester']
    assert receipt['status']=='PASS' and receipt['percentile_families']==['PRICE','RSI','STO','DI']
    assert panel.config['resources']['max_history_bars']==100000
    assert panel.config['resources']['native_export']['path']==str(tmp_path/'native')
    assert panel.config['resources']['conditional_specials'] is False
    module,args,done,history,phase=launched[-1]
    assert module=='generic_backtest.history.cli' and args[0]=='prepare'
    assert args[args.index('--start-ns')+1]=='17' and args[args.index('--end-ns')+1]=='22'
    assert '--seed-config' in args and '--seed-output' in args
    assert done==panel.prepared and history is True and phase=='FETCH'


def test_mt5_strategy_tester_failure_cannot_fall_through_to_python(panel,monkeypatch,tmp_path):
    panel.job=tmp_path/'job';panel.job.mkdir();panel.config={'instrument':{'broker_symbol':'XAUUSD.TEST'}}
    write_json(panel.job/'seed_plan.json',{'start_ns':11,'end_ns':22})
    monkeypatch.setattr(panel,'launch',lambda *a,**k:pytest.fail('history prepare must not start'))
    with pytest.raises(ValueError,match='Strategy Tester native'):
        panel.mt5_strategy_tester_done({'status':'FAIL','runtime':'MT5_STRATEGY_TESTER'})


def test_duckdb_run_never_requests_mt5(panel,monkeypatch):
    panel.execution_mode.set(EXECUTION_DUCKDB);calls=[]
    monkeypatch.setattr(panel,'_config_from_controls',lambda:{'evaluation_mode':'TICK'})
    monkeypatch.setattr(panel,'_begin_duckdb_run',lambda:calls.append('duckdb') or True)
    monkeypatch.setattr(panel,'request_market_watch',lambda *a,**k:pytest.fail('DuckDB backtest must not request MT5'))
    panel.run()
    assert calls==['duckdb'] and panel.duckdb_enabled.get() is True

def test_duckdb_mode_plan_probe_run_chain_uses_no_mt5(panel,monkeypatch,tmp_path):
    panel.execution_mode.set(EXECUTION_DUCKDB);panel.duckdb_enabled.set(True)
    panel.duckdb_path.set(str(tmp_path/'source.duckdb'));panel.symbol.set('XAUUSD.TEST')
    instrument={'broker_symbol':'XAUUSD.TEST','server_fingerprint':'S','chart_mode':'BID','digits':2,'point':.01,
                'trade_tick_size':.01,'description':'test','metadata_revision':'m'}
    panel._duckdb_rows=[{'source_id':'SID','broker':'B','server':'S','symbol':'XAUUSD.TEST','instrument':instrument}]
    panel.config={'resources':{},'instrument':{},'start_ns':1,'end_ns':2,'evaluation_mode':'TICK'}
    monkeypatch.setattr(panel,'_duckdb_symbols',lambda reason:True)
    launched=[]
    monkeypatch.setattr(panel,'launch',lambda module,args,done,history=False,phase=None:launched.append((module,list(map(str,args)),done,history,phase)))
    monkeypatch.setattr(panel,'request_market_watch',lambda *a,**k:pytest.fail('MT5 must not be requested'))
    assert panel._begin_duckdb_run() is True
    assert panel.config['resources']['duckdb_enabled'] is True
    assert panel.config['resources']['duckdb_source_id']=='SID'
    assert panel.config['instrument']==instrument
    assert launched[-1][0]=='generic_backtest' and launched[-1][1][0]=='plan' and launched[-1][4]=='PLAN'

    launched.clear()
    panel._duckdb_plan_done({'start_ns':1,'end_ns':2})
    assert launched[-1][1][0]=='duckdb-probe' and launched[-1][4]=='DUCKDB_PROBE'

    launched.clear()
    panel._duckdb_prepared({'status':'PASS','input_kind':'BROKER_REAL_TICKS','archive_id':'AID'})
    assert panel.config['resources']['duckdb_archive_id']=='AID'
    assert launched[-1][1][0]=='run' and launched[-1][4]=='ARCHIVE_VERIFY'

def test_duckdb_probe_failure_never_falls_back_to_mt5(panel,monkeypatch):
    panel.execution_mode.set(EXECUTION_DUCKDB);panel.duckdb_enabled.set(True)
    panel._operation_phase='DUCKDB_PROBE';panel.cancel_requested=False;panel.active_job_id='J'
    panel._duckdb_run_active=True
    monkeypatch.setattr(panel,'request_market_watch',lambda *a,**k:pytest.fail('DuckDB failure must not request MT5'))
    assert panel._duckdb_failed_exit({'error':'BROKEN_DB'}) is False
    assert panel._duckdb_run_active is False


def test_duckdb_raw_source_is_strict_and_has_no_runtime_archive_fallback(monkeypatch,tmp_path):
    from generic_backtest import hybrid
    import data_warehouse.ticks as ticks_mod
    monkeypatch.setattr(ticks_mod,'TickArchiveSnapshot',lambda *a,**k:(_ for _ in ()).throw(ValueError('BROKEN_DB')))
    class RuntimeReader:
        def __init__(self,*a,**k):pytest.fail('runtime archive fallback must not run')
    config=SimpleNamespace(resources={'duckdb_enabled':True,'duckdb_strict':True,'duckdb_path':str(tmp_path/'x.duckdb')},
        archive='',instrument={'broker_symbol':'X'},end_ns=2)
    (tmp_path/'x.duckdb').touch()
    with pytest.raises(ValueError,match='DUCKDB_STRICT_SOURCE_FAILURE'):
        hybrid.select_archive(config,0,runtime_reader=RuntimeReader)
