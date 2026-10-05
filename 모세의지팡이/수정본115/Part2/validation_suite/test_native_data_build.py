from pathlib import Path
import hashlib
import struct
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_warehouse import native


def make_export(root, symbol='XAUUSD.TEST', tf='1m', count=3):
    root=Path(root);root.mkdir(parents=True)
    rows=np.zeros(count,dtype=native.RECORD_DTYPE)
    rows['observed_time']=np.arange(100,100+count)
    rows['bar_time']=60
    for i,name in enumerate(native.VALUE_COLUMNS):rows[name]=i+np.arange(count)/10
    feed=root/'feed_000.bin'
    with feed.open('wb') as f:
        f.write(native.HEADER.pack(native.MAGIC,native.VERSION,len(native.VALUE_COLUMNS),native.RECORD_BYTES))
        f.write(rows.tobytes())
    session='abcdef1234567890'
    (root/'manifest.tsv').write_text('\n'.join([
        native.REQUEST_MAGIC,f'session\t{session}',f'symbol\t{symbol}',f'format_version\t{native.VERSION}',
        f'value_columns\t{len(native.VALUE_COLUMNS)}','sampling_policy\tMINUTE_OR_STATE_CHANGE_V1',f'feed\t0\t{tf}\tfeed_000.bin\t{count}','']),encoding='ascii')
    (root/'complete.txt').write_text(native.REQUEST_MAGIC+'\n'+session+'\n',encoding='ascii')
    return rows


def test_native_export_binary_roundtrip(tmp_path):
    expected=make_export(tmp_path/'x')
    parsed=native.parse_export(tmp_path/'x')
    assert parsed['symbol']=='XAUUSD.TEST' and len(parsed['feeds'])==1
    got=np.concatenate(list(native.iter_feed_blocks(parsed['feeds'][0])))
    assert got.dtype==native.RECORD_DTYPE
    assert got.tobytes()==expected.tobytes()
    assert native.RECORD_BYTES==16+8*len(native.VALUE_COLUMNS)


def test_native_export_exact_range_filter(tmp_path):
    make_export(tmp_path/'x',count=5)
    feed=native.parse_export(tmp_path/'x')['feeds'][0]
    got=np.concatenate(list(native.iter_feed_blocks(feed,start_s=102,end_s=104)))
    assert got['observed_time'].tolist()==[102,103]


def test_native_export_rejects_truncated_file(tmp_path):
    make_export(tmp_path/'x')
    p=tmp_path/'x'/'feed_000.bin';p.write_bytes(p.read_bytes()[:-1])
    with pytest.raises(ValueError,match='NATIVE_FILE_SIZE_MISMATCH'):
        native.parse_export(tmp_path/'x')


def test_native_export_rejects_non_monotonic_time(tmp_path):
    rows=make_export(tmp_path/'x')
    rows['observed_time']=[100,99,101]
    p=tmp_path/'x'/'feed_000.bin'
    with p.open('wb') as f:
        f.write(native.HEADER.pack(native.MAGIC,native.VERSION,len(native.VALUE_COLUMNS),native.RECORD_BYTES));f.write(rows.tobytes())
    feed=native.parse_export(tmp_path/'x')['feeds'][0]
    with pytest.raises(ValueError,match='NATIVE_TIME_ORDER'):
        list(native.iter_feed_blocks(feed))


def test_tester_config_uses_real_ticks_and_unified_staff(tmp_path):
    from generic_backtest.native_mt5 import write_tester_config
    profile={'data_root':str(tmp_path/'data')}
    p=write_tester_config(tmp_path/'tester.ini',profile,'XAUUSD',0,2*86400*10**9,'THE_STAFF_OF_MOSES')
    text=p.read_text('ascii')
    assert 'Expert=THE_STAFF_OF_MOSES' in text
    assert 'Symbol=XAUUSD' in text and 'Period=M1' in text
    assert 'Model=4' in text and 'Optimization=0' in text and 'Visual=0' in text
    p2=write_tester_config(tmp_path/'tester_shutdown.ini',profile,'XAUUSD',0,2*86400*10**9,'THE_STAFF_OF_MOSES',shutdown_terminal=True)
    assert 'ShutdownTerminal=1' in p2.read_text('ascii')


