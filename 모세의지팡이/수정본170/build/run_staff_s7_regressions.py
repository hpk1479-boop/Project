"""S7 full gate once: Part1 audit, root tests, S7 tests. No old Part2 suites."""
import os,subprocess,sys,time
from staff_s7_evidence import *
folder=OUT/'regressions';folder.mkdir(exist_ok=False)
env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',STAFF_STAGE='S7')
groups={'audit':['Part1/audit/run_tests.py'],
        'root':['-m','pytest','tests'],
        's7':['-m','pytest','Part2/validation_suite/test_staff_s7.py']}
records=[]
for name,args in groups.items():
    print('START',name,flush=True);start=time.perf_counter()
    if args[0]=='-m':args+=['-q','--tb=short','-p','no:cacheprovider','--continue-on-collection-errors',
        '--basetemp='+str(folder/('tmp_'+name)),'--junitxml='+str(folder/(name+'.xml'))]
    with (OUT/('g1_'+name+'.log')).open('x',encoding='utf-8') as f:
        run=subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
    result={'name':name,'returncode':run.returncode,'wall_s':time.perf_counter()-start,'args':args}
    if name=='audit':result['audit']=read(ROOT/'Part1/audit/results/remediation_test_report.json')
    records.append(result);write(folder/'summary.json',records);print('END',name,run.returncode,flush=True)
