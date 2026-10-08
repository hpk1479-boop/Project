"""Summarize explicitly selected paired runs, without treating old signals as an oracle."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '검증결과/revision151/performance'


def table(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return [{key:value for key,value in row.items() if key != 'run_id'}
                for row in csv.DictReader(stream)]


def summarize(pairs, name):
    output = EVIDENCE / (name+'.json')
    if output.exists():
        raise ValueError('Use a fresh summary name')
    observations, grouped = [], {}
    for case, label in pairs:
        paths = [EVIDENCE/'cases'/(prefix+case+'_'+label)
                 for prefix in ('baseline_source_', '수정본151_')]
        before, after = [json.loads((path/'diagnosis.json').read_text('utf-8')) for path in paths]
        source_matches = all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest
                             for path,digest in after['source_hashes'].items())
        if not source_matches:
            raise ValueError('This pair did not measure the current product source')
        same_input = all(before[key] == after[key] for key in ('scenario', 'period', 'capture_path'))
        comparisons = {}
        for filename in ('alerts.csv', 'virtual_trades.csv', 'virtual_summary.csv'):
            rows = [table(path/filename) for path in paths]
            comparisons[filename] = {'equal_except_run_id':rows[0] == rows[1],
                                     'rows':[len(value) for value in rows]}
        row = {'case':case, 'label':label, 'paths':[path.relative_to(ROOT).as_posix() for path in paths],
               'same_input':same_input, 'current_source':source_matches, 'comparisons':comparisons,
               'bundles':[value['replay']['bundles'] for value in (before,after)],
               'alerts':[value['replay']['alerts'] for value in (before,after)]}
        for key in ('replay_wall_seconds','replay_cpu_seconds','virtual_wall_seconds','virtual_cpu_seconds'):
            row[key] = [before[key], after[key]]
        observations.append(row)
        grouped.setdefault(case, []).append(row)
    medians = {}
    for case, rows in grouped.items():
        values = {}
        for key in ('replay_wall_seconds','replay_cpu_seconds','virtual_wall_seconds','virtual_cpu_seconds'):
            old, new = [median(row[key][i] for row in rows) for i in (0,1)]
            values[key] = {'before':old, 'after':new, 'decrease_percent':100*(old-new)/old if old else None}
        medians[case] = {'pairs':len(rows), **values}
    summary = {'observations':observations, 'medians':medians,
               'note':'Actual recorded two-day, single-process sample with three-day warmup. '
                      'CSV agreement is a change diagnostic; expected emission and non-emission tests establish logic. '
                      'No inference of a 70-month or 14-core speedup is made.'}
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'medians':medians, 'semantic_csv_equal':all(
        row['same_input'] and all(v['equal_except_run_id'] for v in row['comparisons'].values())
        for row in observations)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pair', nargs=2, action='append', required=True, metavar=('CASE','LABEL'))
    parser.add_argument('--name', default='final_paired')
    args = parser.parse_args()
    if not args.name.replace('_','').isalnum():
        raise ValueError('A simple summary name is required')
    if any(not value.replace('_','').isalnum() for pair in args.pair for value in pair):
        raise ValueError('Simple case names and labels are required')
    summarize(args.pair, args.name)
