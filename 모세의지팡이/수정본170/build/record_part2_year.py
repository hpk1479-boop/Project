from pathlib import Path
import sys,json,datetime as dt
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings,scenario
from event_backtest.recording import prepare
from generic_backtest.history.selection import discover_terminals
out=R/'검증결과/part2_connection'
def emit(kind,data):
    row={'at':dt.datetime.now().isoformat(),'kind':kind,**data}
    with (out/'year_recording_progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(kind,data.get('message_code',data.get('start','')),data.get('elapsed_seconds',''),data.get('export_bytes',''),flush=True)
def main():
    profiles=discover_terminals()['terminals']
    if len(profiles)!=1:raise ValueError('exactly one terminal required')
    s=scenario(R/'Part2/scenarios/xau_continuous.json')
    data=prepare(s,settings()['warehouse'],profiles[0],emit=emit)
    (out/'year_captures.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
