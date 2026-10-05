"""Wait for bounded correctness runs, then measure with no competing replay."""
from pathlib import Path
import argparse,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args()
    required=('2024-10','2024-11','2024-12','2025-01','2025-02','2025-03','2025-04','2025-05','2025-06','2025-07','2025-08')
    last=None
    while True:
        path=OUT/'special4_actual_search.json';rows=json.loads(path.read_text('utf-8')) if path.exists() else []
        found={r['start'][:7]:r for r in rows};remaining=[m for m in required if m not in found]
        if remaining!=last:print('Remaining SPECIAL4 real-month checks: '+','.join(remaining),flush=True);last=remaining
        if not remaining:break
        time.sleep(15)
    pairs=[Path(a.warehouse)/'runs'/f'parallel26_month_pairs_{variant}_special{i}'/'result.json'
           for i in range(1,8) for variant in ('all','selected')]
    if not all(p.exists() for p in pairs):raise ValueError('monthly OZ comparisons must finish first')
    positive=[Path(a.warehouse)/'runs'/('parallel26_positive_june_'+n)/'result.json'
              for n in ('all',*(f'special{i}' for i in range(1,8) if i!=4))]
    print('Waiting for complete positive-case June ALL and standalone comparisons.',flush=True)
    while not all(p.exists() for p in positive):time.sleep(15)
    subprocess.run([sys.executable,'-X','utf8','-B','build/check_parallel_evidence.py','--warehouse',a.warehouse],cwd=ROOT,check=True,
        stdout=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    comparisons=json.loads((OUT/'comparisons.json').read_text('utf-8'))
    for name in ('SPECIAL4','SPECIAL5'):
        check=comparisons['positive_june_vs_all'][name]
        if not (check['before']>0 and check['identical']):raise ValueError(name+' positive-case equality needs analysis before timing')
    time.sleep(10) # Let the last completed correctness worker release its process.
    print('Correctness jobs finished. Starting isolated measurement matrix.',flush=True)
    raise SystemExit(subprocess.call([sys.executable,'-X','utf8','-B','build/run_parallel_measurements.py','--warehouse',a.warehouse],cwd=ROOT,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
if __name__=='__main__':main()
