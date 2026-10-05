import json,sys,logging,threading
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from module_diagnostics import Diagnostics,MODULES,DiagnosticServer,read_snapshot
from test_event_e1 import new_engine,post,market,Probe,FailingProbe

@pytest.fixture(autouse=True)
def offline(monkeypatch):
 import socket
 def deny(*a,**k):raise AssertionError('Network forbidden')
 monkeypatch.setattr(socket.socket,'connect',deny)
 monkeypatch.setattr(socket.socket,'connect_ex',deny)
 monkeypatch.setattr(socket.socket,'sendto',deny)

def test_rings_lazy_rotation_and_portable_files(tmp_path):
 sink=Diagnostics(tmp_path,{'TRACE_RING_LINES':2,'TRACE_WARNING_BYTES':1024,'TRACE_WARNING_BACKUPS':2})
 for name in MODULES:
  for i in range(3):sink.log(name,logging.INFO,'수신 %s',i)
  assert len(sink.snapshot(name)['lines'])==2
 assert not (tmp_path/'logs').exists()
 for i in range(40):sink.log('OZ',logging.WARNING,'경고 %s',str(i)*200)
 assert len(list((tmp_path/'logs/module_errors').glob('*')))<=3
 sink.close()
 class NeverFormat:
  def __str__(self):raise AssertionError('formatted while disabled')
 sink=Diagnostics(tmp_path,trace=False);sink.log('OZ',logging.INFO,'%s',NeverFormat())
 assert not sink.snapshot('OZ')['lines'];sink.close()

def test_strategy_error_stack_and_other_module_continues(tmp_path):
 bad=FailingProbe();bad.name='WATCH';good=Probe();good.name='INDICATOR'
 e=new_engine(bad,good);e.diagnostics=Diagnostics(tmp_path)
 post(e,market(1000));e.run()
 assert len(good.seen)==1
 assert e.diagnostics.snapshot()['modules']['WATCH']['status']=='오류·끊김'
 assert e.diagnostics.snapshot()['modules']['WATCH']['errors']==1
 log=(tmp_path/'logs/module_errors/WATCH.log').read_text('utf8')
 assert 'Traceback' in log and 'controlled failure' in log
 e.diagnostics.close()

def test_processor_error_isolated(tmp_path):
 from event_engine import Subscriptions,Kind
 class Bad:
  name='OZ_STATE'
  def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,))
  def on_event(self,*args):raise RuntimeError('processor controlled')
 good=Probe();e=new_engine(good,processors=(Bad(),));e.diagnostics=Diagnostics(tmp_path)
 post(e,market(1000));e.run()
 assert good.seen and e.diagnostics.snapshot()['modules']['OZ']['errors']==1
 e.diagnostics.close()

def test_stale_disconnect_and_json_ipc(tmp_path):
 clock=[0];sink=Diagnostics(tmp_path,clock=lambda:clock[0]);sink.touch('STAFF')
 assert not sink.snapshot()['pipe_connected'] and not sink.snapshot()['pipe_seen']
 sink.pipe_state(True);assert sink.snapshot()['pipe_connected'] and sink.snapshot()['pipe_seen']
 sink.pipe_state(False);assert not sink.snapshot()['pipe_connected'] and sink.snapshot()['pipe_seen']
 clock[0]=31;assert sink.snapshot()['modules']['STAFF']['status']=='지연'
 clock[0]=61;assert sink.snapshot()['modules']['STAFF']['status']=='오류·끊김'
 sink.health('XAUUSD+','UNAVAILABLE')
 assert sink.snapshot()['modules']['STAFF']['status']=='오류·끊김'
 sink.health('XAUUSD+','CLOSED')
 assert sink.snapshot()['modules']['STAFF']['status']=='정상'
 sink.log('WATCH',logging.INFO,'감시 등록 %s',123)
 server=DiagnosticServer(sink)
 try:assert '감시 등록 123' in read_snapshot(tmp_path,'WATCH')['lines'][-1][1]
 finally:server.close();sink.close()

def test_ui_reads_memory_and_preserves_controls(tmp_path):
 import tkinter as tk,time
 from module_status_ui import open_window
 root=tk.Tk();root.withdraw();sink=Diagnostics(tmp_path);server=DiagnosticServer(sink)
 sink.log('ENGINE',logging.INFO,'합성 묶음 처리 완료')
 win=open_window(root,tmp_path)
 try:
  until=time.monotonic()+2.2
  while time.monotonic()<until:root.update();time.sleep(.02)
  texts=[w for w in win.winfo_children() if isinstance(w,tk.Text)]
  assert '합성 묶음 처리 완료' in texts[0].get('1.0','end')
 finally:win.destroy();root.destroy();server.close();sink.close()

def test_shared_module_error_not_hidden_by_healthy_consumer(tmp_path):
 from types import SimpleNamespace
 from event_engine import Kind
 e=SimpleNamespace(engine_seq=1,kind=Kind.MARKET_BUNDLE,payload={'symbol':'XAUUSD+'})
 sink=Diagnostics(tmp_path);sink.begin('OZ_STATE')
 try:raise RuntimeError('state failed')
 except RuntimeError as exc:sink.failure('OZ_STATE',e,exc)
 sink.begin('OZ');sink.success('OZ',e)
 assert sink.snapshot()['modules']['OZ']['status']=='오류·끊김'
 sink.begin('OZ_STATE');sink.success('OZ_STATE',e)
 assert sink.snapshot()['modules']['OZ']['status']=='정상'
 sink.close()
