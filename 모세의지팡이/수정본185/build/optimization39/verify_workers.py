"""Compare actual full parent runs on a real external capture warehouse.

Requires normal dependencies including DuckDB. Never fabricates captures,
changes the overlap, retries a failed production run, or records new MT5 data.
Both revisions receive identical worker counts and partition settings.
"""
from pathlib import Path
import argparse
import csv
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time


def parse():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before',type=Path,required=True,help='Unmodified revision38 directory')
    p.add_argument('--after',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--warehouse',type=Path,required=True)
    p.add_argument('--start',required=True,help='YYYY-MM-DD UTC inclusive')
    p.add_argument('--end',required=True,help='YYYY-MM-DD UTC exclusive')
    p.add_argument('--symbol',default='XAUUSD+')
    p.add_argument('--workers',type=int,required=True)
    p.add_argument('--overlap-days',type=int,default=3)
    p.add_argument('--work-size',choices=['MONTH','FORTNIGHT','WEEK','DAY'],default='MONTH')
    p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--output',type=Path)
    p.add_argument('--_variant',choices=['before','after'],help=argparse.SUPPRESS)
    p.add_argument('--_strategy',choices=['SPECIAL1','ALL'],help=argparse.SUPPRESS)
    p.add_argument('--_rep',type=int,help=argparse.SUPPRESS)
    a=p.parse_args()
    if dt.date.fromisoformat(a.start)>=dt.date.fromisoformat(a.end):p.error('start must precede end')
    if a.workers<1 or a.repeat<1 or a.overlap_days<0:p.error('invalid workers/repeat/overlap-days')
    a.before=a.before.resolve();a.after=a.after.resolve();a.warehouse=a.warehouse.resolve()
    a.output=(a.output or a.after/'검증결과/worker39/real_warehouse').resolve()
    for project in (a.before,a.after):
        if not (project/'Part2/event_backtest/runner.py').is_file():p.error('missing project runner')
        if a.warehouse.is_relative_to(project):p.error('warehouse must be outside both projects')
    return a


def child(a):
    project=a.before if a._variant=='before' else a.after
    sys.dont_write_bytecode=True
    sys.path[:0]=[str(project/'Part1/program'),str(project/'Part2')]
    import duckdb  # Deliberately no fallback: missing SQL must fail visibly.
    from event_backtest.runner import run
    from event_backtest.settings import scenario,warehouse_path
    settings=scenario(symbol=a.symbol,start=a.start,end=a.end,mode='BAR',strategies=[a._strategy],
                      overlap_trading_days=a.overlap_days,cores=a.workers,work_size=a.work_size)
    started=time.perf_counter()
    result=run(settings,a.warehouse,cores=a.workers)
    elapsed=time.perf_counter()-started
    if result.get('status')!='COMPLETE':raise RuntimeError('run did not complete')
    plan=sorted((r['task_start'],r['task_end'],r['warm_start'],r['bundles'],r['warmup_bundles'])
                for r in result['chunks'])
    summary={'status':result['status'],'elapsed_seconds':elapsed,'captures':result['captures'],
             'config_hash':result['config_hash'],'plan':plan,'partition':result['partition'],
             'alerts_csv':result['alerts_csv'],'run_id':result['run_id'],
             'worker_scheduling':result.get('worker_scheduling'), 'duckdb_version':duckdb.__version__}
    path=a.output/f'{a._strategy}_{a._rep}_{a._variant}.json'
    path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')


def csv_content(path):
    with Path(path).open(encoding='utf-8',newline='') as handle:
        reader=csv.DictReader(handle)
        fields=[f for f in reader.fieldnames if f!='run_id']
        return fields,[tuple(row[f] for f in fields) for row in reader]


def main():
    a=parse();a.output.mkdir(parents=True,exist_ok=True)
    if a._variant:child(a);return 0
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    comparisons={}
    for strategy in ('SPECIAL1','ALL'):
        pairs=[]
        for rep in range(1,a.repeat+1):
            for variant in (('before','after') if rep%2 else ('after','before')):
                command=[sys.executable,'-B',str(Path(__file__).resolve()),'--before',str(a.before),
                         '--after',str(a.after),'--warehouse',str(a.warehouse),'--start',a.start,
                         '--end',a.end,'--symbol',a.symbol,'--workers',str(a.workers),
                         '--overlap-days',str(a.overlap_days),'--work-size',a.work_size,
                         '--repeat',str(a.repeat),'--output',str(a.output),'--_variant',variant,
                         '--_strategy',strategy,'--_rep',str(rep)]
                with (a.output/f'{strategy}_{rep}_{variant}.log').open('w',encoding='utf-8') as log:
                    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=env)
                if result.returncode:
                    failure={'passed':False,'failed_strategy':strategy,'repeat':rep,'variant':variant,
                             'exit_code':result.returncode,'reason':'Inspect the matching log; no dependency/input bypass.'}
                    (a.output/'comparison.json').write_text(json.dumps(failure,indent=2),encoding='utf-8')
                    print(json.dumps(failure));return 1
            before=json.loads((a.output/f'{strategy}_{rep}_before.json').read_text('utf-8'))
            after=json.loads((a.output/f'{strategy}_{rep}_after.json').read_text('utf-8'))
            left=csv_content(a.warehouse/before['alerts_csv']);right=csv_content(a.warehouse/after['alerts_csv'])
            pairs.append({'csv_equal_excluding_run_id':left==right,
                          'same_captures':before['captures']==after['captures'],
                          'same_config':before['config_hash']==after['config_hash'],
                          'same_task_ranges_and_bundle_counts':before['plan']==after['plan'],
                          'same_partition':before['partition']==after['partition'],
                          'before_rows':len(left[1]),'after_rows':len(right[1]),
                          'before_seconds':before['elapsed_seconds'],'after_seconds':after['elapsed_seconds']})
        before=statistics.median(p['before_seconds'] for p in pairs)
        after=statistics.median(p['after_seconds'] for p in pairs)
        comparisons[strategy]={'pairs':pairs,'before_median_seconds':before,'after_median_seconds':after,
                               'reduction_fraction':1-after/before,
                               'passed':all(all(p[key] for key in ('csv_equal_excluding_run_id','same_captures',
                                   'same_config','same_task_ranges_and_bundle_counts','same_partition')) for p in pairs)}
    report={'passed':all(p['passed'] for p in comparisons.values()),'comparisons':comparisons,
            'scope':'normal parent runner including DuckDB; identical worker counts and partition settings',
            'ignored_csv_columns':['run_id'],'row_order_preserved':True}
    (a.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
