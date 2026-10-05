"""Isolated replay measurements; all evidence paths are project/warehouse relative."""
from pathlib import Path
import argparse,cProfile,json,pstats,sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/engine_optimization'

def main():
    p=argparse.ArgumentParser();p.add_argument('--before',action='store_true');p.add_argument('--profile',action='store_true')
    p.add_argument('--strategies',default='SPECIAL1');p.add_argument('--label',required=True)
    p.add_argument('--warehouse',required=True);p.add_argument('--start',default='2025-09-01');p.add_argument('--end',default='2025-09-08')
    a=p.parse_args();host=OUT/'before_runtime' if a.before else ROOT
    sys.path[:0]=[str(host/'Part2'),str(host/'Part1/program')]
    from event_backtest.settings import scenario
    from event_backtest.runner import run_chunk,runtime_config,resolve_captures
    from event_backtest.warehouse import Warehouse
    s=scenario(symbol='XAUUSD+',start=a.start,end=a.end,strategies=a.strategies,overlap_trading_days=0)
    # Reuse the catalog's captured build plan while another process records.
    # This is a read-only measurement manifest, not a second catalog writer.
    plan=OUT/'year_build/plan.json'
    if plan.is_file() and a.start=='2025-09-01' and a.end=='2025-09-08':
        captures=json.loads(plan.read_text('utf-8'))['reuse'];missing=[]
    else:
        catalog=Warehouse(a.warehouse)
        try:captures,missing=resolve_captures(catalog,s)
        finally:catalog.close()
    if missing:raise ValueError(missing)
    out=Path(a.warehouse)/'runs'/('engineopt_'+a.label);out.mkdir(parents=True,exist_ok=False)
    task={'scenario':s,'config':runtime_config(s),'out':str(out),'run_id':a.label,'start':s['start'],'end':s['end'],
          'warm_start':s['start'],'captures':captures,'warehouse':a.warehouse,'transport':'replay'}
    profile=cProfile.Profile() if a.profile else None
    if profile:profile.enable()
    result=run_chunk(task)
    if profile:
        profile.disable()
        stats=pstats.Stats(profile).strip_dirs();stats.dump_stats(str(out/'profile.pstats'))
        rows=[{'function':f'{k[0]}:{k[1]}({k[2]})','primitive_calls':v[0],'calls':v[1],'self_seconds':v[2],'cumulative_seconds':v[3]}
              for k,v in stats.stats.items()]
        rows.sort(key=lambda r:r['cumulative_seconds'],reverse=True)
        (out/'profile_functions.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    # Worker files belong to evidence, not the external warehouse catalog.
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
