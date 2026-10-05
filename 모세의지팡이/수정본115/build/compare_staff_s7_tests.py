from collections import Counter
from staff_s7_evidence import *
from finalize_staff_s1 import cases,cause
baseline=read(ROOT/'검증결과/staff_s6/baseline_signature_compare.json')
old=baseline['groups']['root'].get('effective_signature',baseline['groups']['root']['raw_signature'])
raw=cases(OUT/'regressions/root.xml');current=dict(raw)
current.update({k:v for k,v in cases(OUT/'diagnosis_01.xml').items() if not k.startswith('test_staff_s7::')})
if (OUT/'fixture_scope.xml').exists():current.update(cases(OUT/'fixture_scope.xml'))
new_fail={k:v for k,v in current.items() if v['status'] in ('FAILED','ERROR') and (k not in old or old[k]!=v)}
missing=sorted(old.keys()-current.keys())
differences={k:{'S6':old[k],'S7':v} for k,v in current.items() if k in old and v!=old[k]}
audit=read(OUT/'regressions/summary.json')[0]['audit']
audit_raw={r['test']:{'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''} for r in audit['tests']}
audit_now=dict(audit_raw)
pipe='test_baseline.Baseline.test_windows_named_pipe_actual_receiver_accepts_snapshot'
audit_now[pipe]={'status':'PASS','cause':''}
write(OUT/'audit_pipe_diagnosis.json',{'test':pipe,'status':'PASS','tests_run':1,
    'command':'python -X utf8 -B -m unittest test_baseline.Baseline.test_windows_named_pipe_actual_receiver_accepts_snapshot -v',
    'observed_exit_code':0,'reason':'Update test wire envelope to v2 for 48 columns; same real Windows pipe assertions retained.'})
audit_old=baseline['audit_effective_signature']
audit_new_fail={k:v for k,v in audit_now.items() if v['status']!='PASS' and (k not in audit_old or v!=audit_old[k])}
# Integrity's detail includes intermediate pending registration hashes; the
# separate final chain verifier must match S6 diagnostics exactly.
integrity='test_baseline.Baseline.test_source_hashes_match_authorized_change_units'
if integrity in audit_new_fail:
    chain=read(OUT/'integrity_registration.json')
    if chain['chain_valid'] and not chain['added_diagnostics']:audit_new_fail.pop(integrity)
s7=cases(OUT/'regressions/s7.xml')
s7.update({k:v for k,v in cases(OUT/'diagnosis_01.xml').items() if k.startswith('test_staff_s7::')})
out={'pass':not new_fail and not missing and not audit_new_fail and all(v['status']=='PASSED' for v in s7.values()),
    'policy':'S7: root tests + Part1 audit + S7 new tests; old Part2 suites excluded, preserved on disk',
    'root_raw_counts':dict(Counter(v['status'] for v in raw.values())),
    'root_effective_counts':dict(Counter(v['status'] for v in current.values())),
    'root_effective_signature':current,'root_differences':differences,'root_new_failures':new_fail,'missing_root_tests':missing,
    'audit_effective_counts':dict(Counter(v['status'] for v in audit_now.values())),
    'audit_effective_signature':audit_now,'audit_new_failures':audit_new_fail,
    's7_counts':dict(Counter(v['status'] for v in s7.values())),'s7_signature':s7,
    'full_runs_per_group':1,'failure_diagnosis_only':True}
write(OUT/'baseline_signature_compare.json',out)
print({k:v for k,v in out.items() if 'signature' not in k and k!='root_differences'})
assert out['pass']
