"""Presentation-only comparator regression; these are ledger units, not signals."""
from pathlib import Path
import sys,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Part2'))
import pandas as pd
import pytest
from live_replay.compare import compare
from live_replay.runtime import close_time_ns

def ledgers():
    common=dict(strategy='SPECIAL1',symbol='XAUUSD+',direction='SHORT',session=['MAIN_NEWYORK'],
                source_spec_id='PIPELINE_1@1h',source_spec_ids=['PIPELINE_1@1h'])
    rows=[]
    for order,tf,stamp in [(1,'3m','13:40:30'),(2,'1m','13:40:45')]:
        ns=pd.Timestamp('2026-08-03 '+stamp,tz='UTC').value
        rows.append(dict(common,source_tf=tf,alert_time_ns=ns,event_order=order,
                         delivery_time_ns=ns,display_time_ns=close_time_ns(ns,tf)))
    return (dict(origin='SYNTHETIC_ORACLE',timestamp_policy='PART1_EVENT_TIME',alerts=copy.deepcopy(rows)),
            dict(alerts=list(reversed(rows)),delivered=rows,pending_close_count=0))

def test_close_different_tfs_may_display_in_different_order_from_live_signals():
    live,replay=ledgers();r=compare(live,replay,mode='CLOSE')
    assert r['status']=='MATCH_NONEMPTY' and r['event_ordering_same']

@pytest.mark.parametrize('corruption',['reverse','duplicate_order','wrong_timestamp'])
def test_close_does_not_sort_away_real_order_or_timestamp_errors(corruption):
    live,replay=ledgers()
    if corruption=='reverse':replay['alerts'].reverse()
    elif corruption=='duplicate_order':replay['alerts'][0]['event_order']=1
    else:replay['alerts'][0]['alert_time_ns']+=1_000_000_000
    assert compare(live,replay,mode='CLOSE')['status']=='MISMATCH'

def test_close_late_delivery_is_not_presented_before_it_is_known():
    live,replay=ledgers()
    first=replay['alerts'][0]
    first['delivery_time_ns']=pd.Timestamp('2026-08-03 13:43:00',tz='UTC').value
    replay['alerts'].reverse()
    assert compare(live,replay,mode='CLOSE')['status']=='MATCH_NONEMPTY'

def test_realtime_still_rejects_changed_signal_order():
    live,replay=ledgers()
    for row in replay['alerts']:row['display_time_ns']=row['alert_time_ns']
    assert compare(live,replay,mode='REALTIME')['status']=='MISMATCH'
