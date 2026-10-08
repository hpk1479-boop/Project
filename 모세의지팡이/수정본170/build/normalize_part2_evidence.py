"""Remove machine path strings from this task's generated, non-golden evidence."""
from pathlib import Path
import json,re,sys
R=Path(__file__).resolve().parents[1];OUT=R/'검증결과/part2_connection'
sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings
WAREHOUSE=Path(settings()['warehouse'])

def portable(value):
    if isinstance(value,dict):return {k:portable(v) for k,v in value.items() if k!='export'}
    if isinstance(value,list):return [portable(v) for v in value]
    if isinstance(value,str):
        for path,label in ((WAREHOUSE,''),(R,''),(R.parent/'수정본20','수정본20')):
            for prefix in (str(path),path.as_posix()):
                if value==prefix:return label or '.'
                if value.startswith(prefix+'\\') or value.startswith(prefix+'/'):
                    return '/'.join(x for x in (label,value[len(prefix)+1:].replace('\\','/')) if x)
    return value

def main():
    changed=[]
    # Active append-only recording evidence is normalized only after completion.
    for path in list(OUT.glob('*.json'))+list(OUT.glob('preserved_*/result.json')):
        if path.name.endswith('_before_manifest.json'):continue
        data=json.loads(path.read_text('utf-8'));clean=portable(data)
        if clean!=data:path.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8');changed.append(path.name)
    logs=list(OUT.glob('*.compile.log'))+list(OUT.glob('legacy_mt5_work_*/deploy/mql_compile/*.compile.log'))
    for path in logs:
        raw=path.read_bytes();text=raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-8-sig')
        text=re.sub(r'[A-Za-z]:[\\/][^\r\n]*?[\\/]([^\\/\r\n]+\.(?:mq5|mqh|ex5))',r'\1',text)
        path.write_text(text,encoding='utf-8')
    if (OUT/'recording_stopped_at_boundary.json').exists():
        for path in OUT.glob('*recording_progress.jsonl'):
            rows=[portable(json.loads(line)) for line in path.read_text('utf-8').splitlines() if line.strip()]
            path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
    print('normalized',changed)

if __name__=='__main__':main()
