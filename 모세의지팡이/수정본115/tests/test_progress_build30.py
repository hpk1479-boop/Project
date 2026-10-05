import sys,json
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]
from event_backtest.progress_view import ProgressView
from event_backtest.terminal_lifecycle import launch_once_retry
from event_backtest import workflow,runner,recording
from event_backtest.settings import scenario

@pytest.fixture(autouse=True)
def no_external(monkeypatch):
 import socket,subprocess
 def fail(*a,**k):raise AssertionError('External services forbidden in tests')
 monkeypatch.setattr(socket.socket,'connect',fail);monkeypatch.setattr(socket.socket,'sendto',fail)
 monkeypatch.setattr(subprocess,'Popen',fail)

def test_progress_stage_only_and_warning_panel():
 p=ProgressView(clock=lambda:100)
 p.accept({'event':'BUILD_PLAN','record':[{}],'convert':[]})
 p.accept({'event':'CAPTURE_START','start':'2025-09-01','index':1,'total':1})
 for i in range(100):p.accept({'event':'PROGRESS','message_code':'NATIVE_EXPORT_PROGRESS','raw':i})
 assert len(p.lines)==1 and p.stage=='MT5 추출'
 p.accept({'event':'CONVERSION_START','start':'2025-09-01'});assert p.stage=='차분 변환'
 p.accept({'event':'VERIFY_START'});assert p.stage=='복원 검증'
 p.accept({'event':'CAPTURE_COMPLETE','start':'2025-09-01','tick_evidence':{'warning':'생성 틱'}})
 assert p.build==100 and p.warnings
 p.accept({'event':'RUN_PROGRESS','percent':50,'elapsed_seconds':30});assert p.eta==30
 p.accept({'event':'COMPLETE','result':{}});assert p.build==p.replay==100
 assert not any('"event"' in x for x in p.lines)

def test_reuse_full_bar():
 p=ProgressView();p.accept({'event':'BUILD_PLAN','record':[],'convert':[]});assert p.build==100

def test_build_gauge_advances_within_each_stage():
 piece={'start':'2025-09-01','end':'2025-09-04'}
 p=ProgressView(clock=lambda:100)
 p.accept({'event':'BUILD_PLAN','record':[piece],'convert':[],
           'estimate':{'temporary_msp3_bytes':900}})
 p.accept({'event':'CAPTURE_START',**piece,'index':1,'total':1})
 assert p.build==0
 p.accept({'event':'PROGRESS','message_code':'NATIVE_EXPORT_PROGRESS','export_bytes':450})
 assert 16<p.build<17 and '0.00GB' in p.stage_detail
 p.accept({'event':'CAPTURE_RECORDED',**piece,'raw_bytes':900})
 assert 33<p.build<34
 p.accept({'event':'CONVERSION_START',**piece})
 p.accept({'event':'CONVERSION_PROGRESS','processed_bytes':450,'total_bytes':900,'processed_days':1,'total_days':3})
 assert 49<p.build<51 and p.stage=='차분 변환'
 p.accept({'event':'VERIFY_START','total_days':3})
 assert 66<p.build<67
 p.accept({'event':'VERIFY_PROGRESS','verified_days':1,'total_days':3})
 assert 77<p.build<78 and p.stage_detail=='1 / 3일'
 p.accept({'event':'CAPTURE_COMPLETE',**piece})
 assert p.build==100
 no_estimate=ProgressView();no_estimate.accept({'event':'BUILD_PLAN','record':[piece],'convert':[]})
 no_estimate.accept({'event':'CAPTURE_START',**piece})
 assert 33<no_estimate.build<34

def test_conversion_and_verified_day_events(tmp_path):
 from test_parallel_oz import keyframe_fixture
 from event_backtest.storage import convert
 _,_,source=keyframe_fixture(tmp_path)
 (source/'storage.json').write_text('{}',encoding='utf8')
 events=[]
 destination,stored=convert(source,tmp_path/'converted','sample',start='2025-09-01',end='2025-09-04',
     emit=lambda name,data:events.append((name,data)))
 assert stored['reconstruction_verified'] and destination.exists()
 conversion=[data for name,data in events if name=='CONVERSION_PROGRESS']
 verified=[data for name,data in events if name=='VERIFY_PROGRESS']
 assert conversion and conversion[-1]['processed_days']==3
 assert [row['verified_days'] for row in verified]==[1,2,3]

