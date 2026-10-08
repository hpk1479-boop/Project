"""Finite comparison batches. Each worker starts a fresh interpreter and state."""
from pathlib import Path
import argparse,concurrent.futures,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def child(job):
    label,args,*seed=job;out=OUT/'jobs';out.mkdir(exist_ok=True)
    with (out/(label+'.log')).open('w',encoding='utf-8') as f:
        began=time.perf_counter()
        p=subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONHASHSEED':seed[0] if seed else '0'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'label':label,'exit_code':p.returncode,'seconds':time.perf_counter()-began}
def main():
    p=argparse.ArgumentParser();p.add_argument('case',choices=('behavior','month_pairs','weeks','seeds'));p.add_argument('--warehouse',required=True)
    p.add_argument('--workers',type=int,default=1);a=p.parse_args();jobs=[]
    if a.case=='behavior':
        jobs=[(case+'_'+mode,['build/verify_parallel_behavior.py',mode,case]) for case in ('synthetic240','timer239') for mode in ('live','replay')]
    elif a.case=='seeds':
        for seed in ('0','1','random'):
            label='final_seed'+seed
            if not (Path(a.warehouse)/'runs'/('parallel26_'+label)/'result.json').exists():
                jobs.append((label,['build/measure_parallel_oz.py','--warehouse',a.warehouse,'--label',label,'--trace'],seed))
    else:
        for i in range(1,8):
            variants=('all','selected') if a.case=='month_pairs' else ('after',)
            for variant in variants:
                label=f'{a.case}_{variant}_special{i}'
                args=['build/measure_parallel_oz.py','--warehouse',a.warehouse,'--strategies',f'SPECIAL{i}','--label',label]
                if a.case=='month_pairs':args+=['--end','2025-10-01']
                if variant=='all':args+=['--oz-full']
                if variant=='before':args+=['--before']
                if not (Path(a.warehouse)/'runs'/('parallel26_'+label)/'result.json').exists():jobs.append((label,args))
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for result in pool.map(child,jobs):
            results.append(result);print(json.dumps(result),flush=True)
            (OUT/(a.case+'_jobs.json')).write_text(json.dumps(results,indent=2),encoding='utf-8')
    if any(x['exit_code'] for x in results):raise SystemExit(1)
if __name__=='__main__':main()
