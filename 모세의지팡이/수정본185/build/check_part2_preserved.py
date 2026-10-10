from pathlib import Path
import sys,json,datetime as dt,csv
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import scenario,milliseconds
from event_backtest.runner import runtime_config,run_chunk

def main():
    folder=R/'검증결과/staff_s7/final_mt5_sigma3_v2'
    info=json.loads((folder/'result.json').read_text('utf-8'))
    capture=folder/Path(info['retained_export']).name
    dates=[dt.datetime.fromtimestamp(info[k],dt.timezone.utc).isoformat() for k in ('start_s','end_s')]
    s=scenario(start=dates[0],end=dates[1],mode='TICK',overlap_trading_days=0)
    from staff_golden.scenario import WATCHES
    s['commands']=[{'chat_id':chat,'text':text} for chat,text in WATCHES]
    s['triggers']={'SPECIAL7':'무지성 올존'}
    cfg=runtime_config(s);out=R/'검증결과/part2_connection'
    results=[]
    for mode in ('live','replay'):
        t={'scenario':s,'config':cfg,'run_id':'preserved-'+mode,'start':s['start'],'end':s['end'],
           'warm_start':s['start'],'captures':[{'path':str(capture)}],'out':str(out/('preserved_'+mode)),'transport':mode}
        result=run_chunk(t);results.append(result);print(mode,result,flush=True)
    def rows(result):
        with (R/result['alerts_csv']).open(encoding='utf-8',newline='') as f:return [{k:v for k,v in row.items() if k!='run_id'} for row in csv.DictReader(f)]
    comparison={'equal':rows(results[0])==rows(results[1]),'notification_count':len(rows(results[0])),'results':results}
    (out/'preserved_comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    assert comparison['equal']

if __name__=='__main__':main()
