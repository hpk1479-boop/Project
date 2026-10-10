from pathlib import Path
import subprocess,shutil,json,re,hashlib
R=Path(__file__).resolve().parents[1];out=R/'검증결과/event_e3/compile';out.mkdir(parents=True,exist_ok=True)
source=R/'Part1/program/MT5'
editor=Path('C:/Program Files/MetaTrader 5/metaeditor64.exe')
local=out/'metaeditor64.exe';shutil.copy2(editor,local)
for p in source.glob('*.mqh'):shutil.copy2(p,out/p.name)
names=['PRICE_of_Moses.mq5','RSI_of_Moses.mq5','STO_of_Moses.mq5','DI_of_Moses.mq5','THE_STAFF_OF_MOSES.mq5']
results=[]
for name in names:
    p=out/name;shutil.copy2(source/name,p)
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    proc=subprocess.run([str(local),'/portable','/compile:'+str(p),'/log'],cwd=out,startupinfo=startup,timeout=90)
    log=p.with_suffix('.log').read_bytes().decode('utf-16')
    match=re.search(r'(\d+) errors?, (\d+) warnings?',log)
    record={'file':name,'errors':int(match[1]) if match else None,'warnings':int(match[2]) if match else None,
            'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'exit':proc.returncode}
    results.append(record);print(record,flush=True)
    if record['errors']==0 and p.with_suffix('.ex5').is_file():shutil.copy2(p.with_suffix('.ex5'),(source/name).with_suffix('.ex5'))
(out.parent/'compile_result.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
assert all(row['errors']==0 for row in results)
