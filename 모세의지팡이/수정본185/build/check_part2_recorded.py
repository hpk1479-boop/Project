"""Stream actual BAR and TIMER inputs; compare values at every BAR boundary."""
from pathlib import Path
import sys,json,hashlib
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]
from event_backtest.bridge import CaptureInputs
from event_host import load_staff
from event_engine.model import Kind

def signature(item):
    h=hashlib.sha256();h.update(str(item.source_time).encode())
    for tf,s in sorted(item.payload['feeds'].items()):
        h.update(tf.encode());h.update(s.time.tobytes());h.update(s.volume.tobytes());h.update(s.values.tobytes())
        h.update(repr(sorted(s.indicator_validity.items())).encode())
    return h.hexdigest()

def inputs(path,bar_filter=False):
    staff=load_staff();clock=[0.0]
    cache=staff.StaffPipeCache('',health_session='TEST',monotonic=lambda:clock[0],gap_journal=R/'검증결과/part2_connection/record_gaps.jsonl')
    last=None
    for item in CaptureInputs(staff,cache,[path],clock=clock):
        if item.kind!=Kind.MARKET_BUNDLE:continue
        s=item.payload['feeds'].get('1m')
        if s is None:continue
        current=int(s.time[-1])
        if not bar_filter or current!=last:yield item
        last=current

def main():
    from itertools import zip_longest
    out=R/'검증결과/part2_connection'
    bar=json.loads((out/'day_BAR.json').read_text('utf-8'))[0]['path']
    timer=json.loads((out/'day_TICK.json').read_text('utf-8'))[0]['path']
    n=0;diff=[]
    for a,b in zip_longest(inputs(bar),inputs(timer,True)):
        n+=1
        if a is None or b is None or signature(a)!=signature(b):
            row={'index':n,'bar_time':a.source_time if a else None,'timer_time':b.source_time if b else None}
            if a and b:
                import numpy as np
                row['feeds']={tf:int(np.sum(~(np.isclose(s.values,b.payload['feeds'][tf].values,rtol=0,atol=0,equal_nan=True)))) for tf,s in a.payload['feeds'].items() if tf in b.payload['feeds']}
            if len(diff)<25:diff.append(row)
        if n%100==0:print(n,'differences (first 25)',len(diff),flush=True)
    result={'bundles':n,'equal':not diff,'differences_first_25':diff}
    (out/'recorded_input_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(result,flush=True)

if __name__=='__main__':main()
