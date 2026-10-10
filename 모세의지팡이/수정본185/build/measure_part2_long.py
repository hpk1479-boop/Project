"""Finite requested measurements; reuse monthly workers for the quarter comparison."""
from pathlib import Path
import sys,json,time,concurrent.futures,importlib.util
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))

def main():
    evidence=R/'검증결과/part2_connection'
    while not (evidence/'year_captures.json').exists():time.sleep(10)
    if not (evidence/'portability_migration.json').exists():
        spec=importlib.util.spec_from_file_location('warehouse_migration',R/'build/migrate_part2_warehouse.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.main()
    # Import the finished implementation only after recording/migration completes.
    from event_backtest.settings import settings,scenario,milliseconds
    from event_backtest.runner import run
    from event_backtest.warehouse import Warehouse
    from event_backtest.system import write_progress
    root=Path(settings()['warehouse'])
    year=scenario(R/'Part2/scenarios/xau_continuous.json')
    quarter=scenario(R/'Part2/scenarios/xau_continuous.json',start='2026-06-01',end='2026-09-01')
    def execute(label,s,sequential):
        path=evidence/(label+'.json')
        if path.exists():return json.loads(path.read_text('utf-8'))
        def emit(kind,data):
            if kind=='CHUNK_COMPLETE':print(label,kind,data,flush=True)
            write_progress(evidence/(label+'_progress.json'),{'event':kind,**data})
        result=run(s,root,sequential=sequential,cores=1 if sequential else None,emit=emit)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(execute,'year_parallel',year,False)
        b=pool.submit(execute,'quarter_sequential',quarter,True)
        parallel=a.result();sequential=b.result()
    w=Warehouse(root)
    diff=root/'runs'/'quarter_comparison.csv'
    count=w.compare(sequential['run_id'],parallel['run_id'],diff,start_ms=milliseconds(quarter['start']),end_ms=milliseconds(quarter['end']))
    counts=w.db.execute('SELECT run_id,count(*) FROM alerts WHERE run_id IN (?,?) AND time_ms>=? AND time_ms<? GROUP BY run_id',
        [sequential['run_id'],parallel['run_id'],milliseconds(quarter['start']),milliseconds(quarter['end'])]).fetchall()
    w.close()
    result={'quarter_sequential_run':sequential['run_id'],'quarter_parallel_from_year_run':parallel['run_id'],
            'period':[quarter['start'],quarter['end']],'differences':count,'counts':counts,'difference_csv':'runs/quarter_comparison.csv',
            'method':'Year parallel June/July/August workers are exactly the requested independent monthly quarter chunks; no duplicate quarter replay.'}
    (evidence/'quarter_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('LONG MEASUREMENTS COMPLETE',result,flush=True)
if __name__=='__main__':main()
