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

def _meaning(overrides):
    """SPECIAL3 exactly as LIVE and backtest load it, with the given trading-time setting."""
    from event_application import load_strategy_inputs
    return load_strategy_inputs({},['SPECIAL3'],overrides,{'SYMBOLS':'XAUUSD+'})[2]['SPECIAL3'].recipe['strategy_intent']

def _allowed(meaning,hour,minute,api):
    from domain_clock import event_scope
    from special_time_slot import session_filters_allowed
    with event_scope(_at(hour,minute),'test',{}):
        return session_filters_allowed(api,meaning['final_time_filters'])

# config MAIN_*와 무관하게 SPECIAL3 전용 시간(09-11/16-18/21-24)으로만 판정한다.
@pytest.mark.parametrize('hour,minute,expected',[
    (8,59,False),(9,0,True),(10,30,True),(11,0,True),(11,1,False),(12,0,False),
    (15,59,False),(16,0,True),(18,0,True),(18,1,False),(20,59,False),
    (21,0,True),(23,1,True),(23,30,True),(23,59,True),(0,0,False),(1,0,False)])
def test_special3_code_default_alert_hours(hour,minute,expected):
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'0000-2359')
    assert _allowed(_meaning({}),hour,minute,api)==expected

def test_special3_setup_has_no_session_limit():
    assert _meaning({})['time_filters']==[]
    from event_composer_domain import TimePolicy
    assert TimePolicy({'OPENING_NEWYORK':'2100-2300'}).allows(())

def test_special3_slot_override_wins():
    override=_meaning({'SPECIAL3':{'MAIN_ASIA':{'enabled':True,'start':'12:00','end':'13:00'},'MAIN_NEWYORK':{'enabled':False}}})
    api=SimpleNamespace(time_allowed=lambda _:True,config_get=lambda *a:'')
    for hour,minute,expected in [(10,0,False),(12,30,True),(22,0,False)]:
        assert _allowed(override,hour,minute,api)==expected
    # 수정본115: 세션을 하나도 고르지 않은 설정은 시간 제한이 없다(24시간).
    nothing=_meaning({'SPECIAL3':{}})
    for hour in (3,10,22):
        assert _allowed(nothing,hour,0,api)

def test_special3_source_default_and_2400():
    from special_settings_model import source_defaults
    from special_time_slot import _hhmm
    _,filters=source_defaults(ROOT/'Part1/program/SPECIAL','SPECIAL3')
    assert filters=={'MAIN_ASIA':'0900-1100','MAIN_LONDON':'1600-1800','MAIN_NEWYORK':'2100-2400'}
    assert _hhmm('24:00')=='2400'
    with pytest.raises(ValueError):_hhmm('24:01')