def test_detail_window_tails_and_reports_read_errors(tmp_path,monkeypatch):
 import tkinter as tk
 from event_backtest.detail_log_window import DetailLogWindow
 from event_backtest import detail_log_window
 errors=[];monkeypatch.setattr(detail_log_window.messagebox,'showerror',lambda *a,**k:errors.append(a))
 root=tk.Tk();root.withdraw();path=tmp_path/'console.log';path.write_text('첫 줄\n',encoding='utf8')
 try:
  viewer=DetailLogWindow(root,path)
  assert '첫 줄' in viewer.text.get('1.0','end')
  viewer.paused.set(True)
  with path.open('a',encoding='utf8') as handle:handle.write('둘째 줄\n')
  viewer.poll();assert '둘째 줄' not in viewer.text.get('1.0','end')
  viewer.paused.set(False);viewer.poll();assert '둘째 줄' in viewer.text.get('1.0','end')
  viewer.show(tmp_path)
  viewer.poll();assert errors and '읽기 실패' in viewer.status.get()
  viewer.show(tmp_path/'missing.log');viewer.is_running=lambda:False
  viewer.poll();assert 'missing.log' in viewer.status.get()
  viewer.close()
 finally:root.destroy()

def test_terminal_wait_retry_order_and_limit():
 calls=[]
 def wait(*a,**k):calls.append('closed')
 def launch():
  calls.append('launch')
  if calls.count('launch')==1:raise ValueError('NATIVE_TESTER_DID_NOT_START')
  return 'ok'
 assert launch_once_retry(launch,{},wait=wait)=='ok'
 assert calls==['closed','launch','closed','closed','launch','closed']
 def failed():raise ValueError('NATIVE_TESTER_DID_NOT_START')
 with pytest.raises(RuntimeError,match='이미 켜진 MT5'):launch_once_retry(failed,{},wait=wait)

def test_terminal_busy_blocks_launch():
 def busy(*a,**k):raise RuntimeError('busy')
 with pytest.raises(RuntimeError,match='busy'):launch_once_retry(lambda:pytest.fail('launched'),{},wait=busy)

def setup_build(monkeypatch,tmp_path):
 piece={'capture_id':'old','symbol':'XAUUSD+','start':'2025-09-01','end':'2025-10-01','path':'captures/x','history_missing':[],'tick_evidence':{'actual':'REAL_TICKS'}}
 plan={'approval_token':'yes','record':[],'reuse':[piece],'convert':[]}
 monkeypatch.setattr(workflow,'proposal',lambda *a,**k:plan)
 monkeypatch.setattr(runner,'runtime_config',lambda s:{})
 monkeypatch.setattr(recording,'prepare',lambda *a,**k:[piece])
 monkeypatch.setattr(runner,'run',lambda *a,**k:pytest.fail('build-only ran engine'))
 s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-10-01',strategies=[],build_only=True)
 return s,plan,piece

def test_build_only_preserves_existing_and_does_not_require_strategies(monkeypatch,tmp_path):
 s,plan,piece=setup_build(monkeypatch,tmp_path)
 result=workflow.execute(s,tmp_path)
 assert not result['backtest_executed'] and result['pieces'][0]['status']=='기존'
 assert result['scenario']['overlap_trading_days']==0
 assert (tmp_path/result['result_path']).exists()

def test_record_requires_confirmation_and_successful_replacement(monkeypatch,tmp_path):
 from event_backtest.build_plan import ConfirmationRequired
 s,plan,piece=setup_build(monkeypatch,tmp_path);plan['record']=[piece];plan['reuse']=[]
 with pytest.raises(ConfirmationRequired):workflow.execute(s,tmp_path)
 result=workflow.execute(s,tmp_path,yes=True,rebuild=True)
 assert result['pieces'][0]['status']=='새로 구축'

def test_partial_failure_keeps_piece_results(monkeypatch,tmp_path):
 s,plan,piece=setup_build(monkeypatch,tmp_path)
 def prepare(*a,emit,**k):
  emit('CAPTURE_START',{'start':'2025-10-01','end':'2025-11-01','symbol':'XAUUSD+'})
  raise RuntimeError('controlled failure')
 monkeypatch.setattr(recording,'prepare',prepare)
 with pytest.raises(RuntimeError):workflow.execute(s,tmp_path)
 data=json.loads(next((tmp_path/'runs').glob('*/result.json')).read_text('utf8'))
 assert [r['status'] for r in data['pieces']]==['기존','실패']

