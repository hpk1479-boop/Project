"""Failure-only profiling; never substitutes for any of the 5 frozen samples."""
import cProfile
import logging
import pstats
import sys
from staff_s5_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from staff_golden import baseline_host
from staff_golden.scenario import market,SYMBOL,START
from part1_host.capture import pack_wire

assert read(OUT/'performance/comparison.json')['metrics']['request_0']['pass'] is False
data=market();request={'symbol':SYMBOL,'timeframes':['1m'],'indicators':[]}
results={}
for name,part1 in [('S0',S0/'baseline_input/Part1'),('S5',ROOT/'Part1')]:
    with baseline_host.Part1Runtime(symbols=[SYMBOL],start_epoch=START+239,specials=['SPECIAL7'],
            part1_root=part1,log_level=logging.CRITICAL) as rt:
        logging.disable(logging.CRITICAL)
        rt.publish(pack_wire(SYMBOL,'1m',*data.payload('1m',START+239),snapshot=1))
        fn=rt._staff_handle if name=='S0' else rt.staff_data_request
        for _ in range(5):assert 'error' not in fn(request)
        profile=cProfile.Profile();profile.enable()
        for _ in range(250):assert 'error' not in fn(request)
        profile.disable()
        stats=pstats.Stats(profile)
        rows=[{'file':Path(file).name,'line':line,'function':func,'primitive_calls':cc,'calls':nc,
               'self_seconds':tt,'cumulative_seconds':ct} for (file,line,func),(cc,nc,tt,ct,callers) in stats.stats.items()]
        results[name]={'total_profiled_seconds':stats.total_tt,'rows':sorted(rows,key=lambda r:-r['cumulative_seconds'])}
write(OUT/'request0_failure_profile.json',{'purpose':'Failure diagnosis only; profiler overhead, not a replacement benchmark or gate retry',
    'calls':250,'results':results})
policy=read(ROOT/'build/staff_performance_policy.json');comparison=read(OUT/'performance/comparison.json')
write(OUT/'performance_failure_analysis.json',{'candidate_failures':{k:v for k,v in comparison['metrics'].items() if not v['pass']},
    'baseline_drift_failures':{k:{**v,'allowed_S0_cpu_range':[policy['calibration'][k]['median']/v['limit'],policy['calibration'][k]['median']*v['limit']]} for k,v in comparison['metrics'].items() if not v['environment_stable']},
    'environment_match':comparison['environment_match'],'retry_or_policy_change':False,
    'measurement_adapter_sha256':sha(ROOT/'build/run_staff_s5_performance.py'),
    'preregistered_boundary_sha256':sha(ROOT/'성능비교경계_STAFF_S5.md')})
for name,result in results.items():
    print(name,[(r['file'],r['function'],round(r['cumulative_seconds'],4)) for r in result['rows'][:12]],flush=True)
