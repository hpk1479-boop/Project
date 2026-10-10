"""Paired isolated measurements of retained operations and the Delta control.

The released Delta codec is unchanged; archived candidate measurements must not
be described as a retained optimization. These are not whole-run speedups.
"""
from pathlib import Path
from types import SimpleNamespace, MethodType
import argparse,ast,gc,importlib.util,json,os,statistics,sys,time


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def old_methods(path,cls,methods,namespace):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    functions=[n for n in node.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    tree=ast.fix_missing_locations(ast.Module(body=[future,*functions],type_ignores=[]))
    exec(compile(tree,str(path),'exec'),namespace)
    return {name:namespace[name] for name in methods}


def paired(before,after,batches=7):
    before();after();samples={'before':[],'after':[]}
    for i in range(batches):
        for name,fn in ([('before',before),('after',after)] if i%2==0 else [('after',after),('before',before)]):
            gc.collect();began=time.perf_counter();fn();samples[name].append(time.perf_counter()-began)
    b=statistics.median(samples['before']);a=statistics.median(samples['after'])
    return {'samples_seconds':samples,'median_before_seconds':b,'median_after_seconds':a,
            'reduction_percent':100*(b-a)/b}


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--cpu',type=int);a=p.parse_args();a.before=a.before.resolve();a.after=a.after.resolve()
    a.output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
    if a.cpu is not None and hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{a.cpu})
    sys.path[:0]=[str(a.after/'Part1/program'),str(a.after/'Part2')]
    from event_engine.model import Subscriptions,Kind,Resolution,freeze
    from event_engine.subscription_cache import cached_subscriptions
    from event_engine.composition_consumer import CompositionConsumer
    result={'cpu':a.cpu,'python':sys.version.split()[0],
            'scope':'isolated operations, profiler off; not end-to-end speedup','steps':{}}
    ns={'Subscriptions':Subscriptions,'Kind':Kind,'Resolution':Resolution}
    method=old_methods(a.before/'Part1/program/event_engine/composition_consumer.py','CompositionConsumer',['subscriptions'],ns)['subscriptions']
    old=SimpleNamespace(symbols=('XAUUSD+',),selection=None)
    new=CompositionConsumer('COMPOSER',None,None,symbols=('XAUUSD+',))
    assert method(old)==new.subscriptions()
    def sub_before():
        for _ in range(100000):method(old)
    def sub_after():
        for _ in range(100000):new.subscriptions()
    result['steps']['subscriptions']={'operations_per_batch':100000,**paired(sub_before,sub_after)}

    import event_composer_domain as composer
    ns={'fact_scope':composer.fact_scope,'stable_id':composer.stable_id}
    methods=old_methods(a.before/'Part1/program/event_composer_domain.py','ComposerManager',
                        ['_sweep_subscription_payload','_condition_source_binding'],ns)
    old=SimpleNamespace(config={'LONDON':'08:00-17:00','NEWYORK':'13:00-22:00'},_source_bindings={})
    old._sweep_subscription_payload=MethodType(methods['_sweep_subscription_payload'],old)
    old._condition_source_binding=MethodType(methods['_condition_source_binding'],old)
    new=composer.ComposerManager.__new__(composer.ComposerManager)
    new.config=old.config.copy();new._source_bindings={}
    spec=SimpleNamespace(symbol='XAUUSD+',sweep_levels=('PDH','PDL'),sweep_atr_period=14,sweep_atr_mult=1.0)
    conds=[SimpleNamespace(kind=k,tf='5m',side='') for k in ('TREND','SWEEP','FVG','MA_STATE','PERCENTILE','WONBI')]
    for c in conds:assert old._condition_source_binding(spec,c)==new._condition_source_binding(spec,c)
    def binding_before():
        for _ in range(10000):
            for c in conds:old._condition_source_binding(spec,c)
    def binding_after():
        for _ in range(10000):
            for c in conds:new._condition_source_binding(spec,c)
    result['steps']['condition_scope']={'operations_per_batch':60000,**paired(binding_before,binding_after)}

    from event_engine.domain_support import plain,emit_facts
    original=load(a.before/'Part1/program/event_engine/domain_support.py','event_engine._optimization38_before_support')
    raw={'symbol':'XAUUSD+','kind':'FACT_SNAPSHOT','source_tf':'1m','event_id':'id',
         'facts':[{'id':i,'values':list(range(16)),'source_health':{'sources':{'1m':'epoch'},'indicators':['PRICE']}}
                  for i in range(16)]}
    prepared=[plain(raw)]
    for label,events in [('mutable',prepared),('sealed',freeze(prepared))]:
        b=[];n=[];original.emit_facts(b.append,'XAUUSD+','FVG',events);emit_facts(n.append,'XAUUSD+','FVG',events,prepared=True)
        assert b==n
        sink=lambda value:None
        def fact_before():
            for _ in range(2000):original.emit_facts(sink,'XAUUSD+','FVG',events)
        def fact_after():
            for _ in range(2000):emit_facts(sink,'XAUUSD+','FVG',events,prepared=True)
        result['steps']['facts_'+label]={'operations_per_batch':2000,**paired(fact_before,fact_after)}

    import numpy as np
    import staff_schema as wire
    before_delta=load(a.before/'Part2/event_backtest/delta.py','_optimization38_before_delta')
    after_delta=load(a.after/'Part2/event_backtest/delta.py','_optimization38_after_delta')
    times=np.arange(650,dtype='<i8')*60+1700000000
    values=np.arange(650*50,dtype='<f8').reshape(650,50);volume=np.ones(650,dtype='<i8')
    enc=before_delta.DeltaCodec();frames=[]
    for i in range(8):
        children=[wire.pack_v2('XAUUSD+',tf,times+i*60,volume,values+i,seq=i+1) for tf in ('1m','5m')]
        raw=wire.pack_bundle('XAUUSD+',children,seq=i+1,sent_at_ms=(i+1)*1000)
        frames.append((*enc.encode(raw),raw))
    def run_delta(module):
        for _ in range(20):
            decoder=module.DeltaCodec()
            for structure,bits,raw in frames:
                actual=decoder.decode(structure,bits)
                assert actual==raw
    result['steps']['delta_decode']={'operations_per_batch':160,'feeds_per_bundle':2,'rows':650,
                                      **paired(lambda:run_delta(before_delta),lambda:run_delta(after_delta))}
    (a.output/'measurements.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
