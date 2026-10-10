"""Serial, resumable measurement matrix. Start only after other replays finish."""
from pathlib import Path
import argparse,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args()
    env={**os.environ,'PYTHONHASHSEED':'0'}
    def invoke(label,args):
        log=OUT/'jobs'/(label+'.log')
        print('START '+label,flush=True)
        with log.open('w',encoding='utf-8') as f:
            result=subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,env=env,
                stdout=f,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError(label+' failed; see relative job log')
        print('DONE '+label,flush=True)
    invoke('weeks',['build/run_parallel_cases.py','weeks','--warehouse',a.warehouse,'--workers','1'])
    def annual(workers,size='MONTH',start='keyframe'):
        label=f'year_{size.lower()}_{workers}_{start}'
        path=OUT/(label+'.json')
        if not path.exists():invoke(label,['build/measure_parallel_year.py','--warehouse',a.warehouse,
            '--workers',str(workers),'--work-size',size,'--capture-start',start])
        return json.loads(path.read_text('utf-8'))
    monthly=[annual(n) for n in (6,12,14)]
    best=min(monthly,key=lambda r:r['elapsed_seconds'])
    fortnight=annual(best['cores'],'FORTNIGHT')
    chosen=min((best,fortnight),key=lambda r:r['elapsed_seconds'])
    annual(chosen['cores'],chosen['scenario']['work_size'],'beginning')
    summary={'month_best_workers':best['cores'],'best_workers':chosen['cores'],
             'best_work_size':chosen['scenario']['work_size'],'best_run_id':chosen['run_id'],
             'criterion':'lowest measured elapsed_seconds including host setup, pool work and result merge; no performance gate'}
    (OUT/'measured_default.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
