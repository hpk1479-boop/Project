"""Separate full-history effects from NumPy implementation on the first mismatch."""
from pathlib import Path
import sys,json,logging,argparse
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'
PROGRAM=ROOT/'검증결과/schema_cleanup/hosts/revision22/Part1/program'
sys.path[:0]=[str(PROGRAM),str(ROOT/'Part2')]
from event_backtest.system import deny_network
deny_network();logging.disable(logging.CRITICAL)
from event_backtest.bridge import CaptureInputs
from event_backtest.settings import settings
from event_engine.market import MarketView
from event_engine.fvg_structure import StructureStore,wilder_atr
from event_engine.model import Kind,FeedSnapshot
from event_host import load_staff
from staff_schema import legacy_frame
from strategy_FVG import build_fvg_state,FVG_WILDER_ATR
import numpy as np

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',type=int,default=1756706760000)
    parser.add_argument('--tf',default='6m')
    parser.add_argument('--candidates',type=int,nargs='+',default=[1756704240,1756702800])
    parser.add_argument('--label',default='first')
    args=parser.parse_args();target=args.target;tf=args.tf
    saved=OUT/(args.label+'_fvg_snapshot.npz')
    if saved.exists():
        data=np.load(saved);s=FeedSnapshot(data['time'],data['volume'],data['values'],1,1,{})
    else:
        capture=next(x for x in json.loads((ROOT/'검증결과/part2_connection/recorded_captures.json').read_text('utf-8')) if x['start']=='2025-09-01')
        staff=load_staff();clock=[0.];cache=staff.StaffPipeCache('',health_session='DIAGNOSTIC',monotonic=lambda:clock[0],gap_journal=OUT/'isolate_gaps.jsonl')
        inputs=CaptureInputs(staff,cache,[Path(settings()['warehouse'])/capture['path']],clock=clock,end_ms=target+1)
        feeds={}
        for item in inputs:
            if item.kind==Kind.MARKET_BUNDLE:feeds.update(item.payload['feeds'])
            if item.source_time==target:s=feeds[tf];break
        else:raise RuntimeError('target publication absent')
        np.savez_compressed(saved,time=s.time,volume=s.volume,values=s.values)
    full=legacy_frame('XAUUSD+',tf,s);window=full.tail(49).reset_index(drop=True)
    old_full=build_fvg_state('XAUUSD+',tf,full,cache=None)
    old_window=build_fvg_state('XAUUSD+',tf,window,cache=None)
    new_full=StructureStore().evaluate('XAUUSD+',tf,MarketView(s))
    cut=FeedSnapshot(s.time[-49:],s.volume[-49:],s.values[-49:],1,1,{})
    new_window=StructureStore().evaluate('XAUUSD+',tf,MarketView(cut))
    def ids(v):return [x['zone_id'] for x in v['zones']]
    details=[]
    for length,frame in ((len(full),full),(49,window)):
        atr=FVG_WILDER_ATR(frame.high,frame.low,frame.close,14)
        for stamp in args.candidates:
            pos=int(np.flatnonzero(frame.time.to_numpy().astype('datetime64[s]').astype('int64')==stamp)[0]);base=float(atr.iloc[pos-2])
            gap=float(frame.low.iloc[pos]-frame.high.iloc[pos-2])
            details.append(dict(rows=length,candidate_time=stamp,atr=base,gap=gap,min_gap=base*.25,max_gap=base*1.75,accepted=.25*base<=gap<=1.75*base))
    result=dict(observed=target,tf=tf,old_window_rows=49,new_rows=len(full),
        old_full_ids=ids(old_full),new_full_ids=ids(new_full),old_window_ids=ids(old_window),new_window_ids=ids(new_window),details=details,
        same_full_history_values=json.loads(json.dumps(old_full))==json.loads(json.dumps(new_full)),
        same_window_values=json.loads(json.dumps(old_window))==json.loads(json.dumps(new_window)),
        same_selected_zones_full=ids(old_full)==ids(new_full),same_selected_zones_window=ids(old_window)==ids(new_window),
        normalization='Only tuple/list container representation is normalized; all values are compared')
    name='fvg_history_diagnostic_final.json' if args.label=='first' else args.label+'_fvg_diagnostic.json'
    (OUT/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
