from collections import Counter
from event_e1_common import *
from finalize_staff_s1 import cases,cause
old=read(ROOT/'검증결과/staff_s8/baseline_signature_compare.json')
raw_current=cases(OUT/'regressions/root.xml');current=dict(raw_current);baseline=old['root_signature']
focused={}
if (OUT/'focused_final.xml').exists():
    focused=cases(OUT/'focused_final.xml')
    assert all(v['status']=='PASSED' for v in focused.values())
    current.update(focused)
    write(OUT/'focused_final.json',{'passed':True,'tests':len(focused),'signature':focused,
        'reason':'E1 review corrections and failed checkpoint diagnostic; no full suite rerun'})
new={k:v for k,v in current.items() if v['status'] in ('FAILED','ERROR') and baseline.get(k)!=v}
audit=read(OUT/'regressions/summary.json')[0]['audit']
signature={r['test']:{'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''} for r in audit['tests']}
audit_new={k:v for k,v in signature.items() if v['status']!='PASS' and old['audit_signature'].get(k)!=v}
chain=read(OUT/'integrity_registration.json')
if chain['chain_valid'] and not chain['added_diagnostics']:
    audit_new.pop('test_baseline.Baseline.test_source_hashes_match_authorized_change_units',None)
e1={k:v for k,v in current.items() if 'test_event_e1' in k}
result={'passed':not new and not audit_new and not (baseline.keys()-current.keys()) and bool(e1)
                  and all(v['status']=='PASSED' for v in e1.values()),
        'root_counts':dict(Counter(v['status'] for v in raw_current.values())),
        'root_effective_counts':dict(Counter(v['status'] for v in current.values())),
        'audit_counts':dict(Counter(v['status'] for v in signature.values())),
        'root_new_failures':new,'audit_new_failures':audit_new,'missing_root_tests':sorted(baseline.keys()-current.keys()),
        'e1_tests':e1,'root_signature':current,'root_raw_signature':raw_current,'audit_signature':signature,
        'root_raw_new_failures':{k:v for k,v in raw_current.items() if v['status'] in ('FAILED','ERROR') and baseline.get(k)!=v},
        'checkpoint_test_policy_change':'S6+ user policy: raw compressed bytes diagnostic only; entire typed payload and existing external event/state assertions retained; targeted rerun passed',
        'full_runs_per_group':1,'Part2_general_suites_executed':False,'network_blocked':True}
write(OUT/'baseline_signature_compare.json',result)
print({k:v for k,v in result.items() if 'signature' not in k and k!='e1_tests'},flush=True)
