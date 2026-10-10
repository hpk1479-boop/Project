"""A1: full selection and each SPECIAL in a month with an actual SPECIAL4 alert."""
from pathlib import Path
import argparse,concurrent.futures,json,sys
from run_parallel_cases import child,OUT,ROOT
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();jobs=[]
    for strategy in ('ALL',*(f'SPECIAL{i}' for i in range(1,8) if i!=4)):
        label='positive_june_'+strategy.lower()
        if (Path(a.warehouse)/'runs'/('parallel26_'+label)/'result.json').exists():continue
        jobs.append((label,['build/measure_parallel_oz.py','--warehouse',a.warehouse,'--label',label,
            '--strategies',strategy,'--start','2025-06-01','--end','2025-07-01']))
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(child,job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            result=future.result();rows.append(result);print(json.dumps(result),flush=True)
            (OUT/'positive_june_jobs.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    if any(r['exit_code'] for r in rows):raise SystemExit(1)
if __name__=='__main__':main()