def test_result_table_and_moved_root(tmp_path):
 import tkinter as tk
 from tkinter import ttk
 from event_backtest.result_ui import open_result
 folder=tmp_path/'moved';folder.mkdir();p=folder/'result.json'
 p.write_text(json.dumps({'run_id':'test','virtual_entry':{'summary':[{'strategy':'SPECIAL1','rr':1,'wins':1,'losses':0,'win_rate':1.,'average_r':1.,'total_r':1.}]}}),encoding='utf8')
 root=tk.Tk();root.withdraw()
 try:
  win=open_result(root,p,folder);root.update()
  def children(w):
   for c in w.winfo_children():yield c;yield from children(c)
  table=next(c for c in children(win) if isinstance(c,ttk.Treeview));assert len(table.get_children())==1
 finally:root.destroy()

def test_gui_build_only_controls_and_human_log(monkeypatch,tmp_path):
 import tkinter as tk,time
 from tkinter import ttk
 from types import SimpleNamespace
 from event_backtest import gui,ui_model
 state={'target_mode':'SPECIAL','specials':{'SPECIAL1':{'enabled':True}},'watch':{'text':'','chat_id':'TEST'}}
 monkeypatch.setattr(ui_model,'load',lambda:state);monkeypatch.setattr(ui_model,'save',lambda *a:None)
 monkeypatch.setattr(gui,'ROOT',tmp_path);(tmp_path/'Part2').mkdir()
 monkeypatch.setattr(gui,'settings',lambda:{'warehouse':str(tmp_path/'warehouse')})
 monkeypatch.setattr(runner,'runtime_config',lambda s:{})
 calls=[]
 def fake(args,**kwargs):
  calls.append(args)
  if args[6]=='plan':payload={'event':'COMPLETE','result':{'record':[],'reuse':[],'convert':[],'approval_token':'t','estimate':{'delta_storage_bytes':0,'recording_seconds':0,'temporary_msp3_bytes':0}}}
  else:payload={'event':'COMPLETE','result':{'build_only':True,'result_path':'runs/test/result.json','warnings':['생성 틱']}}
  return SimpleNamespace(stdout=iter([json.dumps(payload)+'\n']),wait=lambda:0)
 monkeypatch.setattr(gui.subprocess,'Popen',fake)
 def inspect(root):
  def children(w):
   for c in w.winfo_children():yield c;yield from children(c)
  all_widgets=list(children(root))
  from event_backtest.segment_control import Segments
  from modern_widgets import ModernButton
  build=next(w for w in all_widgets if isinstance(w,Segments) and any(v=='BUILD_ONLY' for _,v in w.choices))
  rebuild=next(w for w in all_widgets if isinstance(w,ttk.Checkbutton) and str(w.cget('text'))=='초기화 후 녹화')
  assert not rebuild.winfo_manager();build.choose(2);assert str(rebuild.cget('state'))=='normal'
  next(w for w in all_widgets if isinstance(w,ModernButton) and '백테스트 실행' in w.cget('text')).invoke()
  deadline=time.monotonic()+1.2
  while time.monotonic()<deadline:root.update();time.sleep(.02)
  assert len(calls)==2 and all('--build-only' in a for a in calls)
  assert all('--rebuild' not in a for a in calls)
  assert all(float(w['value'])==100 for w in all_widgets if isinstance(w,ttk.Progressbar))
  text=''.join(w.get('1.0','end') for w in all_widgets if isinstance(w,tk.Text) and int(w.cget('height'))!=2)
  assert '완료' in text and '"event"' not in text
  detail=next(w for w in all_widgets if isinstance(w,ModernButton) and '상세 로그 보기' in w.cget('text'))
  detail.invoke();detail.invoke();root.update()
  windows=[w for w in root.winfo_children() if isinstance(w,tk.Toplevel)]
  assert len(windows)==1
  assert 'COMPLETE' in next(w for w in children(windows[0]) if isinstance(w,tk.Text)).get('1.0','end')
  root.destroy()
 monkeypatch.setattr(tk.Tk,'mainloop',inspect)
 gui.main()
