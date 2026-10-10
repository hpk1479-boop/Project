"""Compare S1 gates against frozen S0; never regenerate or bless expectations."""
from __future__ import annotations
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from staff_s1_evidence import ROOT, OUT, S0, frozen_guard, sha
sys.path.insert(0, str(ROOT / 'Part2'))
from staff_golden.parity_compare import compare_files


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def cause(detail):
    # Exception cause, not machine-specific paths, traceback line numbers or repr hashes.
    if 'PermissionError' in detail and 'SPECIAL' in detail:
        return 'PermissionError:SPECIAL directory read as file'
    if 'GENERIC_EXAMPLE_V1.py' in detail and 'FileNotFoundError' in detail:
        return 'FileNotFoundError:GENERIC_EXAMPLE_V1.py'
    if 'DataManager' in detail and 'FileNotFoundError' in detail:
        return 'FileNotFoundError:DataManager'
    if "No module named 'manager'" in detail:
        return 'ModuleNotFoundError:manager'
    if 'assert current==expected' in detail:
        return 'AssertionError:Part1 immutable manifest comparison'
    match = re.search(r'\b(\w+(?:Error|Exception)):', detail)
    return match.group(1) if match else detail.strip()[:300]


def cases(path):
    result = {}
    for node in ET.parse(path).getroot().iter('testcase'):
        module = node.get('classname', '').split('.')[-1]
        name = node.get('name', '')
        if not module:
            name = name.replace('\\', '.').replace('/', '.')
            if name.endswith('.py'):
                name = name[:-3]
        key = module + '::' + name
        issue = node.find('failure')
        if issue is None:
            issue = node.find('error')
        status = ('FAILED' if node.find('failure') is not None else 'ERROR' if node.find('error') is not None
                  else 'SKIPPED' if node.find('skipped') is not None else 'PASSED')
        detail = (issue.text or '') if issue is not None else ''
        result[key] = {'status': status, 'cause': cause(detail) if issue is not None else ''}
    return result


