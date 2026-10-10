"""Read-only full predecessor comparison plus protected subtree comparison."""
from event_e2_common import *
old=ROOT.parent/'수정본16'
rows=read(OUT/'revision16_before_manifest.json');mismatches=[]
expected={r['path'] for r in rows}
for i,row in enumerate(rows):
    path=old/row['path'];actual=sha(path) if path.is_file() else None
    if actual!=row['sha256']:mismatches.append({'path':row['path'],'before':row['sha256'],'after':actual})
    if i and i%5000==0:print('predecessor verified',i,flush=True)
actual_paths={p.relative_to(old).as_posix() for p in old.rglob('*') if p.is_file()}
added=sorted(actual_paths-expected)
protected=[]
for row in rows:
    name=row['path']
    if name.startswith('Part3/') or name=='Part1/program/config.txt' or name.startswith('검증결과/staff_') or name.startswith('검증결과/event_e1/') or name=='build/staff_performance_policy.json':
        path=ROOT/name
        if not path.is_file() or sha(path)!=row['sha256']:protected.append(name)
result={'predecessor':'수정본16','files_checked':len(rows),'modified_or_missing':mismatches,
        'added_files':added,'protected_candidate_mismatches':protected,
        'passed':not mismatches and not added and not protected}
write(OUT/'preservation.json',result)
print(result,flush=True)
assert result['passed']
