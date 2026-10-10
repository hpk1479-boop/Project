"""Append E2 delta; never modify a prior stage's authorization or baseline."""
import sys
from event_e2_common import *
OUT=ROOT/"검증결과/event_perf1"
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
before=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
unit=ROOT/'Part1/audit/remediation/34-event-perf1'
if unit.exists():raise FileExistsError(unit)
rows=[]
for path in sorted((ROOT/'Part1/program').rglob('*.py')):
    if '__pycache__' in path.parts:continue
    name=path.relative_to(ROOT/'Part1').as_posix();actual=sha(path)
    old=ROOT.parent/'수정본17/Part1'/name
    if not old.exists() or sha(old)!=actual:
        rows.append({'file':name,'before_sha256':before.get(name,empty),'after_sha256':actual})
unit.mkdir()
write(unit/'changes.json',rows)
write(unit/'review.json',{'stage':'PERF1','authorization':'User: bundle caches, subscription filtering, closed SWEEP/OZ computation optimization, NumPy scan, adaptive input windows; cumulative seed changes diagnostic; LIVE/replay identity required',
    'polling_default_preserved':True,'no_Part2_Part3_dependency':True,'network_blocked_in_tests':True})
result=verify_sources();prior=read(ROOT/'검증결과/event_e2/integrity_registration.json')['remaining_errors']
record={'chain_valid':not any('broken hash chain' in e for e in result['integrity_errors']),
        'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(prior)),
        'changed_files':rows}
write(OUT/'integrity_registration.json',record)
assert record['chain_valid'] and not record['added_diagnostics'],record
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
print('PERF1 registered',len(rows),'files',flush=True)
