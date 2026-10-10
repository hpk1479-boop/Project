"""Order-sensitive and multiset notification comparisons, without rerunning input."""
from pathlib import Path
from collections import Counter
import argparse,csv,json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/engine_optimization'

def rows(path):
    with Path(path).open(encoding='utf-8',newline='') as f:
        return [{k:v for k,v in row.items() if k!='run_id'} for row in csv.DictReader(f)]

def compare(before,after):
    left,right=rows(before),rows(after)
    key=lambda row:json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    a,b=Counter(map(key,left)),Counter(map(key,right))
    content=lambda rows:Counter(key({k:v for k,v in row.items() if k!='signal_id'}) for row in rows)
    expand=lambda c:[{'count':n,**json.loads(v)} for v,n in sorted(c.items())]
    return {'before_alerts':len(left),'after_alerts':len(right),'exact_order_equal':left==right,
            'content_equal':content(left)==content(right),
            'added':expand(b-a),'removed':expand(a-b)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();warehouse=Path(a.warehouse)
    comparisons={}
    comparisons['ALL']=compare(warehouse/'runs/engineopt_before_week_all/alerts.csv',warehouse/'runs/engineopt_after_week_all_final/alerts.csv')
    for i in range(1,8):
        old=json.loads((ROOT/f'검증결과/data_selection/approved/week_special{i}.json').read_text('utf-8'))
        # Compare worker emission order on both sides. The public merged CSV is
        # sorted by DuckDB and is not evidence of the engine's dispatch order.
        assert len(old['chunks'])==1
        comparisons[f'SPECIAL{i}']=compare(warehouse/old['chunks'][0]['alerts_csv'],warehouse/f'runs/engineopt_after_week_special{i}/alerts.csv')
    (OUT/'alert_comparisons.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,result in comparisons.items():print(name,result['before_alerts'],result['after_alerts'],result['exact_order_equal'])
    if not all(v['content_equal'] for v in comparisons.values()):
        raise RuntimeError('Alert content differences require investigation before year measurement')

if __name__=='__main__':main()
