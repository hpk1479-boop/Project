"""Same-input old/new OZ diagnosis only; no old result is a pass criterion."""
from pathlib import Path
import sys,json,logging
from dataclasses import is_dataclass,asdict
from types import SimpleNamespace
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'tests'),str(R/'Part1/program')]
from test_oz_rewrite import view,edit,profile,seed
from oz_engine.common import PROFILE_KEYS,PERCENTILES
from staff_schema import legacy_frame
import monitor_OZ as old
logging.disable(logging.CRITICAL)

def canonical(v):
    if isinstance(v,pd.Timestamp):return int(v.timestamp())
    if is_dataclass(v):return canonical(asdict(v))
    if isinstance(v,dict):return {str(k):canonical(x) for k,x in v.items()}
    if isinstance(v,(set,frozenset)):return sorted(canonical(x) for x in v)
    if isinstance(v,(list,tuple)):return [canonical(x) for x in v]
    if isinstance(v,np.generic):return v.item()
    return v

records=[]
for vm,tm in PROFILE_KEYS:
    for case in ('completion','normal_reject','timer_expired','silent_consumption'):
        n=profile(vm,tm);v=view();c=seed(n,v)
        vals=np.full(len(v),100.);vals[-3]=98.;basis=np.arange(len(v))*.01+99
        v=edit(v,price_hma_6=vals,**{f+'_val':vals for f in ('RSI','STO','DI')})
        if n.use_breaker:
            low=np.full(len(v),99.);low[-1]=98.9;v=edit(v,low=low)
        middle=edit(v,price_hma_6=98,open=103,**{f+'_val':98 for f in ('RSI','STO','DI')})
        upper=edit(v,price_hma_6=100,price_regime_basis=basis,**{f+'_val':100 for f in ('RSI','STO','DI')},**{f+'_basis':basis for f in ('RSI','STO','DI')})
        data={'1m':v,'3m':upper if case=='normal_reject' else middle,'6m':upper}
        o=old.OZMonitor('XAUUSD+',{},n.telegram,n.watch,vm,tm,staff_client=SimpleNamespace(),persistence=False,source_time=n._source_time)
        t=pd.Timestamp(int(v.time[-3]),unit='s')
        for family in PERCENTILES:o._register_percentile_candidate('1m','LONG',family,99.,t,t)
        oc=o.candidates['1m','LONG'];oc.hma_b0_price=99.;oc.hma_b0_time=t;oc.hma_cross_time=t;o._refresh_true_b0(oc)
        old_data={tf:old.OZSnapshotFeatures.compose(legacy_frame('XAUUSD+',tf,x.snapshot),tf,3.) for tf,x in data.items()}
        results=[]
        for obj,inputs in ((o,old_data),(n,data)):
            if case=='timer_expired':
                candidate=obj.candidates['1m','LONG'];candidate.hma_cross_time=pd.Timestamp(int(v.time[-10]),unit='s') if obj is o else int(v.time[-10])
                obj._maintain_base_candidate('1m','LONG',inputs['1m']);decision=None
            elif case=='silent_consumption':decision=obj._probe_silent_completion(inputs,'1m','LONG','DIAGNOSTIC')
            else:decision=obj._candidate_completion_decision(inputs,'1m','LONG',require_external=False,commit_validation=True)
            results.append(canonical({'decision':decision,'candidate':obj.candidates['1m','LONG'],
                'registrations':[obj.percentile_candidates['1m','LONG',f] for f in PERCENTILES]}))
        records.append({'profile':[vm,tm],'case':case,'same':results[0]==results[1],'old':results[0],'new':results[1]})
result={'gate':False,'cases':len(records),'differences':[r for r in records if not r['same']],
        'judgment':'Same inputs include full history; completion, permanent rejection, timer drop and silent consumption are compared after timestamp-unit normalization. Old outputs are diagnostic only.','records':records}
out=R/'검증결과/oz_rewrite/old_oz_diagnostic.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('diagnostic cases',len(records),'differences',len(result['differences']))
