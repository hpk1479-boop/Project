"""Polling LIVE regression only, once per S8 external scenario; no new MT5 runs."""
import datetime as dt,sys,socket
from unittest.mock import patch
from event_e1_common import *
import run_staff_s8_behavior as prior

def deny(*args,**kwargs):raise AssertionError('E1 validation blocks external network')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.sendto=deny
cases=[('parity_240','XAUUSD+',prior.scenario.START,240,prior.scenario.market(),None)]
for name,symbol,day in [('weekday_1200','XAUUSD+',24),('weekend_1200','BTCUSD',26)]:
    start=int(dt.datetime(2026,9,day,tzinfo=dt.timezone.utc).timestamp())
    cases.append((name,symbol,start,1200,prior.SyntheticMarket(symbol,start,start+1200,history_days=30,seed=0,pattern=prior.scenario.PATTERN),None))
for name,case,symbol in [('actual_actual','final_mt5_sigma3_v2','XAUUSD+'),('actual_btc','btc_weekend_previous_sigma3_v2','BTCUSD')]:
    info=read(ROOT/'검증결과/staff_s7'/case/'result.json')
    path=ROOT/'검증결과/staff_s7'/case/Path(info['retained_export']).name
    cases.append((name,symbol,info['start_s'],info['end_s']-info['start_s'],None,path))
folder=OUT/'polling';folder.mkdir(exist_ok=False);results=[]
for name,symbol,start,window,data,path in cases:
    feed=prior.SyntheticFeed(data) if data is not None else prior.ActualFeed(path,symbol)
    commands=tuple((chat,text if symbol=='XAUUSD+' else text.replace('골드',symbol)) for chat,text in prior.scenario.WATCHES)
    def host(*args,**kwargs):
        kwargs['config_overrides']={'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol}
        return prior.runtime.Part1Runtime(*args,**kwargs)
    print('START',name,flush=True)
    with patch.object(prior.parity,'START',start),patch.object(prior.parity,'SYMBOL',symbol),patch.object(prior.parity,'WATCHES',commands),patch.object(prior.parity.baseline_host,'Part1Runtime',host):
        record=prior.parity.run(data,ROOT/'Part1',feed,['SPECIAL7'],window)
    write(folder/(name+'_current_live.json'),record)
    before=read(ROOT/'검증결과/staff_s8/baseline_S8/behavior'/name/'after_live.json')['evidence']
    compare=prior.evidence_compare(before,record['evidence'])
    result={'case':name,'passed':compare['equal'],'comparison':compare,
            'counts':prior.counts(record['evidence']),'seconds':window,'symbol':symbol,'runs':1}
    results.append(result);write(OUT/'polling_comparison.json',results)
    print('END',name,result['passed'],result['counts'],flush=True)
    assert result['passed'],compare
