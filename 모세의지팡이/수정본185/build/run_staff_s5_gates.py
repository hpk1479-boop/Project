"""One final run per S4 gate. Failure diagnosis must name a narrower rerun."""
import argparse
import os
import subprocess
import sys
import time
from staff_s5_evidence import ROOT, OUT, S0, frozen_guard, read, write

ENV = dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',STAFF_STAGE='S5',STAFF_PERFORMANCE_RESULTS=str(OUT/'performance'))


def run(name,args,*,cwd=None,allowed=(0,)):
    print('START',name,flush=True)
    started = time.perf_counter()
    with (OUT/(name+'.log')).open('x',encoding='utf-8') as log:
        result = subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=cwd or ROOT,
                                env=ENV,stdout=log,stderr=subprocess.STDOUT)
    record = {'name':name,'returncode':result.returncode,'wall_s':time.perf_counter()-started,'args':args}
    write(OUT/(name+'_execution.json'),record)
    print('END',name,'rc='+str(result.returncode),flush=True)
    assert result.returncode in allowed, record


def g2():
    capture = next((S0/'actual_mt5').glob('capture_*'))
    for kind, expected, original in [('synthetic',(0,),'golden_run1'),('actual',(1,),'actual_run1')]:
        args = ['-m','staff_golden','record' if kind=='synthetic' else 'actual',
                '--part1',str(ROOT/'Part1'),'--out',str(OUT/kind)]
        if kind=='actual': args += ['--capture',str(capture)]
        run(kind,args,cwd=ROOT/'Part2',allowed=expected)
        run(kind+'_compare',['build/compare_staff_golden.py',str(S0/original/'golden.sqlite'),
            str(OUT/kind/'golden.sqlite'),'--out',str(OUT/(kind+'_compare.json'))])


def g3(stage):
    run(('parity_240_'+stage),['-m','staff_golden','parity','--before',str(S0/'baseline_input/Part1'),
        '--after',str(ROOT/'Part1'),'--out',str(OUT/('parity_240_'+stage)),'--window','240'],cwd=ROOT/'Part2')
    sys.path.insert(0,str(ROOT/'Part2'))
    from staff_golden.parity_compare import compare_files
    results = {name:compare_files(S0/'parity_240/before_live.json',OUT/f'parity_240_{stage}/{name}.json')
               for name in ('before_live','after_live','after_backtest')}
    write(OUT/('parity_s0_compare_'+stage+'.json'),results)
    assert all(r['equal'] for r in results.values())


def g1():
    folder=OUT/'regressions'; folder.mkdir(exist_ok=False)
    groups={'audit':['Part1/audit/run_tests.py'],
            'watch_ma':['-m','pytest','Part1/watch_ma_validation'],
            'part2':['-m','pytest','Part2/validation_suite','Part2/cadence_input_validation',
                     'Part2/conditional_validation','Part2/watch_ma_validation'],
            'root':['-m','pytest','tests']}
    records=[]
    for name,args in groups.items():
        if args[0]=='-m':
            args+=['-q','--tb=short','-p','no:cacheprovider','--continue-on-collection-errors',
                   '--basetemp='+str(folder/('tmp_'+name)),'--junitxml='+str(folder/(name+'.xml'))]
        run('g1_'+name,args,allowed=(0,1,2))
        rec=read(OUT/('g1_'+name+'_execution.json'))
        if name=='audit': rec['audit']=read(ROOT/'Part1/audit/results/remediation_test_report.json')
        records.append(rec)
        write(folder/'summary.json',records)


def reference():
    assert not (OUT/'benchmark.json').exists()
    sys.path.insert(0,str(ROOT/'Part2'))
    from staff_golden.benchmark import benchmark
    benchmark(ROOT/'Part1',OUT/'benchmark.json',rounds=1)
    write(OUT/'performance_reference_policy.json',{'stage':'S4','status':'REFERENCE_ONLY',
          'adjudicated':False,'runs':1,'gating_stages':['S5','S8']})


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('gate',choices=['G1','G2','S5'])
    args=parser.parse_args()
    frozen_guard(allow_runtime_drift=True)
    if args.gate=='S5':
        g3(args.gate)
    else:
        {'G1':g1,'G2':g2,'reference':reference}[args.gate]()
    frozen_guard(allow_runtime_drift=True)
