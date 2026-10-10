"""Refresh the same reviewed E1 delta after development, preserving its parent hashes."""
import sys
from event_e1_common import *
path=ROOT/'Part1/audit/remediation/32-event-e1-skeleton/changes.json'
rows=read(path)
for row in rows:row['after_sha256']=sha(ROOT/'Part1'/row['file'])
write(path,rows)
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
v=verify_sources();prior=read(ROOT/'검증결과/staff_s8/final_integrity_verify.json')['integrity_errors']
result={'chain_valid':not any('broken hash chain' in x for x in v['integrity_errors']),
        'remaining_errors':v['integrity_errors'],'added_diagnostics':sorted(set(v['integrity_errors'])-set(prior))}
write(OUT/'integrity_registration.json',result)
assert result['chain_valid'] and not result['added_diagnostics'],result
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
print('Sealed E1',len(rows),'files; inventory',len(inventory),flush=True)
