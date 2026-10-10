"""S7: full S6 tree vs S7 LIVE / MSP3; Wonbi baseline changes are counted only."""
import argparse,copy,datetime as dt,hashlib,json,shutil,sys
from collections import Counter
from unittest.mock import patch
from staff_s7_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture,engine,runtime
from part1_host.synthetic import SyntheticMarket,TIMEFRAMES,capture_market
from part1_host.wire_v2 import Publisher
from staff_golden import parity,scenario
from staff_golden.parity_compare import evidence_compare

BEFORE=OUT/'baseline_input/Part1'
if not BEFORE.exists():
    BEFORE.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(SOURCE/'Part1/program',BEFORE/'program')
expected={r['path'].removeprefix('Part1/program/'):r['sha256'] for r in read(OUT/'s6_frozen_manifest.json') if r['path'].startswith('Part1/program/')}
assert expected=={p.relative_to(BEFORE/'program').as_posix():sha(p) for p in (BEFORE/'program').rglob('*') if p.is_file()}

class SyntheticFeed:
    def __init__(self,data,legacy=False):self.data=data;self.legacy=legacy;self.pub=Publisher(data.symbol)
    def advance_to(self,second):self.second=second
    def wires(self):
        feeds=[(tf,self.data.payload(tf,self.second)) for tf in TIMEFRAMES]
        if self.legacy:
            for tf,(t,v,x) in feeds:
                seq=self.pub.sequences.get(tf,0)+1;self.pub.sequences[tf]=seq
                yield tf,capture.pack_wire(self.data.symbol,tf,t,v,x[:,:45],snapshot=seq)
            return
        yield '*',self.pub.bundle(feeds)

class ActualFeed:
    def __init__(self,path,symbol,legacy=False):self.feed=engine.SecondFeed(path,symbol);self.pub=Publisher(symbol);self.legacy=legacy
    def advance_to(self,second):self.feed.advance_to(second)
    def wires(self):
        feeds=[(f.timeframe,s) for f,s in zip(self.feed.feeds,self.feed.latest) if s is not None]
        if self.legacy:
            for tf,(t,v,x) in feeds:
                seq=self.pub.sequences.get(tf,0)+1;self.pub.sequences[tf]=seq
                yield tf,capture.pack_wire(self.feed.symbol,tf,t,v,x[:,:45],snapshot=seq)
            return
        if feeds:yield '*',self.pub.bundle(feeds)

def external_non_wonbi(evidence):
    out=copy.deepcopy(evidence)
    # Explicit user-approved Wonbi recipient IDs. Preserve every other recipient/time/text.
    out['telegram']=[x for x in out['telegram'] if str(x[1]) not in ('881010','881011')]
    for value in out['watch_state'].values():
        if isinstance(value,dict) and isinstance(value.get('watches'),list):
            value['watches']=[w for w in value['watches'] if w.get('watch_type')!='WONBI_TOUCH']
    for feed in out['source_health'].get('feeds',{}).values():
        feed.get('indicators',{}).pop('WONBI',None)
    # Candidate trigger_hits are serialized as tagged dictionaries. Wonbi
    # substate is an approved basis change, never an old-Python regression gate.
    def without_wonbi(value):
        if isinstance(value,list):return [without_wonbi(x) for x in value]
        if not isinstance(value,dict):return value
        result={k:without_wonbi(v) for k,v in value.items() if not k.lower().startswith('wonbi')}
        if result.get('type')=='dict' and isinstance(result.get('value'),list):
            result['value']=[pair for pair in result['value'] if pair[0]!='WONBI']
        return result
    out['oz_state']=without_wonbi(out['oz_state'])
    # Preserve the entire S7 LIVE/backtest check; this projection is S6-only.
    out['finals']=[x for x in out['finals'] if 'WONBI' not in x.get('message','')]
    return out

def counts(ev):
    notifications=[x for x in ev['telegram'] if '감시' not in x[2] and '개인전략 등록' not in x[2]]
    return {'final_alerts':len(ev['finals']),'special_alerts':len(ev['special']),
            'condition_deliveries':len(notifications),'telegram':len(ev['telegram']),
            'manager_transitions':len(ev['manager_events'])}

