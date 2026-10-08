"""Serialized, idle-after-recording week measurements and approved public year run."""
from pathlib import Path
import argparse,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/engine_optimization'

def command(args,label,warehouse,cwd=ROOT):
    began=time.perf_counter()
    print('START',label,flush=True)
    with (OUT/(label+'.jsonl')).open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-B',*args],cwd=cwd,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        last=0.
        for line in process.stdout:
            clean=line.replace(str(ROOT),'<project>').replace(str(warehouse),'<warehouse>')
            log.write(clean);log.flush()
            try:row=json.loads(line)
            except ValueError:print(clean.rstrip(),flush=True);continue
            if row.get('event')=='RUN_PROGRESS':
                if time.perf_counter()-last>=30:
                    print(label,round(row.get('percent',0),1),'%',flush=True);last=time.perf_counter()
            elif row.get('event')!='COMPLETE':
                print(label,row.get('event','RESULT'),row.get('notifications',''),flush=True)
        code=process.wait()
    (OUT/(label+'_wall.json')).write_text(json.dumps({'seconds':time.perf_counter()-began,'exit_code':code}),encoding='utf-8')
    if code:raise RuntimeError(label+' failed: '+str(code))
    print('DONE',label,flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();warehouse=Path(a.warehouse)
    required=[OUT/'year_build/complete.json',OUT/'year_build/mt5_restoration.json',OUT/'ready_for_measurement.json',
        warehouse/'runs/engineopt_before_week_all/result.json',warehouse/'runs/engineopt_after_week_all/result.json']
    began=time.monotonic()
    while not all(p.exists() for p in required):
        if time.monotonic()-began>14400:raise TimeoutError('required construction/validation did not complete')
        time.sleep(5)
    if not json.loads((OUT/'year_build/mt5_restoration.json').read_text())['equal']:raise RuntimeError('MT5 not restored')
    for i in range(1,8):
        label='after_week_special'+str(i)
        command(['build/measure_engine_optimization.py','--strategies','SPECIAL'+str(i),'--label',label,'--warehouse',str(warehouse)],label,warehouse)
    command(['build/measure_engine_optimization.py','--strategies','ALL','--label','after_week_all_final','--warehouse',str(warehouse)],'after_week_all_final',warehouse)
    command(['build/compare_engine_alerts.py','--warehouse',str(warehouse)],'alert_comparison',warehouse)
    command(['-m','event_backtest','run','--symbol','XAUUSD+','--start','2024-10-01','--end','2025-10-01',
             '--mode','BAR','--strategies','SPECIAL1','--cores','14','--yes','--warehouse',str(warehouse),
             '--output','검증결과/engine_optimization/year_special1.json'],'year_special1',warehouse,ROOT/'Part2')
    (OUT/'measurements_complete.json').write_text(json.dumps({'complete':True}),encoding='utf-8')

if __name__=='__main__':main()
