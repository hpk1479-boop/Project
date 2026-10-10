"""Only root failures absent from S6 plus the one corrected S7 test."""
import os,subprocess,sys,time,xml.etree.ElementTree as ET
from staff_s7_evidence import *
baseline=read(ROOT/'검증결과/staff_s6/baseline_signature_compare.json')['groups']['root']
old=baseline.get('effective_signature',baseline.get('raw_signature'))
old_bad={k for k,v in old.items() if v['status']!='PASSED'}
selected=[]
for case in ET.parse(OUT/'regressions/root.xml').iter('testcase'):
    if case.find('failure') is None:continue
    short=case.attrib['classname'].split('.')[-1]+'::'+case.attrib['name']
    if short not in old_bad:
        selected.append(case.attrib['classname'].replace('.','/')+'.py::'+case.attrib['name'])
selected+=['Part2/validation_suite/test_staff_s7.py::test_new_columns_do_not_weaken_wire_rejection[truncated]']
write(OUT/'diagnosis_selected.json',{'nodes':selected,'reason':'Only newly failed cases after full run; corrected native synthetic input and approved MT5-only Wonbi expectations.'})
assert selected
command=[sys.executable,'-X','utf8','-B','-m','pytest',*selected,'-q','--tb=short','-p','no:cacheprovider',
         '--basetemp='+str(OUT/'pytest_diagnosis_01'),'--junitxml='+str(OUT/'diagnosis_01.xml')]
with (OUT/'diagnosis_01.log').open('x',encoding='utf-8') as f:
    run=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=f,stderr=subprocess.STDOUT)
print('Selected',len(selected),'tests; exit',run.returncode)
