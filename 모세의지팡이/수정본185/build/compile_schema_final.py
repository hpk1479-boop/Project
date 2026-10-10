"""Compile a portable validation copy, without touching the normal MT5 install."""
from pathlib import Path
import sys,subprocess,shutil,json,re,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'Part2'))
from generic_backtest.history.selection import discover_terminals
from generic_backtest.native_mt5 import _metaeditor64,NATIVE_MQL_SOURCES
OUT=ROOT/'검증결과/schema_cleanup/compile';OUT.mkdir(parents=True,exist_ok=True)
source=ROOT/'Part1/program/MT5'
profiles=discover_terminals()['terminals']
assert len(profiles)==1,'select the installed MT5 profile explicitly'
editor=_metaeditor64(profiles[0]);local=OUT/'metaeditor64.exe';shutil.copy2(editor,local)
for p in source.glob('*.mqh'):shutil.copy2(p,OUT/p.name)
results=[]
for name in NATIVE_MQL_SOURCES:
    p=OUT/name;shutil.copy2(source/name,p)
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    proc=subprocess.run([str(local),'/portable','/compile:'+str(p),'/log'],cwd=OUT,startupinfo=startup,timeout=90)
    log=p.with_suffix('.log').read_bytes().decode('utf-16')
    summary=re.search(r'(\d+) errors?, (\d+) warnings?',log)
    record={'file':name,'errors':int(summary[1]) if summary else None,'warnings':int(summary[2]) if summary else None,
            'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'exit':proc.returncode}
    log=log.replace(str(OUT),'validation_compile').replace(str(editor.parent),'metaeditor_install')
    p.with_suffix('.log').write_text(log,encoding='utf-8')
    results.append(record);print(record,flush=True)
    if record['errors']==0 and record['warnings']==0 and p.with_suffix('.ex5').is_file():
        shutil.copy2(p.with_suffix('.ex5'),(source/name).with_suffix('.ex5'))
(OUT/'result.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
assert all(r['errors']==r['warnings']==0 for r in results),results
