"""Read-only original audit plus protected copied content comparison."""
import json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/event_perf1'
before=json.loads((OUT/'revision17_before_manifest.json').read_text('utf-8'))
old=ROOT.parent/'수정본17'
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
changed=[];protected=[];count=0
for item in before:
    name=item['path'];path=old/name
    if not path.is_file() or sha(path)!=item['sha256']:changed.append(name)
    # All old validation/baseline evidence, Part3 and user configurations.
    if name.startswith(('Part3/','검증결과/')) or Path(name).name in ('config.txt','command_aliases.json','special_settings.json','staff_performance_policy.json'):
        count+=1
        if not (ROOT/name).is_file() or sha(ROOT/name)!=item['sha256']:protected.append(name)
known={r['path'] for r in before}
added=[p.relative_to(old).as_posix() for p in old.rglob('*') if p.is_file() and p.relative_to(old).as_posix() not in known]
result={'revision17_files':len(before),'revision17_mismatches':changed,'revision17_added':added,
        'protected_revision18_files':count,'protected_revision18_mismatches':protected}
(OUT/'source_preservation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
assert not changed and not added and not protected,result
print('Original and protected files unchanged',len(before),count)
