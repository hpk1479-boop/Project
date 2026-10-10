"""Final one full run per permitted suite; preserve output even on failure."""
import os,subprocess,sys,time
from event_e1_common import *
folder=OUT/'regressions';folder.mkdir(exist_ok=False)
env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',STAFF_STAGE='E1')
env['PYTHONPATH']=str(ROOT/'build/event_network_guard')+os.pathsep+env.get('PYTHONPATH','')
groups={'audit':['Part1/audit/run_tests.py'],'root':['-m','pytest','tests']}
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
