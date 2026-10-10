"""Retain raw signatures; adjudicate only explicitly documented policy diagnostics."""
from collections import Counter
from staff_s6_evidence import *
from finalize_staff_s1 import cases,cause

INTERNAL={
 'test_staff_s1::test_wonbi_and_watch_history_are_unchanged':'Old EA-byte freeze; v2 EA edit is authorized; formula checks independently preserved in scope_verification.json.',
 'test_staff_s4::test_special_and_calculation_owners_unchanged':'Old STAFF method-AST freeze; authorized v2 transport; client ownership/health unchanged.',
 'test_staff_s5::test_server_removal_scope':'Old STAFF method-AST freeze; authorized v2 transport; no calculation reintroduced.',
 'test_staff_s5::test_static_no_server_derivation_or_legacy_production_data_callers':'Old EA-byte freeze, after legacy/import assertions passed; normalizer/owner invariants checked separately.',
}
baseline=read(S5/'baseline_signature_compare.json');groups={}
for name in ('part2','watch_ma','root'):
    old=baseline['groups'][name]['s5_signature'];raw=cases(OUT/f'regressions/{name}.xml');new=dict(raw)
    if name=='part2':new.update(cases(OUT/'targeted_final.xml'))
    if name=='root' and (OUT/'root_inventory_diagnosis.xml').exists():new.update(cases(OUT/'root_inventory_diagnosis.xml'))
    policy={};variants={};recovered={};issues={}
    if name=='part2':
        omitted='test_staff_s3::test_600_random_legacy_combinations_exact'
        new[omitted]={'status':'POLICY_DIAGNOSTIC_NOT_RUN','cause':'User prohibits large internal combination grids in S6+.'}
        policy[omitted]=new[omitted]
    for key in old.keys()&new.keys():
        if old[key]==new[key]:continue
        delta={'s5':old[key],'s6':new[key]}
        if key in policy:continue
        if key in INTERNAL and new[key]['status']=='FAILED' and (new[key]['cause']=='AssertionError' or 'assert methods(before)==methods(after)' in new[key]['cause']):
            policy[key]={**delta,'reason':INTERNAL[key]}
        elif key=='test_events::test_midnight_checkpoint_has_unfinished_candidate_then_one_event' and new[key]=={'status':'FAILED','cause':'AssertionError'} and read(OUT/'checkpoint_diagnosis.json')['typed_set_order_normalized_equal']:
            policy[key]={**delta,'reason':'The original external candidate/event assertions pass; only typed set/frozenset serialization order differs. See checkpoint_diagnosis.json.'}
        elif old[key]['status'] in ('FAILED','ERROR','SKIPPED') and new[key]['status']=='PASSED':recovered[key]=delta
        elif key=='test_execution_modes::test_mt5_plan_runs_strategy_tester_before_history_prepare' and new[key]=={'status':'ERROR','cause':'PermissionError:SPECIAL directory read as file'}:
            variants[key]={**delta,'reason':'S5 approved Tk environment variance; identical S4 error signature.'}
        else:issues[key]=delta
    missing=sorted(old.keys()-new.keys());added={k:v for k,v in new.items() if k not in old}
    new_failures={k:v for k,v in added.items() if v['status']!='PASSED'}
    groups[name]={'pass':not issues and not missing and not new_failures,
        'raw_signature':raw,'s5_signature':old,'s6_signature':new,
        's5_counts':dict(Counter(v['status'] for v in old.values())),
        'raw_counts':dict(Counter(v['status'] for v in raw.values())),
        's6_counts':dict(Counter(v['status'] for v in new.values())),
        'policy_diagnostics':policy,'environment_variants':variants,'recovered_baseline':recovered,
        'unresolved_differences':issues,'missing':missing,'added':added,'new_test_failures':new_failures}

def audit(folder,diagnosis):
    r=next(x['audit'] for x in read(folder/'regressions/summary.json') if x['name']=='g1_audit')
    def sig(rows):return {x['test']:{'status':x['status'],'cause':cause(x.get('detail','')) if x['status']!='PASS' else ''} for x in rows}
    raw=sig(r['tests']);new=dict(raw)
    if (folder/diagnosis).exists():new.update(sig(read(folder/diagnosis)['tests']))
    return raw,new
_,old=audit(S5,'audit_diagnosed_rerun.json');raw,new=audit(OUT,'audit_import_diagnosis.json')
audit_diff={k:{'s5':old.get(k),'s6':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
result={'pass':all(g['pass'] for g in groups.values()) and not audit_diff,
    'policy':'S6 user external-behavior gate, explicit internal diagnostics; no new external failures allowed',
    'groups':groups,'audit_raw_counts':dict(Counter(x['status'] for x in raw.values())),
    'audit_effective_counts':dict(Counter(x['status'] for x in new.values())),
    'audit_differences':audit_diff,'audit_effective_signature':new}
write(OUT/'baseline_signature_compare.json',result)
collection=read(OUT/'regressions/part2_final_collection_collection.json')
ids={p.split('::',1)[0].replace('\\','/').rsplit('/',1)[-1].removesuffix('.py')+'::'+p.split('::',1)[1] for p in collection['all_ids']}
expected=set(groups['part2']['s5_signature'])
scope={'s5_ids':len(expected),'s6_collected':len(ids),'missing':sorted(expected-ids),
    'new_ids':sorted(ids-expected),'policy_deselected':collection['policy_deselected'],
    'source_collection_file':'regressions/part2_final_collection_collection.json'}
assert len(expected)==824 and not scope['missing']
write(OUT/'part2_collection_scope.json',scope)
fourteen={k:v for k,v in groups['part2']['s6_signature'].items() if k.startswith('test_oz_fvg_optimization::')}
write(OUT/'comparison_14_preservation.json',{'total':len(fourteen),'checks':fourteen,
    'source_unchanged':sha(SOURCE/'Part2/validation_suite/test_oz_fvg_optimization.py')==sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py'),
    'all_passed':all(v['status']=='PASSED' for v in fourteen.values()),'performance_is_reference_only_in_S6':True})
print('G1 effective pass',result['pass'])
for name,g in groups.items():print(name,g['s6_counts'],'unresolved',g['unresolved_differences'],'missing',g['missing'])
print('audit',result['audit_effective_counts'],audit_diff)
