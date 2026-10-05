"""Real worker comparison; optional complete emitted-output trace.

Output MUST be below warehouse, matching the production portable-path contract.
Without DuckDB, only the unchanged validation loader from optimization37 is used;
this is NOT a test of parent SQL planning/merging. No production code is patched.
"""
from pathlib import Path
import argparse, hashlib, json, os, platform, sys, time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('warehouse', type=Path)
    parser.add_argument('capture')
    parser.add_argument('output', type=Path)
    parser.add_argument('--strategy', choices=['SPECIAL1','ALL'], required=True)
    parser.add_argument('--transport', choices=['replay','live'], default='replay')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--cpu', type=int)
    args=parser.parse_args()
    args.project=args.project.resolve();args.warehouse=args.warehouse.resolve();args.output=args.output.resolve()
    args.output.relative_to(args.warehouse)
    args.output.mkdir(parents=True,exist_ok=True)
    sys.dont_write_bytecode=True
    if args.cpu is not None and hasattr(os,'sched_setaffinity'):
        os.sched_setaffinity(0,{args.cpu})
    sys.path.insert(0,str(args.project/'build/optimization37'))
    from support import install_warehouse
    module=install_warehouse(args.project)
    from event_backtest.runner import run_chunk,runtime_config,code_hash
    from event_backtest.settings import scenario,digest
    from event_engine.engine import EventEngine
    from event_engine.domain_support import plain
    production_hash=code_hash()
    trace=hashlib.sha256();trace_count=0;counts={};errors=[]
    original_emit=EventEngine._emit
    original_error=EventEngine._error
    if args.trace:
        def tracked_emit(self,strategy,event,output):
            nonlocal trace_count
            row={'strategy':strategy,'engine_seq':event.engine_seq,'source_time':event.source_time,
                 'output_type':type(output).__name__,'output':plain(vars(output))}
            trace.update(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')+b'\n')
            trace_count+=1
            category=output.content.get('type','DEGRADED') if hasattr(output,'content') else 'TIMER_REQUEST'
            counts[category]=counts.get(category,0)+1
            return original_emit(self,strategy,event,output)
        def tracked_error(self,consumer,event,exc):
            errors.append({'consumer':consumer.name,'source_time':event.source_time,'error':type(exc).__name__+': '+str(exc)})
            return original_error(self,consumer,event,exc)
        EventEngine._emit=tracked_emit;EventEngine._error=tracked_error
    settings=scenario(symbol='XAUUSD+',start='2026-09-01',end='2026-09-02',mode='BAR',
                      strategies=[args.strategy],overlap_trading_days=0,cores=1)
    config=runtime_config(settings)
    task={'scenario':settings,'config':config,'start':settings['start'],'end':settings['end'],
          'warm_start':settings['start'],'captures':[{'path':args.capture}],'run_id':'0'*32,
          'out':str(args.output),'warehouse':str(args.warehouse),'transport':args.transport}
    began=time.perf_counter();result=run_chunk(task);wall=time.perf_counter()-began
    summary={'strategy':args.strategy,'transport':args.transport,'traced':args.trace,
             'worker_replay_seconds':result['elapsed_seconds'],'wall_including_setup_seconds':wall,
             'bundles':result['bundles'],'alerts':result['alerts'],'notifications':result['notifications'],
             'csv_sha256':hashlib.sha256((args.output/'alerts.csv').read_bytes()).hexdigest(),
             'config_hash':digest(config),'production_code_hash':production_hash,'scenario':settings,'trace_count':trace_count,
             'trace_sha256':trace.hexdigest() if args.trace else None,'trace_categories':counts,'errors':errors,
             'max_memory_bytes':result['max_memory_bytes'],'cpu':args.cpu,
             'python':platform.python_version(),'platform':platform.system(),
             'without_duckdb':module._validation_without_duckdb,
             'limits':['one supplied day, not one week','overlap=0 for comparison only',
                       'single worker; parent SQL path not executed',
                       'live transport uses in-memory PipeReceiver, not native Windows/MT5']}
    (args.output/'comparison_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
