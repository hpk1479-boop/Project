"""Compile only in S6's isolated MT5 installation; keep every compiler log."""
import hashlib,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/staff_s8'
sys.path.insert(0,str(ROOT/'Part2'))
from generic_backtest import native_mt5 as native
terminal=OUT/'mt5_terminal_before'
if not terminal.exists():
    source=ROOT/'검증결과/staff_s7/mt5_terminal'
    terminal.mkdir()
    for name in ('terminal64.exe','metaeditor64.exe','metatester64.exe'):
        shutil.copy2(source/name,terminal/name)
    for name in ('bases','config'):
        shutil.copytree(source/name,terminal/name)
profile={'executable':str(terminal/'terminal64.exe'),'portable':True,
         'data_root':str(terminal),'account_server':'MonetaMarkets-Demo'}
(OUT/'mt5_profile_before.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
hidden=subprocess.STARTUPINFO();hidden.dwFlags|=subprocess.STARTF_USESHOWWINDOW;hidden.wShowWindow=0
source=ROOT.parent/'수정본14/Part1/program/MT5';mql=terminal/'MQL5'
for path in source.iterdir():
    if path.suffix not in ('.mq5','.mqh'):continue
    folder='Experts' if path.name.startswith(('THE_STAFF','STAFF_')) else 'Indicators'
    dest=mql/folder/path.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
attempt=1
while (OUT/f'before_compile_attempt_{attempt}').exists():attempt+=1
logs=OUT/f'before_compile_attempt_{attempt}';logs.mkdir()
builds=[]
for name in native.NATIVE_MQL_SOURCES:
    path=mql/('Experts' if name.startswith('THE_STAFF') else 'Indicators')/name
    run=subprocess.run([str(terminal/'metaeditor64.exe'),'/portable','/compile:'+str(path),
        '/inc:'+str(mql),'/log'],cwd=terminal,startupinfo=hidden,timeout=120,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    log=native._read_compile_log(path.with_suffix('.log'))
    (logs/(path.stem+'.log')).write_text(log,encoding='utf-8')
    ok='0 errors, 0 warnings' in log and path.with_suffix('.ex5').is_file()
    builds.append({'source':name,'success':ok,'exit_code':run.returncode,
        'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'compiler_sha256':hashlib.sha256((terminal/'metaeditor64.exe').read_bytes()).hexdigest()})
    print(name,ok,log.splitlines()[-1:] if log else '',flush=True)
(logs/'result.json').write_text(json.dumps(builds,indent=2),encoding='utf-8')
assert all(x['success'] for x in builds),builds
