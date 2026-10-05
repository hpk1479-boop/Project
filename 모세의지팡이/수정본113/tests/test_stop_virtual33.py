from pathlib import Path
from types import SimpleNamespace
import csv,json,sys,threading
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.cancellation import Cancelled,file_check,coverage
from event_backtest.virtual_entry import calculate,RATIOS

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def fail(*a,**kw):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'sendto',fail)

def alerts(path,n=2):
    from test_virtual_entry import alert
    rows=[{**alert(), 'signal_id':str(i),'b0_time':-60} for i in range(n)]
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['signal_id','strategy','symbol','tf','direction','time_ms','b0_price','b0_time'])
        w.writeheader();w.writerows(rows)
    return rows

def inputs(monkeypatch):
    from test_virtual_entry import view
    from event_backtest import virtual_source
    read=[]
    def stream(*a,check=lambda:None,**kw):
        rows=[(0,10,10,9,9,8),(60,10,30,7,9,8),(120,10,11,9,9,8),(180,10,11,9,9,8)]
        for i,row in enumerate(rows):
            check();read.append(row[0]);yield row[0]*1000,{('XAUUSD+','1m'):view(rows[:i+1])}
    monkeypatch.setattr(virtual_source,'shared_observations',stream)
    return read

def test_no_alerts_does_not_read(tmp_path,monkeypatch):
    read=inputs(monkeypatch);alerts(tmp_path/'alerts.csv',0)
    result=calculate(tmp_path/'alerts.csv',[],tmp_path,{'start':'1970-01-01','end':'1970-01-02','symbol':'XAUUSD+','strategies':['SPECIAL1']},{'POINT_XAUUSD+':'.01'},tmp_path)
    assert not read and result['processed_signals']==0

def test_all_rr_closed_stops_each_read(tmp_path,monkeypatch):
    read=inputs(monkeypatch);alerts(tmp_path/'alerts.csv')
    r=calculate(tmp_path/'alerts.csv',[],tmp_path,{'start':'1970-01-01','end':'1970-01-02','symbol':'XAUUSD+','strategies':['SPECIAL1']},{'POINT_XAUUSD+':'.01'},tmp_path)
    assert read==[0,60,120]  # both signals share each observation
    assert r['processed_signals']==2 and all(x['losses']==2 for x in r['summary'])

def test_virtual_stop_preserves_current_trade(tmp_path,monkeypatch):
    inputs(monkeypatch);alerts(tmp_path/'alerts.csv')
    stop=tmp_path/'stop.request';events=[]
    def emit(kind,data):
        events.append(kind)
        if kind=='VIRTUAL_ENTRY_PROGRESS':stop.touch()
    r=calculate(tmp_path/'alerts.csv',[],tmp_path,{'start':'1970-01-01','end':'1970-01-02','symbol':'XAUUSD+','strategies':['SPECIAL1']},{'POINT_XAUUSD+':'.01'},tmp_path,emit=emit,cancel=file_check(stop))
    assert r['cancelled'] and r['observed_signals']==2 and r['processed_signals']==0
    assert len(list(csv.DictReader((tmp_path/'virtual_trades.csv').open(encoding='utf-8-sig'))))==18
    assert 'VIRTUAL_ENTRY_PROGRESS' in events

@pytest.mark.parametrize('stage',['CONVERSION_PROGRESS','VERIFY_START'])
def test_cancel_conversion_removes_unfinished_only(tmp_path,stage):
    from test_parallel_oz import keyframe_fixture
    from event_backtest.storage import convert
    _,_,source=keyframe_fixture(tmp_path);(source/'storage.json').write_text('{}')
    dest=tmp_path/'dest';complete=dest/'old';complete.mkdir(parents=True);(complete/'complete.txt').write_text('KEEP')
    stop=tmp_path/'stop'
    def emit(kind,data):
        if kind==stage:stop.touch()
    with pytest.raises(Cancelled):convert(source,dest,'new',cancel=file_check(stop),emit=emit)
    assert list(dest.iterdir())==[complete]

def test_partitions_and_disjoint_coverage():
    from event_backtest.partition import plan_periods
    assert plan_periods('2025-09-01','2025-10-01',4)[1]=='WEEK'
    assert len(plan_periods('2025-09-01','2025-10-01',12)[0])==30
    assert plan_periods('2025-09-01','2025-10-01',12,'MONTH',adaptive=False)[1]=='MONTH'
    rows=coverage([dict(task_start='a',processed_start_ms=0,processed_end_ms=1000),dict(task_start='b',processed_start_ms=9000,processed_end_ms=10000)])
    assert len(rows)==2 and rows[0]['last_observation']!=rows[1]['start']

def test_virtual_gauge_and_partial_not_complete():
    from event_backtest.progress_view import ProgressView
    p=ProgressView();p.accept({'event':'VIRTUAL_ENTRY_START','alerts':4})
    p.accept({'event':'VIRTUAL_ENTRY_PROGRESS','processed_alerts':2,'total_alerts':4,'percent':50,'elapsed_seconds':10})
    p.accept({'event':'COMPLETE','result':{'status':'CANCELLED'}})
    assert p.virtual==50 and p.phase=='중단됨(부분 결과)' and p.virtual_started

def test_missing_token_once_no_request(tmp_path):
    from module_diagnostics import Diagnostics
    from event_host import HTTPServices
    d=Diagnostics(tmp_path,trace=True)
    calls=[];d.log=lambda *a,**k:calls.append(a)
    h=HTTPServices({'TELEGRAM_TOKEN':'  '},None,d)
    h.telegram_loop(None,threading.Event());h.telegram_loop(None,threading.Event())
    assert len(calls)==1 and calls[0][2]=='텔레그램 토큰 미설정'
    assert not d.telegram_attempted

