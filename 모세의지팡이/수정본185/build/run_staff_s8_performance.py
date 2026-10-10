"""S8: frozen workload/method; only two user-selected workloads gate."""
import argparse,importlib.util,inspect,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import staff_performance_protocol as measurement
import staff_performance_policy as policy

def frozen_market():
    # Exact S0 45-column input generator for BOTH workers. No S7 numerical oracle.
    path=measurement.ROOT/'검증결과/staff_s0/baseline_input/Part2/part1_host/synthetic.py'
    spec=importlib.util.spec_from_file_location('part1_host._s8_frozen_perf_input',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    from staff_golden.scenario import SYMBOL,START,PATTERN
    return module.SyntheticMarket(SYMBOL,START,START+240,history_days=30,seed=0,pattern=PATTERN)

def worker(part1,output):
    source=inspect.getsource(measurement.worker)
    source=source.replace('from part1_host import runtime, capture',
        'from part1_host import capture\n    from staff_golden import baseline_host as runtime')
    source=source.replace('    data = market()', '    data = frozen_market()')
    source=source.replace('        logging.disable(logging.CRITICAL)',
        "        request_fn = rt.staff_data_request if part1.resolve() == (measurement.ROOT/'Part1').resolve() else rt._staff_handle\n        logging.disable(logging.CRITICAL)",1)
    source=source.replace('rt._staff_handle(request)','request_fn(request)').replace('rt._staff_handle, [r]*250','request_fn, [r]*250')
    namespace=dict(vars(measurement),measurement=measurement,frozen_market=frozen_market)
    exec(compile(source,'<S8 frozen input and complete client boundary>','exec'),namespace)
    namespace['worker'](part1,output)

def invoke(label,part1):
    print('START',label,flush=True)
    with (measurement.OUT/(label+'.log')).open('x',encoding='utf-8') as log:
        subprocess.run([sys.executable,'-X','utf8','-B',__file__,'worker','--part1',str(part1),'--out',str(measurement.OUT/(label+'.json'))],
            cwd=measurement.ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONHASHSEED':'0'})
    print('END',label,flush=True);return measurement.read(measurement.OUT/(label+'.json'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['worker','compare']);p.add_argument('--part1',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.mode=='worker':worker(a.part1,a.out)
    else:
        measurement.OUT=a.out.resolve();measurement.OUT.mkdir(exist_ok=False)
        locked=measurement.read(policy.POLICY)
        assert measurement.sha(measurement.__file__)==locked['measurement_runner_sha256']
        assert policy.scenario_digest()==locked['scenario_contract_sha256']
        measurement.write_new(measurement.OUT/'policy.json',locked)
        pairs=[(invoke(f'pair_{i}_s0',measurement.BASE),invoke(f'pair_{i}_candidate',measurement.ROOT/'Part1')) for i in range(1,6)]
        report=policy.evaluate(locked,pairs,measurement.program_hashes(measurement.ROOT/'Part1'))
        report['all_workloads_diagnostic_pass']=report.pop('pass')
        targets=('parser_650_rows','oz_fvg_live_seconds')
        report['gating_workloads']=targets;report['reference_only_workloads']=['request_'+str(i) for i in range(4)]
        report['pass']=report['environment_match'] and all(report['metrics'][k]['pass'] and report['metrics'][k]['environment_stable'] for k in targets)
        report.update(stage='S8',policy_sha256=measurement.sha(policy.POLICY),policy_modified=False)
        measurement.write_new(measurement.OUT/'comparison.json',report)
        print('S8 performance gate',report['pass'],flush=True)
