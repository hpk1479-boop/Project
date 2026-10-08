from pathlib import Path
import sys,json,csv,concurrent.futures,datetime as dt
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def execute(task):
    from event_backtest import runner
    original=runner.relative_path
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    def relative(root,path):
        path=Path(path).resolve()
        return path.relative_to(warehouse).as_posix() if path.is_relative_to(warehouse) else original(root,path)
    runner.relative_path=relative
    return runner.run_chunk(task)

if __name__=='__main__':
    from event_backtest.settings import scenario,work_periods
    from event_backtest.runner import runtime_config
    from event_backtest.keyframes import read_index
    from event_backtest.calendar import warm_start
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    captures=[]
    for month in ('08','09'):
        paths=list((warehouse/('captures/XAUUSD+/BAR/2025/'+month)).glob('*/capture.delta2'));assert len(paths)==1
        p=paths[0];index=read_index(p)
        captures.append({'path':str(p.parent),'start':'2025-'+month+'-01','end':'2025-'+('09' if month=='08' else '10')+'-01',
            'observed_days':[dt.datetime.fromtimestamp(r['first_ms']/1000,dt.timezone.utc).date().isoformat() for r in index['days']]})
    s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-10-01',strategies=['SPECIAL1'],overlap_trading_days=3)
    config=runtime_config(s);tasks=[]
    for size in ('MONTH','WEEK'):
        for i,(start,end) in enumerate(work_periods(s['start'],s['end'],size)):
            warm=warm_start(start,3,captures)
            tasks.append({'scenario':s,'config':config,'start':start,'end':end,'warm_start':warm,
                'captures':[c for c in captures if c['end']>warm and c['start']<end],
                'out':str(OUT/('partition_'+size)/f'chunk_{i:03d}'),'run_id':'partition','warehouse':''})
    for task in tasks:Path(task['out']).mkdir(parents=True,exist_ok=True)
    if '--summarize-existing' not in sys.argv:
        with concurrent.futures.ProcessPoolExecutor(max_workers=6) as pool:
            futures={pool.submit(execute,t):t for t in tasks}
            for future in concurrent.futures.as_completed(futures):
                r=future.result();print(json.dumps({'start':r['task_start'],'end':r['task_end'],'bundles':r['bundles'],'seconds':r['elapsed_seconds']},ensure_ascii=False),flush=True)
    else:
        for task in tasks:
            assert json.loads((Path(task['out'])/'progress.json').read_text('utf-8'))['percent']==100
    records={}
    for size in ('MONTH','WEEK'):
        rows=[]
        for f in sorted((OUT/('partition_'+size)).glob('chunk_*/alerts.csv')):
            with f.open(encoding='utf-8-sig') as h:rows.extend(csv.DictReader(h))
        records[size]=rows
    def signature(row):return json.dumps({k:v for k,v in row.items() if k!='run_id'},sort_keys=True,ensure_ascii=False)
    from collections import Counter
    m,w=(Counter(signature(r) for r in records[k]) for k in ('MONTH','WEEK'))
    result={'identical':m==w,'monthly_alerts':sum(m.values()),'weekly_alerts':sum(w.values()),
        'only_monthly':[json.loads(k) for k in (m-w).elements()],
        'only_weekly':[json.loads(k) for k in (w-m).elements()],'warmup_trading_days':3}
    (OUT/'partition_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print({k:v for k,v in result.items() if not isinstance(v,list)},flush=True)