def test_projection_matches_full_row_heartbeat_keyframe(tmp_path):
    from test_parallel_oz import keyframe_fixture
    from event_backtest.virtual_source import observations,NAMES
    from event_backtest.bridge import CaptureInputs
    from event_host import load_staff
    from event_engine.model import Kind
    from staff_schema import PIPE_VALUE_COLUMNS
    import numpy as np
    rows,index,root=keyframe_fixture(tmp_path);clock=[0.];staff=load_staff()
    cache=staff.StaffPipeCache('',monotonic=lambda:clock[0],gap_journal=tmp_path/'gaps')
    start=rows[3][0];end=rows[-1][0]
    expected=[i for i in CaptureInputs(staff,cache,[root],clock=clock,start_ms=start,end_ms=end+1) if i.kind==Kind.MARKET_BUNDLE]
    actual=list(observations([{'start':'2025-09-01','end':'2025-09-04','path':'capture'}],tmp_path,'XAUUSD+','5m',start,end))
    assert len(expected)==len(actual)
    for item,(stamp,feeds) in zip(expected,actual):
        assert stamp==item.source_time
        # Reader yields a mutable latest-feed registry: copy at observation in
        # consumers, exactly as VirtualEntry.observe does, not after exhaustion.
    actual=observations([{'start':'2025-09-01','end':'2025-09-04','path':'capture'}],tmp_path,'XAUUSD+','5m',start,end)
    for item,(stamp,feeds) in zip(expected,actual):
        for tf in ('1m','5m'):
            np.testing.assert_array_equal(item.payload['feeds'][tf].time,feeds[tf].time)
            np.testing.assert_array_equal(item.payload['feeds'][tf].values[:,[PIPE_VALUE_COLUMNS.index(n) for n in NAMES]],feeds[tf].values)

@pytest.mark.parametrize('text,expected',[
    ('골드 1분 상단 원비 터치 계속 알려줘',{'WATCH_CONDITIONS','COMPOSER'}),
    ('XAUUSD+ 1분봉 올존 계속 알려줘',{'OZ','SWEEP','COMPOSER'}),
])
def test_watch_dependencies_from_canonical_commands(text,expected):
    from event_application import create_event_engine
    from event_engine.model import Kind
    from event_watch_selection import specialize
    e=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+','TELEGRAM_CHAT_ID':'BACKTEST'},symbols=('XAUUSD+',),selection=['WATCH'],backtest=True)
    e.ingress.post(Kind.COMMAND,source='test',source_seq=1,source_time=1756684800000,payload={'symbol':'XAUUSD+','strategy':'WATCH','chat_id':'BACKTEST','text':text});e.run()
    assert not e.error_log
    regs=[x.payload['content']['command'] for x in e.signals if x.payload['content'].get('type')=='WATCH_COMMAND']
    assert regs,'command must actually register a watch'
    result=specialize(e,regs)
    assert result['specialized'] and {c.name for c in e.strategies}==expected

def test_build_stop_saves_completed_pieces(monkeypatch,tmp_path):
    from test_progress_build30 import setup_build
    from event_backtest import workflow,recording
    s,plan,piece=setup_build(monkeypatch,tmp_path)
    def prepare(*a,emit,**kw):
        emit('CAPTURE_START',{'symbol':'XAUUSD+','start':'2025-10-01','end':'2025-11-01'})
        raise Cancelled()
    monkeypatch.setattr(recording,'prepare',prepare)
    r=workflow.execute(s,tmp_path)
    assert r['status']=='CANCELLED' and r['pieces'][0]['status']=='기존'
    assert r['processed_periods']==[{'start':piece['start'],'end':piece['end']}]
    assert (tmp_path/r['result_path']).exists()

def test_empty_token_does_not_deliver_or_mark_receipt(tmp_path):
    from event_host import EventHost,HTTPServices
    from types import SimpleNamespace
    engine=SimpleNamespace(signal_sink=None)
    interpreter=SimpleNamespace()
    external=HTTPServices({'TELEGRAM_TOKEN':''},interpreter)
    host=EventHost(engine,{'TELEGRAM_TOKEN':''},interpreter,transport=lambda *a:pytest.fail('send called'),external=external)
    assert host._deliver(None)==() and not host.output.receipts

def test_future_watch_chain_dependency_declaration():
    from event_watch_selection import specialize
    from event_selection import resolve
    plan=resolve(['WATCH'],{})
    trigger=lambda kind:SimpleNamespace(watch_type=kind)
    manager=SimpleNamespace(_all_specs_locked=lambda:(),fvg_created_watches={},
        _desired_subscriptions_locked=lambda:{},
        timed_chains={'x':SimpleNamespace(final_action='OZ',triggers=(trigger('FVG_NEW'),trigger('WONBI_TOUCH')),invalidation_triggers=(trigger('EMA_CROSS'),))})
    kernel=SimpleNamespace(manager=manager,selection=plan)
    e=SimpleNamespace(selection=plan,strategy_state={'COMPOSER':{'kernels':{'XAUUSD+':kernel}}},
        processors=tuple(SimpleNamespace(name=n) for n in ('OZ_STATE','SWEEP_STATE','FVG_STATE')),
        strategies=tuple(SimpleNamespace(name=n) for n in ('COMPOSER','OZ','FVG','SWEEP','WATCH_CONDITIONS','INDICATOR')))
    r=specialize(e,[])
    assert set(r['capabilities'])=={'OZ','FVG','WATCH','MA','WONBI','CHAINS'}
    assert 'INDICATOR' not in r['consumers'] and {'OZ_STATE','FVG_STATE','SWEEP_STATE'}<=set(r['processors'])
