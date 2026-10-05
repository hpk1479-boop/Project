"""Register the explicitly user-owned config edit without rewriting config."""
import sys
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
result=verify_sources();name='program/config.txt'
unit=ROOT/'Part1/audit/remediation/29-staff-s6-user-symbol-config'
unit.mkdir(exist_ok=False)
write(unit/'changes.json',[{'file':name,'before_sha256':result['final_sha256'][name],
    'after_sha256':sha(ROOT/'Part1'/name),'user_owned_change':True,
    'observed_s5_before_sha256':sha(SOURCE/'Part1'/name)}])
write(unit/'review.json',{'stage':'S6','authorization':'User directly edited revision 13 config and supplied both values; preserve the edit.',
    'STAFF_ALLOWED_SYMBOLS':'XAUUSD+,NAS100, BTCUSD','TARGET_SYMBOLS':'XAUUSD+,NAS100, BTCUSD',
    'config_rewritten_by_agent':False,'verification':'user_symbol_validation.json'})
result=verify_sources();old=read(S5/'integrity_registration.json')['remaining_errors']
report={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
    'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
    'resolved_baseline_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
write(OUT/'integrity_registration.json',report)
assert report['chain_valid'] and not report['added_diagnostics'],report
print('User-owned config registered; no new integrity diagnostics')
