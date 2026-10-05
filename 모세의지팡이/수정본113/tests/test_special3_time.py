from pathlib import Path
import datetime as dt,socket,sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
KST=dt.timezone(dt.timedelta(hours=9))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a,**k):raise AssertionError('test network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,denied)
    monkeypatch.setattr(socket,'create_connection',denied)

def _at(hour,minute):
    return int(dt.datetime(2025,9,1,hour,minute,tzinfo=KST).timestamp()*1000)

def _plugin(overrides):
    from event_application import load_strategy_inputs
    return load_strategy_inputs({},['SPECIAL3'],overrides)[2]['SPECIAL3']

# config MAIN_*와 무관하게 SPECIAL3 전용 시간(09-11/16-18/21-24)으로만 판정한다.
@pytest.mark.parametrize('hour,minute,expected',[
    (8,59,False),(9,0,True),(10,30,True),(11,0,True),(11,1,False),(12,0,False),
    (15,59,False),(16,0,True),(18,0,True),(18,1,False),(20,59,False),
    (21,0,True),(23,1,True),(23,30,True),(23,59,True),(0,0,False),(1,0,False)])
def test_special3_code_default_alert_hours(hour,minute,expected):
    from domain_clock import event_scope
    plugin=_plugin({})
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'0000-2359')
    with event_scope(_at(hour,minute),'test',{}):
        assert plugin._final_alert_time_allowed(api)==expected

def test_special3_no_opening_filter_on_final_touch():
    plugin=_plugin({})
    chains=[plugin._main_3_timed_chain(s,tf) for s in plugin.SYMBOLS for tf in plugin.CROSS_TFS]
    assert len(chains)==4 and all(c['final_time_filters']==() for c in chains)
    from event_composer_domain import TimePolicy
    assert TimePolicy({'OPENING_NEWYORK':'2100-2300'}).allows(())

def test_special3_slot_override_wins():
    from domain_clock import event_scope
    plugin=_plugin({'SPECIAL3':{'MAIN_ASIA':{'enabled':True,'start':'12:00','end':'13:00'},'MAIN_NEWYORK':{'enabled':False}}})
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'')
    for hour,minute,expected in [(10,0,False),(12,30,True),(22,0,False)]:
        with event_scope(_at(hour,minute),'test',{}):
            assert plugin._final_alert_time_allowed(api)==expected
    off=_plugin({'SPECIAL3':{}})
    with event_scope(_at(10,0),'test',{}):assert not off._final_alert_time_allowed(api)

def test_special3_source_default_and_2400():
    from special_settings_model import source_defaults
    from special_time_slot import _hhmm
    _,filters=source_defaults(ROOT/'Part1/program/SPECIAL','SPECIAL3')
    assert filters=={'MAIN_ASIA':'0900-1100','MAIN_LONDON':'1600-1800','MAIN_NEWYORK':'2100-2400'}
    assert _hhmm('24:00')=='2400'
    with pytest.raises(ValueError):_hhmm('24:01')

def test_special3_time_dialog_shows_and_saves_own_hours():
    import tkinter as tk
    import special_ui as ui
    from special_settings_model import source_defaults
    _,filters=source_defaults(ROOT/'Part1/program/SPECIAL','SPECIAL3')
    root=tk.Tk();root.withdraw();saved=[]
    try:
        ui.time_dialog(root,'SPECIAL3',None,filters,{'MAIN_ASIA':'0800-1200','MAIN_LONDON':'1400-1800','MAIN_NEWYORK':'2100-2400'},saved.append)
        dialog=root.winfo_children()[-1]
        entries=[root.getvar(w.cget('textvariable')) for w in dialog.winfo_children() if w.winfo_class()=='TEntry']
        assert entries==['09:00','11:00','16:00','18:00','21:00','24:00']
        [w for w in dialog.winfo_children() if w.winfo_class()=='TButton' and w.cget('text')=='저장'][0].invoke()
    finally:root.destroy()
    assert saved==[{'MAIN_ASIA':{'enabled':True,'start':'0900','end':'1100'},
                    'MAIN_LONDON':{'enabled':True,'start':'1600','end':'1800'},
                    'MAIN_NEWYORK':{'enabled':True,'start':'2100','end':'2400'}}]
