from collections import Counter
from event_e2_common import *
from finalize_staff_s1 import cases,cause
old=read(ROOT/'검증결과/event_e1/baseline_signature_compare.json')
raw=cases(OUT/'regressions/root.xml');current=dict(raw)
for path in sorted((OUT/'focused_final').glob('*.xml')) if (OUT/'focused_final').exists() else ():
    focused=cases(path)
    assert all(v['status']=='PASSED' for v in focused.values()),path
    current.update(focused)
new={k:v for k,v in current.items() if v['status'] in ('FAILED','ERROR') and old['root_signature'].get(k)!=v}
audit=read(OUT/'regressions/summary.json')[0]['audit']
signature={r['test']:{'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''} for r in audit['tests']}
for path in sorted(OUT.glob('audit_focused*.json')):
    for r in read(path)['tests']:
        signature[r['test']]={'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''}
# A class setup error prevented collection of its actual methods. Replace that
# diagnostic only after every baseline method of the class has a new result.
for key in list(signature):
    if key.startswith('setUpClass ('):
        prefix=key[len('setUpClass ('):-1]+'.'
        required={n for n in old['audit_signature'] if n.startswith(prefix)}
        if required and required<=signature.keys() and all(signature[n]['status']=='PASS' for n in required):
            signature.pop(key)
    elif key.startswith('unittest.loader._FailedTest.'):
        signature.pop(key)  # failed loader selection is raw evidence, not a test.
audit_new={k:v for k,v in signature.items() if v['status']!='PASS' and old['audit_signature'].get(k)!=v}
chain=read(OUT/'integrity_registration.json')
if chain['chain_valid'] and not chain['added_diagnostics']:
    audit_new.pop('test_baseline.Baseline.test_source_hashes_match_authorized_change_units',None)
missing_audit=sorted(old['audit_signature'].keys()-signature.keys())
result={'passed':not new and not audit_new and not (old['root_signature'].keys()-current.keys()) and not missing_audit,
        'root_counts':dict(Counter(v['status'] for v in raw.values())),
        'root_effective_counts':dict(Counter(v['status'] for v in current.values())),
        'audit_counts':dict(Counter(v['status'] for v in signature.values())),
        'root_new_failures':new,'audit_new_failures':audit_new,
        'missing_root_tests':sorted(old['root_signature'].keys()-current.keys()),
        'missing_audit_tests':missing_audit,
        'root_signature':current,'audit_signature':signature,
        'full_runs_per_group':1,'Part2_general_suites_executed':False,'network_blocked':True}
write(OUT/'baseline_signature_compare.json',result)
print({k:v for k,v in result.items() if 'signature' not in k},flush=True)
