"""Wait for all functional workers, then measure once without their CPU load."""
import subprocess,sys,time
from staff_s8_evidence import *
names=['parity_240','weekday_1200','weekend_1200','actual_actual','actual_btc']
deadline=time.monotonic()+5400
while not all((OUT/name/'summary.json').is_file() for name in names):
    if time.monotonic()>deadline:raise TimeoutError('External validation still incomplete')
    time.sleep(5)
assert read(OUT/'baseline_signature_compare.json')['pass']
time.sleep(10)
print('Functional workers completed; start fixed 5-pair CPU measurement',flush=True)
with (OUT/'performance.log').open('x',encoding='utf-8') as log:
    result=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'build/run_staff_s8_performance.py'),
        'compare','--out',str(OUT/'performance')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
print('Performance runner finished',result.returncode,flush=True)
