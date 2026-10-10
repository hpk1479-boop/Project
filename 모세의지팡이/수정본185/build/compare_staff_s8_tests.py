from collections import Counter
from staff_s8_evidence import *
from finalize_staff_s1 import cases,cause
old=read(ROOT/'검증결과/staff_s7/baseline_signature_compare.json')
root=cases(OUT/'regressions/root.xml');baseline=old['root_effective_signature']
new={k:v for k,v in root.items() if v['status'] in ('FAILED','ERROR') and baseline.get(k)!=v}
audit=read(OUT/'regressions/summary.json')[0]['audit']
signature={r['test']:{'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''} for r in audit['tests']}
audit_new={k:v for k,v in signature.items() if v['status']!='PASS' and old['audit_effective_signature'].get(k)!=v}
integrity='test_baseline.Baseline.test_source_hashes_match_authorized_change_units'
chain=read(OUT/'integrity_registration.json')
if chain['chain_valid'] and not chain['added_diagnostics']:audit_new.pop(integrity,None)
newtests=cases(OUT/'regressions/s8.xml')
result={'pass':not new and not audit_new and not (baseline.keys()-root.keys()) and all(v['status']=='PASSED' for v in newtests.values()),
    'root_counts':dict(Counter(v['status'] for v in root.values())),'root_signature':root,'root_new_failures':new,
    'missing_root_tests':sorted(baseline.keys()-root.keys()),'audit_counts':dict(Counter(v['status'] for v in signature.values())),
    'audit_signature':signature,'audit_new_failures':audit_new,'new_tests_counts':dict(Counter(v['status'] for v in newtests.values())),
    'new_tests_signature':newtests,'full_runs_per_group':1,'Part2_general_suites_executed':False}
write(OUT/'baseline_signature_compare.json',result)
print({k:v for k,v in result.items() if 'signature' not in k})
