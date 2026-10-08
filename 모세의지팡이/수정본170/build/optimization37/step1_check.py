"""Compare exact ResultWriter implementations and time their accept callbacks."""
from pathlib import Path
from types import SimpleNamespace
import argparse,csv,itertools,json,statistics,sys,time
from support import load_warehouse


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
    old=load_warehouse(a.before,'event_backtest.warehouse_before37')
    new=load_warehouse(a.after,'event_backtest.warehouse_after37')
    from event_engine.model import Event,Kind
    from event_engine import domain_support
    start=1788224400000
    def event(content,stamp=start,ident='s'):
        return Event(1,0,'test',1,stamp,Kind.SIGNAL,{'strategy':'OZ','symbol':'XAUUSD+','signal_id':ident,'content':content})
    deep={'event':{'facts':[{'zone_id':str(i),'source_tf':'1m','direction':'LONG','low':100.+i,'high':110.+i,'ready':True} for i in range(16)]}}
    types=[None,'','DOMAIN_FACT','NOTIFICATION','EXTERNAL_REQUEST','DEGRADED']
    statuses=[None,'','OK','DEGRADED']
    events=[]
    for i,(typ,status,stamp,recipients) in enumerate(itertools.product(types,statuses,[start-1,start,start+9999,start+10000],[[],['A'],['A','B']])):
        content={**deep,'message':'[1. notification] S급','text':'command','recipients':recipients,
                 'event':{**deep['event'],'source_tf':'1m','direction':'LONG','b0_price':12.5,'b0_time':start-60000}}
        if typ is not None:content['type']=typ
        if status is not None:content['status']=status
        events.append(event(content,stamp,str(i)))
    snapshots=[]
    for label,module in [('before',old),('after',new)]:
        writer=module.ResultWriter(a.output/f'{label}.csv',{'run_id':'same'},start,start+10000)
        writer.context={str(i):{'grade':'A','b0_price':99.1,'b0_time':start-12345} for i in range(len(events))}
        for e in events:writer.accept(e)
        snapshots.append({'count':writer.count,'notifications':writer.notifications,'external_error':writer.external_error,
                          'context':writer.context,'alert_months':writer.alert_months,'alert_ids':sorted(writer.alert_ids)})
        writer.close()
    assert snapshots[0]==snapshots[1]
    assert (a.output/'before.csv').read_bytes()==(a.output/'after.csv').read_bytes()
    # The discarded event must not invoke plain even once.
    w=new.ResultWriter(a.output/'guard.csv',{'run_id':'same'},start,start+1)
    original=domain_support.plain
    def fail(*args):raise AssertionError('plain called for a discarded domain fact')
    domain_support.plain=fail
    try:w.accept(event({'type':'DOMAIN_FACT',**deep}))
    finally:domain_support.plain=original;w.close()
    # Time five independent interleaved batches without cProfile, using the same event.
    bench=event({'type':'DOMAIN_FACT',**deep})
    samples={'before':[],'after':[]};iterations=10000
    writers={'before':old.ResultWriter(a.output/'bench_before.csv',{'run_id':'same'},start,start+1),
             'after':new.ResultWriter(a.output/'bench_after.csv',{'run_id':'same'},start,start+1)}
    for repeat in range(5):
        for label in (('before','after') if repeat%2==0 else ('after','before')):
            fn=writers[label].accept
            for _ in range(100):fn(bench)
            began=time.perf_counter()
            for _ in range(iterations):fn(bench)
            samples[label].append(time.perf_counter()-began)
    for w in writers.values():w.close()
    result={'step':1,'cases':len(events),'csv_byte_equal':True,'state_equal':True,'discarded_plain_calls':0,
            'benchmark':'discarded DOMAIN_FACT with 16 nested facts','iterations_per_batch':iterations,
            'samples_seconds':samples,'median_seconds':{k:statistics.median(v) for k,v in samples.items()},
            'database_integration_tested':False}
    (a.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
