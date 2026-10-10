"""Real partial-result and entry-M1 diagnostics. No new backtest execution."""
from pathlib import Path
import csv,json,sys,datetime as dt
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/virtual_read_optimized'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def main():
    from event_backtest.system import deny_network
    deny_network()
    from event_backtest import virtual_entry,virtual_source,settings
    from event_backtest.runner import runtime_config
    from event_backtest.cancellation import file_check
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    file=next((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'))
    captures=[dict(path=file.parent.relative_to(warehouse).as_posix(),start='2025-09-01',end='2025-10-01')]
    source=ROOT/'검증결과/stop_virtual_parallel/partition_MONTH/chunk_000/alerts.csv'
    s=settings.scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-10-01',strategies=['SPECIAL1'],overlap_trading_days=0)
    settings.relative_path=lambda root,path:Path(path).resolve().relative_to(OUT).as_posix()
    dest=OUT/'partial/same_run';dest.mkdir(parents=True,exist_ok=True)
    stop=dest/'stop.request';events=[]
    def emit(kind,data):
        events.append(dict(event=kind,**data))
        if kind=='VIRTUAL_ENTRY_PROGRESS' and data['processed_alerts']>=2:stop.touch()
    result=virtual_entry.calculate(source,captures,warehouse,s,runtime_config(s),dest,emit=emit,cancel=file_check(stop))
    assert result['cancelled'] and 0<result['processed_signals']<result['eligible_signals']
    assert (dest/'virtual_trades.csv').stat().st_size>0
    data=dict(run_id=dest.name,status='CANCELLED',status_label='중단됨(부분 결과)',virtual_entry=result)
    (dest/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    (dest/'progress.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf8')
    # Exercise the real result window against the saved partial artifact.
    import tkinter as tk
    from tkinter import ttk
    from event_backtest.result_ui import open_result
    parent=tk.Tk();parent.withdraw();win=open_result(parent,dest/'result.json',OUT);win.update()
    def children(w):
        for c in w.winfo_children():yield c;yield from children(c)
    tables=[w for w in children(win) if isinstance(w,ttk.Treeview)]
    assert len(tables)==1 and len(tables[0].get_children())==9
    import ctypes
    from PIL import ImageGrab
    user32=ctypes.windll.user32;user32.GetParent.argtypes=[ctypes.c_void_p];user32.GetParent.restype=ctypes.c_void_p
    ImageGrab.grab(window=user32.GetParent(win.winfo_id())).save(OUT/'partial_result.png')
    win.destroy();parent.destroy()
    (OUT/'partial_check.json').write_text(json.dumps(dict(cancelled=True,processed=result['processed_signals'],
        observed=result['observed_signals'],eligible=result['eligible_signals'],actual_result_window_rows=9,
        periods=result['processed_periods']),ensure_ascii=False,indent=2),encoding='utf8')
    rows=list(csv.DictReader((OUT/'after/month/same_run/virtual_trades.csv').open(encoding='utf-8-sig')))
    groups=defaultdict(list)
    for r in rows:groups[(r['signal_id'],r['strategy'],r['symbol'],r['tf'])].append(r)
    entered=[r for r in groups.values() if r[0]['entry_time']]
    stopped=[r for r in entered if any(x['result']=='LOSS' and x['exit_time']==x['entry_time'] for x in r)]
    examples=[r[0] for r in stopped[:5]];remaining={r['signal_id']:r for r in examples};observed=[]
    if examples:
        selected={('XAUUSD+','1m')};start=min(int(r['entry_time']) for r in examples)
        end=max(int(r['entry_time']) for r in examples)+60000
        for stamp,feeds in virtual_source.shared_observations(captures,warehouse,selected,start,end):
            v=feeds[('XAUUSD+','1m')]
            for signal_id,r in list(remaining.items()):
                bar=int(r['entry_time'])
                if stamp<bar+60000:continue
                matches=(v.time==bar//1000).nonzero()[0]
                if not len(matches):continue
                i=int(matches[0]);value=lambda n:float(v.values[i,v.columns.index(n)])
                observed.append(dict(signal_id=signal_id,direction=r['direction'],tf=r['tf'],
                    entry_time=bar,entry_utc=dt.datetime.fromtimestamp(bar/1000,dt.timezone.utc).isoformat(),
                    entry_price=float(r['entry_price']),b0=float(r['stop_price']),
                    entry_m1_open=value('open'),entry_m1_high=value('high'),entry_m1_low=value('low'),
                    observation_ms=stamp,spread_price=result['pricing']['spread_price']))
                del remaining[signal_id]
            if not remaining:break
    assert not remaining
    diagnosis=dict(alerts=len(groups),entered=len(entered),stopped_in_entry_m1=len(stopped),
        percentage=100*len(stopped)/len(entered) if entered else None,
        per_rr={str(rr):dict(entered=sum(bool(r['entry_time']) for r in rows if float(r['rr'])==rr),
            stopped=sum(bool(r['entry_time']) and r['result']=='LOSS' and r['entry_time']==r['exit_time'] for r in rows if float(r['rr'])==rr)) for rr in virtual_entry.RATIOS},examples=observed)
    (OUT/'entry_m1_diagnosis.json').write_text(json.dumps(diagnosis,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(diagnosis,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
