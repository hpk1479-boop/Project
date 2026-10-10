"""Run the real production worker/STAFF/engine on a supplied capture.

DuckDB is not needed by run_chunk. If absent, support.py omits only the warehouse
module's import of DuckDB; the production ResultWriter remains unchanged. This
is explicitly NOT evidence for parent-process SQL planning or CSV merging.
"""
from pathlib import Path
import argparse,hashlib,json,os,platform,sys,time
from support import install_warehouse


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);p.add_argument('warehouse',type=Path)
    p.add_argument('capture',help='Capture directory relative to warehouse');p.add_argument('output',type=Path)
    p.add_argument('--strategy',choices=['SPECIAL1','ALL'],required=True);p.add_argument('--start',default='2026-09-01')
    p.add_argument('--end',default='2026-09-02');p.add_argument('--warm-start');p.add_argument('--overlap-days',type=int,default=0)
    p.add_argument('--affinity',action='store_true');a=p.parse_args()
    a.project=a.project.resolve();a.warehouse=a.warehouse.resolve();a.output=a.output.resolve()
    sys.dont_write_bytecode=True;a.output.mkdir(parents=True,exist_ok=True)
    pinned=None
    if a.affinity and hasattr(os,'sched_getaffinity'):
        pinned=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{pinned})
    module=install_warehouse(a.project)
    from event_backtest.runner import run_chunk,runtime_config
    from event_backtest.settings import scenario
    s=scenario(symbol='XAUUSD+',start=a.start,end=a.end,mode='BAR',strategies=[a.strategy],overlap_trading_days=a.overlap_days,cores=1)
    task={'scenario':s,'config':runtime_config(s),'start':s['start'],'end':s['end'],'warm_start':a.warm_start or s['start'],
          'captures':[{'path':a.capture}],'run_id':'0'*32,'out':str(a.output),'warehouse':str(a.warehouse),'transport':'replay'}
    began=time.perf_counter();result=run_chunk(task);wall=time.perf_counter()-began
    summary={'strategy':a.strategy,'symbol':'XAUUSD+','mode':'BAR','start':a.start,'end_exclusive':a.end,
             'warm_start':task['warm_start'],'overlap_days':a.overlap_days,'wall_including_setup_seconds':wall,
             'worker_replay_seconds':result['elapsed_seconds'],'bundles':result['bundles'],'alerts':result['alerts'],
             'notifications':result['notifications'],'processed_start_ms':result['processed_start_ms'],'processed_end_ms':result['processed_end_ms'],
             'csv_sha256':hashlib.sha256((a.output/'alerts.csv').read_bytes()).hexdigest(),
             'python':platform.python_version(),'platform':platform.system(),'affinity_cpu':pinned,
             'without_duckdb_import':module._validation_without_duckdb,
             'limitations':['single replay worker; parent SQL planning/import/export not executed',
                            'supplied capture is one day, not a complete week',
                            'no prior overlap capture available; overlap=0 for this regression comparison',
                            'Linux, not the original Windows/MT5 host']}
    (a.output/'comparison_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
