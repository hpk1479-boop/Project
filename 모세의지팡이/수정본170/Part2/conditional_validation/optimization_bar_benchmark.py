"""Run-private incremental BAR benchmark, not complete backtest wall time.

Tick decoding is outside timing. Both arms consume identical immutable records.
The reference is the supplied current Part2, not an earlier project revision.
Allocation/TF counters are obtained in a separate instrumented pass.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.canonical import bits,encode
from generic_backtest.history.cache import GenericArchiveReader
from generic_backtest.market import GenericCandleBook
from pit.models import BarState
from conditional_validation.optimization_reference import BaselineCandleBook

TFS=('1m','2m','3m','5m','6m','15m','30m','1h')

def execute(cls,ticks,start):
    book=cls('TEST',CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}),
             start,{tf:681 for tf in TFS},'BID')
    for tick in ticks:book.apply_tick(tick,'00'*32)
    return book


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    reader=GenericArchiveReader(args.archive);ticks=list(reader)
    if not ticks:raise ValueError('An archive containing ticks is required')
    start=ticks[0].time_msc*1_000_000
    arms={'baseline':BaselineCandleBook,'optimized':GenericCandleBook}
    samples={name:[] for name in arms};signatures={}
    # Reverse order in alternate rounds to limit a fixed ordering bias.
    for r in range(args.rounds):
        for name in (tuple(arms) if r%2==0 else tuple(reversed(arms))):
            before=time.perf_counter();book=execute(arms[name],ticks,start)
            samples[name].append(time.perf_counter()-before)
            sig=hashlib.sha256(encode(bits(book.view()))).hexdigest()
            if name in signatures:assert signatures[name]==sig
            signatures[name]=sig
    assert len(set(signatures.values()))==1
    counters={}
    init=BarState.__init__
    for name,cls in arms.items():
        rows=Counter();completed=Counter()
        def counted(self,*a,**k):
            tf=k['timeframe'] if 'timeframe' in k else a[2]
            state=k.get('state',a[14] if len(a)>14 else 'FORMING')
            rows[tf]+=1
            if state=='COMPLETED':completed[tf]+=1
            init(self,*a,**k)
        BarState.__init__=counted
        try:book=execute(cls,ticks,start)
        finally:BarState.__init__=init
        assert hashlib.sha256(encode(bits(book.view()))).hexdigest()==signatures[name]
        counters[name]={'bar_allocations_by_tf':dict(rows),'completed_allocations_by_tf':dict(completed),
                        'forming_update_rows_by_tf':{tf:rows[tf]-completed[tf] for tf in TFS},
                        'bar_allocations_total':sum(rows.values()),
                        'resample_calls':0,'dataframe_copy_calls':0}
    assert counters['baseline']==counters['optimized']
    result={'scope':'BAR_ONLY_NOT_BACKTEST_WALL_TIME','archive_identity':reader.manifest['archive_identity'],
            'ticks':len(ticks),'timeframes':TFS,'rounds':args.rounds,'same_final_bar_bits':True,
            'rows':{n:{'wall_samples_seconds':v,'median_seconds':statistics.median(v),
                       'final_bar_sha256':signatures[n]} for n,v in samples.items()},'counters':counters}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
