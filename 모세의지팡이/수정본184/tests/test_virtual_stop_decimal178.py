"""178: ATR price arithmetic uses the Fact value unchanged and preserves float stop_price API."""
from pathlib import Path
from types import SimpleNamespace
import math
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

from event_backtest.virtual_contract import normalize_virtual_entry
from event_backtest.virtual_entry import VirtualEntry
from event_backtest.virtual_rules import stop_price


class PriceFacts:
    """Isolate price arithmetic from indicator calculation, whose values must be unchanged."""
    def __init__(self, atr, extreme=None):
        self.atr_value=atr;self.extreme_value=extreme;self.calls=[]
    def update(self, *args):
        pass
    def atr(self, tf, stamp, period):
        self.calls.append(('atr',tf,stamp,period))
        return self.atr_value
    def extreme(self, tf, stamp, bars, *, long):
        self.calls.append(('extreme',tf,stamp,bars,long))
        return self.extreme_value


def policy(kind='ATR', multiplier=1):
    stop={'kind':kind,'tf':'1m','multiplier':multiplier} if kind=='ATR' else {'kind':kind,'tf':'1m','bars':1}
    return normalize_virtual_entry({'schema':2,'mode':'IMMEDIATE','stop':stop})


def outcome(direction, price, spread, atr, multiplier, low, high, *, extreme=None, rr=1.):
    facts=PriceFacts(atr,extreme)
    alert=dict(signal_id='atr',strategy='CUSTOM',symbol='TEST',tf='1m',direction=direction,
               time_ms=0,signal_price=price)
    calc=VirtualEntry([alert],spread,policy('ATR' if extreme is None else 'RECENT_EXTREME',multiplier),facts=facts)
    def view(final=False):
        times=np.array([-60,0,60] if final else [-60,0])
        values=np.array([[price,price,price,price],[price,high,low,price],
                         [price,1e6,-1e6,price]] if final else [[price,price,price,price]]*2)
        return SimpleNamespace(time=times,values=values,columns=('open','high','low','close'))
    calc.observe(0,{'1m':view()},end_ms=60_000)
    calc.observe(60_000,{'1m':view(True)},end_ms=60_000)
    return next(row for row in calc.results()[1] if row['rr']==rr), facts.calls


@pytest.mark.parametrize('direction,entry,atr,multiplier,expected',[
    ('LONG',.3,.1,1,.2), ('LONG',.6,.1,3,.3), ('SHORT',.1,.2,1,.3), ('SHORT',.2,.1,3,.5),
])
def test_atr_stop_price_float_api_and_exact_source_arithmetic(direction,entry,atr,multiplier,expected):
    facts=PriceFacts(atr)
    value=stop_price(policy(multiplier=multiplier),{'direction':direction,'tf':'1m'},0,entry,facts)
    assert isinstance(value,float)
    assert value==expected
    assert facts.calls==[('atr','1m',0,14)]
    assert facts.atr_value==atr


@pytest.mark.parametrize('direction,price,spread,atr,multiplier,low,high,expected_stop',[
    ('LONG',.2,.1,.1,1,.2,.2,.2),
    ('LONG',.6,0,.1,3,.3,.6,.3),
    ('SHORT',.1,.1,.2,1,.1,.2,.3),
])
def test_atr_exact_stop_touch_is_loss(direction,price,spread,atr,multiplier,low,high,expected_stop):
    row,calls=outcome(direction,price,spread,atr,multiplier,low,high)
    assert row['result']=='LOSS'
    assert row['stop_price']==expected_stop
    assert len(calls)==1


def test_atr_multiplier_target_touch_non_touch_and_simultaneous_stop():
    assert outcome('LONG',.6,0,.1,3,.5,.9)[0]['result']=='WIN'
    assert outcome('LONG',.6,0,.1,3,.5,math.nextafter(.9,-math.inf))[0]['result']=='UNCLOSED'
    assert outcome('LONG',.6,0,.1,3,.3,.9)[0]['result']=='LOSS'


@pytest.mark.parametrize('direction,price,spread,extreme,low,high',[
    ('LONG',.3,0,.2,.2,.3), ('SHORT',.1,.1,.3,.1,.2),
])
def test_recent_extreme_stop_is_unchanged_and_only_exact_touch_loses(direction,price,spread,extreme,low,high):
    row,calls=outcome(direction,price,spread,.1,1,low,high,extreme=extreme)
    assert row['stop_price']==extreme and row['result']=='LOSS'
    if direction=='LONG':low=math.nextafter(low,math.inf)
    else:high=math.nextafter(high,-math.inf)
    assert outcome(direction,price,spread,.1,1,low,high,extreme=extreme)[0]['result']=='UNCLOSED'
    assert calls==[('extreme','1m',0,1,direction=='LONG')]
