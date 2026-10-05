"""Seal the reviewed E2 delta only; preserve all preceding chain entries."""
import sys
from event_e2_common import *
unit=ROOT/'Part1/audit/remediation/33-event-e2-strategies/changes.json'
if not (OUT/'integrity_registration_initial.json').exists():
    write(OUT/'integrity_registration_initial.json',read(OUT/'integrity_registration.json'))
rows=read(unit)
for row in rows:row['after_sha256']=sha(ROOT/'Part1'/row['file'])
write(unit,rows)
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
v=verify_sources();prior=read(ROOT/'검증결과/event_e1/integrity_registration.json')['remaining_errors']
record={'chain_valid':not any('broken hash chain' in e for e in v['integrity_errors']),
        'remaining_errors':v['integrity_errors'],'added_diagnostics':sorted(set(v['integrity_errors'])-set(prior)),
        'changed_files':rows}
write(OUT/'integrity_registration.json',record)
assert record['chain_valid'] and not record['added_diagnostics'],record
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
print('E2 sealed',len(rows),'sources;',len(inventory),'immutable entries',flush=True)
