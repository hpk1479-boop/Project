"""Append S5 server removal to the chain and regenerate only current inventory."""
import sys
from staff_s5_evidence import *

frozen_guard()
changes=provenance()
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
previous=verify_sources()['final_sha256']
rows=[]
for c in changes:
    if not c['file'].startswith('Part1/program/'):continue
    path=c['file'].removeprefix('Part1/')
    assert previous[path]==c['before_sha256'],path
    rows.append({'file':path,'before_sha256':c['before_sha256'],'after_sha256':c['after_sha256']})
unit=ROOT/'Part1/audit/remediation/27-staff-s5-server-calculation-removal'
unit.mkdir(exist_ok=False)
write(unit/'changes.json',rows)
write(unit/'review.json',{'stage':'S5','authorization':'User requested S5 server calculation removal',
    'golden_expected_modified':False,'special_1_7_modified':False,'source_manifest_sha256':sha(OUT/'s4_frozen_manifest.json')})
result=verify_sources();old=read(S4/'integrity_registration.json')['remaining_errors']
report={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old))}
write(OUT/'integrity_registration.json',report)
assert report['chain_valid'] and not report['added_diagnostics']
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
write(OUT/'immutable_regeneration.json',{'files':len(inventory),'scope':'current source inventory only'})
print('Registered S5:',len(rows),'files;',len(report['remaining_errors']),'baseline integrity diagnostics')
