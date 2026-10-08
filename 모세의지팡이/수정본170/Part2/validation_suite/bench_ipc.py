"""Actual isolated WATCH worker + existing cache RPC; synthetic undefined bands.
No source seed is fabricated. OZ bridge rejects these unavailable bands. This
measures numeric-HMA/cache/IPC hot paths, NOT positive OZ strategy performance.
"""
import argparse, json, sys, time, tempfile, hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True)
p.add_argument('--count',type=int,default=120);p.add_argument('--batch',action='store_true');p.add_argument('--profile',action='store_true')
a=p.parse_args();sys.path.insert(0,a.root);sys.path.insert(0,str(Path(__file__).parent))
from generic_backtest.worker import StrategyWorker
from generic_backtest.plugins import parse_literal_metadata
from generic_backtest.fast.feature_memo import CommonFeatureMemo
from generic_backtest.watch.compiler import compile_watch
from generic_backtest.canonical import encode
from ipc_fixtures import Contexts
plan=compile_watch('1분 올존 알려줘','TEST')
record=parse_literal_metadata(Path(a.root)/'backtest_specials/WATCH_UI_V1.py')
parameters={'plan_json':json.dumps(plan,ensure_ascii=False)}
results=[]
with tempfile.TemporaryDirectory(prefix='backtest_ipc_') as folder:
 for mode in ('cold','warm'):
    scope={'symbol':'TEST','fixture':'SYNTHETIC_UNAVAILABLE_SOURCE_BANDS','repeat':0}
    cache=CommonFeatureMemo(scope,root=Path(folder))
    resources={'worker_timeout_seconds':60,'profile_ipc':True,'worker_cache_batch':a.batch,
        '_common_feature_cache':True,'profile_stages':a.profile}
    start=time.perf_counter();worker=StrategyWorker(record,parameters,{},'SIGNAL',resources)
    startup=time.perf_counter()-start;worker.cache_handler=cache.handle
    raw_hash=hashlib.sha256();output_hash=hashlib.sha256();times=[];alert_count=0
    for ctx in Contexts(a.count):
        raw_hash.update(encode(ctx))
        s=time.perf_counter();value=worker.observe(ctx);times.append(time.perf_counter()-s)
        output_hash.update(encode(value));alert_count+=len(value)
    s=time.perf_counter();finish=worker.observe(ctx,op='finish');finish_s=time.perf_counter()-s
    worker.close();cache.close()
    row={'mode':mode,'observations':a.count,'startup_wall_s':startup,'observe_wall_s':sum(times),
         'first_observation_wall_s':times[0],'subsequent_observe_wall_s':sum(times[1:]),'finish_s':finish_s,
         'input_sha256':raw_hash.hexdigest(),'ordered_output_sha256':output_hash.hexdigest(),'alerts':alert_count,
         'finish':finish,'parent':worker.metrics.snapshot(),'worker':worker.worker_metrics,'cache':cache.stats}
    results.append(row)
    if a.profile:
        Path(a.out.replace('.json','_'+mode+'_profile.json')).write_text(json.dumps(worker.profile,indent=2))
    Path(a.out).write_text(json.dumps({'input_kind':'SYNTHETIC_UNAVAILABLE_BUNDLES_NOT_MARKET_RAW',
        'batch':a.batch,'plan_id':plan['plan_id'],'rows':results},indent=2))
    print(json.dumps(row,ensure_ascii=False),flush=True)
