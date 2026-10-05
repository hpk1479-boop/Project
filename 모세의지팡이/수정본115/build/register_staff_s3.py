"""Register S3 delta only; previous chain/goldens remain immutable."""
import hashlib
import sys
from staff_s3_evidence import *

frozen_guard()
changes=provenance()
unit=ROOT/'Part1/audit/remediation/24-staff-s3-snapshot-api'
unit.mkdir(exist_ok=False)
records=[]
for change in changes:
    if change['file'].startswith('Part1/program/'):
        records.append({**change,'file':change['file'].removeprefix('Part1/'),
            'before_sha256':change['before_sha256'] or hashlib.sha256(b'').hexdigest()})
write(unit/'changes.json',records)
write(unit/'review.json',{'stage':'S3','authorization':'User requested S3 Snapshot API and client library',
    'before_manifest_sha256':sha(OUT/'s2_frozen_manifest.json'),'golden_expected_modified':False})
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
result=verify_sources(); old=read(S2/'integrity_registration.json')['remaining_errors']
report={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
    'removed_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
write(OUT/'integrity_registration.json',report)
assert report['chain_valid'] and not report['added_diagnostics'] and not report['removed_diagnostics']
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
write(OUT/'immutable_regeneration.json',{'files':len(inventory),'scope':'current source inventory only'})
print('Registered S3:',len(records),'source files')