def run_case(name,symbol,start,window,data=None,path=None):
    folder=OUT/name
    if folder.exists():
        # Preserve each failed diagnostic attempt; never replace a finalized baseline.
        assert not (folder/'summary.json').exists() or not read(folder/'summary.json')['passed']
        i=1
        while (OUT/(name+'_attempt'+str(i))).exists():i+=1
        folder.rename(OUT/(name+'_attempt'+str(i)))
    folder.mkdir(exist_ok=False)
    if data is not None:
        export=folder/'MSP3';capture_market(data,export)
        feeds=[SyntheticFeed(data,True),SyntheticFeed(data),engine.SecondFeed(export,symbol)]
    else:feeds=[ActualFeed(path,symbol,True),ActualFeed(path,symbol),engine.SecondFeed(path,symbol)]
    commands=tuple((chat,text if symbol=='XAUUSD+' else text.replace('골드',symbol)) for chat,text in scenario.WATCHES)
    def host(*a,**kw):
        kw['config_overrides']={'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol}
        return runtime.Part1Runtime(*a,**kw)
    records={}
    with patch.object(parity,'START',start),patch.object(parity,'SYMBOL',symbol),patch.object(parity,'WATCHES',commands),patch.object(parity.baseline_host,'Part1Runtime',host):
        for label,part1,feed in zip(('before_live','after_live','after_backtest'),(BEFORE,ROOT/'Part1',ROOT/'Part1'),feeds):
            print('START',name,label,flush=True)
            record=parity.run(data,part1,feed,['SPECIAL7'],window);records[label]=record;write(folder/(label+'.json'),record)
    before,live,bt=(records[k]['evidence'] for k in ('before_live','after_live','after_backtest'))
    parity_result=evidence_compare(live,bt)
    non_wonbi=evidence_compare(external_non_wonbi(before),external_non_wonbi(live))
    # Record only changed notification counts; no old/new Wonbi calculation regression.
    old=Counter(json.dumps(x,sort_keys=True,ensure_ascii=False) for x in before['telegram'])
    new=Counter(json.dumps(x,sort_keys=True,ensure_ascii=False) for x in live['telegram'])
    result={'passed':parity_result['equal'] and non_wonbi['equal'],'live_backtest':parity_result,'non_wonbi_S6':non_wonbi,
        'changed_notifications_removed':sum((old-new).values()),'changed_notifications_added':sum((new-old).values()),
        'change_authorization':'원비 원본 MT5 전환에 따른 승인된 기준 변경',
        'counts':{k:counts(v['evidence']) for k,v in records.items()},'symbol':symbol,'start':start,'seconds':window,
        'input':'synthetic' if data is not None else 'actual_MT5','old_python_wonbi_numeric_regression':False,
        'metrics':{k:v['metrics'] for k,v in records.items()}}
    write(folder/'summary.json',result);print(name,json.dumps(result,ensure_ascii=False),flush=True)
    assert result['passed']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('case',choices=['G3','weekday','weekend','actual','btc']);args=p.parse_args()
    if args.case=='G3':run_case('parity_240',scenario.SYMBOL,scenario.START,240,data=scenario.market())
    elif args.case in ('weekday','weekend'):
        weekend=args.case=='weekend';symbol='BTCUSD' if weekend else 'XAUUSD+'
        start=int(dt.datetime(2026,9,26 if weekend else 24,tzinfo=dt.timezone.utc).timestamp())
        run_case(args.case+'_1200',symbol,start,1200,data=SyntheticMarket(symbol,start,start+1200,history_days=30,seed=0,pattern=scenario.PATTERN))
    else:
        name='btc_weekend_previous' if args.case=='btc' else 'final_mt5'
        info=read(OUT/f'{name}_sigma3_v2/result.json')
        run_case('actual_'+args.case, 'BTCUSD' if args.case=='btc' else 'XAUUSD+',info['start_s'],info['end_s']-info['start_s'],path=Path(info['retained_export']))