def test_selected_two_days_are_not_expanded_to_seed_period(tmp_path):
    from datetime import datetime,timezone
    from generic_backtest.native_mt5 import write_tester_config,_write_request
    ns=lambda date:int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())*10**9
    start,end=ns('2026-09-22T15:00:00'),ns('2026-09-24T15:00:00')
    path=write_tester_config(tmp_path/'test.ini',{},'XAUUSD+',start,end,'STAFF')
    text=path.read_text('ascii')
    assert 'FromDate=2026.09.22' in text
    assert 'ToDate=2026.09.25' in text  # cover final partial UTC day
    assert '2024' not in text
    request=_write_request(tmp_path/'common','test_session','XAUUSD+',start,end)
    lines=request.read_text('ascii').splitlines()
    assert list(map(int,lines[3:]))==[start//10**9,end//10**9]
    assert int(lines[4])-int(lines[3])==2*86400


def test_native_backtest_gate_requires_tester_and_retains_export(monkeypatch,tmp_path):
    from generic_backtest import native_mt5
    export=tmp_path/'export';export.mkdir();(export/'payload.bin').write_bytes(b'x')
    monkeypatch.setattr(native,'parse_export',lambda p:{'payload_sha256':'f'*64})
    calls=[]
    exe=tmp_path/'terminal64.exe';exe.write_bytes(b'')
    profile={'pid':10,'created_at':20,'executable':str(exe),'data_root':str(tmp_path/'data')}
    refreshed={**profile,'pid':30,'created_at':40}
    monkeypatch.setattr(native_mt5,'ensure_native_mql_current',lambda value,work_dir,**kwargs:tmp_path/'THE_STAFF_OF_MOSES.ex5')
    monkeypatch.setattr(native_mt5,'locate_compiled_expert',lambda value:tmp_path/'THE_STAFF_OF_MOSES.ex5')
    monkeypatch.setattr(native_mt5,'common_files_root',lambda:tmp_path/'common')
    monkeypatch.setattr(native_mt5,'_stop_selected_terminal',
        lambda value,**kwargs:calls.append(('stop',dict(value))))
    monkeypatch.setattr(native_mt5,'_restart_selected_terminal',
        lambda value,**kwargs:calls.append(('restart',dict(value))) or dict(refreshed))
    def fake_tester(value,symbol,start_ns,end_ns,work_dir,**kwargs):
        calls.append(('tester',dict(value),symbol,start_ns,end_ns,Path(work_dir),kwargs.get('phase'),
                      kwargs.get('shutdown_terminal'),kwargs.get('start_timeout')))
        return {'session':'S','export':str(export),'symbol':symbol,'elapsed_seconds':2.0,
                'feeds':19,'rows':321,'payload_sha256':'f'*64}
    monkeypatch.setattr(native_mt5,'run_native_tester',fake_tester)
    persisted=[]
    result=native_mt5.run_native_backtest_gate(profile,'XAUUSD.TEST',1,2,tmp_path/'work',on_profile_restarted=lambda value:persisted.append(dict(value)))
    assert calls[0]==('stop',profile)
    assert calls[1]==('tester',profile,'XAUUSD.TEST',1,2,tmp_path/'work','MT5_STRATEGY_TESTER',True,90)
    assert calls[2]==('restart',profile)
    assert result['status']=='PASS' and result['runtime']=='MT5_STRATEGY_TESTER'
    assert tuple(result['percentile_families'])==('PRICE','RSI','STO','DI')
    assert result['feeds']==19 and result['rows']==321 and result['export_removed'] is False
    assert (Path(result['export'])/'payload.bin').read_bytes()==b'x'
    assert result['restarted_profile']==refreshed and persisted==[refreshed]
    assert not export.exists()


def test_native_backtest_gate_restores_terminal_when_tester_fails(monkeypatch,tmp_path):
    from generic_backtest import native_mt5
    calls=[]
    exe=tmp_path/'terminal64.exe';exe.write_bytes(b'')
    profile={'pid':10,'created_at':20,'executable':str(exe),'data_root':str(tmp_path/'data')}
    monkeypatch.setattr(native_mt5,'ensure_native_mql_current',lambda value,work_dir,**kwargs:tmp_path/'THE_STAFF_OF_MOSES.ex5')
    monkeypatch.setattr(native_mt5,'locate_compiled_expert',lambda value:tmp_path/'THE_STAFF_OF_MOSES.ex5')
    monkeypatch.setattr(native_mt5,'common_files_root',lambda:tmp_path/'common')
    monkeypatch.setattr(native_mt5,'_stop_selected_terminal',
        lambda value,**kwargs:calls.append('stop'))
    monkeypatch.setattr(native_mt5,'_restart_selected_terminal',
        lambda value,**kwargs:calls.append('restart') or {**profile,'pid':30,'created_at':40})
    def fail(*args,**kwargs):
        calls.append('tester')
        raise ValueError('NATIVE_TESTER_DID_NOT_START')
    monkeypatch.setattr(native_mt5,'run_native_tester',fail)
    persisted=[]
    with pytest.raises(ValueError,match='NATIVE_TESTER_DID_NOT_START'):
        native_mt5.run_native_backtest_gate(profile,'XAUUSD.TEST',1,2,tmp_path/'work',on_profile_restarted=lambda value:persisted.append(dict(value)))
    assert calls==['stop','tester','restart']
    assert persisted and persisted[0]['pid']==30


def test_build_native_wrapper_keeps_import_separate(monkeypatch,tmp_path):
    from generic_backtest import data_build
    calls=[]
    def fake_tester(*a,**k):
        calls.append(('tester',a[1],a[2],a[3]))
        (tmp_path/'export').mkdir(exist_ok=True)
        return {'session':'S','export':str(tmp_path/'export'),'feeds':19,'elapsed_seconds':1.0}
    monkeypatch.setattr('generic_backtest.native_mt5.run_native_tester',fake_tester)
    class FakeStore:
        def __init__(self,path):calls.append(('open',Path(path)))
        def __enter__(self):return self
        def __exit__(self,*a):calls.append(('close',))
        def checkpoint_file(self):calls.append(('checkpoint',))
    monkeypatch.setattr('data_warehouse.store.Store',FakeStore)
    monkeypatch.setattr('data_warehouse.native.import_native_export',lambda store,root,sid,**k:
        calls.append(('import',Path(root),sid,k)) or {'status':'PASS','rows':7})
    result=data_build.build_native_to_duckdb({},'XAUUSD',1_100_000_000,3_200_000_000,tmp_path/'x.duckdb','SID',tmp_path/'work')
    assert result['status']=='PASS' and result['rows']==7 and result['feeds']==19 and result['export_removed'] is True
    assert calls[0][0]=='tester' and calls[1][0]=='open' and calls[2][0]=='import'
    assert calls[2][3]=={'requested_start':2,'requested_end':4}


def test_unified_staff_native_export_is_backtest_only():
    staff=(Path(__file__).resolve().parents[2]/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8-sig')
    assert 'STAFF_NATIVE_REQUEST_FILE' in staff
    assert 'NativeBeginExport()' in staff and 'NativeWriteSnapshot(i,g_backtest_inputs[i])' in staff
    live_publish=staff[staff.index('bool PublishFeed('):staff.index('// BACKTEST INPUT SNAPSHOT')]
    assert 'NativeWriteSnapshot' not in live_publish and 'MosesDataBuild' not in live_publish
    timer=staff[staff.index('void OnTimer()'):]
    assert 'if(StaffBacktestRuntime()) return;' in timer


def test_data_build_tick_import_chains_native_before_completion(tmp_path):
    from generic_backtest.duckdb_gui import DuckDBPanelMixin
    calls=[]
    class Dummy:
        _data_build_context={'start_ns':1,'end_ns':2,'database':str(tmp_path/'x.duckdb')}
        run_symbol='XAUUSD'
        job=tmp_path/'job'
        def _log(self,msg):calls.append(('log',msg))
        def profile_path(self):return tmp_path/'profile.json'
        def launch(self,module,args,done,history=False,phase=None):calls.append(('launch',module,list(map(str,args)),done,phase))
        _data_build_native_completed=lambda self,result:None
    d=Dummy()
    DuckDBPanelMixin._data_build_completed(d,{'source_id':'SID','count':99})
    launch=[c for c in calls if c[0]=='launch'][0]
    assert launch[1]=='generic_backtest' and launch[2][0]=='native-build' and launch[4]=='DATA_BUILD_NATIVE'
    assert '--source-id' in launch[2] and launch[2][launch[2].index('--source-id')+1]=='SID'
    assert d._data_build_context['tick_result']['count']==99


def test_native_mql_deploy_compiles_current_staff_and_four_indicators(monkeypatch,tmp_path):
    from generic_backtest import native_mt5
    source=tmp_path/'project_mt5';source.mkdir()
    names=list(native_mt5.NATIVE_MQL_SOURCES)+['STAFF_Identity_Status.mqh','STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh']
    for name in names:(source/name).write_text('// '+name,encoding='utf-8')
    terminal=tmp_path/'terminal64.exe';terminal.write_bytes(b'x')
    editor=tmp_path/'MetaEditor64.exe';editor.write_bytes(b'x')
    data=tmp_path/'data'
    profile={'executable':str(terminal),'data_root':str(data)}
    monkeypatch.setattr(native_mt5,'_project_mt5_source_root',lambda:source)
    compiled=[]
    def fake_compile(metaeditor,src,mql5_root,work_dir):
        src=Path(src);out=src.with_suffix('.ex5');out.write_bytes(b'ex5');compiled.append(src)
        return out
    monkeypatch.setattr(native_mt5,'_compile_one',fake_compile)
    messages=[]
    expert=native_mt5.ensure_native_mql_current(profile,tmp_path/'work',emit=lambda kind,payload:messages.append(payload['message_code']))
    assert expert==data/'MQL5'/'Experts'/'THE_STAFF_OF_MOSES.ex5'
    assert [p.name for p in compiled]==list(native_mt5.NATIVE_MQL_SOURCES)
    assert (data/'MQL5'/'Experts'/'THE_STAFF_OF_MOSES.mq5').read_text()=='// THE_STAFF_OF_MOSES.mq5'
    assert (data/'MQL5'/'Indicators'/'PRICE_of_Moses.mq5').read_text()=='// PRICE_of_Moses.mq5'
    for name in ('STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh'):
        assert (data/'MQL5'/'Experts'/name).read_text()=='// '+name
    assert messages==['NATIVE_MQL_SYNC_START','NATIVE_MQL_SYNC_DONE']


def test_current_staff_tester_path_does_not_use_named_pipe():
    staff=(Path(__file__).resolve().parents[2]/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8-sig')
    backtest=staff[staff.index('int BacktestOnInit()'):staff.index('int OnInit()')]
    assert 'NativeBeginExport()' in backtest
    assert 'EnsureStaffPipe' not in backtest and 'WritePipeSnapshot' not in backtest
    assert 'if(StaffBacktestRuntime())' in staff[staff.index('int OnInit()'):]


def test_started_export_does_not_wait_forever_after_terminal_exit(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from generic_backtest import native_mt5 as m
    exe=tmp_path/'terminal64.exe';exe.touch()
    common=tmp_path/'common';output=common/'MosesDataBuild'/'test_session'
    output.mkdir(parents=True);(output/'started.txt').touch()
    monkeypatch.setattr(m.uuid,'uuid4',lambda:SimpleNamespace(hex='test_session'))
    monkeypatch.setattr(m,'common_files_root',lambda:common)
    monkeypatch.setattr(m,'locate_compiled_expert',lambda p:tmp_path/'staff.ex5')
    monkeypatch.setattr(m,'tester_expert_name',lambda p,e:'staff')
    # The directory is created by the EA only after Popen in a real run.
    (output/'started.txt').unlink();output.rmdir()
    def launch(*a,**k):
        output.mkdir();(output/'started.txt').touch()
        return SimpleNamespace(poll=lambda:0)
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    clock=[0.0]
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(m.time,'sleep',lambda s:clock.__setitem__(0,clock[0]+s))
    events=[]
    def cancel():
        assert clock[0]<10, 'Exited tester was left waiting indefinitely'
    with pytest.raises(ValueError,match='NATIVE_TESTER_INCOMPLETE'):
        m.run_native_tester({'executable':str(exe)},'TEST',1,2,tmp_path/'work',
                            emit=lambda k,p:events.append(p),cancel=cancel)
    assert sum(p['message_code']=='NATIVE_EXPORT_ACTIVE' for p in events)==1
    assert any(p['message_code']=='NATIVE_EXPORT_PROGRESS' for p in events)
    assert not (common/m.REQUEST_REL).exists()


@pytest.mark.parametrize('counts', [[],[0],[1,0]])
def test_completed_export_requires_rows_in_every_feed(monkeypatch,tmp_path,counts):
    from types import SimpleNamespace
    from generic_backtest import native_mt5 as m
    exe=tmp_path/'terminal64.exe';exe.touch()
    common=tmp_path/'common'
    monkeypatch.setattr(m.uuid,'uuid4',lambda:SimpleNamespace(hex='test_session'))
    monkeypatch.setattr(m,'common_files_root',lambda:common)
    monkeypatch.setattr(m,'locate_compiled_expert',lambda p:tmp_path/'staff.ex5')
    monkeypatch.setattr(m,'tester_expert_name',lambda p,e:'staff')
    def launch(*a,**k):
        output=common/'MosesDataBuild'/'test_session'
        output.mkdir();(output/'complete.txt').touch()
        return SimpleNamespace(poll=lambda:0)
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    monkeypatch.setattr(native,'parse_export',lambda p:{'symbol':'TEST',
        'feeds':[{'count':n} for n in counts],'payload_sha256':'x'})
    with pytest.raises(ValueError,match='NATIVE_EXPORT_EMPTY'):
        m.run_native_tester({'executable':str(exe)},'TEST',1,2,tmp_path/'work')
