"""Compact numerical tables from completed, isolated replay measurements."""
from pathlib import Path
import argparse,csv,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args()
    runs=Path(a.warehouse)/'runs';rows=[]
    for i in range(1,8):
        for version,name in ((25,f'engineopt_after_week_special{i}'),(26,f'parallel26_weeks_after_special{i}')):
            path=runs/name/'result.json'
            if not path.exists():continue
            r=json.loads(path.read_text('utf-8'));n=r['bundles']
            timings={k:v['ms_per_bundle'] for k,v in r['processor_timings'].items()}
            rows.append({'revision':version,'strategy':f'SPECIAL{i}','bundles':n,'alerts':r['notifications'],
                'seconds':r['elapsed_seconds'],'input_included_ms':r['elapsed_seconds']*1000/n,
                'engine_ms':r['mean_ms'],'input_and_host_ms':r['elapsed_seconds']*1000/n-r['mean_ms'],
                'other_engine_ms':r['mean_ms']-sum(timings.values()),'processor_ms':timings,
                'max_rss_mib':r['max_memory_bytes']/2**20})
    (OUT/'weekly_measurements.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    columns=('revision','strategy','bundles','alerts','seconds','input_included_ms','engine_ms','input_and_host_ms','other_engine_ms','max_rss_mib')
    names=sorted({k for row in rows for k in row['processor_ms']})
    with (OUT/'weekly_measurements.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=(*columns,*names));w.writeheader()
        w.writerows({**{k:row[k] for k in columns},**row['processor_ms']} for row in rows)
    print(json.dumps({'complete_candidate_runs':sum(r['revision']==26 for r in rows),'rows':len(rows)}))

if __name__=='__main__':main()
