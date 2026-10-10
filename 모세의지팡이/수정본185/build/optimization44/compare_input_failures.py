"""Retry evidence is separate from diagnosis on the supplied input project."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from run_checks import ROOT, OUT, portable_log


def integrity(root, name):
    spec = importlib.util.spec_from_file_location(name, root / 'Part1/audit/source_integrity.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_sources()['integrity_errors']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('labels', nargs='*')
    args = parser.parse_args()
    original = args.original.resolve()
    results_path = OUT / 'input43_failure_comparison.json'
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    checks = json.loads((OUT / 'regression_results.json').read_text(encoding='utf-8'))
    for label, row in checks.items():
        if row['exit_code'] == 0 or args.labels and label not in args.labels:
            continue
        targets = row['targets'] if row['counts']['errors'] else row['failures']
        xml = OUT / ('input43_' + label + '.xml')
        proc = subprocess.run([sys.executable, '-m', 'pytest', *targets, '-q', '-p', 'no:cacheprovider',
                               '--tb=short', '--junitxml=' + str(xml)], cwd=original,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0'),
                              capture_output=True, text=True, timeout=180)
        (OUT / ('input43_' + label + '.log')).write_text(
            portable_log((proc.stdout + proc.stderr).replace(str(original), 'input43')), encoding='utf-8')
        counts = dict(passed=0, failed=0, errors=0, skipped=0)
        if xml.exists():
            xml.write_text(portable_log(xml.read_text(encoding='utf-8').replace(str(original), 'input43')), encoding='utf-8')
            for case in ET.parse(xml).iter('testcase'):
                status = ('failed' if case.find('failure') is not None else 'errors' if case.find('error') is not None
                          else 'skipped' if case.find('skipped') is not None else 'passed')
                counts[status] += 1
        results[label] = {'targets': targets, 'exit_code': proc.returncode, 'counts': counts,
                          'same_failure_counts': all(counts[k] == row['counts'][k] for k in ('failed', 'errors'))}
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label, counts, 'same=', results[label]['same_failure_counts'], flush=True)
    before = integrity(original, '_input43_integrity43')
    after_first = integrity(ROOT, '_revision44_integrity')
    after_retry = integrity(ROOT, '_revision44_integrity_retry') if after_first else after_first
    (OUT / 'integrity_comparison.json').write_text(json.dumps({
        'input43_errors': before, 'revision44_errors_first': after_first, 'revision44_errors_retry': after_retry,
        'same_preexisting_errors': before == after_first == after_retry}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
