"""Actual one-day BAR/TIMER evidence; no strategy execution or Telegram."""
from pathlib import Path
import sys,json,datetime as dt
R=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings,scenario
from event_backtest.recording import prepare
from generic_backtest.history.selection import discover_terminals
out=R/'검증결과/part2_connection';out.mkdir(parents=True,exist_ok=True)
def emit(kind,data):
    row={'at':dt.datetime.now().isoformat(),'kind':kind,**data}
    with (out/'recording_progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(kind,data.get('message_code',data.get('start','')),data.get('elapsed_seconds',''),data.get('export_bytes',''),flush=True)
rows=discover_terminals()['terminals']
if len(rows)!=1:raise ValueError('Select one MT5 terminal explicitly')
profile=rows[0]
for mode in ('BAR','TICK'):
    s=scenario(start='2026-09-25',end='2026-09-26',mode=mode,overlap_trading_days=0)
    result=prepare(s,settings()['warehouse'],profile,emit=emit,include_overlap=False)
    (out/('day_'+mode+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