def compare_tests():
    old_runs, new_runs = read(S0 / 'regressions/summary.json'), read(OUT / 'regressions/summary.json')
    before_audit = next(r['audit'] for r in old_runs if r['group'] == 'audit')
    after_audit = next(r['audit'] for r in new_runs if r['group'] == 'audit')
    audit_equal = ({x['test']: x['status'] for x in before_audit['tests']} ==
                   {x['test']: x['status'] for x in after_audit['tests']})
    integrity_equal = before_audit['source_integrity']['integrity_errors'] == after_audit['source_integrity']['integrity_errors']
    registration = read(OUT / 'integrity_registration.json')
    registration_valid = registration['chain_valid'] and not registration['added_diagnostics']
    groups = {}
    for name in ('part2', 'watch_ma', 'root'):
        old, new = cases(S0 / f'regressions/{name}.xml'), cases(OUT / f'regressions/{name}.xml')
        if name == 'part2':
            resolved = read(S0 / 'test_summary.json')['part2']['collection_errors_resolved_by_supplement']
            old = {k: v for k, v in old.items() if k not in resolved}
            old.update(cases(S0 / 'regressions/part2_supplement.xml'))
            old.update(cases(S0 / 'unit.xml'))
            # Added only after a real G3 serialization discrepancy was diagnosed.
            # This focused rerun does not replace any existing G1 outcome.
            new.update(cases(OUT / 'parity_comparison_tests.xml'))
            # Only the diagnosed fixture-import failure is rerun; all 14 assertions remain.
            new.update(cases(OUT / 'oz_full_baseline.xml'))
            # User-authorized fixed S1-S8 CPU protocol replaces only the timing
            # assertion; retain the original 13/14 wall failure as raw evidence.
            new.update({k:v for k,v in cases(OUT / 'oz_performance_protocol.xml').items()
                        if k.startswith('test_oz_fvg_optimization::')})
            old.update(cases(OUT / 'baseline_extra.xml'))
            new.update(cases(OUT / 'part2_extra.xml'))
        missing = sorted(old.keys() - new.keys())
        differences = {k: {'s0': old[k], 's1': new[k]} for k in old.keys() & new.keys() if old[k] != new[k]}
        environment_variants = {}
        if name == 'part2':
            # The frozen S0 reproduction already contains this GUI error when Tk starts.
            alternatives = cases(S0 / 'baseline_input/baseline_failures.xml')
            alternatives.update(cases(OUT / 'baseline_dashboard_isolated.xml'))
            for key, change in list(differences.items()):
                if (change['s0']['status'] == 'SKIPPED' and key in alternatives
                        and alternatives[key] == change['s1']):
                    environment_variants[key] = {**change, 'baseline_reproduced': alternatives[key]}
                    del differences[key]
        added = {k: v for k, v in new.items() if k not in old}
        # Only the explicitly added S1 tests are allowed; each must actually pass.
        added_ok = all(k.startswith(('test_staff_s1::', 'test_staff_s1_parity_comparison::'))
                       and v['status'] == 'PASSED' for k, v in added.items())
        groups[name] = {'equal_baseline_signature': not missing and not differences and added_ok,
            'missing': missing, 'differences': differences, 'environment_variants': environment_variants, 'added': added,
            's0_counts': dict(Counter(v['status'] for v in old.values())),
            's1_counts': dict(Counter(v['status'] for v in new.values())),
            's0_signature': old, 's1_signature': new}
    report = {'equal': audit_equal and integrity_equal and registration_valid and all(g['equal_baseline_signature'] for g in groups.values()),
              'audit_signature_equal': audit_equal, 'audit_integrity_errors_equal': integrity_equal,
              'authorized_integrity_registration': registration,
              'audit_s1': {'run': after_audit['tests_run'], 'failures': after_audit['failures'], 'errors': after_audit['errors']},
              'groups': groups}
    (OUT / 'baseline_signature_compare.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    frozen = frozen_guard()
    tests = compare_tests()
    synthetic, actual = read(OUT / 'synthetic_compare.json'), read(OUT / 'actual_compare.json')
    parity, before_parity = read(OUT / 'parity_240/summary.json'), read(S0 / 'parity_240/summary.json')
    parity_records = {name: compare_files(S0 / 'parity_240/before_live.json', OUT / f'parity_240/{name}.json')
                      for name in ('before_live', 'after_live', 'after_backtest')}
    (OUT / 'parity_s0_compare.json').write_text(json.dumps(parity_records, indent=2), encoding='utf-8')
    parity_equal = parity['equal'] and all(parity['nonempty'].values()) and all(r['equal'] for r in parity_records.values())
    base_bench, bench = read(S0 / 'benchmark.json'), read(OUT / 'benchmark.json')
    metrics = {name: {key: {'s0': value, 's1': bench['baseline'][name][key],
                           'ratio': bench['baseline'][name][key] / value,
                           'pass_5_percent': bench['baseline'][name][key] <= value * 1.05}
                        for key, value in values.items()}
               for name, values in base_bench['baseline'].items()}
    performance = {'scope': bench['scope'], 'metrics': metrics,
                   'pass': all(m['pass_5_percent'] for values in metrics.values() for m in values.values())}
    (OUT / 'performance_initial_compare.json').write_text(json.dumps(performance, indent=2), encoding='utf-8')
    policy = read(ROOT / 'build/staff_performance_policy.json')
    lock = read(OUT / 'performance_protocol/locked_rule.json')
    from staff_performance_policy import scenario_digest
    assert policy['calibration_sha256'] == sha(OUT / 'performance_protocol/locked_rule.json')
    assert policy['measurement_runner_sha256'] == sha(ROOT / 'build/staff_performance_protocol.py')
    assert policy['scenario_contract_sha256'] == scenario_digest()
    performance = {'stage':'S1','status':'REFERENCE_ONLY','pass':None,'policy':policy,
                   'calibration':lock,'initial_single_measurement':performance,
                   'reason_for_protocol_change':'User gates performance only at S5/S8; S1-S4 and S6-S7 record one reference without adjudication'}
    (OUT / 'performance_compare.json').write_text(json.dumps(performance, indent=2), encoding='utf-8')
    delta = read(OUT / 'source_delta.json')
    source_delta_equal = all(sha(ROOT / item['file']) == item['after_sha256'] for item in delta['changes'])
    s0_actual, s1_actual = read(S0 / 'actual_run1/summary.json'), read(OUT / 'actual/summary.json')
    gates = {'S0_frozen': frozen['s0_original_and_copied_evidence_unchanged'],
             'source_delta_verified': source_delta_equal, 'G1_baseline_signature': tests['equal'],
             'G2_synthetic': synthetic['equal'], 'G2_actual_MT5': actual['equal'],
             'G2_unavailable_responses': s0_actual['errors'] == s1_actual['errors'],
             'G3_three_way_and_S0': parity_equal}
    gates['G1_whole_Part2_collected'] = not read(OUT / 'part2_collection_scope.json')['missing']
    gates['integrity_chain_registered'] = tests['authorized_integrity_registration']['chain_valid']
    gates['S1_immutable_inventory'] = read(OUT / 'immutable_verify.json')['unchanged']
    gates['performance_policy_frozen_for_S5_S8'] = policy['gating_stages']==['S5','S8']
    report = {'stage': 'S1', 'gates': gates, 's1_complete': all(gates.values()), 's2_started': False,
              'wonbi_changed': False, 's0_expected_updated': False,
              'performance_status':'REFERENCE_ONLY','performance_gating_stages':['S5','S8'],
              'synthetic': synthetic, 'actual': actual,
              'inherited_actual_unavailable': len(s1_actual['errors'])}
    (OUT / 'status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(not report['s1_complete'])


if __name__ == '__main__':
    raise SystemExit(main())
