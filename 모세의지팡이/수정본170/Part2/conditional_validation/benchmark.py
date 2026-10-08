"""Reproducible SHORT SYNTHETIC dormant-path benchmark, not a market-month claim.

Both controls use the corrected current strategy implementation. The sole switch
is eager native features versus conditional native features. No disk memo/tape.
Startup + core + real isolated callbacks + finish are timed. An independent
profiled run counts actual calls; its time is not substituted for unprofiled time.
"""
from pathlib import Path
import argparse,json,time,hashlib,statistics,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'validation_suite')]
from generic_backtest.canonical import encode
from generic_backtest.plugins import parse_literal_metadata,validate_requirements
from generic_backtest.worker import StrategyWorker
from generic_backtest.market import GenericMarketCore
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.contracts import InstrumentSpec
from generic_backtest.runner import context_payload
from generic_backtest.conditional import ConditionalFeatureRegistry
from generic_backtest.features import OptionalFeatureRegistry
from generic_backtest.watch.compiler import compile_watch
from test_gate import tick,CAL
from pit.features.percentile.provider import PercentileFeatureProvider

def run(name,conditional,count,profile=False,source=None):
    params={}
    if name=='WATCH_UI_V1':
        plan=compile_watch('15분 추세 상승이고 1분 올존 알려줘','XAUUSD+')
        params={'plan_json':json.dumps(plan,ensure_ascii=False)}
    record=parse_literal_metadata(ROOT/'BACKTEST_SPECIAL'/f'{name}.py')
    bundle_calls=0
    original=PercentileFeatureProvider.on_market
    def measured(*args,**kwargs):
        nonlocal bundle_calls
        bundle_calls+=1
        return original(*args,**kwargs)
    PercentileFeatureProvider.on_market=measured
    started=time.perf_counter();events=[]
    try:
        with StrategyWorker(record,params,{},'SIGNAL',{'profile_stages':profile,'worker_timeout_seconds':120}) as worker:
            req=validate_requirements(worker.requirements['signal'],100000)
            start=1736726400
            start_ns=source[0].time_msc*1000000 if source else start*10**9
            core=GenericMarketCore(InstrumentSpec('XAUUSD+','benchmark-only'),CalendarRegistry(CAL),
                start_ns,req.completed_lookback_by_tf,'CONDITIONAL_BENCH')
            features=ConditionalFeatureRegistry(req,'SIGNAL',core) if conditional else OptionalFeatureRegistry(req,'SIGNAL')
            if conditional:worker.conditional_handler=features
            loopstart=time.perf_counter()
            for i in range(1,count+1):
                raw=source[i-1] if source else tick(start+i*60,i,100.)
                view,change=core.step(raw)
                if conditional:features.on_raw(raw,False,view)
                bundles=features.advance(view,change)
                payload=context_payload(view,bundles,req,'MEASURE')
                events.extend(worker.observe(payload))
            loopsec=time.perf_counter()-loopstart
            diagnostics=worker.observe(payload,op='finish')
            calls=worker.profile
        elapsed=time.perf_counter()-started
    finally:PercentileFeatureProvider.on_market=original
    def num(suffix,function):
        return sum(r['calls'] for r in calls if r['file'].replace('\\','/').endswith(suffix) and r['function']==function)
    return {'strategy':name,'conditional':conditional,'observations':count,'profiled':profile,
        'wall_seconds_including_startup':elapsed,'loop_seconds':loopsec,'events':events,
        'event_sha256':hashlib.sha256(encode(events)).hexdigest(),
        'percentile_bundle_calls':bundle_calls,'percentile_family_oncalculate_calls':bundle_calls*4,
        'oz_observe_calls':num('/watch/engines/oz.py','observe') if profile else None,
        'hma_wma_calls':num('/pit/features/hma_open.py','wma_at') if profile else None,
        'hma_full_history_calls':num('/fast/frame_cache.py','history') if profile else None,
        'fvg_heavy_calls':num('/watch/engines/fvg_math.py','add_fvg_local') if profile else None,
        'frame_requests':num('/fast/frame_cache.py','frame') if profile else None,
        'worker_diagnostics':diagnostics,
        'conditional_diagnostics':features.diagnostics() if conditional else None,
        'profile_relevant':[r for r in calls if any(s in r['file'].replace('\\','/') for s in
            ('/watch/engines/fvg_math.py','/fast/frame_cache.py','/watch/engines/oz.py'))] if profile else []}

def main():
    p=argparse.ArgumentParser();p.add_argument('--observations',type=int,default=96)
    p.add_argument('--broker-prefix',action='store_true');p.add_argument('--rounds',type=int,default=2);p.add_argument('--names',nargs='*')
    p.add_argument('--output',default=str(ROOT/'conditional_validation/benchmark_results.json'))
    args=p.parse_args();out=Path(args.output)
    names=args.names or ['WATCH_UI_V1']
    result={'scope':'SYNTHETIC_FLAT_DORMANT_SHORT_PREFIX_NOT_MARKET_MONTH',
        'comparison':'CORRECTED_CURRENT_STRATEGY_EAGER_VS_CONDITIONAL_NOT_ORIGINAL_PART2',
        'input':'96 default observations, one flat tick per minute, XAUUSD+, UTC research calendar; no adequate multi-TF warmup',
        'not_proven':['nonempty full-strategy parity','captured LIVE seed/poll equivalence','month performance'],
        'rows':[]}
    source=None
    if args.broker_prefix:
        import numpy as np
        from pit.models import TickRecord
        directory=next((ROOT/'generic_cache/raw').iterdir())
        manifest=json.loads((directory/'manifest.json').read_text())
        chunk=next(c for c in manifest['chunks'] if c['count']>=args.observations)
        data=np.load(directory/chunk['file'],mmap_mode='r')[:args.observations]
        source=[TickRecord.from_bytes('BROKER_PREFIX_BENCH',i,row.tobytes()) for i,row in enumerate(data,1)]
        result.update(scope='BROKER_REAL_TICKS_SHORT_PREFIX_NOT_WARMED_MARKET_MONTH',
            input={'archive_identity':manifest['archive_identity'],'chunk':chunk['file'],
                'chunk_sha256':chunk['file_sha256'],'observations':len(source),
                'start_ns':source[0].time_msc*1000000,'end_ns':source[-1].time_msc*1000000,
                'prior_market_history_injected':False,'calendar':CAL})
    for name in names:
        for lazy in (False,True):
            for profile in [False]*args.rounds+[True]:
                try:r=run(name,lazy,args.observations,profile,source)
                except Exception as exc:r={'strategy':name,'conditional':lazy,'profiled':profile,'error':repr(exc)}
                result['rows'].append(r);out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                print(name,lazy,profile,r.get('wall_seconds_including_startup',r.get('error')),flush=True)
    for name in names:
        good=[r for r in result['rows'] if r['strategy']==name and 'error' not in r]
        hashes={r['event_sha256'] for r in good}
        if len(hashes)>1:raise AssertionError(name+' event prefix mismatch')
if __name__=='__main__':main()
