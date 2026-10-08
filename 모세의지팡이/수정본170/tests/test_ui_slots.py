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

@pytest.mark.parametrize('date,expected',[('2026-03-31','2026-02-28'),('2024-03-31','2024-02-29'),('2026-01-10','2025-12-10')])
def test_calendar_month_not_thirty_days(date,expected):
    from special_settings_model import gui_dates
    assert gui_dates(dt.date.fromisoformat(date))==(expected,date)

def test_discovery_and_unsupported_execution(tmp_path):
    from special_settings_model import discover
    from event_backtest.ui_model import make_scenario
    from strategy_recipe.registry import list_presets
    for name in ('SPECIAL9','SPECIAL2','SPECIAL10','Test_SPECIAL88','other'):(tmp_path/(name+'.py')).write_text('')
    # Built-in strategies come from the registered recipes, never from loose .py files in a folder.
    assert discover(tmp_path)==list(list_presets('Part1'))
    assert discover(tmp_path,True)[-1]=='Test_SPECIAL88'
    # SPECIAL10 is not registered (SPECIAL9 is a shipped strategy since 수정본139).
    with pytest.raises(ValueError,match='미지원'):
        make_scenario({'target_mode':'SPECIAL','specials':{'SPECIAL10':{'enabled':True}}},{})

def test_independent_mode_settings_and_empty_sessions(tmp_path):
    from event_backtest import ui_model
    ui={'target_mode':'SPECIAL','specials':{'SPECIAL1':{'enabled':True,'trigger':'브레이커 올존','time_filters':{}}},
        'watch':{'text':'골드 1분 올존 계속 알려줘','chat_id':'TEST','time_filters':None}}
    path=tmp_path/'part2.json';ui_model.save(ui,path);restored=ui_model.load(path)
    s=ui_model.make_scenario(restored,{})
    assert s['special_time_filters']=={'SPECIAL1':{}} and s['commands']==[]
    restored['target_mode']='WATCH';s=ui_model.make_scenario(restored,{})
    assert s['strategies']==['WATCH'] and s['special_time_filters']=={}
    # The saved file lists every registered strategy; the one chosen here keeps exactly its settings.
    assert s['commands'][0]['chat_id']=='TEST' and restored['specials']['SPECIAL1']==ui['specials']['SPECIAL1']
    assert not any(row['enabled'] for name,row in restored['specials'].items() if name!='SPECIAL1')
    restored['watch']['time_filters']={}
    ui_model.save(restored,path)
    assert 'time_filters' not in ui_model.load(path)['watch']
    assert ui_model.make_scenario(ui_model.load(path),{})['commands'][0]['chat_id']=='TEST'

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
    from strategy_recipe.registry import default_settings
    monkeypatch.setenv('OZ_SPECIAL_TIME_FILTERS',time_env(value))
    _,_,live=load_strategy_inputs({},['SPECIAL1'],None,{'SYMBOLS':'XAUUSD+'})
    _,_,backtest=load_strategy_inputs({},['SPECIAL1'],{},{'SYMBOLS':'XAUUSD+'})
    # LIVE takes the saved setting from its environment; a backtest given its own (empty) setting does not.
    assert live['SPECIAL1'].recipe['strategy_intent']['final_time_filters']=={}
    assert backtest['SPECIAL1'].recipe['strategy_intent']['final_time_filters']==default_settings('SPECIAL1')[1]
    from event_backtest.ui_model import save
    save({'target_mode':'WATCH','specials':{},'watch':{'text':'test'}},tmp_path/'backtest.json')
    assert control.load_special_settings()==value
