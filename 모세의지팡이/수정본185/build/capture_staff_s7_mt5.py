"""Two actual MT5 passes, same exact input interval, isolated terminal only."""
import argparse,datetime as dt,hashlib,json,shutil,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/staff_s7'
sys.path.insert(0,str(ROOT/'Part2'))
from generic_backtest import native_mt5 as native
profile=json.loads((OUT/'mt5_profile.json').read_text('utf-8'))
parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true')
parser.add_argument('--sigma',type=float,default=3.0);parser.add_argument('--btc-weekend',action='store_true');options=parser.parse_args()
symbol='BTCUSD' if options.btc_weekend else 'XAUUSD+'
start=int((dt.datetime(2026,9,19,0,0,tzinfo=dt.timezone.utc) if options.btc_weekend else dt.datetime(2026,9,23,12,0,tzinfo=dt.timezone.utc)).timestamp())
end=start+(600 if options.btc_weekend else 240)
hidden=subprocess.STARTUPINFO();hidden.dwFlags|=subprocess.STARTF_USESHOWWINDOW;hidden.wShowWindow=0
popen=subprocess.Popen
def hidden_popen(*a,**kw):
    kw.setdefault('startupinfo',hidden);return popen(*a,**kw)
config=native.write_tester_config
for version in ((2,) if options.final else (1,2)):
    label='btc_weekend_previous' if options.btc_weekend else 'final_mt5' if options.final else 'actual_mt5'
    work=OUT/f'{label}_sigma{options.sigma:g}_v{version}';work.mkdir(exist_ok=False)
    def write_config(*a,**kw):
        p=config(*a,**kw)
        with p.open('a',encoding='ascii') as f:
            f.write(f'\n[TesterInputs]\nInpMode=1\nInpWireVersion={version}\nInpWonbiSigma={options.sigma}\n')
        return p
    launched=time.monotonic()
    def cancel():
        if time.monotonic()-launched>900:raise TimeoutError('S6 tester 15 minute limit')
    def emit(kind,value):
        with (work/'progress.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'kind':kind,'value':value},ensure_ascii=False)+'\n')
        print('v'+str(version),value.get('message_code',kind),value.get('elapsed_seconds',''),flush=True)
    with patch.object(native,'write_tester_config',write_config),patch.object(subprocess,'Popen',hidden_popen):
        result=native.run_native_tester(profile,symbol,start*10**9,end*10**9,work,
            emit=emit,cancel=cancel,shutdown_terminal=True,start_timeout=120)
    export=Path(result['export']);retained=work/('capture_'+result['session'])
    shutil.copytree(export,retained)
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={p.name:sha(p) for p in retained.iterdir() if p.is_file()}
    assert hashes=={p.name:sha(p) for p in export.iterdir() if p.is_file()}
    result.update({'retained_export':str(retained),'files_sha256':hashes,
        'start_s':start,'end_s':end,'wire_version':version,'wonbi_sigma':options.sigma,'actual_mt5_execution':True})
    (work/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CAPTURE COMPLETE v'+str(version),flush=True)
