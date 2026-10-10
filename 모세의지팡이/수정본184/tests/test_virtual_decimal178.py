"""178: decimal RR targets, without a price tolerance or different tie/spread rules."""
from pathlib import Path
from types import SimpleNamespace
import math
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest.virtual_entry import VirtualEntry


def result(direction, entry, stop, reached, *, rr=2., spread=0., stop_touch=False):
    alert = dict(signal_id='decimal', strategy='CUSTOM', symbol='TEST', tf='1m', direction=direction,
                 time_ms=0, signal_source='OZ', signal_price=entry, b0_price=stop)
    calc = VirtualEntry([alert], spread, {'schema': 2, 'mode': 'IMMEDIATE', 'stop': {'kind': 'OZ_B0'}})
    if direction == 'LONG':
        high, low = reached, stop if stop_touch else entry
    else:
        high, low = stop - spread if stop_touch else entry - spread, reached
    view = SimpleNamespace(time=np.array([0, 60]), columns=('open', 'high', 'low', 'close'),
        values=np.array([[entry, high, low, entry], [entry, 1e6, -1e6, entry]]))
    calc.observe(60_000, {'1m': view}, end_ms=60_000)
    return next(row for row in calc.results()[1] if row['rr'] == rr)


@pytest.mark.parametrize('direction,entry,stop,target', [
    ('SHORT', 28189.85, 28210.75, 28148.05),
    ('LONG', 10.1, 10., 10.3),
])
def test_exact_rr2_touch_and_representable_non_touch(direction, entry, stop, target):
    assert result(direction, entry, stop, target)['result'] == 'WIN'
    before = math.nextafter(target, -math.inf if direction == 'LONG' else math.inf)
    assert result(direction, entry, stop, before)['result'] == 'UNCLOSED'
    before_tick = target + (-.01 if direction == 'LONG' else .01)
    assert result(direction, entry, stop, before_tick)['result'] == 'UNCLOSED'
    assert result(direction, entry, stop, target, stop_touch=True)['result'] == 'LOSS'


def test_short_exact_target_after_spread_addition():
    # 0.1 + 0.2 is 0.30000000000000004 in binary; ask exactly touches the RR2 target 0.3.
    assert result('SHORT', .5, .6, .1, spread=.2)['result'] == 'WIN'
    assert result('SHORT', .5, .6, math.nextafter(.1, math.inf), spread=.2)['result'] == 'UNCLOSED'


def test_long_spread_entry_target_and_non_touch():
    # A bid entry 0.2 plus spread 0.1 gives decimal entry 0.3 and RR1 target 0.5 from stop 0.1.
    assert result('LONG', .2, .1, .5, rr=1., spread=.1)['result'] == 'WIN'
    assert result('LONG', .2, .1, math.nextafter(.5, -math.inf), rr=1., spread=.1)['result'] == 'UNCLOSED'


def test_spread_points_conversion_keeps_decimal_exact_touch():
    from event_backtest.virtual_entry import pricing
    spread=pricing({'symbol': 'TEST', 'spread_points': 3}, [{'point': .1}], {})['spread_price']
    assert result('LONG', .2, .1, .9, rr=1., spread=spread)['result'] == 'WIN'
    assert result('LONG', .2, .1, math.nextafter(.9, -math.inf), rr=1., spread=spread)['result'] == 'UNCLOSED'


def test_decimal_zero_risk_is_not_a_tiny_positive_entry():
    assert result('LONG', .2, .3, 1., spread=.1)['result'] == 'PASS_RISK'


@pytest.mark.parametrize('workers', [1, 2])
def test_decimal_outcomes_match_in_sequential_and_parallel_recording_runs(tmp_path, workers):
    import csv
    from datetime import datetime, timezone
    from test_virtual_finalization178 import capture, frame, START, DAY, MINUTE
    from event_backtest.virtual_entry import calculate
    observations=[];alerts=[];expected={};periods=[]
    date=lambda stamp: datetime.fromtimestamp(stamp/1000, timezone.utc).date().isoformat()
    cases=[('SHORT',28189.85,28210.75,28148.05),('LONG',10.1,10.,10.3)]
    for direction,entry,stop,target in cases:
        for kind in ('touch','before','both'):
            end=START+(len(alerts)+1)*DAY;opened=end-MINUTE
            signal=f'{direction}-{kind}'
            reached=math.nextafter(target,-math.inf if direction=='LONG' else math.inf) if kind=='before' else target
            hi,lo=(reached,stop if kind=='both' else entry) if direction=='LONG' else (stop if kind=='both' else entry,reached)
            history=[(opened//1000-n,entry,entry,entry,entry) for n in (120,60,0)]
            observations.append((opened,[frame('1m',history,len(observations)+1)]))
            observations.append((end+1,[frame('1m',history[:-1]+[(opened//1000,entry,hi,lo,entry),
                (end//1000,entry,1e6,-1e6,entry)],len(observations)+1)]))
            alerts.append(dict(signal_id=signal,strategy='CUSTOM',symbol='TEST',tf='1m',direction=direction,
                time_ms=opened,signal_source='OZ',signal_price=entry,b0_price=stop))
            expected[signal]={'touch':'WIN','before':'UNCLOSED','both':'LOSS'}[kind]
            periods.append({'start':date(end-DAY),'end':date(end)})
    capture(tmp_path/'capture',observations)
    path=tmp_path/'alerts.csv'
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(alerts[0]));writer.writeheader();writer.writerows(alerts)
    out=tmp_path/'run';out.mkdir()
    calculate(path,[dict(start=date(START),end=date(end+DAY),path='capture',
        server_time={'utc_offset':0,'dst':'NONE'})],tmp_path,
        dict(start=date(START),end=date(end),symbol='TEST',strategies=['CUSTOM'],_available_periods=periods,
            virtual_entry={'schema':2,'mode':'IMMEDIATE','stop':{'kind':'OZ_B0'}}),{},out,workers=workers)
    with (out/'virtual_trades.csv').open(encoding='utf-8-sig',newline='') as f:
        rr2={row['signal_id']:row['result'] for row in csv.DictReader(f) if float(row['rr'])==2.}
    assert rr2==expected
