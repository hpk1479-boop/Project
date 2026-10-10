"""Record S4 deltas, explicitly identifying pre-existing unregistered S3 files."""
import hashlib
import sys
from staff_s4_evidence import *

frozen_guard()
changes=provenance()
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
previous=verify_sources()['final_sha256']
empty=hashlib.sha256(b'').hexdigest()
bridges=[]; records=[]
for c in changes:
    if not c['file'].startswith('Part1/program/'): continue
    path=c['file'].removeprefix('Part1/')
    baseline=c['before_sha256'] or empty
    registered=previous.get(path,empty)
    if registered != baseline:
        # Both bytes and the old diagnostic are frozen in revision 10. This
        # reconciliation is provenance only, never a retrospective test pass.
        assert sha(SOURCE/c['file']) == baseline
        bridges.append({'file':path,'before_sha256':registered,'after_sha256':baseline})
    records.append({'file':path,'before_sha256':baseline,'after_sha256':c['after_sha256']})
for name,rows,review in [
    ('25-staff-s4-frozen-s3-provenance',bridges,{'purpose':'Register only the already frozen S3 bytes needed as S4 predecessors',
        'baseline_defects_resolved':False,'source_manifest_sha256':sha(OUT/'s3_frozen_manifest.json')}),
    ('26-staff-s4-client-migration',records,{'stage':'S4','authorization':'User requested S4',
        'golden_expected_modified':False,'special_1_7_modified':False})]:
    unit=ROOT/'Part1/audit/remediation'/name;unit.mkdir(exist_ok=False)
    write(unit/'changes.json',rows);write(unit/'review.json',review)
result=verify_sources();old=read(S3/'integrity_registration.json')['remaining_errors']
removed=sorted(set(old)-set(result['integrity_errors']))
allowed={f'Unrecorded source change: {r["file"]}' for r in bridges}|{f'Unrecorded new source: {r["file"]}' for r in bridges}
report={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
    'removed_diagnostics':removed,'frozen_baseline_reconciliations':bridges}
write(OUT/'integrity_registration.json',report)
assert report['chain_valid'] and not report['added_diagnostics'] and set(removed)<=allowed
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
write(OUT/'immutable_regeneration.json',{'files':len(inventory),'scope':'current source inventory only'})
print('Registered S4:',len(records),'source files;',len(bridges),'documented S3 predecessors')
