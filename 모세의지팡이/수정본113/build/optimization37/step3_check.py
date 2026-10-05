"""Execute the exact worker-loop AST with deterministic clocks and test inputs.

Strategy evaluation is not simulated as evidence; this test targets housekeeping
only. The separate replay check executes the real engine on the recorded input.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,ast,datetime as dt,json,statistics,sys,time


def loop_code(project):
    path=Path(project)/'Part2/event_backtest/runner.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_chunk')
    start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='stop' for t in n.targets))
    body=[]
    for n in fn.body[start:]:
        if isinstance(n,ast.Try):
            # Execute the complete production for-loop and the final month flush.
            body.extend(n.body[:4]);break
        body.append(n)
    return compile(ast.Module(body=body,type_ignores=[]),str(path),'exec')


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
    sys.path[:0]=[str(a.after/'Part1/program'),str(a.after/'Part2')]
    from event_engine.model import Kind
    from event_backtest.cancellation import Cancelled,requested
    from event_backtest.settings import milliseconds
    code={k:loop_code(v) for k,v in [('before',a.before),('after',a.after)]}
    def execute(which,stamps,walls=None,stop_at=None,real=False,kinds=None):
        clock=SimpleNamespace(value=0.)
        tm=time if real else SimpleNamespace(perf_counter=lambda:clock.value,process_time=lambda:clock.value,
                                             perf_counter_ns=lambda:int(clock.value*1e9))
        posts=[];checks=[];cpus=[]
        def stream(*args):
            for i,stamp in enumerate(stamps):
                if not real:clock.value=walls[i] if walls is not None else i*.1
                yield SimpleNamespace(kind=kinds[i] if kinds else Kind.MARKET_BUNDLE,source='test',source_seq=i+1,source_time=stamp,payload={})
        def stop_factory(path):
            def check():
                checks.append(tm.perf_counter())
                if real:return path.exists()
                if stop_at is not None and clock.value>=stop_at:raise Cancelled('test stop')
            return check
        def processor():cpus.append(tm.perf_counter());return 1
        engine=SimpleNamespace(ingress=SimpleNamespace(post=lambda *args,**kw:posts.append(kw['source_time'])),run=lambda:None,error_log=[])
        ns={'Path':Path,'dt':dt,'time':tm,'file_check':stop_factory,'requested':requested,'milliseconds':milliseconds,
            'task':{'out':str(a.output/'worker'),'start':'2026-01-01'},'task_started':0.,'out':a.output/'worker',
            'inputs':None,'subs':None,'resolution':None,'select_inputs':stream,'writer':SimpleNamespace(external_error=None),
            'engine':engine,'Kind':Kind,'current_processor':processor,'write_progress':lambda *args:None,
            'peak_memory':lambda:0,'os':SimpleNamespace(getpid=lambda:1),'start_ms':0,'end_ms':2**63-1,
            '_STOP_CHECK_SECONDS':.5,'_CPU_SAMPLE_SECONDS':.5}
        began=time.perf_counter();exec(code[which],ns);elapsed=time.perf_counter()-began
        return {'posts':posts,'checks':checks,'cpus':cpus,'months':ns['month_metrics'],'cancelled':ns['interrupted'],
                'warmup_bundles':ns['warmup_bundles'],'processed_start':ns['processed_start'],'processed_end':ns['processed_end'],
                'count':ns['count'],'seconds':elapsed}
    def stamps(*strings):return [milliseconds(s) for s in strings]
    cases=[stamps('2026-01-31T23:59:59.999','2026-02-01T00:00:00','2026-02-28T23:59:59.999','2026-03-01'),
           stamps('2024-02-28','2024-02-29T23:59:59.999','2024-03-01'),
           stamps('2026-12-31T23:59:59.999','2027-01-01','2027-03-01'),
           stamps('2026-01-01','2026-04-01','2026-02-01','2026-02-28','2025-12-31'),[]]
    for rows in cases:
        old=execute('before',rows);new=execute('after',rows)
        for key in ('posts','months','cancelled','warmup_bundles','processed_start','processed_end','count'):assert old[key]==new[key],(key,old,new)
    wall=[0,.1,.49,.5,.51,.99,1.,1.49,1.5]
    rows=[milliseconds('2026-01-01')+i*60000 for i in range(len(wall))]
    new=execute('after',rows,wall)
    assert new['checks']==[0,.5,1.,1.5] and new['cpus']==[0,.5,1.,1.5]
    stopped=execute('after',rows,wall,stop_at=.51)
    assert stopped['cancelled'] and len(stopped['posts'])==6 and stopped['checks']==[0,.5,1.]
    pre=execute('after',rows,wall,stop_at=0)
    assert pre['cancelled'] and pre['posts']==[]
    mixed=execute('after',rows,wall,kinds=[Kind.TIMER]+[Kind.MARKET_BUNDLE]*8)
    assert mixed['cpus'][0]==.1 and mixed['count']==8
    # A one-week timestamp span is a calendar test, not a real one-week capture.
    week=[milliseconds('2026-08-29')+i*60000 for i in range(7*1440)]
    old=execute('before',week);new=execute('after',week)
    for key in ('posts','months','warmup_bundles','processed_start','processed_end','count'):assert old[key]==new[key]
    samples={'before':[],'after':[]};rows=[milliseconds('2026-01-01')+i*1000 for i in range(100000)]
    for rep in range(5):
        for label in (('before','after') if rep%2==0 else ('after','before')):
            r=execute(label,rows,real=True);samples[label].append(r['seconds'])
    result={'step':3,'calendar_cases':len(cases),'week_timestamp_items':len(week),
            'production_loop_calendar_and_coverage_equal':True,'stop_check_interval_seconds':.5,'cpu_sampling_interval_seconds':.5,
            'preexisting_stop_prevents_first_post':True,'new_stop_observed_on_next_scheduled_check':True,
            'benchmark_items':len(rows),'benchmark':'exact housekeeping loop, no-op engine, real filesystem stop checks, no-op CPU API',
            'samples_seconds':samples,'median_seconds':{k:statistics.median(v) for k,v in samples.items()}}
    (a.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
