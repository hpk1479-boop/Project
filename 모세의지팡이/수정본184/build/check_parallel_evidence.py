"""Regenerate compact evidence from completed runs, never from partial CSVs."""
from pathlib import Path
import argparse,collections,csv,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'

def alerts(folder):
    with (folder/'alerts.csv').open(encoding='utf-8-sig',newline='') as f:
        return [{k:v for k,v in row.items() if k!='run_id'} for row in csv.DictReader(f)]

def compare(left,right):
    def encoded(row,omit=()):return json.dumps({k:v for k,v in row.items() if k not in omit},ensure_ascii=False,sort_keys=True)
    a=collections.Counter(map(encoded,left));b=collections.Counter(map(encoded,right))
    removed=[json.loads(x) for x in (a-b).elements()];added=[json.loads(x) for x in (b-a).elements()]
    semantic=collections.Counter(encoded(x,('signal_id',)) for x in left)==collections.Counter(encoded(x,('signal_id',)) for x in right)
    return {'before':len(left),'after':len(right),'identical':a==b,'ordered_identical':left==right,
            'same_except_id':semantic,'removed':removed,'added':added}

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();runs=Path(a.warehouse)/'runs'
    result={}
    seeds=[]
    for seed in ('0','1','random'):
        f=runs/('parallel26_final_seed'+seed)
        if (f/'result.json').exists():
            r=json.loads((f/'result.json').read_text('utf-8'))
            seeds.append({'seed':seed,'sha256':r['signal_sha256'],'signal_counts':r['signal_counts'],'alerts':alerts(f)})
    result['hash_seeds']={'runs':seeds,'complete':len(seeds)==3,'identical':len(seeds)==3 and all(x['sha256']==seeds[0]['sha256'] and x['alerts']==seeds[0]['alerts'] for x in seeds)}
    first=runs/'engineopt_after_week_all_seed0'
    last=runs/'parallel26_final_seed0'
    if not (last/'result.json').exists():last=runs/'parallel26_a_seed0'
    if all((x/'result.json').exists() for x in (first,last)):
        result['week_all_before_after']=compare(alerts(first),alerts(last))
    result['behavior']={}
    for case in ('synthetic240','timer239'):
        paths=[OUT/'behavior'/('26_'+case+'_'+mode)/'result.json' for mode in ('live','replay')]
        latest=[OUT/'behavior'/('26_'+case+'_'+mode+'_epoch')/'result.json' for mode in ('live','replay')]
        if all(x.exists() for x in latest):paths=latest
        if not all(x.exists() for x in paths):continue
        l,r=[json.loads(x.read_text('utf-8')) for x in paths]
        result['behavior'][case]={'live':l,'replay':r,'same':{k:l.get(k)==r.get(k) for k in ('signal_sha256','alerts','deliveries','errors')}}
    result['oz_month_pairs']={};result['week_before_after']={};result['standalone_vs_all']={}
    allfolder=runs/'parallel26_a_month_all'
    for i in range(1,8):
        full,selected=[runs/f'parallel26_month_pairs_{variant}_special{i}' for variant in ('all','selected')]
        if all((x/'result.json').exists() for x in (full,selected)):
            result['oz_month_pairs'][f'SPECIAL{i}']=compare(alerts(full),alerts(selected))
        before=runs/f'engineopt_after_week_special{i}'
        after=runs/f'parallel26_weeks_after_special{i}'
        if all((x/'result.json').exists() for x in (before,after)):
            result['week_before_after'][f'SPECIAL{i}']=compare(alerts(before),alerts(after))
        if all((x/'result.json').exists() for x in (allfolder,full)):
            result['standalone_vs_all'][f'SPECIAL{i}']=compare([x for x in alerts(allfolder) if x['strategy']==f'SPECIAL{i}'],alerts(full))
    positive_all=runs/'parallel26_positive_june_all';result['positive_june_vs_all']={}
    if (positive_all/'result.json').exists():
        for i in range(1,8):
            solo=runs/('parallel26_find_special4_2025-06' if i==4 else f'parallel26_positive_june_special{i}')
            if (solo/'result.json').exists():
                result['positive_june_vs_all'][f'SPECIAL{i}']=compare([x for x in alerts(positive_all) if x['strategy']==f'SPECIAL{i}'],alerts(solo))
    (OUT/'comparisons.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:({n:{f:v for f,v in row.items() if f not in ('removed','added','alerts')} for n,row in value.items()} if k in ('oz_month_pairs','week_before_after','standalone_vs_all') else 'recorded') for k,value in result.items()},ensure_ascii=False))
if __name__=='__main__':main()
