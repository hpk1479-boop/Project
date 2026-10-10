from pathlib import Path
import sys,json,csv
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]

def main():
 from event_backtest.settings import scenario,settings
 from event_backtest.runner import run
 from event_backtest.system import deny_network
 deny_network();warehouse=Path(settings()['warehouse']);out=R/'검증결과/virtual_entry';results={}
 for mode in ('ALERT_ONLY','VIRTUAL_ENTRY'):
  s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-09-08',mode='BAR',strategies=['SPECIAL1'],result_mode=mode,spread_points={'XAUUSD+':0})
  data=run(s,warehouse,cores=1,sequential=True,emit=lambda k,d:print(json.dumps({'event':k,**d},ensure_ascii=False),flush=True))
  results[mode]=data
  (out/(mode+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
 def alerts(data):
  with (warehouse/data['alerts_csv']).open(encoding='utf8',newline='') as f:return [{k:v for k,v in row.items() if k!='run_id'} for row in csv.DictReader(f)]
 equal=alerts(results['ALERT_ONLY'])==alerts(results['VIRTUAL_ENTRY'])
 report={'alerts_equal':equal,'alerts':len(alerts(results['ALERT_ONLY'])),'virtual_rows':len(results['VIRTUAL_ENTRY']['virtual_entry']['summary']),'runs':{k:d['run_id'] for k,d in results.items()}}
 (out/'week_comparison.json').write_text(json.dumps(report,indent=2),encoding='utf8');assert equal
 print('VIRTUAL_WEEK_COMPLETE',report,flush=True)
if __name__=='__main__':main()
