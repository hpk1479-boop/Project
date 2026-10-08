"""Band-only P20 microbenchmark, separate from complete native source kernels."""
import sys,math,time,json,argparse,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pit.features.percentile.incremental_bands import RollingNativeWindow, IncrementalBandEngine
from pit.features.percentile.bands import Grid100BandEngine,PriceExactPercentile,OscillatorExactPercentile
from pit.features.percentile.contracts import NativeCell
p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--count',type=int,default=12000);args=p.parse_args()
report=[]
for family in ('PRICE','RSI','STO','DI'):
    rng=random.Random(56)
    offset={'PRICE':100.,'RSI':50.,'STO':40.,'DI':100.}[family]
    values=[NativeCell.from_float(offset+math.sin(i/7)*3+rng.uniform(-1,1),'SOURCE_WRITTEN') for i in range(args.count+20)]
    ids=tuple(range(len(values)))
    for exact in (False,True):
        results=[];times=[]
        for optimized in (False,True):
            w=RollingNativeWindow(20);w.bind(ids,'REINDEX',False)
            engine=IncrementalBandEngine.__new__(IncrementalBandEngine)
            engine.window=w;engine.period=20;engine.target=2;engine.leveling=10.;engine.price=family=='PRICE'
            engine.precision=exact
            source=lambda shift:values[len(ids)-1-shift]
            out=[];start=time.perf_counter()
            for end in range(20,len(values)):
                if optimized:
                    w.move(end,source)
                    if exact:
                        pair=(w.percentile(10.,price=family=='PRICE'),w.percentile(90.,price=family=='PRICE'))
                    else:
                        engine.minimum=engine.maximum=NativeCell.from_float(0.)
                        pair=engine._grid(True,False,None)
                else:
                    win=tuple(reversed(values[end-19:end+1]))
                    if exact:
                        cls=PriceExactPercentile if family=='PRICE' else OscillatorExactPercentile
                        pair=(cls.calculate(win,10.),cls.calculate(win,90.))
                    else:pair=Grid100BandEngine(20,10.).row(win,first=True)
                out.append(pair)
            times.append(time.perf_counter()-start);results.append(out)
        mismatch=sum(a!=b for a,b in zip(*results))
        row={'family':family,'precision':'exact' if exact else 'grid100','observations':args.count,
             'period':20,'scope':'band only; finite deterministic values, no source recurrence or DataFrame',
             'reference_seconds':times[0],'incremental_seconds':times[1],'speedup':times[0]/times[1],
             'mismatch_count':mismatch,'window_stats':w.stats}
        report.append(row);print(json.dumps(row),flush=True)
Path(args.out).write_text(json.dumps(report,indent=2))
