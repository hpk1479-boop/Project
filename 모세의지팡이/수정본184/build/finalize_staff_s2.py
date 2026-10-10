"""Compare final S2 evidence to frozen S0/S1 without blessing new failures."""
from collections import Counter
import ast
import sys
from staff_s2_evidence import ROOT, OUT, S0, S1, SOURCE, read, write, sha, frozen_guard
from finalize_staff_s1 import cases, cause


def compare_tests():
    baseline = read(S1/'baseline_signature_compare.json')
    groups = {}
    for name in ('part2','watch_ma','root'):
        old = baseline['groups'][name]['s1_signature']
        new = cases(OUT/f'regressions/{name}.xml')
        reruns = OUT/'diagnosed_reruns.json'
        if reruns.exists():
            for rerun in read(reruns):
                if rerun['group'] == name:
                    update = cases(OUT/rerun['xml'])
                    assert set(update) <= set(rerun['ids'])
                    new.update(update)
        differences = {k:{'s1':old[k],'s2':new[k]} for k in old.keys() & new.keys() if old[k]!=new[k]}
        # Previously documented Tk availability alternatives only, not new excuses.
        variants = {}
        if name=='part2':
            known = dict(baseline['groups'][name]['environment_variants'])
            for k, diff in list(differences.items()):
                if k in known and diff['s2'] in (known[k]['s0'],known[k]['s1']):
                    variants[k] = differences.pop(k)
        added = {k:v for k,v in new.items() if k not in old}
        missing = sorted(old.keys()-new.keys())
        added_ok = all(k.startswith('test_staff_s2::') and v['status']=='PASSED' for k,v in added.items())
        groups[name] = {'equal':not differences and not missing and added_ok,'missing':missing,
                       'differences':differences,'environment_variants':variants,'added':added,
                       's1_counts':dict(Counter(v['status'] for v in old.values())),
                       's2_counts':dict(Counter(v['status'] for v in new.values())),
                       's1_signature':old,'s2_signature':new}
    old_audit = next(r['audit'] for r in read(S1/'regressions/summary.json') if r['group']=='audit')
    new_audit = next(r['audit'] for r in read(OUT/'regressions/summary.json') if r['name']=='g1_audit')
    def audit_signature(report):
        return {r['test']:{'status':r['status'],'cause':cause(r.get('detail','')) if r['status']!='PASS' else ''}
                for r in report['tests']}
    old, new = audit_signature(old_audit),audit_signature(new_audit)
    audit_diff = {k:{'s1':old.get(k),'s2':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
    integrity = read(OUT/'integrity_registration.json')
    integrity_equal = new_audit['source_integrity']['integrity_errors'] == integrity['remaining_errors']
    result = {'equal':all(g['equal'] for g in groups.values()) and not audit_diff and integrity_equal,
              'groups':groups,'audit_differences':audit_diff,'audit_s2':{
                  'run':new_audit['tests_run'],'failures':new_audit['failures'],'errors':new_audit['errors']},
              'audit_integrity_matches_registered_S1_chain':integrity_equal}
    result['legacy_Part3'] = read(OUT/'part3_legacy_references.json')
    write(OUT/'baseline_signature_compare.json',result)
    write(OUT/'part2_collection_scope.json',{'s1_collected':len(groups['part2']['s1_signature']),
        's2_collected':len(groups['part2']['s2_signature']),'missing':groups['part2']['missing'],
        'added':sorted(groups['part2']['added']),
        'directories':['validation_suite','cadence_input_validation','conditional_validation','watch_ma_validation']})
    return result


def main():
    frozen = frozen_guard()
    tests = compare_tests()
    inventory = read(ROOT/'build/part1_immutable_sha256.json')
    current = {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
        and p.name!='special_settings.json' and p.suffix!='.ex5'}
    immutable = {'unchanged':inventory==current,'files':len(current),
                 'differences':[k for k in inventory.keys()|current.keys() if inventory.get(k)!=current.get(k)]}
    write(OUT/'immutable_verify.json',immutable)
    synthetic,actual = read(OUT/'synthetic_compare.json'),read(OUT/'actual_compare.json')
    parity = read(OUT/'parity_240/summary.json')
    parity_s0 = read(OUT/'parity_s0_compare.json')
    delta = read(OUT/'source_delta.json')['changes']
    policy = read(ROOT/'build/staff_performance_policy.json')
    from staff_performance_policy import scenario_digest
    policy_valid = policy['scenario_contract_sha256']==scenario_digest()
    before,after = read(S0/'benchmark.json'),read(OUT/'benchmark.json')
    performance = {'stage':'S2','status':'REFERENCE_ONLY','adjudicated':False,'runs':len(after['rounds']),
        'note':'S2 single reference versus historical S0 median; not the S5/S8 controlled CPU gate.',
        'metrics':{key:{m:{'s0':v,'s2':after['baseline'][key][m],
                   'ratio':after['baseline'][key][m]/v if v else None} for m,v in values.items()}
                   for key,values in before['baseline'].items()}}
    performance['post_measurement_patch'] = read(OUT/'private_storage_diagnosis.json')
    write(OUT/'performance_compare.json',performance)
    unchanged_sources = []
    for prefix in ('Part1/program/MT5/','Part1/program/SPECIAL/','Part3/'):
        unchanged_sources += [r for r in read(OUT/'s1_frozen_manifest.json') if r['path'].startswith(prefix)
                              and r['path'].endswith(('.py','.mq5','.mqh'))]
    for name in ('manager_KIM.py','monitor_OZ.py','strategy_FVG.py','strategy_SWEEP.py','strategy_INDICATOR.py',
                 'indicator_facts.py','watch_ma.py','watch_ma_features.py','watch_orchestrator.py'):
        assert (ROOT/'Part1/program'/name).read_bytes()==(SOURCE/'Part1/program'/name).read_bytes()
    scope_ok = all(sha(ROOT/r['path'])==r['sha256'] for r in unchanged_sources)
    program_changes = [r['path'] for r in read(OUT/'s1_frozen_manifest.json')
                       if r['path'].startswith('Part1/program/') and sha(ROOT/r['path'])!=r['sha256']]
    write(OUT/'part1_program_changes.json',program_changes)
    unavailable_equal = read(S0/'actual_run1/summary.json')['errors']==read(OUT/'actual/summary.json')['errors']
    gates = {'original_and_goldens_frozen':frozen['unchanged'],'G1_baseline_signature':tests['equal'],
        'G1_Part2_745_preserved':len(tests['groups']['part2']['s1_signature'])==745 and not tests['groups']['part2']['missing'],
        'G2_synthetic':synthetic['equal'],'G2_actual_MT5':actual['equal'],'G2_unavailable':unavailable_equal,
        'G3_three_way_and_S0':parity['equal'] and all(parity['nonempty'].values()) and all(r['equal'] for r in parity_s0.values()),
        'new_S2_tests':bool(tests['groups']['part2']['added']) and all(v['status']=='PASSED' for v in tests['groups']['part2']['added'].values()),
        'integrity_chain':read(OUT/'integrity_registration.json')['chain_valid'],
        'immutable_inventory':immutable['unchanged'],'protected_logic_unchanged':scope_ok,
        'Part1_program_only_STAFF_changed':program_changes==['Part1/program/THE STAFF OF MOSES.py'],
        'Part3_legacy_frozen':read(OUT/'part3_legacy_references.json')['Part3_all_files_hash_identical'],
        'source_delta_verified':all(sha(ROOT/r['file'])==r['after_sha256'] for r in delta),
        'performance_reference_only':len(after['rounds'])==1 and policy_valid and policy['gating_stages']==['S5','S8']}
    status = {'stage':'S2','gates':gates,'s2_complete':all(gates.values()),'s3_started':False,
        'golden_expected_updated':False,'wonbi_changed':False,'performance_status':'REFERENCE_ONLY'}
    write(OUT/'status.json',status)
    print(status)
    return int(not status['s2_complete'])


if __name__=='__main__': raise SystemExit(main())
