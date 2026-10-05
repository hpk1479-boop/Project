from pathlib import Path
import datetime as dt,difflib,json,socket,sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a,**k):raise AssertionError('test network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,denied)
    monkeypatch.setattr(socket,'create_connection',denied)

def test_special5_exact_eight_inserted_lines():
    old=(ROOT.parent/'수정본26/Part1/program/SPECIAL/SPECIAL5.py').read_bytes()
    # The approved slot merge remains eight lines; revision30 additionally guards trace-only formatting.
    new=(ROOT.parent/'수정본29/Part1/program/SPECIAL/SPECIAL5.py').read_bytes()
    ops=[x for x in difflib.SequenceMatcher(None,old.splitlines(True),new.splitlines(True),autojunk=False).get_opcodes() if x[0]!='equal']
    assert len(ops)==2 and all(t=='insert' and v-u==4 for t,a,b,u,v in ops)
    import ast
    class WithoutLogs(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.unparse(node.test).startswith('logging.getLogger().isEnabledFor('):return None
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if isinstance(node.value,ast.Call) and ast.unparse(node.value.func).startswith('logging.'):return None
            return self.generic_visit(node)
    current=(ROOT/'Part1/program/SPECIAL/SPECIAL5.py').read_text('utf-8-sig')
    assert ast.dump(WithoutLogs().visit(ast.parse(new.decode('utf-8-sig'))))==ast.dump(WithoutLogs().visit(ast.parse(current)))

    assert new.count(b'\r\n')==old.count(b'\r\n')+8
    assert b'for wid in sorted({str(x) for x in watch_ids if str(x)}):' in new

@pytest.mark.parametrize('date,expected',[('2026-03-31','2026-02-28'),('2024-03-31','2024-02-29'),('2026-01-10','2025-12-10')])
def test_calendar_month_not_thirty_days(date,expected):
    from special_settings_model import gui_dates
    assert gui_dates(dt.date.fromisoformat(date))==(expected,date)

def test_discovery_and_unsupported_execution(tmp_path):
    from special_settings_model import discover
    from event_backtest.ui_model import make_scenario
    for name in ('SPECIAL9','SPECIAL2','SPECIAL10','Test_SPECIAL88','other'):(tmp_path/(name+'.py')).write_text('')
    assert discover(tmp_path)==['SPECIAL2','SPECIAL9','SPECIAL10']
    assert discover(tmp_path,True)[-1]=='Test_SPECIAL88'
    with pytest.raises(ValueError,match='미지원'):
        make_scenario({'target_mode':'SPECIAL','specials':{'SPECIAL9':{'enabled':True}}},{})

def test_independent_mode_settings_and_empty_sessions(tmp_path):
    from event_backtest import ui_model
    ui={'target_mode':'SPECIAL','specials':{'SPECIAL1':{'enabled':True,'trigger':'브레이커 올존','time_filters':{}}},
        'watch':{'text':'골드 1분 올존 계속 알려줘','chat_id':'TEST','time_filters':None}}
    path=tmp_path/'part2.json';ui_model.save(ui,path);restored=ui_model.load(path)
    s=ui_model.make_scenario(restored,{})
    assert s['special_time_filters']=={'SPECIAL1':{}} and s['commands']==[]
    restored['target_mode']='WATCH';s=ui_model.make_scenario(restored,{})
    assert s['strategies']==['WATCH'] and s['special_time_filters']=={}
    assert s['commands'][0]['chat_id']=='TEST' and restored['specials']==ui['specials']
    restored['watch']['time_filters']={}
    ui_model.save(restored,path)
    assert 'time_filters' not in ui_model.load(path)['watch']
    assert ui_model.make_scenario(ui_model.load(path),{})['commands'][0]['chat_id']=='TEST'

@pytest.mark.parametrize('number',range(1,8))
def test_time_slots_boundaries_isolation_and_source_clock(number):
    from event_application import load_strategy_inputs
    from domain_clock import event_scope
    name=f'SPECIAL{number}'
    _,_,default=load_strategy_inputs({},[name],{})
    _,_,disabled=load_strategy_inputs({},[name],{name:{}})
    _,_,window=load_strategy_inputs({},[name],{name:{'MAIN_NEWYORK':{'enabled':True,'start':'23:00','end':'01:00'}}})
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'0800-1200')
    for hour,minute,expected in [(22,59,False),(23,0,True),(0,0,True),(1,0,True),(1,1,False)]:
        stamp=int(dt.datetime(2025,9,1,hour,minute,tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()*1000)
        with event_scope(stamp,'test',{}):
            if name!='SPECIAL3':assert default[name]._final_alert_time_allowed(api)
            assert not disabled[name]._final_alert_time_allowed(api)
            assert window[name]._final_alert_time_allowed(api)==expected

def test_slot_session_disable_and_config_default():
    from event_application import load_strategy_inputs
    from domain_clock import event_scope
    config={'MAIN_ASIA':'0800-1200','MAIN_NEWYORK':'2100-2400'}
    api=SimpleNamespace(config_get=lambda k,d:config.get(k,d),time_allowed=lambda _:True)
    _,_,plugins=load_strategy_inputs({},['SPECIAL1'],{'SPECIAL1':{'MAIN_ASIA':{'enabled':False},'MAIN_NEWYORK':{'enabled':True}}})
    for hour,expected in [(10,False),(22,True)]:
        stamp=int(dt.datetime(2025,9,1,hour,tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()*1000)
        with event_scope(stamp,'test',{}):assert plugins['SPECIAL1']._final_alert_time_allowed(api)==expected

def test_tk_dialog_controls_without_services(monkeypatch):
    import tkinter as tk
    import special_ui as ui
    root=tk.Tk();root.withdraw()
    try:
        ui.trigger_dialog(root,None,'올존',lambda value:None)
        dialog=root.winfo_children()[-1]
        radios=[w for w in dialog.winfo_children() if w.winfo_class()=='TRadiobutton']
        assert len(radios)==4 and len({str(w.cget('variable')) for w in radios})==1
        dialog.destroy()
        date=tk.StringVar(value='2026-09-25');ui.calendar_dialog(root,date)
        root.winfo_children()[-1].destroy();assert date.get()=='2026-09-25'
        ui.time_dialog(root,'SPECIAL1',{},('MAIN_ASIA',),{'MAIN_ASIA':'0800-1200','MAIN_LONDON':'1400-1800','MAIN_NEWYORK':'2100-2400'},lambda value:None)
        dialog=root.winfo_children()[-1]
        checks=[w for w in dialog.winfo_children() if w.winfo_class()=='TCheckbutton']
        assert len(checks)==3 and not any(root.getvar(w.cget('variable')) for w in checks)
    finally:root.destroy()

def test_gui_modes_dates_and_disabled_watch_time(monkeypatch):
    import tkinter as tk
    from event_backtest import gui,ui_model
    state={'target_mode':'SPECIAL','specials':{'SPECIAL1':{'enabled':True}},'watch':{'text':'골드 1분 올존 계속 알려줘','chat_id':'TEST'}}
    monkeypatch.setattr(ui_model,'load',lambda:state)
    monkeypatch.setattr(ui_model,'save',lambda value:None)
    def inspect(root,*args):
        root.withdraw()
        def descendants(w):
            return [child for c in w.winfo_children() for child in [c,*descendants(c)]]
        items=descendants(root)
        text=lambda w:str(w.cget('text')) if 'text' in w.keys() else ''
        labels=[text(w) for w in items]
        assert '데이터' in labels and '시나리오 JSON' not in labels and not any('공식 전략 ID' in x for x in labels)
        assert labels.count('📅')==2
        radios=[w for w in items if w.winfo_class()=='TRadiobutton']
        next(w for w in radios if text(w)=='WATCH 모드').invoke()
        watch=next(w for w in items if w.winfo_class()=='TLabelframe' and text(w).startswith('WATCH 모드'))
        button=next(w for w in descendants(watch) if text(w)=='거래시간')
        assert str(button.cget('state'))=='disabled'
        next(w for w in radios if text(w)=='SPECIAL 모드').invoke()
        assert state['watch']['text']=='골드 1분 올존 계속 알려줘'
        root.destroy()
    monkeypatch.setattr(tk.Tk,'mainloop',inspect)
    gui.main()

def test_watch_scenario_uses_existing_command_registration():
    from event_backtest.ui_model import make_scenario
    from event_application import create_event_engine
    from event_engine import Kind
    ui={'target_mode':'WATCH','watch':{'text':'골드 1분 상단 원비 터치 알려줘','chat_id':'TEST'},'specials':{}}
    s=make_scenario(ui,{})
    e=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
                           'TELEGRAM_CHAT_ID':'TEST','TELEGRAM_COMMAND_CHAT_IDS':'TEST'},
                          selection=s['strategies'],trigger_overrides={},time_overrides={},backtest=True)
    e.ingress.post(Kind.COMMAND,source='scenario',source_seq=0,source_time=1790380679000,
                   payload={'symbol':'XAUUSD+',**s['commands'][0]})
    e.run()
    assert not e.error_log,e.error_log
    assert e.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager
    assert any('감시' in str(signal.payload) for signal in e.signals)

def test_part1_settings_roundtrip_and_environment_isolation(tmp_path,monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('live_settings_test',ROOT/'Part1/live_control.py')
    control=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    path=tmp_path/'live_settings.json'
    monkeypatch.setattr(control,'SPECIAL_SETTINGS_PATH',path)
    value={'SPECIAL1':{'enabled':True,'trigger':None,'time_filters':{}},
           'SPECIAL5':{'enabled':True,'trigger':'무지성 올존','time_filters':None}}
    control.save_special_settings(value)
    assert control.load_special_settings()==value
    from special_settings_model import time_env
    assert json.loads(time_env(value))=={'SPECIAL1':{}}
    from event_application import load_strategy_inputs
    monkeypatch.setenv('OZ_SPECIAL_TIME_FILTERS',time_env(value))
    _,_,live=load_strategy_inputs({},['SPECIAL1'])
    _,_,backtest=load_strategy_inputs({},['SPECIAL1'],{})
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'0800-1200')
    assert not live['SPECIAL1']._final_alert_time_allowed(api)
    assert backtest['SPECIAL1']._final_alert_time_allowed(api)
    from event_backtest.ui_model import save
    save({'target_mode':'WATCH','specials':{},'watch':{'text':'test'}},tmp_path/'backtest.json')
    assert control.load_special_settings()==value
