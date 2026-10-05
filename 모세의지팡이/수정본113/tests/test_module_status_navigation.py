"""The live status screens keep module identities and strategy logs separate."""
import sys
import time
import re
import threading
import queue
import tkinter as tk
import importlib.machinery
import importlib.util
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
import module_status_ui as status_ui
from special_ui import strategy_dialog
from strategy_settings_view import StrategyStatusButton


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def update_until(root,condition,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        root.update()
        if condition():return
        time.sleep(.02)
    assert condition()


def test_main_view_names_selected_log_and_connection_state(monkeypatch):
    requests=[]
    def fake(_root,name,**_options):
        requests.append((name,_options))
        return {'modules':{key:{'status':'정상','last':100000,'errors':0,'monitoring':True} for key in status_ui.MODULES},
                'pipe_connected':True,'pipe_seen':True,
                'telegram_attempted':True,'telegram_connected':True,
                'enabled_specials':status_ui.SPECIAL_MODULES,
                'lines':[(1,'['+name+'] 실제 수신 기록')], 'trace_enabled':True}
    monkeypatch.setattr(status_ui,'read_snapshot',fake)
    renderers=[]
    def watch(_parent,path,render):
        renderers.append(render);render(fake(path,'ENGINE'),None)
    monkeypatch.setattr(status_ui,'watch_status',watch)
    root=tk.Tk();root.withdraw()
    try:
        path=ROOT/'Part1/OZ_SYSTEM CONTROL.pyw'
        spec=importlib.util.spec_from_file_location('control_status_test',path,
                 loader=importlib.machinery.SourceFileLoader('control_status_test',str(path)))
        control=importlib.util.module_from_spec(spec)
        with patch('shutil.rmtree'):
            spec.loader.exec_module(control)
        controller=control.MosesController(root)
        assert tuple(controller.module_buttons)==status_ui.MAIN_MODULES
        assert not hasattr(controller,'status_labels')
        assert not any(isinstance(w,tk.Label) and w.cget('text')=='EVENT ENGINE'
                       for w in descendants(root))
        labels=[w.cget('text') for w in descendants(root) if isinstance(w,tk.Label)]
        buttons=[w.cget('text') for w in descendants(root) if isinstance(w,tk.Button)]
        assert '모듈 연결 상태' in labels
        assert not any('클릭하면 추적 로그' in text for text in labels)
        assert '모듈 상태 / 추적 로그' not in buttons
        assert '연결중' in controller.module_buttons['STAFF'].cget('text')
        assert not any(re.search(r'\d\d:\d\d:\d\d',button.cget('text'))
                       for button in controller.module_buttons.values())
        idle=fake(ROOT/'Part1','ENGINE')
        idle['modules']['ENGINE']={'status':'대기','last':None,'errors':0}
        renderers[0](idle,None)
        assert '연결 대기' in controller.module_buttons['ENGINE'].cget('text')
        idle['modules']['WATCH']={'status':'정상','last':100000,'errors':0,'monitoring':False}
        renderers[0](idle,None)
        assert '연결 대기' in controller.module_buttons['WATCH'].cget('text')
        renderers[0](None,RuntimeError('엔진 중지'))
        assert all('연결 대기' in button.cget('text') for button in controller.module_buttons.values())
        disconnected=fake(ROOT/'Part1','ENGINE');disconnected['pipe_connected']=False
        renderers[0](disconnected,None)
        assert '오류' in controller.module_buttons['STAFF'].cget('text')
        delayed=fake(ROOT/'Part1','ENGINE')
        delayed['modules']['OZ']['status']='지연'
        renderers[0](delayed,None)
        assert '오류' in controller.module_buttons['OZ'].cget('text')
        renderers[0](fake(ROOT/'Part1','ENGINE'),None)
        controller.module_buttons['OZ'].invoke()
        win=root.winfo_children()[-1]
        tree=next(w for w in win.winfo_children() if isinstance(w,ttk.Treeview))
        assert len(tree.get_children())==9 and tree.get_children()[0]=='ALL'
        assert tree.item('ALL','text')=='모두 보기'
        assert tree.item('STAFF','text')=='STAFF'
        assert tree.item('ENGINE','text')=='EVENT'
        assert tree.item('OZ','text')=='OZ'
        assert tree.item('KIM','text')=='김비서'
        assert not any(name.startswith('SPECIAL') for name in tree.get_children())
        assert '[OZ]' in win.title()
        text=next(w for w in win.winfo_children() if isinstance(w,tk.Text))
        update_until(root,lambda:'[OZ] 실제 수신 기록' in text.get('1.0','end'))
        tree.selection_set('KIM');tree.event_generate('<<TreeviewSelect>>')
        assert '[김비서]' in win.title()
        assert 'OZ' not in text.get('1.0','end')
        overview=status_ui.open_window(root,ROOT/'Part1')
        overview_tree=next(w for w in overview.winfo_children() if isinstance(w,ttk.Treeview))
        assert overview_tree.selection()==('ALL',)
        checks=[w for w in descendants(overview) if isinstance(w,ttk.Checkbutton)]
        assert [w.cget('text') for w in checks[:3]]==['상세 로그 보기','일시정지','자동 스크롤']
        checks[0].invoke()
        update_until(root,lambda:any(name=='ALL' and options.get('detail') is True for name,options in requests))
        overview.destroy()
        check_special_dialog(root)
    finally:root.destroy()


def test_window_close_stops_host_after_inflight_start(monkeypatch):
    path=ROOT/'Part1/OZ_SYSTEM CONTROL.pyw'
    spec=importlib.util.spec_from_file_location('control_close_test',path,
             loader=importlib.machinery.SourceFileLoader('control_close_test',str(path)))
    control=importlib.util.module_from_spec(spec)
    with patch('shutil.rmtree'):
        spec.loader.exec_module(control)
    callbacks=queue.Queue();stopped=threading.Event()
    class Root:
        destroyed=False
        def after(self,_delay,callback):callbacks.put(callback)
        def destroy(self):self.destroyed=True
    def stop(_script):
        stopped.set();return True,'종료 완료'
    monkeypatch.setattr(control,'stop_program',stop)
    root=Root();controller=object.__new__(control.MosesController)
    controller.root=root;controller.busy=True;controller._closing=False
    controller._worker_lock=threading.Lock()
    controller.set_busy=lambda busy:setattr(controller,'busy',busy)
    controller._stop_legacy=lambda:None
    controller.message=type('Message',(),{'config':lambda self,**_options:None})()
    controller._worker_lock.acquire()  # a start operation still owns the worker
    try:
        controller.close_window()
        assert not stopped.is_set()
        assert controller._closing and controller.busy
    finally:
        controller._worker_lock.release()
    assert stopped.wait(3)
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and not root.destroyed:
        try:callbacks.get(timeout=.1)()
        except queue.Empty:pass
    assert root.destroyed


def check_special_dialog(root):
    strategy_dialog(root,ROOT/'Part1/program/SPECIAL',None,{},lambda settings:None,
                    live=True,diagnostics_root=ROOT/'Part1')
    dialog=root.winfo_children()[-1]
    assert not any(w.cget('text')=='추적 로그' for w in descendants(dialog) if isinstance(w,ttk.Button))
    update_until(root,lambda:len([w for w in descendants(dialog) if isinstance(w,StrategyStatusButton) and
                                 w.cget('text')=='연결중'])==7)
    buttons=[w for w in descendants(dialog) if isinstance(w,StrategyStatusButton) and w.cget('text')=='연결중']
    buttons[0].invoke()
    log=dialog.winfo_children()[-1]
    assert '[SPECIAL1]' in log.title()
    tree=next(w for w in log.winfo_children() if isinstance(w,ttk.Treeview))
    assert tree.get_children()==('ALL',*status_ui.SPECIAL_MODULES)
    assert tree.item('ALL','text')=='모두 보기'
    buttons[1].invoke()
    assert dialog.winfo_children()[-1] is log
    assert tree.selection()==('SPECIAL2',)
    assert '[SPECIAL2]' in log.title()


def test_special_folder_uses_direct_path_and_stop_needs_no_confirmation(monkeypatch):
    path=ROOT/'Part1/OZ_SYSTEM CONTROL.pyw'
    spec=importlib.util.spec_from_file_location('control_direct_special_test',path,
             loader=importlib.machinery.SourceFileLoader('control_direct_special_test',str(path)))
    control=importlib.util.module_from_spec(spec)
    with patch('shutil.rmtree'):
        spec.loader.exec_module(control)
    monkeypatch.setattr(control,'resolve_script_path',lambda _name:(_ for _ in ()).throw(
        AssertionError('recursive lookup must not run')))
    assert control.special_dir()==ROOT/'Part1/program/SPECIAL'
    monkeypatch.setattr(control.messagebox,'askyesno',lambda *_args,**_kwargs:(_ for _ in ()).throw(
        AssertionError('stop must not ask for confirmation')))
    started=[]
    class Thread:
        def __init__(self,*,target,daemon):self.target=target;self.daemon=daemon
        def start(self):started.append(self.target)
    monkeypatch.setattr(control.threading,'Thread',Thread)
    controller=object.__new__(control.MosesController)
    controller.busy=False;controller._closing=False
    controller.set_busy=lambda value:setattr(controller,'busy',value)
    controller._stop_worker=lambda:None
    controller.stop_all()
    assert controller.busy and started==[controller._stop_worker]
