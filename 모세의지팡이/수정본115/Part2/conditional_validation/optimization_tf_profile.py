"""Test-only BAR/TF/DataFrame instrumentation around the current coordinator.

Does not patch any production file. Child counters are returned through the
existing optional profile metadata channel, never through strategy inputs.
Instrumented wall times must not be substituted for uninstrumented benchmarks.
"""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


def install_child():
    import pandas as pd
    from generic_backtest.fast.frame_cache import PITFrameCache
    from generic_backtest.fast import profiling
    totals={}
    frame_original=PITFrameCache.frame
    def counted_frame(self,symbol,timeframe,bars,token,**kwargs):
        # SDK bars are immutable sequences. Do not consume an arbitrary iterator
        # merely for counters; the original implementation remains the consumer.
        n=min(len(bars),682) if hasattr(bars,'__len__') else None
        row=totals.setdefault(timeframe,dict(calls=0,input_rows=0,unknown_row_count_calls=0,
                      rebuilt_frames=0,forming_patches=0,output_rows=0,wall_seconds=0.))
        row['calls']+=1
        if n is None:row['unknown_row_count_calls']+=1
        else:row['input_rows']+=n
        rebuilds,patches=self.rebuilds,self.forming_updates
        before=time.perf_counter()
        try:
            result=frame_original(self,symbol,timeframe,bars,token,**kwargs)
            row['output_rows']+=len(result)
            return result
        finally:
            row['wall_seconds']+=time.perf_counter()-before
            row['rebuilt_frames']+=self.rebuilds-rebuilds
            row['forming_patches']+=self.forming_updates-patches
    PITFrameCache.frame=counted_frame
    operations={}
    for name in ('copy','resample','groupby'):
        original=getattr(pd.DataFrame,name)
        def measured(self,*a,_original=original,_name=name,**k):
            row=operations.setdefault(_name,dict(calls=0,rows=0,wall_seconds=0.))
            row['calls']+=1;row['rows']+=len(self)
            before=time.perf_counter()
            try:return _original(self,*a,**k)
            finally:row['wall_seconds']+=time.perf_counter()-before
        setattr(pd.DataFrame,name,measured)
    original_export=profiling.export
    def export(profiler):
        rows=original_export(profiler)
        rows.append(dict(file=__file__,line=0,function='OPTIMIZATION_TF_COUNTERS',calls=0,
                         recursive_calls=0,self_seconds=0.,cumulative_seconds=0.,
                         tf_counters=totals,dataframe_operations=operations))
        return rows
    profiling.export=export


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--plugin',required=True)
    p.add_argument('--rows',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.root.resolve();own=Path(__file__).resolve()
    sys.path[:0]=[str(root),str(root/'cadence_input_validation')]
    from pit.models import BarState
    init=BarState.__init__;counts=Counter();completed=Counter()
    def counted(self,*a,**k):
        tf=k['timeframe'] if 'timeframe' in k else a[2]
        state=k.get('state',a[14] if len(a)>14 else 'FORMING')
        counts[tf]+=1
        if state=='COMPLETED':completed[tf]+=1
        init(self,*a,**k)
    BarState.__init__=counted
    original_popen=subprocess.Popen
    hook=("import importlib.util;"
          f"_s=importlib.util.spec_from_file_location('_optimization_tf_instrument',{str(own)!r});"
          "_m=importlib.util.module_from_spec(_s);_s.loader.exec_module(_m);_m.install_child();")
    marker='from generic_backtest.worker import child_main;child_main()'
    def popen(command,*a,**k):
        if isinstance(command,list) and '-c' in command:
            command=list(command);i=command.index('-c')+1
            if marker in command[i]:command[i]=command[i].replace(marker,hook+marker)
        return original_popen(command,*a,**k)
    subprocess.Popen=popen
    original_argv=sys.argv
    sys.argv=['benchmark.py','--root',str(root),'--archive',str(args.archive.resolve()),
              '--plugin',args.plugin,'--rows',str(args.rows),'--profile','--output',str(args.output)]
    try:
        spec=importlib.util.spec_from_file_location('_optimization_coordinator_bench',root/'cadence_input_validation/benchmark.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.main()
    finally:
        sys.argv=original_argv;subprocess.Popen=original_popen;BarState.__init__=init
    result=json.loads(args.output.read_text())
    result['instrumentation_scope']='COUNTERS_ONLY_NOT_HEADLINE_WALL_TIME; parent BAR allocations + worker PIT frames'
    result['parent_bar_counters']={'allocations_by_tf':dict(counts),'completed_allocations_by_tf':dict(completed),
         'forming_allocations_by_tf':{tf:counts[tf]-completed[tf] for tf in counts}}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
