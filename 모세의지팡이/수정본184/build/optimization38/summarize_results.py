"""Verify final-run evidence and summarize timings without changing production code."""
from pathlib import Path
import argparse
import csv
import hashlib
import io
import json
import statistics
import xml.etree.ElementTree as ET


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_summary(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    failures = {}
    skipped = 0
    for case in cases:
        key = case.get('classname', '') + '::' + case.get('name', '')
        failure = case.find('failure')
        if failure is None:
            failure = case.find('error')
        if failure is not None:
            failures[key] = {'type': failure.get('type', ''),
                             'message': failure.get('message', '')}
        skipped += case.find('skipped') is not None
    return {'total': len(cases), 'passed': len(cases) - len(failures) - skipped,
            'failed': len(failures), 'skipped': skipped, 'failures': failures}


def summarize(project):
    evidence = project / '검증결과/part2_optimization38'
    for mode, count in [('benchmark', 12), ('trace', 6)]:
        finished = read_json(evidence / (mode + '_finished.json'))
        if not finished.get('completed') or finished.get('jobs') != count:
            raise ValueError('Incomplete final executions: ' + mode)
    attempts = read_json(evidence / 'benchmark_status.json') + read_json(evidence / 'trace_status.json')
    jobs = {row['name']: row for row in attempts}
    if len(jobs) != 18 or any(row['returncode'] != 0 for row in jobs.values()):
        raise ValueError('A final execution failed or is missing')
    summaries = {name: row['summary'] for name, row in jobs.items()}
    if any(s['bundles'] != 1378 or s['errors'] for s in summaries.values()):
        raise ValueError('Input count or engine error mismatch')
    for version in ('before', 'after'):
        hashes = {row['summary']['production_code_hash'] for row in jobs.values() if row['version'] == version}
        if len(hashes) != 1:
            raise ValueError('Production files changed during executions: ' + version)
    config_hashes = {s['config_hash'] for s in summaries.values()}
    if len(config_hashes) != 1:
        raise ValueError('Runtime configuration mismatch')
    result = {'source': 'final_replay only; discarded-candidate runs excluded',
              'executions': 18, 'timing_executions': 12, 'traced_executions': 6,
              'all_final_attempts_succeeded_first_try': len(attempts) == 18,
              'config_hash': next(iter(config_hashes)),
              'production_code_hashes': {v: next(row['summary']['production_code_hash'] for row in jobs.values() if row['version'] == v)
                                         for v in ('before', 'after')},
              'strategies': {},
              'limits': next(iter(summaries.values()))['limits'],
              'timing_note': 'Three alternating-order before/after pairs per strategy; profiler and trace off. Linux CPU affinity 0. The separate trace validation used CPU 2 concurrently for part of the run; this was not an otherwise idle or dedicated machine. Timings are observations, not guaranteed speedups.'}
    for strategy in ('SPECIAL1', 'ALL'):
        selected = {n: r for n, r in jobs.items() if r['strategy'] == strategy}
        csv_bytes = []
        for name, row in selected.items():
            directory = name if row['attempt'] == 1 else name + '_retry'
            content = (evidence / 'final_replay' / directory / 'alerts.csv').read_bytes()
            if hashlib.sha256(content).hexdigest() != row['summary']['csv_sha256']:
                raise ValueError('CSV digest mismatch: ' + name)
            csv_bytes.append(content)
        if len(csv_bytes) != 9 or any(b != csv_bytes[0] for b in csv_bytes):
            raise ValueError('CSV contents changed: ' + strategy)
        traces = [row['summary'] for row in selected.values() if row['trace']]
        for field in ('trace_count', 'trace_sha256', 'trace_categories'):
            if any(t[field] != traces[0][field] for t in traces):
                raise ValueError('Emitted-output trace differs: ' + strategy + ' / ' + field)
        times = {v: [row['summary']['worker_replay_seconds'] for row in selected.values()
                     if row['version'] == v and not row['trace']] for v in ('before', 'after')}
        if any(len(values) != 3 for values in times.values()):
            raise ValueError('Expected three timing repetitions')
        b, a = [statistics.median(times[v]) for v in ('before', 'after')]
        rows = list(csv.DictReader(io.StringIO(csv_bytes[0].decode('utf-8-sig'))))
        result['strategies'][strategy] = {
            'csv_copies_compared': len(csv_bytes), 'csv_bytes_equal': True, 'alert_rows': len(rows),
            'csv_sha256': hashlib.sha256(csv_bytes[0]).hexdigest(),
            'bundles_per_execution': 1378, 'trace_count': traces[0]['trace_count'],
            'trace_sha256': traces[0]['trace_sha256'], 'trace_categories': traces[0]['trace_categories'],
            'before_replay_after_replay_after_memory_live_traces_equal': True,
            'timings_seconds': times, 'median_before_seconds': b, 'median_after_seconds': a,
            'reduction_percent': 100 * (b - a) / b,
            'memory_peak_bytes': {v: [row['summary']['max_memory_bytes'] for row in selected.values()
                                     if row['version'] == v and not row['trace']] for v in ('before', 'after')}}
    before_tests = test_summary(evidence / 'current_regressions/before_attempt2/results.xml')
    after_tests = test_summary(evidence / 'final_regressions/after_attempt2/results.xml')
    new_tests = test_summary(evidence / 'final_regressions/new_attempt1/results.xml')
    regressions = {'before': before_tests, 'after': after_tests, 'new_tests': new_tests,
                   'new_failures': sorted(set(after_tests['failures']) - set(before_tests['failures'])),
                   'resolved_failures': sorted(set(before_tests['failures']) - set(after_tests['failures'])),
                   'note': 'Legacy failures are retained, not repaired or counted as passing. Unsupported retired live_replay groups failed collection on both versions and are recorded separately.'}
    if regressions['new_failures'] or new_tests['failed'] or before_tests['total'] != after_tests['total']:
        raise ValueError('Regression validation did not pass')
    (evidence / 'final_comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (evidence / 'final_regression_comparison.json').write_text(json.dumps(regressions, ensure_ascii=False, indent=2), encoding='utf-8')
    measurements = read_json(evidence / 'measurements/measurements.json')
    measurements['steps'] = {k: v for k, v in measurements['steps'].items() if k != 'delta_decode'}
    measurements['scope'] += '; retained operations only; excluded Delta results stored separately'
    (evidence / 'measurements/retained_measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path, nargs='?', default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = summarize(args.project.resolve())
    print(json.dumps({k: {n: v[n] for n in ('alert_rows', 'trace_count', 'median_before_seconds', 'median_after_seconds', 'reduction_percent')}
                      for k, v in result['strategies'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
