"""S5 boundary adapter; invokes the byte-frozen policy and unchanged worker loops."""
import argparse
import ast
import inspect
import os
from pathlib import Path
import subprocess
import sys
import staff_performance_protocol as measurement
import staff_performance_policy as policy

def worker(part1,output):
    source=inspect.getsource(measurement.worker)
    source=source.replace('from part1_host import runtime, capture',
        'from part1_host import capture\n    from staff_golden import baseline_host as runtime')
    source=source.replace('        logging.disable(logging.CRITICAL)',
        "        request_fn = rt.staff_data_request if part1.resolve() == (measurement.ROOT/'Part1').resolve() else rt._staff_handle\n        logging.disable(logging.CRITICAL)",1)
    source=source.replace('rt._staff_handle(request)','request_fn(request)').replace("rt._staff_handle, [r]*250", "request_fn, [r]*250")
    namespace=dict(vars(measurement),measurement=measurement)
    exec(compile(source,'<S5 preregistered full-client boundary>','exec'),namespace)
    namespace['worker'](part1,output)

def invoke(label,part1):
    print('START '+label,flush=True)
    with (measurement.OUT/(label+'.log')).open('x',encoding='utf-8') as log:
        subprocess.run([sys.executable,'-X','utf8','-B',__file__,'worker','--part1',str(part1),
                        '--out',str(measurement.OUT/(label+'.json'))],cwd=measurement.ROOT,
                       stdout=log,stderr=subprocess.STDOUT,check=True,
                       env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONHASHSEED':'0'})
    print('DONE '+label,flush=True)
    return measurement.read(measurement.OUT/(label+'.json'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['worker','compare'])
    p.add_argument('--part1',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    if args.mode=='worker':worker(args.part1,args.out)
    else:
        assert (measurement.ROOT/'성능비교경계_STAFF_S5.md').is_file()
        measurement.invoke=invoke
        raise SystemExit(policy.compare(args.out.resolve(),'S5'))
