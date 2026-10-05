"""S5 exactness and frozen performance adjudication, retaining every failed gate."""
from collections import Counter
import ast
import sys
from staff_s5_evidence import *
from finalize_staff_s1 import cases,cause

def compare_tests():
    baseline=read(S4/'baseline_signature_compare.json');groups={}
    for name in ('part2','watch_ma','root'):
        old=baseline['groups'][name]['s4_signature'];new=cases(OUT/f'regressions/{name}.xml')
        if (OUT/'diagnosed_reruns.json').exists():
            for rerun in read(OUT/'diagnosed_reruns.json'):
                if rerun['group']==name:
                    update=cases(OUT/rerun['xml']);assert set(update)<=set(rerun['ids'])
                    new.update(update)
        diffs={k:{'s4':old[k],'s5':new[k]} for k in old.keys()&new.keys() if old[k]!=new[k]}
        variants={}
        if name=='part2':
            known=read(S1/'baseline_signature_compare.json')['groups'][name]['environment_variants']
            for k,diff in list(diffs.items()):
                if k in known and diff['s5'] in (known[k]['s0'],known[k]['s1']):variants[k]=diffs.pop(k)
        added={k:v for k,v in new.items() if k not in old};missing=sorted(old.keys()-new.keys())
        groups[name]={'equal':not diffs and not missing and all(k.startswith('test_staff_s5::') and v['status']=='PASSED' for k,v in added.items()),
            'differences':diffs,'missing':missing,'added':added,'environment_variants':variants,
            's4_counts':dict(Counter(v['status'] for v in old.values())),
            's5_counts':dict(Counter(v['status'] for v in new.values())),
            's4_signature':old,'s5_signature':new}
    def audit(folder):
        report=next(r['audit'] for r in read(folder/'regressions/summary.json') if r['name']=='g1_audit')
        def sig(rows):return {x['test']:{'status':x['status'],'cause':cause(x.get('detail','')) if x['status']!='PASS' else ''} for x in rows}
        result=sig(report['tests'])
        if (folder/'audit_diagnosed_rerun.json').exists():result.update(sig(read(folder/'audit_diagnosed_rerun.json')['tests']))
        return report,result
    old_report,old=audit(S4);new_report,new=audit(OUT)
    differences={k:{'s4':old.get(k),'s5':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
    integrity=new_report['source_integrity']['integrity_errors']==read(OUT/'integrity_registration.json')['remaining_errors']
    result={'equal':all(g['equal'] for g in groups.values()) and not differences and integrity,
        'groups':groups,'audit_differences':differences,'audit_integrity_matches':integrity,
        'audit_s5':{'run':len(new),'failures':sum(v['status']=='FAIL' for v in new.values()),'errors':sum(v['status']=='ERROR' for v in new.values())}}
    result['new_differences']={group:values['differences'] for group,values in groups.items() if values['differences']}
    write(OUT/'baseline_signature_compare.json',result)
    write(OUT/'part2_collection_scope.json',{'s4_collected':len(groups['part2']['s4_signature']),
        's5_collected':len(groups['part2']['s5_signature']),'missing':groups['part2']['missing'],'added':sorted(groups['part2']['added'])})
    return result

def main():
    frozen=frozen_guard(allow_runtime_drift=True);tests=compare_tests();delta=provenance()
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    chain=verify_sources()
    chain_ok=chain['integrity_errors']==read(OUT/'integrity_registration.json')['remaining_errors']
    write(OUT/'final_integrity_verify.json',{'matches_registered_diagnostics':chain_ok,'integrity_errors':chain['integrity_errors']})
    inventory=read(ROOT/'build/part1_immutable_sha256.json')
    current={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(OUT/'immutable_verify.json',{'unchanged':current==inventory,'files':len(current),'differences':[k for k in inventory.keys()|current.keys() if inventory.get(k)!=current.get(k)]})
    changes=[r['path'] for r in read(OUT/'s4_frozen_manifest.json') if r['path'].startswith('Part1/program/') and sha(ROOT/r['path'])!=r['sha256']]
    write(OUT/'part1_program_changes.json',changes)
    changed_tests=[r['path'] for r in read(OUT/'s4_frozen_manifest.json') if Path(r['path']).name.startswith('test_') and sha(ROOT/r['path'])!=r['sha256']]
    write(OUT/'existing_test_changes.json',changed_tests)
    fields={k:v for k,v in tests['groups']['part2']['s5_signature'].items() if k.startswith('test_oz_fvg_optimization::')}
    comparison={'total':len(fields),'all_passed':all(v['status']=='PASSED' for v in fields.values()),'checks':fields,
                'source_unchanged':sha(SOURCE/'Part2/validation_suite/test_oz_fvg_optimization.py')==sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py')}
    write(OUT/'comparison_14_preservation.json',comparison)
    performance=read(OUT/'performance/comparison.json')
    parity=read(OUT/'parity_240_S5/summary.json')
    gates={'original_and_goldens_frozen':frozen['unchanged'],
        'original_source_and_protected_evidence_unchanged':frozen['original_source_and_protected_copy_unchanged'],
        'G1_baseline_signature':tests['equal'],
        'G1_Part2_816_preserved':len(tests['groups']['part2']['s4_signature'])==816 and not tests['groups']['part2']['missing'],
        'G2_synthetic':read(OUT/'synthetic_compare.json')['equal'],'G2_actual_MT5':read(OUT/'actual_compare.json')['equal'],
        'G2_unavailable':read(S0/'actual_run1/summary.json')['errors']==read(OUT/'actual/summary.json')['errors'],
        'G2_actual_OZ_4302':read(OUT/'actual_oz_supplement.json')['equal'] and read(OUT/'actual_oz_supplement.json')['frames']==4302,
        'G3_240_and_S0':parity['equal'] and all(parity['nonempty'].values()) and all(r['equal'] for r in read(OUT/'parity_s0_compare_S5.json').values()),
        'new_S5_tests':bool(tests['groups']['part2']['added']) and all(v['status']=='PASSED' for v in tests['groups']['part2']['added'].values()),
        'integrity_chain':chain_ok and read(OUT/'integrity_registration.json')['chain_valid'],'immutable_inventory':inventory==current,
        'scope_only_S5':set(changes)=={'Part1/program/THE STAFF OF MOSES.py','Part1/program/staff_schema.py'},
        'existing_14_comparisons':comparison['total']==14 and comparison['source_unchanged'] and comparison['all_passed'],
        'Part3_legacy_frozen':read(OUT/'part3_legacy_references.json')['Part3_all_files_hash_identical'],
        'wonbi_and_client_formulas_unchanged':sha(SOURCE/'Part1/program/staff_compat.py')==sha(ROOT/'Part1/program/staff_compat.py'),
        'performance_S5':performance['pass']}
    status={'stage':'S5','gates':gates,'s5_complete':all(gates.values()),'s6_started':False,
        'golden_expected_updated':False,'wonbi_changed':False,'performance_status':'PASS' if performance['pass'] else 'FAIL'}
    write(OUT/'status.json',status);print(status,flush=True)
    return int(not status['s5_complete'])

if __name__=='__main__':raise SystemExit(main())
