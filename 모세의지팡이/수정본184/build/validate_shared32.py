"""Offline same-capture diagnosis; every artifact stays in this revision."""
from pathlib import Path
import argparse,cProfile,csv,gzip,hashlib,json,os,pstats,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/shared_oz_composer_input'
parser=argparse.ArgumentParser()
parser.add_argument('phase');parser.add_argument('strategy',nargs='?',default='ALL')
parser.add_argument('--end',default='2025-10-01');parser.add_argument('--profile',action='store_true')
args=parser.parse_args();sys.dont_write_bytecode=True
if args.phase=='snapshot':
    target=OUT/'policy_runtime'
    for folder in ('Part1/program','Part2/event_backtest','Part2/generic_backtest','Part2/data_warehouse'):
        shutil.copytree(ROOT/folder,target/folder,ignore=shutil.ignore_patterns('__pycache__','logs','*.log'),dirs_exist_ok=False)
    print('policy-only runtime snapshot saved');raise SystemExit
runtime=OUT/'policy_runtime' if args.phase=='policy' else ROOT
sys.path[:0]=[str(runtime/'Part2'),str(runtime/'Part1/program')]
from event_backtest.settings import scenario
from event_backtest.runner import run_chunk,runtime_config
from event_backtest import warehouse as warehouse_module
from event_backtest import settings as settings_module
# Outputs are project-relative even for the self-contained diagnostic runtime.
import event_backtest.runner as runner
runner.relative_path=lambda root,path:Path(path).resolve().relative_to(ROOT).as_posix()
warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
captures=list((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'))
if len(captures)!=1:raise ValueError('expected exactly one preserved September capture')
label=args.phase+'_'+args.strategy+('_profile' if args.profile else '')+('_short' if args.end!='2025-10-01' else '')
dest=OUT/label;dest.mkdir(parents=True,exist_ok=True)
s=scenario(symbol='XAUUSD+',start='2025-09-01',end=args.end,strategies=args.strategy,overlap_trading_days=0)
task={'scenario':s,'config':runtime_config(s),'out':str(dest),'run_id':label,'start':s['start'],
      'end':s['end'],'warm_start':s['start'],'warehouse':'','captures':[{'path':str(captures[0].parent)}]}
original=warehouse_module.ResultWriter.accept
signal_hash=hashlib.sha256();counts={};traced=0
trace=gzip.open(dest/'oz_signals.jsonl.gz','wt',encoding='utf-8')
def accept(self,event):
    global traced
    from event_engine.domain_support import plain
    p=plain(event.payload);c=p['content']
    signature={'source_time':event.source_time,**p}
    signal_hash.update(json.dumps(signature,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode());signal_hash.update(b'\n')
    key=p['strategy']+':'+str(c.get('type',''));counts[key]=counts.get(key,0)+1
    raw=c.get('event',{})
    if c.get('type')=='NOTIFICATION' or (c.get('family')=='OZ' and raw.get('kind')=='FINAL_ALERT'):
        trace.write(json.dumps(signature,ensure_ascii=False,separators=(',',':'))+'\n');traced+=1
    return original(self,event)
warehouse_module.ResultWriter.accept=accept
profile=cProfile.Profile() if args.profile else None
try:
    if profile:profile.enable()
    result=run_chunk(task)
finally:
    if profile:profile.disable()
    trace.close()
if profile:
    with (dest/'profile.txt').open('w',encoding='utf-8') as f:pstats.Stats(profile,stream=f).sort_stats('cumulative').print_stats(50)
    profile.dump_stats(str(dest/'profile.pstats'))
(dest/'signals_digest.json').write_text(json.dumps({'sha256':signal_hash.hexdigest(),'counts':counts,'traced':traced},indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ('bundles','mean_ms','elapsed_seconds','notifications')},ensure_ascii=False))
