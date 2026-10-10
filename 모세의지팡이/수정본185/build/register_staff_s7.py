import hashlib,sys
from staff_s7_evidence import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
previous=verify_sources()['final_sha256'];rows=[]
for c in provenance():
    if not c['file'].startswith('Part1/program/'):continue
    name=c['file'].removeprefix('Part1/')
    rows.append({'file':name,'before_sha256':previous.get(name,hashlib.sha256(b'').hexdigest()),
        'after_sha256':c['after_sha256'],'observed_s6_before_sha256':c['before_sha256']})
unit=ROOT/'Part1/audit/remediation/30-staff-s7-mt5-wonbi';unit.mkdir(exist_ok=False)
write(unit/'changes.json',rows)
write(unit/'review.json',{'stage':'S7','authorization':'User: MT5-authoritative Wonbi; no Python Wonbi regression; revised Part2 execution policy',
    'golden_expected_modified':False,'source_manifest_sha256':sha(OUT/'s6_frozen_manifest.json')})
result=verify_sources();old=read(ROOT/'검증결과/staff_s6/final_integrity_verify.json')['integrity_errors']
record={'chain_valid':not any('broken hash chain' in s for s in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
    'resolved_baseline_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
write(OUT/'integrity_registration.json',record)
assert record['chain_valid'] and not record['added_diagnostics'],record
print('S7 chain registered',len(rows),'files; retained baseline diagnostics',len(record['remaining_errors']))
