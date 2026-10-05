"""Seven existing week inputs; no recording or timing gate."""
from pathlib import Path
import argparse,concurrent.futures,csv,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/ui_slots'

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args()
    source=(ROOT/'build/measure_parallel_oz.py').read_text('utf-8')
    source=source.replace("('parallel26_'+a.label)","('ui27_'+a.label)")
    helper=ROOT/'build/ui_week_case.py';helper.write_text(source,encoding='utf-8')
    def run(i):
        log=OUT/f'week_special{i}.log'
        with log.open('w',encoding='utf-8') as f:
            child=subprocess.run([sys.executable,'-B','-X','utf8',str(helper),'--warehouse',a.warehouse,
                '--strategies',f'SPECIAL{i}','--label',f'week_special{i}'],cwd=ROOT,
                stdout=f,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONHASHSEED':'0'},
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if child.returncode:raise RuntimeError(log.name)
        def rows(name):
            with (Path(a.warehouse)/'runs'/name/'alerts.csv').open(encoding='utf-8-sig',newline='') as stream:
                return [{k:v for k,v in r.items() if k!='run_id'} for r in csv.DictReader(stream)]
        old=rows(f'parallel26_weeks_after_special{i}');new=rows(f'ui27_week_special{i}')
        return {'strategy':f'SPECIAL{i}','before':len(old),'after':len(new),'identical':old==new}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(run,range(1,8)):
            results.append(r);print(json.dumps(r),flush=True)
            (OUT/'week_comparison.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    assert all(r['identical'] for r in results)
if __name__=='__main__':main()
