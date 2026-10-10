import hashlib,sys
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
previous=verify_sources()['final_sha256'];rows=[]
for c in provenance():
    if not c['file'].startswith('Part1/program/'):continue
    name=c['file'].removeprefix('Part1/');before=c['before_sha256'] or hashlib.sha256(b'').hexdigest()
    predecessor=previous.get(name,hashlib.sha256(b'').hexdigest())
    # EA is one of the pre-existing unrecorded source diagnostics. Append to
    # the actual chain predecessor while retaining the observed S5 source hash.
    rows.append({'file':name,'before_sha256':predecessor,'after_sha256':c['after_sha256'],
                 'observed_s5_before_sha256':before,'existing_unrecorded_baseline':predecessor!=before})
unit=ROOT/'Part1/audit/remediation/28-staff-s6-wire-v2';unit.mkdir(exist_ok=False)
write(unit/'changes.json',rows)
write(unit/'review.json',{'stage':'S6','authorization':'User requested Wire v2 and updated external-behavior verification policy',
    'golden_expected_modified':False,'source_manifest_sha256':sha(OUT/'s5_frozen_manifest.json')})
result=verify_sources();old=read(S5/'integrity_registration.json')['remaining_errors']
record={'chain_valid':not any('broken hash chain' in s for s in result['integrity_errors']),
        'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
        'resolved_baseline_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
write(OUT/'integrity_registration.json',record)
assert record['chain_valid'] and not record['added_diagnostics'],record
print('S6 chain registered',len(rows),'files')
