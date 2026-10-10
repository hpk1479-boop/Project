"""Run the remaining final gates once, after all performance workers exit."""
import os
import subprocess
import sys
from staff_s5_evidence import *

assert (OUT/'performance/comparison.json').is_file(), 'Complete all 5 performance pairs first'
jobs=[('G2',['build/run_staff_s5_gates.py','G2']),
      ('actual_oz',['build/verify_staff_s5_actual_oz.py']),
      ('G3',['build/run_staff_s5_gates.py','S5']),
      ('G1',['build/run_staff_s5_gates.py','G1']),
      ('legacy_review',['build/staff_s5_legacy_review.py']),
      ('part3',['build/inventory_part3_legacy_s5.py'])]
results=[]
for name,args in jobs:
    print('START',name,flush=True)
    with (OUT/('final_'+name+'.log')).open('x',encoding='utf-8') as log:
        done=subprocess.run([sys.executable,'-X','utf8','-B',*args],cwd=ROOT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'},stdout=log,stderr=subprocess.STDOUT)
    results.append({'gate':name,'returncode':done.returncode});write(OUT/'final_gate_executions.json',results)
    print('END',name,done.returncode,flush=True)
    if done.returncode and name not in {'G1','G2','G3','actual_oz'}:
        raise SystemExit(done.returncode)
