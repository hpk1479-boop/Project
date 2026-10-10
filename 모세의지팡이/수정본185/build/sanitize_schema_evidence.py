"""Keep new retained validation diagnostics portable after MT5 restoration."""
from pathlib import Path
import json,re,os
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup'
assert json.loads((OUT/'mt5_restoration.json').read_text('utf-8'))['restored']
changed=[]
for p in (OUT/'mt5_work').rglob('*'):
    if not p.is_file() or p.suffix not in ('.json','.ini','.log'):continue
    text=p.read_text('utf-8-sig')
    if p.suffix=='.json':
        try:json.loads(text)
        except json.JSONDecodeError:
            # Recover one earlier diagnostics-only decoding pass without a BOM check.
            text=text.encode('utf-16-le').decode('utf-8-sig')
            json.loads(text)
    elif p.suffix=='.ini' and '[' not in text:
        text=text.encode('utf-16-le').decode('utf-8-sig')
    for value,label in ((str(ROOT),'PROJECT'),(os.environ.get('USERPROFILE',''),'USERPROFILE'),(os.environ.get('PROGRAMFILES',''),'PROGRAMFILES')):
        if value:
            for key in (value,value.replace('\\','/'),value.replace('\\','\\\\')):text=text.replace(key,label)
    p.write_text(text,encoding='utf-8');changed.append(p.relative_to(ROOT).as_posix())
for p in OUT.glob('*.xml'):
    text=p.read_text('utf-8');text=text.replace(str(ROOT),'PROJECT').replace(str(ROOT.parent),'WORKSPACE')
    p.write_text(text,encoding='utf-8')
(OUT/'diagnostic_path_cleanup.json').write_text(json.dumps({'paths':changed,'runtime_files_no_longer_used':True},indent=2),encoding='utf-8')
print('Portable diagnostic files',len(changed))
