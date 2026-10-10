"""Approved year input construction only; replay waits for optimization validation."""
from pathlib import Path
import argparse,hashlib,json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);p.add_argument('--after-profiles',action='store_true');a=p.parse_args()
    root=Path(a.warehouse);out=ROOT/'검증결과/engine_optimization/year_build';out.mkdir(parents=True,exist_ok=True)
    if a.after_profiles:
        until=time.monotonic()+7200
        while not (root/'runs/engineopt_before_profile_special4/profile_functions.json').is_file():
            if time.monotonic()>until:raise TimeoutError('initial profiles not completed; no recording started')
            time.sleep(5)
    from event_backtest.settings import scenario
    from event_backtest.workflow import proposal
    from event_backtest.recording import prepare,deployment_targets
    from event_backtest.history_check import require_complete
    from generic_backtest.history.selection import discover_terminals
    # Three trading days precede each month. September2024 supplies the first
    # month's warmup; Sep2025 is reused. Alert period remains exactly one year.
    s=scenario(symbol='XAUUSD+',start='2024-10-01',end='2025-10-01',strategies='SPECIAL1',overlap_trading_days=3)
    profiles=discover_terminals()['terminals']
    if len(profiles)!=1:raise ValueError('one selected terminal required')
    profile=profiles[0];targets=deployment_targets(profile)
    def hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in targets}
    def save(name,data):(out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    before=hashes();save('mt5_before.json',before)
    plan=proposal(s,a.warehouse);save('plan.json',plan)
    save('approval.json',{'approved':True,'period':['2024-10-01','2025-10-01'],'warmup_trading_days':3,'strategy':'SPECIAL1',
        'scope':'user approved year BAR recording; retain and reuse existing valid pieces'})
    begun=time.perf_counter();last=[0.0]
    with (out/'progress.jsonl').open('w',encoding='utf-8') as log:
        def emit(kind,data=None):
            if data is None:data={}
            if not isinstance(data,dict):data={'message':str(data)}
            line=json.dumps({'event':kind,**data},ensure_ascii=False,default=str)
            line=line.replace(str(ROOT),'<project>').replace(str(root),'<warehouse>')
            # Native messages occasionally include their temporary export root.
            import re
            line=re.sub(r'[A-Za-z]:\\\\[^"\n]*', '<host-path>',line)
            log.write(line+'\n');log.flush()
            if kind in ('CAPTURE_COMPLETE','CONVERSION_START','BUILD_PLAN') or time.perf_counter()-last[0]>30:
                print(kind,str({k:v for k,v in data.items() if k in ('start','end','elapsed_seconds','message','recorded_at')})[:300],flush=True)
                last[0]=time.perf_counter()
        try:
            captures=prepare(s,a.warehouse,profile,plan=plan,approved_token=plan['approval_token'],emit=emit)
            require_complete(captures)
            save('complete.json',{'elapsed_seconds':time.perf_counter()-begun,'scenario':s,'captures':captures})
        finally:
            after=hashes();save('mt5_restoration.json',{'before':before,'after':after,'equal':before==after})
            if after!=before:raise RuntimeError('MT5 restoration mismatch')
    print('YEAR_BUILD_COMPLETE',len(captures),flush=True)

if __name__=='__main__':main()
