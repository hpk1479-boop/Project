"""Diagnose selected failures against an explicitly supplied original project."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '검증결과/Fact최적화42'


def integrity(root, name):
    spec = importlib.util.spec_from_file_location(name, root / 'Part1/audit/source_integrity.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_sources()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    base = args.original.resolve()
    if not (base / 'Part1/program/event_engine/facts.py').is_file():
        parser.error('original project does not contain event_engine/facts.py')
    from run_checks import portable_log
    checks = json.loads((OUT / 'regression_results.json').read_text(encoding='utf-8'))
    results = {}
    for label, result in checks.items():
        if not result['exit_code']:
            continue
        targets = result['failures'] if not result['counts']['errors'] else ['tests/test_part2_event_runner.py']
        xml = OUT / ('original_' + label + '.xml')
        cmd = [sys.executable, '-m', 'pytest', *targets, '-q', '-p', 'no:cacheprovider',
               '--tb=short', '--junitxml=' + str(xml)]
        response = subprocess.run(cmd, cwd=base, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                                  PYTHONHASHSEED='0'), text=True, capture_output=True, timeout=180)
        log = portable_log((response.stdout + response.stderr).replace(str(base), 'original'))
        (OUT / ('original_' + label + '.log')).write_text(log, encoding='utf-8')
        counts = dict(passed=0, failed=0, errors=0, skipped=0)
        if xml.exists():
            xml.write_text(portable_log(xml.read_text(encoding='utf-8').replace(str(base), 'original')), encoding='utf-8')
            for case in ET.parse(xml).iter('testcase'):
                status = ('failed' if case.find('failure') is not None else 'errors' if case.find('error') is not None
                          else 'skipped' if case.find('skipped') is not None else 'passed')
                counts[status] += 1
        results[label] = dict(targets=targets, exit_code=response.returncode, counts=counts,
            same_failed_count_as_modified=counts['failed'] == result['counts']['failed']
                and counts['errors'] == result['counts']['errors'])
    (OUT / 'original_failure_comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    original, modified = integrity(base, 'original_integrity42'), integrity(ROOT, 'modified_integrity42')
    (OUT / 'integrity_retry_comparison.json').write_text(json.dumps({
        'original_errors': original['integrity_errors'], 'revision42_errors': modified['integrity_errors'],
        'same_preexisting_errors': original['integrity_errors'] == modified['integrity_errors']},
        ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
