"""Full collection once; one named massive internal matrix excluded by new policy."""
import os,subprocess,sys,time
from staff_s6_evidence import *
folder=OUT/'regressions';folder.mkdir(exist_ok=False)
env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',STAFF_STAGE='S6',
         PYTHONPATH=str(ROOT/'build')+os.pathsep+os.environ.get('PYTHONPATH',''))
groups={'audit':['Part1/audit/run_tests.py'],
        'watch_ma':['-m','pytest','Part1/watch_ma_validation'],
        'part2':['-m','pytest','Part2/validation_suite','Part2/cadence_input_validation',
                 'Part2/conditional_validation','Part2/watch_ma_validation'],
        'root':['-m','pytest','tests']}
records=[]
for name,args in groups.items():
    print('START',name,flush=True);start=time.perf_counter()
    if args[0]=='-m':args+=['-q','--tb=short','-p','no:cacheprovider','-p','staff_s6_test_policy',
        '--continue-on-collection-errors','--basetemp='+str(folder/('tmp_'+name)),
        '--junitxml='+str(folder/(name+'.xml'))]
    with (OUT/('g1_'+name+'.log')).open('x',encoding='utf-8') as f:
        run=subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,
            env=dict(env,STAFF_TEST_GROUP=name),stdout=f,stderr=subprocess.STDOUT)
    result={'name':'g1_'+name,'returncode':run.returncode,'wall_s':time.perf_counter()-start,'args':args}
    if name=='audit':result['audit']=read(ROOT/'Part1/audit/results/remediation_test_report.json')
    records.append(result);write(folder/'summary.json',records)
    print('END',name,run.returncode,flush=True)
