"""User-approved public month run, followed by serial one-week measurements."""
from pathlib import Path
import hashlib,json,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/data_selection/approved'
sys.path.insert(0,str(ROOT/'Part2'))

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def run(label,strategy,end,approve=False):
    result=OUT/(label+'.json')
    if result.exists():raise FileExistsError('measurement already complete: '+label)
    args=[sys.executable,'-X','utf8','-B','-m','event_backtest','run','--symbol','XAUUSD+',
          '--start','2025-09-01','--end',end,'--strategies',strategy,'--overlap','0','--sequential',
          '--output',result.relative_to(ROOT).as_posix()]
    if approve:args.append('--yes')
    began=time.perf_counter();last=began
    print('START',label,flush=True)
    with (OUT/(label+'.jsonl')).open('w',encoding='utf-8') as log:
        p=subprocess.Popen(args,cwd=ROOT/'Part2',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for line in p.stdout:
            # Persist no workstation-specific root in measurement evidence.
            clean=line.replace(str(ROOT),'<project>')
            from event_backtest.settings import settings
            clean=clean.replace(settings()['warehouse'],'<warehouse>')
            log.write(clean);log.flush()
            try:event=json.loads(line)
            except ValueError:
                print(clean.rstrip(),flush=True);continue
            kind=event.get('event','')
            if kind=='RUN_PROGRESS':
                if time.perf_counter()-last<30:continue
                last=time.perf_counter()
                print(label,round(event.get('percent',0),2),'%',round(event.get('elapsed_seconds',0),1),'s',flush=True)
            elif kind=='COMPLETE':print('COMPLETE',label,event['result'].get('run_id'),flush=True)
            else:print(label,kind,str({k:v for k,v in event.items() if k in ('message','elapsed_seconds','percent','phase')})[:250],flush=True)
        code=p.wait()
    if code:raise RuntimeError(f'{label} failed with exit code {code}')
    data=json.loads(result.read_text('utf-8'))
    save(label+'_wall.json',{'public_workflow_seconds':time.perf_counter()-began})
    return data

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    from event_backtest.recording import deployment_targets
    from generic_backtest.history.selection import discover_terminals
    profiles=discover_terminals()['terminals']
    if len(profiles)!=1:raise ValueError('one MT5 profile required')
    profile=profiles[0];targets=deployment_targets(profile)
    def hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in targets}
    before=hashes();save('mt5_before.json',before)
    save('approval.json',{'user_approved':True,'symbol':'XAUUSD+','period':['2025-09-01','2025-10-01'],
        'strategy':'SPECIAL1','tick_policy':'Generated ticks warn; only missing M1 history blocks',
        'week':['2025-09-01','2025-09-08'],'week_strategies':[f'SPECIAL{i}' for i in range(1,8)]})
    try:run('month_special1','SPECIAL1','2025-10-01',True)
    finally:
        after=hashes();save('mt5_restoration.json',{'before':before,'after':after,'equal':before==after})
        if after!=before:raise RuntimeError('MT5 restoration mismatch')
    for i in range(1,8):run('week_special'+str(i),'SPECIAL'+str(i),'2025-09-08')
    save('complete.json',{'month_complete':True,'week_complete':list(range(1,8))})

if __name__=='__main__':main()
