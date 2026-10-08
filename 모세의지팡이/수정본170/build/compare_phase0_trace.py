"""Read complete or currently flushed traces; never execute domain logic."""
from pathlib import Path
import argparse,gzip,json,math,itertools,collections
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'

def records(path):
    try:
        with gzip.open(path,'rt',encoding='utf-8') as handle:
            for line in handle:
                try:yield json.loads(line)
                except json.JSONDecodeError:return
    except (EOFError,OSError):return

def difference(a,b,path='',tol=0):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        if math.isnan(a) and math.isnan(b):return []
        if a==b or abs(a-b)<=tol:return []
    elif isinstance(a,dict) and isinstance(b,dict):
        result=[]
        for key in sorted(a.keys()|b.keys()):
            if key not in a or key not in b:result.append(dict(path=path+'.'+key,before=a.get(key),after=b.get(key)))
            else:result.extend(difference(a[key],b[key],path+'.'+key,tol))
            if len(result)>=20:break
        return result[:20]
    elif isinstance(a,list) and isinstance(b,list):
        result=[]
        if len(a)!=len(b):result.append(dict(path=path+'.length',before=len(a),after=len(b)))
        for i,(x,y) in enumerate(zip(a,b)):
            result.extend(difference(x,y,path+f'[{i}]',tol))
            if len(result)>=20:break
        return result[:20]
    elif a==b:return []
    return [dict(path=path,before=a,after=b)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=0);args=p.parse_args()
    first={};counts=[collections.Counter(),collections.Counter()];index=0
    pairs=zip(records(OUT/'revision21/signals.jsonl.gz'),records(OUT/'revision22/signals.jsonl.gz'))
    for index,(a,b) in enumerate(pairs,1):
        for i,row in enumerate((a,b)):
            c=row['payload']['content'];counts[i][str(c.get('family',c.get('type')))+':'+str(c.get('event',{}).get('kind',''))]+=1
        for label,tol in [('exact',0),('numeric_tolerance',1e-10)]:
            if label in first:continue
            aa={k:v for k,v in a.items() if k!='engine_seq'};bb={k:v for k,v in b.items() if k!='engine_seq'}
            d=difference(aa,bb,tol=tol)
            if d:first[label]=dict(index=index,before=a,after=b,differences=d)
        if args.limit and index>=args.limit:break
    result=dict(compared_prefix=index,first=first,prefix_counts=[dict(x) for x in counts])
    (OUT/'first_difference.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(compared_prefix=index,first={k:{'index':v['index'],'bundle':v['before']['bundle'],'time':v['before']['time'],'differences':v['differences']} for k,v in first.items()}),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
