"""STEP 1 gap predicate benchmark on an explicitly supplied raw archive.

Unprofiled wall times are separate from cProfile call-count/timing evidence.
Input conversion is outside the timed predicate loop; optimized setup is inside.
No market state, worker or strategy work is represented by these timings.
"""
from pathlib import Path
import argparse
import cProfile
import hashlib
import json
import statistics
import sys
import time
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from generic_backtest.runner import _GapCursor


def original_gap(gaps, previous_ns, now):
    return any(g['status'] in ('KNOWN_MISSING','ACQUISITION_ERROR')
               and previous_ns<g['end_ns'] and now>=g['start_ns'] for g in gaps)


def run_old(gaps,start,times):
    previous=start;out=bytearray()
    for now in times:
        out.append(original_gap(gaps,previous,now));previous=now
    return out


def run_new(gaps,start,times):
    cursor=_GapCursor(gaps);previous=start;out=bytearray()
    for now in times:
        out.append(cursor.intersects(previous,now));previous=now
    return out


def counted_new(gaps,start,times):
    cursor=_GapCursor(gaps);index=0;end_checks=0;start_checks=0;moves=0;out=bytearray();previous=start
    intervals=cursor.intervals
    for now in times:
        while index<len(intervals):
            end_checks+=1
            if intervals[index][1]>previous:break
            index+=1;moves+=1
        if index<len(intervals):start_checks+=1
        out.append(index<len(intervals) and intervals[index][0]<=now);previous=now
    return out,dict(relevant_intervals=len(intervals),end_comparisons=end_checks,
                    candidate_start_comparisons=start_checks,cursor_moves=moves,
                    relevant_interval_visits=end_checks,
                    average_relevant_interval_visits_per_tick=end_checks/len(times))


def main():
    import numpy as np
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--skip-baseline',action='store_true',help='For optional full archive; baseline wall time remains unmeasured')
    p.add_argument('--profile',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    m=json.loads((args.archive/'manifest.json').read_text())
    times=[]
    for chunk in m['chunks']:
        if chunk['count']:
            a=np.load(args.archive/chunk['file'],mmap_mode='r',allow_pickle=False)
            times.extend(int(t)*1_000_000 for t in a['time_msc'])
    gaps=m.get('gaps',());start=m['coverage_start_ns']
    result={'scope':'PREDICATE_ONLY_NOT_BACKTEST_WALL_TIME','raw_archive_identity':m['archive_identity'],
            'ticks':len(times),'input_gaps':len(gaps),'rounds':args.rounds,'rows':{}}
    outputs={}
    methods=[('optimized',run_new)] if args.skip_baseline else [('baseline',run_old),('optimized',run_new)]
    for name,fn in methods:
        samples=[]
        for _ in range(args.rounds):
            t=time.perf_counter();output=fn(gaps,start,times);samples.append(time.perf_counter()-t)
        outputs[name]=output
        result['rows'][name]={'wall_samples_seconds':samples,'median_seconds':statistics.median(samples),
                              'true_count':sum(output),'boolean_sha256':hashlib.sha256(output).hexdigest()}
        print(name,len(times),samples,flush=True)
        args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(json.dumps(result,indent=2))
    counted,counters=counted_new(gaps,start,times)
    assert counted==outputs['optimized']
    result['optimized_counters']=counters
    if 'baseline' in outputs:
        assert outputs['baseline']==outputs['optimized'];result['tick_by_tick_equal']=True
    elif all(g['status'] not in ('KNOWN_MISSING','ACQUISITION_ERROR') for g in gaps):
        assert not any(outputs['optimized']);result['literal_predicate_empty_filter_proof']=True
    if args.profile:
        for name,fn in methods:
            prof=cProfile.Profile();prof.enable();out=fn(gaps,start,times);prof.disable()
            assert out==outputs[name]
            rows=[dict(function=getattr(e.code,'co_name',str(e.code)),file=getattr(e.code,'co_filename','builtin'),
                       calls=e.callcount,self_seconds=e.inlinetime,cumulative_seconds=e.totaltime)
                  for e in prof.getstats()]
            result['rows'][name]['profile']=rows
            gen=next(c for c in original_gap.__code__.co_consts if isinstance(c,types.CodeType) and c.co_name=='<genexpr>')
            result['rows'][name]['old_gap_generator_calls']=sum(e.callcount for e in prof.getstats() if e.code is gen)
    args.output.write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
