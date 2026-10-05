from pathlib import Path
import sys,json,os,csv
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

if __name__=='__main__':
    from event_backtest.runner import run
    from event_backtest.settings import scenario
    from event_backtest.cancellation import file_check
    from event_backtest.build_plan import current_build,schema_id
    from event_backtest.system import deny_network
    deny_network()
    source=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    src=list((source/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'));assert len(src)==1
    root=OUT/'stop_runs';capture=root/'captures/september';capture.mkdir(parents=True,exist_ok=True)
    for name in ('capture.delta2','storage.json','complete.txt'):
        target=capture/name
        if not target.exists():os.link(src[0].parent/name,target)
    captures=[{'path':'captures/september','start':'2025-09-01','end':'2025-10-01',
        'ea_build_hash':current_build(root),'schema_id':schema_id(),'tick_evidence':{'actual':'MIXED_OR_GENERATED'}}]
    stage=sys.argv[1];run_id=('a' if stage=='replay' else 'b')*32
    output=root/'runs'/run_id;output.mkdir(parents=True,exist_ok=True)
    s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-09-02',strategies=['SPECIAL1'],overlap_trading_days=0,
        result_mode='ALERT_ONLY' if stage=='replay' else 'VIRTUAL_ENTRY')
    s['_run_id']=run_id;events=[];stop=output/'stop.request'
    def emit(kind,data):
        events.append({'event':kind,**data})
        if (stage=='replay' and kind=='RUN_PROGRESS' and data['percent']>=75) or (stage=='virtual' and kind=='VIRTUAL_ENTRY_PROGRESS'):
            stop.touch()
    result=run(s,root,cores=1,sequential=True,captures=captures,emit=emit,cancel=file_check(stop))
    assert result['status']=='CANCELLED',result['status']
    with (root/result['alerts_csv']).open(encoding='utf-8-sig') as f:alerts=list(csv.DictReader(f))
    assert alerts and result['processed_periods']
    if stage=='virtual':
        assert result['virtual_entry']['cancelled']
        assert (root/result['virtual_entry']['trades_csv']).exists()
    (OUT/('stop_'+stage+'.json')).write_text(json.dumps({'result_path':(output/'result.json').relative_to(ROOT).as_posix(),
        'status':result['status'],'alerts':len(alerts),'periods':result['processed_periods'],'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
    print(stage,len(alerts),result['status'],flush=True)
