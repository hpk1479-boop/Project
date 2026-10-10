"""Short actual BTCUSD LIVE receipt, isolated pipe and data-only EA; no strategy services."""
import ctypes,importlib.util,json,shutil,struct,subprocess,sys,threading,time,uuid
from pathlib import Path
from staff_s7_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from generic_backtest import native_mt5 as native
folder=OUT/'live_BTC';folder.mkdir(exist_ok=False)
program=folder/'server';program.mkdir()
for name in ('THE STAFF OF MOSES.py','staff_schema.py','staff_snapshot.py'):
    shutil.copy2(ROOT/'Part1/program'/name,program/name)
sys.path.insert(0,str(program))
spec=importlib.util.spec_from_file_location('live_s6_staff',program/'THE STAFF OF MOSES.py')
staff=importlib.util.module_from_spec(spec);spec.loader.exec_module(staff)
pipe=r'\\.\pipe\StaffS7_'+uuid.uuid4().hex
records=[]
class CaptureCache(staff.StaffPipeCache):
    def receive_one(self,read_exact,*,finish=None):
        chunks=[]
        def traced(size):
            raw=read_exact(size);chunks.append(raw);return raw
        result=super().receive_one(traced,finish=finish)
        raw=b''.join(chunks)
        with (folder/'live_frames.bin').open('ab') as f:f.write(struct.pack('<qI',time.time_ns()//1000000,len(raw))+raw)
        records.append({'bytes':len(raw),'kind':staff.wire.decode_v2(raw).kind})
        return result
cache=CaptureCache(pipe,gap_journal=folder/'gaps.jsonl')
stop=threading.Event();cache.start(stop)
profile=read(OUT/'mt5_profile.json');terminal=Path(profile['data_root'])
presets=terminal/'MQL5/Presets';presets.mkdir(exist_ok=True)
(presets/'s7_live.set').write_text('InpMode=0\nInpSymbols=BTCUSD\nInpWireVersion=2\nInpPipeName='+pipe+'\n',encoding='ascii')
config=folder/'live.ini';config.write_text('[Experts]\nEnabled=1\nAllowLiveTrading=0\nAllowDllImport=0\n'
    '[StartUp]\nExpert=THE_STAFF_OF_MOSES\nExpertParameters=s7_live.set\nSymbol=BTCUSD\nPeriod=M1\n',encoding='ascii')
hidden=subprocess.STARTUPINFO();hidden.dwFlags|=subprocess.STARTF_USESHOWWINDOW;hidden.wShowWindow=0
process=subprocess.Popen([profile['executable'],'/portable','/config:'+str(config)],cwd=terminal,startupinfo=hidden)
start=time.monotonic()
try:
    while time.monotonic()-start<60:
        time.sleep(1)
        if process.poll() is not None:break
        if len(records)>25 and time.monotonic()-start>30:break
finally:
    stop.set()
    if process.poll() is None:
        native._request_terminal_close(process.pid)
        native._windows_process_wait(process.pid,15)
    if cache._thread is not None:cache._thread.join(timeout=5)
diag=cache.wire_diagnostics();diag['feeds']={'/'.join(k):v for k,v in diag['feeds'].items()}
health=cache.health('BTCUSD',[tf for _,tf in cache.keys()])
write(folder/'result.json',{'actual_live_received':bool(diag['ea_build_hash'] and diag['feeds']),
    'symbol':'BTCUSD','wall_seconds':time.monotonic()-start,'frames':records,'diagnostics':diag,
    'wonbi_samples':{tf:cache.snapshot('BTCUSD',tf).values[-1,[12,45,46,47]].tolist() for _,tf in cache.keys()},
    'source_health':health,'terminal_exit_code':process.poll(),'strategy_services_started':False,
    'external_notifications_sent':False,'isolated_pipe':pipe})
print('LIVE received',len(records),'feeds',len(diag['feeds']),flush=True)
