"""Locate first semantic difference per family, aligned by input publication."""
from pathlib import Path
import collections,itertools,json
from compare_phase0_trace import records,difference
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'
IDENTITY={'signal_id','condition_key','event_id','fact_revision'}
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if k not in IDENTITY}
    if isinstance(v,list):return [clean(x) for x in v]
    return v
def groups(revision):
    previous=None
    for key,rows in itertools.groupby(records(OUT/f'revision{revision}/signals.jsonl.gz'),lambda x:x['bundle']):
        if previous is not None:yield previous
        previous=key,list(rows)
    if previous is not None and (OUT/f'revision{revision}/result.json').exists():yield previous
def family(row):
    c=row['payload']['content'];return c.get('family',c.get('type','UNKNOWN'))
def main():
    first={};different=collections.Counter();counts=[collections.Counter(),collections.Counter()];last=0
    for (ka,a),(kb,b) in zip(groups(21),groups(22)):
        if ka!=kb:raise ValueError(('bundle mismatch',ka,kb))
        last=ka
        for i,rows in enumerate((a,b)):
            for r in rows:
                c=r['payload']['content'];counts[i][family(r)+':'+str(c.get('event',{}).get('kind',c.get('command',{}).get('action','')))]+=1
        for name in set(map(family,a))|set(map(family,b)):
            aa=[clean(r['payload']) for r in a if family(r)==name]
            bb=[clean(r['payload']) for r in b if family(r)==name]
            diff=difference(aa,bb,tol=1e-10)
            if diff:
                different[name]+=1
                first.setdefault(name,dict(bundle=ka,time=(a or b)[0]['time'],before_count=len(aa),after_count=len(bb),differences=diff))
    result=dict(through_bundle=last,counts=[dict(v) for v in counts],different_bundles=dict(different),first=first,
        note='identity fields ignored only in this semantic diagnostic; original traces retain all fields')
    (OUT/'family_diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(through_bundle=last,different_bundles=dict(different),first=first),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
