"""Finalize S6 telemetry within its still-open remediation unit; keep first registration."""
import sys
from staff_s6_evidence import *
unit=ROOT/'Part1/audit/remediation/28-staff-s6-wire-v2'
backup=OUT/'integrity_registration_initial_changes.json'
if not backup.exists():backup.write_bytes((unit/'changes.json').read_bytes())
rows=read(unit/'changes.json')
for row in rows:row['after_sha256']=sha(ROOT/'Part1'/row['file'])
write(unit/'changes.json',rows)
review=read(unit/'review.json');review['final_adjustment']='Complete per-feed reconnect counter and preserve its diagnostic test; no calculation or external decision change.'
write(unit/'review.json',review)
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
result=verify_sources();old=read(S5/'integrity_registration.json')['remaining_errors']
report={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
    'resolved_baseline_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
write(OUT/'integrity_registration.json',report)
assert report['chain_valid'] and not report['added_diagnostics'],report
provenance();print('Final S6 chain valid',len(report['remaining_errors']),'baseline diagnostics')
