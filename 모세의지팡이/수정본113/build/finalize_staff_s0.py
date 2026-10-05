"""Build an honest gate report and verify original files remained untouched."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s0'


def read(name):
    return json.loads((OUT / name).read_text('utf-8-sig'))


def main():
    baseline = read('source6_manifest.json')
    source = ROOT.parent / '수정본6'
    expected = {row['path']: row['sha256'] for row in baseline}
    actual = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    changed = sorted(k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k))
    copied_changed = [k for k, digest in expected.items() if not (ROOT / k).is_file() or
                      hashlib.sha256((ROOT / k).read_bytes()).hexdigest() != digest]
    production = [k for k in expected if k.startswith('Part1/program/') and '/logs/' not in k
                  and Path(k).suffix in {'.py', '.mq5', '.mqh', '.txt'}]
    candidate_changed = [k for k in production if not (ROOT / k).is_file() or
                         hashlib.sha256((ROOT / k).read_bytes()).hexdigest() != expected[k]]
    original = {'original_files': len(actual), 'unchanged': not changed, 'differences': changed,
                'all_copied_files_unchanged': not copied_changed,
                'copied_file_differences': copied_changed,
                'part1_production_files_checked': len(production),
                'part1_production_unchanged': not candidate_changed,
                'part1_production_differences': candidate_changed}
    (OUT / 'original_preservation.json').write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
    comparison = read('determinism.json')
    first, second = read('golden_run1/summary.json'), read('golden_run2/summary.json')
    parity = read('parity_240/summary.json')
    benchmarks = read('benchmark.json')
    actual_comparison = read('actual_determinism.json')
    actual_first, actual_second = read('actual_run1/summary.json'), read('actual_run2/summary.json')
    mt5 = read('actual_mt5/result.json')
    tests = read('test_summary.json')
    integrity = read('integrity_baseline_comparison.json')
    inherited_failures = [case for group in ('part2', 'root')
                          for case in tests[group]['failures'].values()]
    failures_reproduced = bool(inherited_failures) and all(
        case['reproduced_on_baseline_copy'] for case in inherited_failures)
    regressions = read('regressions/summary.json')
    groups = {}
    for r in regressions:
        if r['group'] == 'audit':
            a = r['audit']
            groups['audit'] = {'run': a['tests_run'], 'failures': a['failures'], 'errors': a['errors'],
                               'integrity_errors': a['source_integrity']['integrity_errors']}
        else:
            groups[r['group']] = dict(Counter(c['status'] for c in r['cases']))
    result = {
        'stage': 'S0', 'original': original,
        'golden_determinism': comparison['equal'] and first['sha256'] == second['sha256'],
        'wire_identical': first['wire_sha256'] == second['wire_sha256'],
        'cases_each_run': first['cases'], 'exact_frames_checked': comparison['exact_frames_checked'],
        'three_way_equal': parity['equal'], 'nonempty': parity['nonempty'],
        'actual_tester_sample': {'status': mt5['validation'], 'runtime': 'ACTUAL_MT5_STRATEGY_TESTER',
                                'publications': actual_first['publications'],
                                'deterministic': actual_comparison['equal'] and
                                    actual_first['sha256'] == actual_second['sha256'],
                                'wire_identical': actual_first['wire_sha256'] == actual_second['wire_sha256'],
                                'unavailable_responses': len(actual_first['errors']),
                                'unavailable_identical': actual_first['errors'] == actual_second['errors'],
                                'first_unavailable': actual_first['errors'][:1]},
        'benchmark_recorded': bool(benchmarks['baseline']), 'regressions': groups,
        'inherited_failures_reproduced': failures_reproduced,
        'integrity_errors_identical_to_baseline': integrity['same_errors'],
        'golden_verifier_tests_passed': tests['staff_s0'] == {'PASSED': 11},
        'next_stage_started': False,
        'test_summary': tests,
        's0_complete': False,
        'remaining': [],
    }
    result['s0_complete'] = all((original['unchanged'], original['all_copied_files_unchanged'],
        original['part1_production_unchanged'],
        result['golden_determinism'], result['wire_identical'], result['three_way_equal'],
        all(parity['nonempty'].values()), result['benchmark_recorded'],
        mt5['validation'] == 'PASS', result['actual_tester_sample']['deterministic'],
        result['actual_tester_sample']['wire_identical'],
        result['actual_tester_sample']['unavailable_identical'], failures_reproduced,
        integrity['same_errors'], result['golden_verifier_tests_passed']))
    # Test failures are deliberately retained. A complete S0 is a measured
    # baseline, not a claim that the inherited application has no defects.
    result['g1_all_green'] = all(r['returncode'] == 0 for r in regressions)
    if not result['g1_all_green']:
        result['remaining'].append('기준본의 기존 테스트 실패/오류를 결과.md에 별도 기록; 모두 통과한 상태는 아님')
    (OUT / 'status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('stage', 's0_complete', 'g1_all_green',
        'original', 'golden_determinism', 'three_way_equal', 'actual_tester_sample',
        'inherited_failures_reproduced', 'benchmark_recorded')}, ensure_ascii=False, indent=2))
    return int(not result['s0_complete'])


if __name__ == '__main__':
    raise SystemExit(main())
