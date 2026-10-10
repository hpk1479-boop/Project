"""External behavior gates with Wire v2 LIVE/bundled MSP3 BACKTEST.
Raw evidence remains available; only tagged set ordering is canonicalized.
"""
import argparse,datetime as dt,json,sys
from pathlib import Path
from unittest.mock import patch
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture,engine
from part1_host.synthetic import SyntheticMarket,TIMEFRAMES,capture_market
from part1_host.wire_v2 import Publisher
from staff_golden import parity,scenario
from staff_golden.parity_compare import evidence_compare

class LiveV2:
    def __init__(self,data):self.data=data;self.publisher=Publisher(data.symbol)
    def advance_to(self,second):self.second=second
    def wires(self):
        yield '*',self.publisher.bundle((tf,self.data.payload(tf,self.second)) for tf in TIMEFRAMES)

class ActualLiveV2:
    def __init__(self,folder,symbol):
        self.source=engine.SecondFeed(folder,symbol);self.publisher=Publisher(symbol)
    def advance_to(self,second):self.source.advance_to(second)
    def wires(self):
        feeds=[(f.timeframe,s) for f,s in zip(self.source.feeds,self.source.latest) if s is not None]
        if feeds:yield '*',self.publisher.bundle(feeds)

def counts(result):
    ev=result['evidence']
    return {'final_alerts':len(ev['finals']),'special_alerts':len(ev['special']),
        'telegram_deliveries':len(ev['telegram']),
        'recipient_ids':sorted({str(x[1]) for x in ev['telegram']}),
        'fvg_events':sum(x['request'].get('strategy')=='FVG' and x['request'].get('kind')!='FVG_CURRENT' for x in ev['manager_events']),
        'manager_transitions':len(ev['manager_events']),'oz_profiles':len(ev['oz_state'])}

def run_case(name,*,symbol,start,window,data=None,capture_v1=None,capture_v2=None,s0_reference=False):
    folder=OUT/name;folder.mkdir(exist_ok=False)
    commands=tuple((chat,text if symbol=='XAUUSD+' else text.replace('골드',symbol)) for chat,text in scenario.WATCHES)
    if data is not None:
        export=folder/'MSP3';capture_market(data,export,wire_version=2)
        feeds=('live',LiveV2(data),engine.SecondFeed(export,symbol))
    else:
        feeds=(engine.SecondFeed(capture_v1,symbol),ActualLiveV2(capture_v1,symbol),engine.SecondFeed(capture_v2,symbol))
    records={}
    original_factory=parity.baseline_host.Part1Runtime
    def configured_runtime(*a,**kw):
        if symbol=='BTCUSD':
            kw['config_overrides']={'STAFF_ALLOWED_SYMBOLS':'BTCUSD','TARGET_SYMBOLS':'BTCUSD'}
        return original_factory(*a,**kw)
    with patch.object(parity,'START',start),patch.object(parity,'SYMBOL',symbol),patch.object(parity,'WATCHES',commands), \
         patch.object(parity.baseline_host,'Part1Runtime',configured_runtime):
        for label,part1,feed in zip(('before_live','after_live','after_backtest'),
                                  (S0/'baseline_input/Part1',ROOT/'Part1',ROOT/'Part1'),feeds):
            print('START',name,label,flush=True)
            record=parity.run(data,part1,feed,['SPECIAL7'],window)
            write(folder/(label+'.json'),record);records[label]=record
            assert 'error' not in record['evidence']['source_health'],record['evidence']['source_health']
            assert not any('전략을 이해하지 못했습니다' in x[2] for x in record['evidence']['telegram'])
    base=records['before_live']['evidence']
    comparisons={key:evidence_compare(base,value['evidence']) for key,value in records.items() if key!='before_live'}
    if s0_reference:
        original=read(S0/'parity_240/before_live.json')['evidence']
        comparisons['S0_reference']=evidence_compare(original,base)
    summary={'equal':all(v['equal'] for v in comparisons.values()),'comparisons':comparisons,
        'symbol':symbol,'start':start,'end':start+window,'seconds':window,
        'input':'synthetic' if data is not None else 'ACTUAL_MT5_STRATEGY_TESTER',
        'counts':{k:counts(v) for k,v in records.items()},
        'mandatory_fields':['finals','special','telegram','manager_events','source_health','oz_state','fvg_state','watch_state'],
        'internal_dataframe_gate':False,'metrics':{k:v['metrics'] for k,v in records.items()}}
    write(folder/'summary.json',summary);print(name,summary['equal'],summary['counts'],flush=True)
    assert summary['equal'],comparisons
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('case',choices=['G3','expanded','weekday_expanded','actual','actual_btc']);args=parser.parse_args()
    if args.case=='G3':
        run_case('parity_240_S6',symbol=scenario.SYMBOL,start=scenario.START,window=240,data=scenario.market(),s0_reference=True)
    elif args.case=='expanded':
        start=int(dt.datetime(2026,9,26,0,0,tzinfo=dt.timezone.utc).timestamp());window=1200
        data=SyntheticMarket('BTCUSD',start,start+window,history_days=30,seed=0,pattern=scenario.PATTERN)
        run_case('weekend_BTC_1200_configured',symbol='BTCUSD',start=start,window=window,data=data)
    elif args.case=='weekday_expanded':
        start=int(dt.datetime(2026,9,24,0,0,tzinfo=dt.timezone.utc).timestamp());window=1200
        data=SyntheticMarket('XAUUSD+',start,start+window,history_days=30,seed=0,pattern=scenario.PATTERN)
        run_case('weekday_XAU_1200',symbol='XAUUSD+',start=start,window=window,data=data)
    elif args.case=='actual':
        a=next((S0/'actual_mt5').glob('capture_*'));b=next((OUT/'final_mt5_v2').glob('capture_*'))
        info=read(OUT/'final_mt5_v2/result.json')
        run_case('actual_behavior_240',symbol='XAUUSD+',start=info['start_s'],window=240,capture_v1=a,capture_v2=b)
    else:
        a=next((OUT/'btc_weekend_previous_v1').glob('capture_*'));b=next((OUT/'btc_weekend_previous_v2').glob('capture_*'))
        info=read(OUT/'btc_weekend_previous_v2/result.json')
        run_case('actual_BTC_weekend_600_configured',symbol='BTCUSD',start=info['start_s'],window=600,capture_v1=a,capture_v2=b)
