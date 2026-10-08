"""Pricing/CSV regression; post-alert predicates are in test_virtual_common113."""
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pytest
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]
from event_backtest.virtual_entry import VirtualEntry,pricing,load_alerts,RATIOS
from staff_schema import PIPE_VALUE_COLUMNS as C


def alert(direction='LONG',stop=8):
    return dict(signal_id='a',strategy='SPECIAL1',symbol='XAUUSD+',tf='1m',direction=direction,
                time_ms=0,b0_price=stop,b0_time=-60,signal_source='OZ',signal_price=10)


def view(rows):
    values=np.ones((len(rows),len(C)));times=[]
    for i,row in enumerate(rows):
        stamp,op,hi,lo,ma,unused=row;times.append(stamp)
        for name,value in dict(open=op,high=hi,low=lo,close=op+.5,hma_6=ma,hma_17=unused).items():
            values[i,C.index(name)]=value
    return SimpleNamespace(time=np.array(times),values=values)


def test_metadata_point_fallback_and_missing():
    s={'symbol':'XAUUSD+','spread_points':{'XAUUSD+':20}}
    assert pricing(s,[{'point':.01}],{})['spread_price']==.2
    assert pricing(s,[{}],{'POINT_XAUUSD+':'.01'})['point_source']=='config'
    with pytest.raises(ValueError,match='point'):pricing(s,[{}],{})
    with pytest.raises(ValueError):pricing(s,[{'point':.01},{'point':.1}],{})


@pytest.mark.parametrize('spread',[float('nan'),float('inf'),-1])
def test_invalid_spread_is_not_silently_normalized(spread):
    with pytest.raises(ValueError):pricing({'symbol':'TEST','spread_points':spread},[{'point':.01}],{})


def test_registration_notice_not_market_entry(tmp_path):
    path=tmp_path/'alerts.csv'
    path.write_text('signal_id,strategy,symbol,tf,direction,time_ms\na,WATCH,TEST,,REGISTERED,0\n',encoding='utf8')
    rows,ignored=load_alerts(path)
    assert rows==[] and ignored==1
