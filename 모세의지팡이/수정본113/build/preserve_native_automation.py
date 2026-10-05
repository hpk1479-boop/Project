"""Preserve the explicitly requested native automation dependency closure."""
from pathlib import Path
import ast,hashlib,json,shutil
R=Path(__file__).resolve().parents[1];old=R.parent/'수정본18';base=old/'Part2'
roots=['generic_backtest/native_mt5.py','generic_backtest/native_features.py','generic_backtest/bar_seed.py']
pending=list(roots);seen=set();edges=[]
def resolve(parts):
    if parts[:3]==['pit','features','percentile']:
        parts=['calculations','percentile']+parts[3:]
    p=base.joinpath(*parts)
    if p.with_suffix('.py').is_file():return p.with_suffix('.py')
    if (p/'__init__.py').is_file():return p/'__init__.py'
while pending:
    name=pending.pop()
    if name in seen:continue
    seen.add(name);p=base/name
    package=list(p.relative_to(base).parts[:-1])
    for parent in p.parents:
        if parent==base:break
        init=parent/'__init__.py'
        if init.is_file():pending.append(init.relative_to(base).as_posix())
    for n in ast.walk(ast.parse(p.read_text('utf-8-sig'))):
        names=[]
        if isinstance(n,ast.Import):names=[a.name.split('.') for a in n.names]
        elif isinstance(n,ast.ImportFrom):
            prefix=package[:len(package)-n.level+1] if n.level else []
            stem=prefix+((n.module or '').split('.') if n.module else [])
            names=[stem]+[stem+a.name.split('.') for a in n.names]
        for parts in names:
            q=resolve(parts)
            if q:
                rel=q.relative_to(base).as_posix();pending.append(rel);edges.append([name,rel])
restored=[]
for name in sorted(seen):
    src=base/name;dst=R/'Part2'/name
    if not dst.exists():
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        restored.append({'path':'Part2/'+name,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest()})
evidence=R/'검증결과/event_e3'
path=evidence/'deletions.json';prior=json.loads(path.read_text('utf-8'))
names={'Part2/'+name for name in seen}
path.write_text(json.dumps([r for r in prior if r['path'] not in names],ensure_ascii=False,indent=2),encoding='utf-8')
previous_path=evidence/'native_automation_preserved.json'
if previous_path.exists():
    previous=json.loads(previous_path.read_text('utf-8'))
    restored=previous['restored']+restored
(evidence/'native_automation_preserved.json').write_text(json.dumps({'roots':roots,'preserved_closure':sorted(names),
    'restored':restored,'edges':edges,'remaining_deletions':len([r for r in prior if r['path'] not in names]),
    'reason':'User explicitly preserves native automation and every dependency. Shared provider files remain dormant; old backtest execution entry is removed.'},ensure_ascii=False,indent=2),encoding='utf-8')
print('Preserved',len(seen),'restored',len(restored),'remaining deletions',len([r for r in prior if r['path'] not in names]))
