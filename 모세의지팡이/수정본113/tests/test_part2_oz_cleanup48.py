"""Isolated Part2 OZ facade checks (no historical FVG/TREND adapters required)."""
from __future__ import annotations
import inspect
import pandas as pd
import pytest
from calculations import allzone
from generic_backtest.live_source import load_live_program_module


def test_allzone_retains_same_live_calculation_methods():
    live=load_live_program_module('monitor_OZ')
    for name in allzone._CORE_METHODS:
        expected=inspect.getattr_static(live.OZMonitor,name)
        if name in allzone._CHECKPOINT_WRAPPED:
            expected=getattr(live.OZMonitor,name).__wrapped__
        assert inspect.getattr_static(allzone._OZRules,name) is expected
    assert allzone.percentile_states is live.percentile_states
    for name in ('regime_filter','super_filter','REGIME_COLUMNS','SUPER_COLUMNS'):
        assert not hasattr(allzone,name)
    for name in ('use_regime','use_super','_super_fact'):
        assert not hasattr(allzone._OZRules,name)
    assert set(allzone.PROFILE_KEYS)=={('NORMAL','OZ'),('BLIND','OZ'),('NORMAL','BREAKER'),('BLIND','BREAKER')}


@pytest.mark.parametrize('direction,col,sign',[('LONG','low',-1),('SHORT','high',1)])
def test_shared_breaker_requires_strict_forming_b0_break(direction,col,sign):
    live=load_live_program_module('monitor_OZ')
    f=pd.DataFrame({'time':pd.date_range('2026-09-01',periods=8,freq='min',tz='UTC'),
                    'low':[100.]*8,'high':[100.]*8})
    cross=f.time.iloc[-3]
    for same,price,expected in [(False,100.,False),(False,100.+sign,True),(True,100.+sign,False)]:
        sample=f.copy();sample.loc[len(sample)-1,col]=price
        when=sample.time.iloc[-1] if same else cross
        assert allzone._OZRules._breaker_bo_break_trigger(sample,direction,100.,when) is expected
        assert live.OZMonitor._breaker_bo_break_trigger(sample,direction,100.,when) is expected
