"""Full one-week SPECIAL1/ALL A/B verification using a real external warehouse.

Requires the project's normal dependencies, including DuckDB, in BOTH runs.
No DuckDB shim, capture extension, synthetic input or automatic recording is used.
The runner rejects missing capture/overlap coverage before replay starts.
"""
from pathlib import Path
import argparse,csv,datetime as dt,hashlib,json,os,subprocess,sys,time


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before',type=Path,required=True,help='Unmodified revision 36 directory')
    p.add_argument('--after',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--warehouse',type=Path,required=True,help='Existing complete external capture warehouse')
    p.add_argument('--start',required=True,help='YYYY-MM-DD UTC, inclusive')
    p.add_argument('--end',required=True,help='YYYY-MM-DD UTC, exclusive; exactly seven days after start')
    p.add_argument('--symbol',default='XAUUSD+')
    p.add_argument('--overlap-days',type=int,default=3)
    p.add_argument('--output',type=Path)
    p.add_argument('--_child',choices=['before','after'],help=argparse.SUPPRESS)
    p.add_argument('--_strategy',choices=['SPECIAL1','ALL'],help=argparse.SUPPRESS)
    a=p.parse_args()
    if dt.date.fromisoformat(a.end)-dt.date.fromisoformat(a.start)!=dt.timedelta(days=7):
        p.error('--end must be exactly seven days after --start')
    if a.overlap_days<0:p.error('--overlap-days must not be negative')
    a.before=a.before.resolve();a.after=a.after.resolve();a.warehouse=a.warehouse.resolve()
    a.output=(a.output or a.after/'검증결과/part2_optimization37/real_week').resolve()
    for root in (a.before,a.after):
        if not (root/'Part2/event_backtest/runner.py').is_file():p.error('project is missing Part2/event_backtest/runner.py')
    return a


def child(a):
    project=a.before if a._child=='before' else a.after
    sys.dont_write_bytecode=True;sys.path[:0]=[str(project/'Part1/program'),str(project/'Part2')]
    import duckdb  # Required; a missing dependency is an explicit failed run.
    from event_backtest.runner import run
    from event_backtest.settings import scenario,warehouse_path
    s=scenario(symbol=a.symbol,start=a.start,end=a.end,mode='BAR',strategies=[a._strategy],
               overlap_trading_days=a.overlap_days,cores=1)
    began=time.perf_counter();result=run(s,a.warehouse,cores=1,sequential=True);wall=time.perf_counter()-began
    if result.get('status')!='COMPLETE':raise RuntimeError('replay did not complete')
    csv_path=warehouse_path(a.warehouse,result['alerts_csv'])
    summary={'strategy':a._strategy,'variant':a._child,'run_id':result['run_id'],'status':result['status'],
             'start':a.start,'end_exclusive':a.end,'overlap_days':a.overlap_days,'mode':'BAR','symbol':a.symbol,
             'elapsed_wall_seconds':wall,'runner_elapsed_seconds':result['elapsed_seconds'],
             'alerts_csv':result['alerts_csv'],'captures':result['captures'],'config_hash':result['config_hash'],
             'code_hash':result['code_hash'],'csv_sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest(),
             'duckdb_version':duckdb.__version__}
    (a.output/f'{a._strategy}_{a._child}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')


def canonical_csv(path):
    with Path(path).open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f);fields=tuple(name for name in reader.fieldnames if name!='run_id')
        rows=[tuple(row[name] for name in fields) for row in reader]
    # Keep duplicate rows, compare all notification columns, ignore only run ID.
    return fields,sorted(rows)


def main():
    a=arguments();a.output.mkdir(parents=True,exist_ok=True)
    if a._child:child(a);return 0
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    results={}
    for strategy in ('SPECIAL1','ALL'):
        for variant in ('before','after'):
            cmd=[sys.executable,'-B',str(Path(__file__).resolve()),'--before',str(a.before),'--after',str(a.after),
                 '--warehouse',str(a.warehouse),'--start',a.start,'--end',a.end,'--symbol',a.symbol,
                 '--overlap-days',str(a.overlap_days),'--output',str(a.output),'--_child',variant,'--_strategy',strategy]
            with (a.output/f'{strategy}_{variant}.log').open('w',encoding='utf-8') as log:
                proc=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
            if proc.returncode:
                result={'complete':False,'failed_strategy':strategy,'failed_variant':variant,'exit_code':proc.returncode,
                        'message':'Inspect the matching log. No missing capture or dependency is bypassed.'}
                (a.output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False));return 1
        before=json.loads((a.output/f'{strategy}_before.json').read_text(encoding='utf-8'))
        after=json.loads((a.output/f'{strategy}_after.json').read_text(encoding='utf-8'))
        left=canonical_csv(a.warehouse/before['alerts_csv']);right=canonical_csv(a.warehouse/after['alerts_csv'])
        match=left==right and before['captures']==after['captures'] and before['config_hash']==after['config_hash']
        results[strategy]={'csv_equal_excluding_run_id':left==right,'same_input_captures':before['captures']==after['captures'],
                           'same_config':before['config_hash']==after['config_hash'],'before_csv_rows':len(left[1]),'after_csv_rows':len(right[1]),
                           'before_seconds':before['elapsed_wall_seconds'],'after_seconds':after['elapsed_wall_seconds'],'passed':match}
    report={'complete':True,'passed':all(r['passed'] for r in results.values()),'comparisons':results,
            'scope':'real seven-day capture with requested overlap; full parent runner, SQL merge and CSV export',
            'ignored_csv_columns':['run_id']}
    (a.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
