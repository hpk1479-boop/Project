"""Reporting only: fixed diagnostic tasks, no production partition changes."""
from pathlib import Path
import concurrent.futures
import csv
import datetime as dt
import json
import time
from collections import Counter
import sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/live_daily_partition_virtual'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def execute(task):
    if task['scenario']['end']>'2025-09-08':
        raise RuntimeError('USER_SCOPE_END: month measurements cancelled before replay')
    from event_backtest import runner
    original=runner.relative_path
    def relative(root,path):
        p=Path(path).resolve()
        return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else original(root,p)
    runner.relative_path=relative
    r=runner.run_chunk(task)
    r['process_cpu_at_finish']=time.process_time()
    return r

def main():
    from event_backtest.settings import scenario
    from event_backtest.runner import runtime_config,code_hash
    from event_backtest.keyframes import read_index
    from event_backtest.calendar import warm_start
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    OUT.mkdir(parents=True,exist_ok=True)
    captures=[]
    for month in ('08','09'):
        matches=list((warehouse/('captures/XAUUSD+/BAR/2025/'+month)).glob('*/capture.delta2'))
        assert len(matches)==1
        p=matches[0];index=read_index(p)
        captures.append({'path':p.parent.relative_to(warehouse).as_posix(),'start':'2025-'+month+'-01',
            'end':'2025-'+('09' if month=='08' else '10')+'-01',
            'observed_days':[dt.datetime.fromtimestamp(r['first_ms']/1000,dt.timezone.utc).date().isoformat() for r in index['days']]})
    cases=[('week_single','2025-09-08',7),('week_3day','2025-09-08',3),('week_daily','2025-09-08',1)]
    measurements=[];references={}
    for name,end,days in cases:
        s=scenario(symbol='XAUUSD+',start='2025-09-01',end=end,strategies=['SPECIAL1'],overlap_trading_days=3)
        config=runtime_config(s);tasks=[];cursor=dt.date(2025,9,1);limit=dt.date.fromisoformat(end)
        while cursor<limit:
            stop=min(cursor+dt.timedelta(days=days),limit);start=cursor.isoformat();finish=stop.isoformat()
            warm=warm_start(start,3,captures);out=OUT/name/f'chunk_{len(tasks):03d}';out.mkdir(parents=True,exist_ok=True)
            tasks.append({'scenario':s,'config':config,'start':start,'end':finish,'warm_start':warm,
                'captures':[c for c in captures if c['end']>warm and c['start']<finish],
                'out':str(out),'run_id':name,'warehouse':str(warehouse)})
            cursor=stop
        began=time.perf_counter();cpu=time.process_time();started=dt.datetime.now(dt.timezone.utc).isoformat()
        print(json.dumps({'case':name,'phase':'start','chunks':len(tasks)}),flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=min(12,len(tasks))) as pool:
            results=list(pool.map(execute,tasks))
        ended=dt.datetime.now(dt.timezone.utc).isoformat();wall=time.perf_counter()-began
        worker_cpu={}
        for r in results:worker_cpu[r['pid']]=max(worker_cpu.get(r['pid'],0),r['process_cpu_at_finish'])
        rows=[]
        for t in tasks:
            with (Path(t['out'])/'alerts.csv').open(encoding='utf-8-sig') as f:rows.extend(csv.DictReader(f))
        sig=Counter(json.dumps({k:v for k,v in r.items() if k!='run_id'},sort_keys=True,ensure_ascii=False) for r in rows)
        group='week' if name.startswith('week') else 'month'
        references.setdefault(group,sig);ref=references[group]
        record={'case':name,'start':'2025-09-01','end_exclusive':end,'started_utc':started,'finished_utc':ended,
            'wall_seconds':wall,'total_cpu_seconds':time.process_time()-cpu+sum(worker_cpu.values()),
            'cpu_definition':'parent elapsed CPU + each worker total process CPU at its final task; excludes final worker teardown',
            'chunks':len(tasks),'workers':min(12,len(tasks)),'alerts':len(rows),'identical':ref==sig,
            'added':[json.loads(v) for v in (sig-ref).elements()],'removed':[json.loads(v) for v in (ref-sig).elements()],
            'warmup_bundles':sum(r['warmup_bundles'] for r in results),'bundles':sum(r['bundles'] for r in results),
            'code_hash':code_hash()}
        measurements.append(record)
        (OUT/'partition_timings.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in record.items() if k not in ('added','removed')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
