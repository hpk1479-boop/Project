"""Validate STAFF's exact scalar predicate and full publication behavior."""
from pathlib import Path
import argparse,ast,importlib.util,json,statistics,sys,time
import numpy as np


def load(project,name):
    p=Path(project)/'Part1/program/THE STAFF OF MOSES.py'
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m


def validity(module):
    tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='StaffPipeCache')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_publish_arrays')
    assign=next(n for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='valid' for t in n.targets))
    wrapper=ast.parse('def evaluate(last):\n    return None\n')
    wrapper.body[0].body=[assign,ast.Return(value=ast.Name(id='valid',ctx=ast.Load()))]
    ast.fix_missing_locations(wrapper);ns=dict(module.__dict__);exec(compile(wrapper,str(module.__file__),'exec'),ns)
    return ns['evaluate']


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
    sys.path[:0]=[str(a.after/'Part1/program'),str(a.after/'Part2')]
    modules={'before':load(a.before,'staff_before37'),'after':load(a.after,'staff_after37')}
    fns={k:validity(m) for k,m in modules.items()};cols=len(modules['after'].PIPE_VALUE_COLUMNS)
    rows=[np.ones(cols),np.full(cols,np.nan)]
    for col in range(cols):
        for bad in (np.nan,np.inf,-np.inf,1.7976931348623157e308):
            v=np.ones(cols);v[col]=bad;rows.append(v)
    rng=np.random.default_rng(37)
    for _ in range(1000):
        v=rng.normal(size=cols);v[rng.random(cols)<.2]=np.nan;rows.append(v)
    for row in rows:assert fns['before'](row)==fns['after'](row)
    def cache(module):return module.StaffPipeCache('',monotonic=lambda:1.,health_session='TEST37',gap_journal=a.output/'gaps.jsonl')
    times=np.arange(650,dtype=np.int64)*60+1788184800;volumes=np.ones(650,dtype=np.int64);values=np.full((650,cols),100.)
    def snap_result(m,times=times,values=values,max_bars=650):
        c=cache(m);c.max_bars=max_bars
        try:c._publish_arrays('XAUUSD+','1m',1,times,volumes[:len(times)],values);s,age=c.snapshot_with_age('XAUUSD+','1m')
        except Exception as e:return ('error',type(e).__name__,str(e))
        # Compare NaNs by their canonical NumPy representation, not Python ==.
        for arr in (s.time,s.volume,s.values):
            assert not arr.flags.writeable
            try:arr.setflags(write=True)
            except ValueError:pass
            else:raise AssertionError('snapshot became mutable')
        return ('ok',s.time.tobytes(),s.volume.tobytes(),s.values.tobytes(),s.seq,s.source_epoch,dict(s.indicator_validity),s.legacy_wonbi)
    full_cases=[]
    for row in rows[:202]:
        v=values.copy();v[-1]=row;full_cases.append((times,v,650))
    # Earlier OHLC invalidity, duplicates, NaT, short buffers and max-bars trimming.
    v=values.copy();v[100,0]=np.nan;full_cases.append((times,v,650))
    t=times.copy();t[-1]=t[-2];full_cases.append((t,values,650))
    t=times.copy();t[0]=np.iinfo(np.int64).min;full_cases.append((t,values,650))
    full_cases.extend([(times[:2],values[:2],650),(times,values,3),(times,values,0)])
    for t,v,limit in full_cases:
        assert snap_result(modules['before'],t,v,limit)==snap_result(modules['after'],t,v,limit)
    # FULL/ROW validity transition, duplicate sequence and reconnect continuation.
    transitions=[]
    for m in modules.values():
        c=cache(m);trace=[]
        c._publish_arrays('XAUUSD+','1m',1,times,volumes,values)
        def note():
            s,_=c.snapshot_with_age('XAUUSD+','1m');trace.append((s.seq,s.source_epoch,dict(s.indicator_validity),s.values.tobytes()))
        note();c._publish_arrays('XAUUSD+','1m',2,times,volumes,values,advance_epoch=False);note()
        bad=values.copy();bad[-1,m.PIPE_VALUE_COLUMNS.index('ema_20')]=np.nan
        try:c._publish_arrays('XAUUSD+','1m',3,times,volumes,bad,advance_epoch=False)
        except m.wire.WireError as e:trace.append(str(e))
        else:raise AssertionError('ROW transition not rejected')
        c._publish_arrays('XAUUSD+','1m',3,times,volumes,bad);note()
        c._publish_arrays('XAUUSD+','1m',2,times,volumes,values);note()
        c._publish_arrays('XAUUSD+','1m',4,times,volumes,values);note()
        c.reconnect();c._publish_arrays('XAUUSD+','1m',1,times,volumes,values);note()
        transitions.append(trace)
    assert transitions[0]==transitions[1]
    # Module-load mapping retains missing-column and empty-requirement behavior.
    m=modules['after'];tree=ast.parse(Path(m.__file__).read_text(encoding='utf-8'))
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_MT5_REQUIRED_COLUMN_INDICES' for t in n.targets))
    ns={'PIPE_VALUE_COLUMNS':['a','b'],'MT5_REQUIRED_BY_INDICATOR':{'present':['a'],'missing':['absent'],'empty':[]}}
    exec(compile(ast.Module(body=[assignment],type_ignores=[]),str(m.__file__),'exec'),ns)
    assert ns['_MT5_REQUIRED_COLUMN_INDICES']=={'present':(0,),'missing':(None,),'empty':()}
    def benchmark(functions,n,repeat=5):
        result={k:[] for k in functions}
        for rep in range(repeat):
            for label in (('before','after') if rep%2==0 else ('after','before')):
                fn=functions[label]
                for i in range(50):fn(i)
                began=time.perf_counter()
                for i in range(n):fn(i+100)
                result[label].append(time.perf_counter()-began)
        return {'iterations_per_batch':n,'samples_seconds':result,'median_seconds':{k:statistics.median(v) for k,v in result.items()}}
    row=np.ones(cols)
    validity_bench=benchmark({k:lambda i,fn=fn:fn(row) for k,fn in fns.items()},20000)
    # Use immutable storage as on the decoded path, with a fresh sequence per call.
    vals=np.frombuffer(values.tobytes(),dtype='<f8').reshape(values.shape);seq={'before':0,'after':0}
    full_functions={}
    for k,m in modules.items():
        c=cache(m)
        def publish(i,k=k,c=c):
            seq[k]+=1;c._publish_arrays('XAUUSD+','1m',seq[k],times,volumes,vals)
        full_functions[k]=publish
    publish_bench=benchmark(full_functions,2000)
    result={'step':4,'scalar_predicate_cases':len(rows),'full_publication_cases':len(full_cases),'same_predicates':True,
            'same_snapshots_and_errors':True,'same_full_row_epoch_sequence_reconnect':True,'immutable_arrays_preserved':True,
            'missing_column_and_empty_requirements_preserved':True,'validity_benchmark':validity_bench,'publication_benchmark':publish_bench,
            'change_scope':'precompute indices only; float/isfinite/all semantics unchanged'}
    (a.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
