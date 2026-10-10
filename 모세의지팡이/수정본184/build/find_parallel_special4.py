"""Find non-empty real SPECIAL4 evidence if the comparison month has none."""
from pathlib import Path
import argparse,concurrent.futures,csv,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);p.add_argument('--workers',type=int,default=3)
    p.add_argument('--months',default='2024-10,2024-11,2025-04');a=p.parse_args()
    import datetime as dt
    jobs=[]
    for month in a.months.split(','):
        start=dt.date.fromisoformat(month+'-01');end=(start.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
        jobs.append((start.isoformat(),end.isoformat()))
    def run(pair):
        start,end=pair;label='find_special4_'+start[:7];folder=Path(a.warehouse)/'runs'/('parallel26_'+label)
        if not (folder/'result.json').exists():
            with (OUT/'jobs'/(label+'.log')).open('w',encoding='utf-8') as f:
                p=subprocess.run([sys.executable,'-X','utf8','-B','build/measure_parallel_oz.py','--warehouse',a.warehouse,
                    '--strategies','SPECIAL4','--label',label,'--start',start,'--end',end],cwd=ROOT,
                    stdout=f,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONHASHSEED':'0'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if p.returncode:raise RuntimeError(label+' failed')
        result=json.loads((folder/'result.json').read_text('utf-8'))
        with (folder/'alerts.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        return {'start':start,'end':end,'bundles':result['bundles'],'alerts':rows,'notifications':result['notifications']}
    saved=OUT/'special4_actual_search.json'
    rows=json.loads(saved.read_text('utf-8')) if saved.exists() else []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for row in pool.map(run,jobs):
            rows=[r for r in rows if r['start']!=row['start']]
            rows.append(row)
            saved.write_text(json.dumps(sorted(rows,key=lambda r:r['start']),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in row.items() if k!='alerts'}),flush=True)
if __name__=='__main__':main()
