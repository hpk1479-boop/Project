"""Backtest TREND = Part1 LIVE INDICATOR Facts (trend state, on-demand score, reuse)."""
from pathlib import Path
import importlib.util
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generic_backtest.watch.engines.trend_math import _live,_facts,TrendCalculator
from generic_backtest.watch.engines.trend import HistoricalTrendEngine

LEGACY=Path(__file__).resolve().parents[2]/'Part1'/'audit'/'fixtures'/'legacy_strategy_TREND.py'


def legacy():
    spec=importlib.util.spec_from_file_location('legacy_strategy_TREND_p2',LEGACY)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def data(seed,n=650):
    rng=np.random.default_rng(seed);c=100+np.cumsum(rng.normal(0,.3,n))
    return pd.DataFrame({'time':pd.date_range('2026-01-01',periods=n,freq='h'),
        'open':c+.1,'high':c+rng.random(n),'low':c-rng.random(n),'close':c,
        'volume':rng.integers(1,100,n),'hma_50':c-.2})


@pytest.mark.parametrize('seed',range(4))
def test_mss_atr_kernels_match_pre_split_code_exactly(seed):
    old=legacy();f=data(seed,100)
    if seed==1:f.loc[10:15,['high','low','close']]=np.nan
    if seed==2:f.loc[20,['high','low','close']]=np.inf
    if seed==3:f.loc[:,['high','low','close']]=100.
    for left,right in ((5,5),(1,2),(0,0)):
        pd.testing.assert_series_equal(_facts.mss_state(f,left,right),old.mss_state(f,left,right),check_exact=True)
    pd.testing.assert_series_equal(_facts.atr_trend_state(f),old.atr_trend_state(f),check_exact=True)


@pytest.mark.parametrize('seed',range(3))
def test_full_trend_scores_match_pre_split_code_after_input_changes(seed):
    old=legacy();baseline=old.TrendEngine.__new__(old.TrendEngine);baseline.threshold=40.
    fast=TrendCalculator();f=data(seed)
    for delta in (0.,2.):
        f.loc[len(f)-1,'close']+=delta
        expected=old.TrendEngine.evaluate(baseline,'TEST',f,'1h')
        assert fast.evaluate_score('TEST',f,'1h')==expected
    # the second evaluation reused OPEN-based Facts (only close changed)
    assert fast.facts.stats.carried>0


def test_backtest_trend_state_is_the_live_trend_fact():
    frames=[data(9)]
    for step in range(1,4):
        nxt=data(9,650+step).tail(650).reset_index(drop=True)
        frames.append(nxt)
    engine=HistoricalTrendEngine('TEST','1h')
    live=_live.IndicatorEngine.__new__(_live.IndicatorEngine);live.threshold=40.
    for f in frames:
        expected=live.evaluate_trend('TEST',f,'1h',frame=_facts.standalone_frame(f,'1h'))
        assert engine.calculator.evaluate_trend('TEST',f,'1h')==expected
        assert expected['trend'] in {'UP','DOWN','NEUTRAL'} and 'long_score' not in expected
    assert 'dmi' not in engine.calculator.facts.stats.computed
