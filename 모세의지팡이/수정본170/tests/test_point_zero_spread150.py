"""Zero spread needs no symbol point; a nonzero spread still requires it."""
from pathlib import Path
import csv,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.virtual_entry import calculate,pricing
from test_virtual_common113 import oz_policy
from test_virtual_entry import alert,view

MISSING=[{},{'POINT_NAS100':''}]


@pytest.mark.parametrize('config',MISSING)
def test_zero_spread_needs_no_point(config):
    s={'symbol':'NAS100','spread_points':{'NAS100':0.0}}
    assert pricing(s,[{}],config)=={'symbol':'NAS100','point':None,'point_source':None,
                                     'spread_points':0.0,'spread_price':0.0}


@pytest.mark.parametrize('config',MISSING)
def test_nonzero_spread_still_requires_point(config):
    s={'symbol':'NAS100','spread_points':{'NAS100':5}}
    with pytest.raises(ValueError,match=r'종목 point \(NAS100\) 값이 없습니다'):pricing(s,[{}],config)


def test_given_point_is_kept_and_checked_at_zero_spread():
    s={'symbol':'NAS100','spread_points':{'NAS100':0}}
    assert pricing(s,[{}],{'POINT_NAS100':'0.01'})['point']==.01
    assert pricing(s,[{'point':.1}],{})['point_source']=='capture'
    for wrong in ('abc','-1','0'):
        with pytest.raises(ValueError):pricing(s,[{}],{'POINT_NAS100':wrong})


def test_zero_spread_run_without_point_matches_run_with_point(tmp_path,monkeypatch):
    from event_backtest import virtual_source
    rows=[(0,10,10,9,9,8),(60,10,30,7,9,8),(120,10,11,9,9,8),(180,10,11,9,9,8)]
    def stream(*a,check=lambda:None,**kw):
        for i,row in enumerate(rows):
            check();yield row[0]*1000,{('NAS100','1m'):view(rows[:i+1])}
    monkeypatch.setattr(virtual_source,'shared_observations',stream)
    signals=[{**alert(),'symbol':'NAS100','signal_id':str(i)} for i in range(2)]
    runs=[]
    for name,config in (('missing',{}),('given',{'POINT_NAS100':'0.01'})):
        out=tmp_path/name;out.mkdir()
        with (out/'alerts.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(signals[0]));w.writeheader();w.writerows(signals)
        s={'start':'1970-01-01','end':'1970-01-02','symbol':'NAS100','strategies':['SPECIAL1'],
           'spread_points':{'NAS100':0},'virtual_entry':oz_policy()}
        result=calculate(out/'alerts.csv',[],out,s,config,out)
        trades=[{k:v for k,v in row.items() if k!='run_id'}
                for row in csv.DictReader((out/'virtual_trades.csv').open(encoding='utf-8-sig'))]
        runs.append((result,trades))
    (missing,missing_trades),(given,given_trades)=runs
    assert missing['pricing']['point'] is None and given['pricing']['point']==.01
    assert missing['processed_signals']==2 and missing_trades
    assert missing['summary']==given['summary'] and missing_trades==given_trades
