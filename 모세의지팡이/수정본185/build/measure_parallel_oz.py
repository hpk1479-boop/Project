"""Offline isolated replay evidence. Saved paths stay warehouse-relative."""
from pathlib import Path
import argparse,collections,gzip,hashlib,json,logging,os,sys,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def main():
    p=argparse.ArgumentParser();p.add_argument('--before',action='store_true')
    p.add_argument('--strategies',default='ALL');p.add_argument('--label',required=True)
    p.add_argument('--warehouse',required=True);p.add_argument('--start',default='2025-09-01');p.add_argument('--end',default='2025-09-08')
    p.add_argument('--trace',action='store_true');p.add_argument('--oz-full',action='store_true')
    a=p.parse_args();host=OUT/'before_runtime' if a.before else ROOT
    sys.path[:0]=[str(host/'Part2'),str(host/'Part1/program')]
    from event_backtest.settings import scenario
    import event_backtest.runner as runner
    from event_backtest.warehouse import Warehouse
    from event_engine.domain_support import plain
    s=scenario(symbol='XAUUSD+',start=a.start,end=a.end,strategies=a.strategies,overlap_trading_days=0)
    if a.oz_full:s['oz_evaluation']='all'
    if (OUT/'old_captures.json').exists():
        inventory=json.loads((OUT/'old_captures.json').read_text('utf-8'))
        if not a.before and (OUT/'keyframe_conversion.json').exists():
            converted={r['capture_id']:r['capture'] for r in json.loads((OUT/'keyframe_conversion.json').read_text('utf-8'))}
            inventory=[converted.get(c['capture_id'],c) for c in inventory]
        captures=[c for c in inventory if c['end']>a.start and c['start']<a.end]
        missing=[] if captures else ['no saved reference capture']
    else:
        catalog=Warehouse(a.warehouse)
        try:captures,missing=runner.resolve_captures(catalog,s)
        finally:catalog.close()
    if missing:raise ValueError(missing)
    out=Path(a.warehouse)/'runs'/('parallel26_'+a.label);out.mkdir(parents=True,exist_ok=False)
    task={'scenario':s,'config':runner.runtime_config(s),'out':str(out),'run_id':a.label,'start':s['start'],'end':s['end'],
          'warm_start':s['start'],'captures':captures,'warehouse':a.warehouse,'transport':'replay'}
    original=runner.ResultWriter;counts=collections.Counter();digest=hashlib.sha256()
    class TraceWriter(original):
        def __init__(self,*args,**kw):
            super().__init__(*args,**kw)
            self.trace=gzip.open(out/'signals.jsonl.gz','wt',encoding='utf-8',compresslevel=1) if a.trace else None
        def accept(self,event):
            payload=plain(event.payload)
            line=json.dumps({'time':event.source_time,**payload},ensure_ascii=False,sort_keys=True,separators=(',',':'))
            digest.update(line.encode());counts[payload['content'].get('family') or payload['content'].get('type') or payload['strategy']]+=1
            if self.trace:self.trace.write(line+'\n')
            return super().accept(event)
        def close(self):
            super().close()
            if self.trace:self.trace.close()
    if a.trace:runner.ResultWriter=TraceWriter
    suppressed=collections.Counter();original_exception=logging.exception
    def capture_exception(message,*args,**kwargs):
        if str(message).startswith('[SPECIAL Watch]'):
            error=sys.exc_info()[1]
            suppressed[(str(args[0]) if args else '',type(error).__name__)]+=1
        return original_exception(message,*args,**kwargs)
    logging.exception=capture_exception
    result=runner.run_chunk(task)
    result.update(hash_seed=os.environ.get('PYTHONHASHSEED'),signal_trace_recorded=a.trace)
    result['suppressed_special_poll_errors']=[{'handler':h,'error':e,'count':n} for (h,e),n in suppressed.items()]
    if a.trace:result.update(signal_sha256=digest.hexdigest(),signal_counts=dict(counts))
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'label':a.label,'bundles':result['bundles'],'notifications':result['notifications'],
        'signal_sha256':digest.hexdigest() if a.trace else None},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
