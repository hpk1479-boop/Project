"""Ship the exact compiled binaries exercised by the successful MT5 captures."""
from pathlib import Path
import json,hashlib,shutil
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup';target=ROOT/'Part1/program/MT5'
ready=list((OUT/'mt5_work/builds').glob('*/ready.json'));assert len(ready)==1
build=ready[0].parent;files=json.loads(ready[0].read_text('utf-8'))['files']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,value in files.items():
    assert sha(build/name)==value,name
    if not name.endswith('.ex5'):assert sha(target/name)==value,name
result={}
for name,value in files.items():
    if name.endswith('.ex5'):
        shutil.copy2(build/name,target/name);assert sha(target/name)==value
        result[name]=value
(OUT/'tested_binaries.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print('Packaged exact tested binaries:',len(result))
