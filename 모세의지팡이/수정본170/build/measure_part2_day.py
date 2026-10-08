from pathlib import Path
import sys,json,uuid,csv,concurrent.futures
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings,scenario,relative_path
from event_backtest.runner import run_chunk,runtime_config,code_hash

def main():
    evidence=R/'검증결과/part2_connection';warehouse=Path(settings()['warehouse'])
    captures={key:json.loads((evidence/f'day_{key}.json').read_text('utf-8'))[0] for key in ('BAR','TICK')}
    for c in captures.values():
        if Path(c['path']).is_absolute():c['path']=relative_path(warehouse,c['path'])
        c.pop('export',None)
    run_id='day_validation_'+uuid.uuid4().hex
    before=code_hash();tasks=[]
    for label,capture,mode in [('BAR',captures['BAR'],'BAR'),('TIMER_filtered',captures['TICK'],'BAR'),('TIMER_tick',captures['TICK'],'TICK')]:
        s=scenario(R/'Part2/scenarios/xau_continuous.json',start='2026-09-25',end='2026-09-26',mode=mode,overlap_trading_days=0)
        tasks.append({'scenario':s,'config':runtime_config(s),'run_id':run_id,'start':s['start'],'end':s['end'],
            'warm_start':s['start'],'captures':[capture],'out':str(warehouse/'runs'/run_id/label),'warehouse':str(warehouse)})
    results={};reused=[]
    for label in ('BAR','TIMER_filtered'):
        prior=evidence/('day_replay_'+label+'.json')
        if prior.exists():results[label]=json.loads(prior.read_text('utf-8'));reused.append(label)
    (evidence/'day_tick_retry.json').write_text(json.dumps({'reason':'Progress JSON replace failed under a Windows reader lock; calculation path unchanged.',
        'failed_run':'day_validation_c71d4a421ee349c4814a76fa6bfb55b2','completed_labels_reused':reused,'retry_run':run_id,
        'retry_code_hash_before':before,'prior_completed_code_hash':'not recorded before the earlier process failed'},indent=2),encoding='utf-8')
    with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
        fs={pool.submit(run_chunk,t):label for label,t in zip(('BAR','TIMER_filtered','TIMER_tick'),tasks) if label not in reused}
        for future in concurrent.futures.as_completed(fs):
            label=fs[future];results[label]=future.result()
            (evidence/('day_replay_'+label+'.json')).write_text(json.dumps(results[label],ensure_ascii=False,indent=2),encoding='utf-8')
            print('DONE',label,results[label],flush=True)
    def alerts(result):
        with (warehouse/result['alerts_csv']).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
    comparison={'BAR_TIMER_alerts_equal':alerts(results['BAR'])==alerts(results['TIMER_filtered']),
                'notifications':results['BAR']['notifications'],'code_hash_before':before,'code_hash_after':code_hash(),'results':results}
    (evidence/'day_replay_comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    assert comparison['BAR_TIMER_alerts_equal']
if __name__=='__main__':main()
