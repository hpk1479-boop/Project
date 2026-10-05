"""Compare S3 against S2 signatures and immutable S0 results."""
from collections import Counter
import ast
import sys
from staff_s3_evidence import *
from finalize_staff_s1 import cases,cause

def compare_tests():
    baseline=read(S2/'baseline_signature_compare.json')
    groups={}
    for name in ('part2','watch_ma','root'):
        old=baseline['groups'][name]['s2_signature']
        new=cases(OUT/f'regressions/{name}.xml')
        if (OUT/'diagnosed_reruns.json').exists():
            for rerun in read(OUT/'diagnosed_reruns.json'):
                if rerun['group']==name:
                    update=cases(OUT/rerun['xml'])
                    assert set(update)<=set(rerun['ids'])
                    if not rerun.get('allow_status_change', False):
                        assert all(new[k]==update[k] for k in update.keys()&new.keys()), 'Undiagnosed difference between full run and targeted check'
                    new.update(update)
        diffs={k:{'s2':old[k],'s3':new[k]} for k in old.keys()&new.keys() if old[k]!=new[k]}
        variants={}
        # Use only previously documented S0/S1 GUI variants, no new allowances.
        if name=='part2':
            known=read(S1/'baseline_signature_compare.json')['groups'][name]['environment_variants']
            for k,diff in list(diffs.items()):
                if k in known and diff['s3'] in (known[k]['s0'],known[k]['s1']):
                    variants[k]=diffs.pop(k)
        added={k:v for k,v in new.items() if k not in old}
        missing=sorted(old.keys()-new.keys())
        groups[name]={'equal':not diffs and not missing and all(k.startswith('test_staff_s3::') and v['status']=='PASSED' for k,v in added.items()),
            'differences':diffs,'missing':missing,'added':added,'environment_variants':variants,
            's2_counts':dict(Counter(v['status'] for v in old.values())),
            's3_counts':dict(Counter(v['status'] for v in new.values())),
            's2_signature':old,'s3_signature':new}
    old_audit=next(r['audit'] for r in read(S2/'regressions/summary.json') if r['name']=='g1_audit')
    new_audit=next(r['audit'] for r in read(OUT/'regressions/summary.json') if r['name']=='g1_audit')
    def signature(r):
        return {x['test']:{'status':x['status'],'cause':cause(x.get('detail','')) if x['status']!='PASS' else ''} for x in r['tests']}
    old,new=signature(old_audit),signature(new_audit)
    diffs={k:{'s2':old.get(k),'s3':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
    integrity_equal=new_audit['source_integrity']['integrity_errors']==read(OUT/'integrity_registration.json')['remaining_errors']
    result={'equal':all(g['equal'] for g in groups.values()) and not diffs and integrity_equal,
        'groups':groups,'audit_differences':diffs,'audit_s3':{'run':new_audit['tests_run'],
        'failures':new_audit['failures'],'errors':new_audit['errors']},'audit_integrity_matches':integrity_equal}
    write(OUT/'baseline_signature_compare.json',result)
    write(OUT/'part2_collection_scope.json',{'s2_collected':len(groups['part2']['s2_signature']),
        's3_collected':len(groups['part2']['s3_signature']),'missing':groups['part2']['missing'],
        'added':sorted(groups['part2']['added']),'directories':['validation_suite','cadence_input_validation','conditional_validation','watch_ma_validation']})
    return result

def main():
    frozen=frozen_guard(); tests=compare_tests(); delta=provenance()
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    final_chain=verify_sources()
    chain_ok=final_chain['integrity_errors']==read(OUT/'integrity_registration.json')['remaining_errors']
    write(OUT/'final_integrity_verify.json',{'matches_registered_diagnostics':chain_ok,
        'integrity_errors':final_chain['integrity_errors'],'final_sha256':final_chain['final_sha256']})
    inventory=read(ROOT/'build/part1_immutable_sha256.json')
    current={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
        and p.name!='special_settings.json' and p.suffix!='.ex5'}
    immutable={'unchanged':inventory==current,'files':len(current),
        'differences':[k for k in inventory.keys()|current.keys() if inventory.get(k)!=current.get(k)]}
    write(OUT/'immutable_verify.json',immutable)
    allowed={'Part1/program/THE STAFF OF MOSES.py','Part1/program/staff_schema.py',
        'Part1/program/staff_snapshot.py','Part1/program/staff_compat.py',
        'Part2/part1_host/runtime.py','Part2/validation_suite/test_staff_s3.py'}
    scope_ok=set(r['file'] for r in delta)==allowed
    program_changes=[r['path'] for r in read(OUT/'s2_frozen_manifest.json')
        if r['path'].startswith('Part1/program/') and sha(ROOT/r['path'])!=r['sha256']]
    write(OUT/'part1_program_changes.json',program_changes)
    # Frozen tests still include all 14 comparison fields and numerical owner tests.
    def nodes(path):
        return {n.name:ast.dump(n) for n in ast.parse(path.read_bytes()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    old=nodes(SOURCE/'Part1/program/THE STAFF OF MOSES.py')
    new=nodes(ROOT/'Part1/program/THE STAFF OF MOSES.py')
    wonbi_equal=all(old[n]==new[n] for n in ('WonbiState','add_wonbi_features','apply_requested_features'))
    before,after=read(S0/'benchmark.json'),read(OUT/'benchmark.json')
    write(OUT/'performance_compare.json',{'stage':'S3','status':'REFERENCE_ONLY','adjudicated':False,
        'runs':len(after['rounds']),'metrics':{k:{m:{'s0':v,'s3':after['baseline'][k][m],
        'ratio':after['baseline'][k][m]/v if v else None} for m,v in values.items()} for k,values in before['baseline'].items()}})
    policy=read(ROOT/'build/staff_performance_policy.json')
    from staff_performance_policy import scenario_digest
    parity=read(OUT/'parity_240/summary.json')
    unavailable=read(S0/'actual_run1/summary.json')['errors']==read(OUT/'actual/summary.json')['errors']
    gates={'original_and_goldens_frozen':frozen['unchanged'],
        'G1_baseline_signature':tests['equal'],
        'G1_Part2_774_preserved':len(tests['groups']['part2']['s2_signature'])==774 and not tests['groups']['part2']['missing'],
        'G2_synthetic':read(OUT/'synthetic_compare.json')['equal'],
        'G2_actual_MT5':read(OUT/'actual_compare.json')['equal'],'G2_unavailable':unavailable,
        'G2_legacy_and_snapshot_compat':all(read(OUT/p/'dual_path_comparison.json')['equal'] for p in ('synthetic','actual')),
        'G3_three_way_and_S0':parity['equal'] and all(parity['nonempty'].values()) and all(r['equal'] for r in read(OUT/'parity_s0_compare.json').values()),
        'new_S3_tests':bool(tests['groups']['part2']['added']) and all(v['status']=='PASSED' for v in tests['groups']['part2']['added'].values()),
        'integrity_chain':chain_ok and read(OUT/'integrity_registration.json')['chain_valid'],
        'immutable_inventory':immutable['unchanged'],'scope_only_S3':scope_ok,
        'existing_Part1_program_only_STAFF_changed':program_changes==['Part1/program/THE STAFF OF MOSES.py'],
        'Part3_legacy_frozen':read(OUT/'part3_legacy_references.json')['Part3_all_files_hash_identical'],
        'wonbi_unchanged':wonbi_equal,
        'configuration_fix_verified':read(OUT/'max_bars_diagnosis.json')['after_sha256']==sha(ROOT/'Part1/program/staff_snapshot.py') and read(OUT/'max_bars_diagnosis.json')['golden_condition_identical_for_650'],
        'performance_reference_only':len(after['rounds'])==1 and policy['gating_stages']==['S5','S8'] and policy['scenario_contract_sha256']==scenario_digest()}
    status={'stage':'S3','gates':gates,'s3_complete':all(gates.values()),'s4_started':False,
        'golden_expected_updated':False,'wonbi_changed':False,'performance_status':'REFERENCE_ONLY'}
    write(OUT/'status.json',status); print(status)
    return int(not status['s3_complete'])

if __name__=='__main__': raise SystemExit(main())
