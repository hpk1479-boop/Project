"""Record existing test outcomes without changing production code or hiding failures."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과' / 'staff_s0' / 'regressions'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    groups = {
        'audit': ['Part1/audit/run_tests.py'],
        'watch_ma': ['-m', 'pytest', 'Part1/watch_ma_validation'],
        'part2': ['-m', 'pytest', 'Part2/validation_suite'],
        'root': ['-m', 'pytest', 'tests'],
    }
    reports = []
    for name, args in groups.items():
        command = [sys.executable, '-B', *args]
        xml = OUT / (name + '.xml')
        if args[0] == '-m':
            command += ['-q', '--tb=short',
                        '-p', 'no:cacheprovider', '--continue-on-collection-errors',
                        '--basetemp=' + str(OUT / ('tmp_' + name)), '--junitxml=' + str(xml)]
        print('START ' + name, flush=True)
        started = time.perf_counter()
        with (OUT / (name + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        report = {'group': name, 'returncode': result.returncode,
                  'wall_s': time.perf_counter() - started, 'command': command}
        if xml.exists():
            cases = list(ET.parse(xml).getroot().iter('testcase'))
            report['cases'] = [{'id': c.get('classname', '') + '::' + c.get('name', ''),
                                'status': 'FAILED' if c.find('failure') is not None else
                                'ERROR' if c.find('error') is not None else
                                'SKIPPED' if c.find('skipped') is not None else 'PASSED'} for c in cases]
        elif name == 'audit':
            audit = json.loads((ROOT / 'Part1/audit/results/remediation_test_report.json').read_text('utf-8'))
            report['audit'] = audit
        reports.append(report)
        (OUT / 'summary.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
        print('END ' + name + ' rc=' + str(result.returncode), flush=True)
    return int(any(r['returncode'] for r in reports))


if __name__ == '__main__':
    raise SystemExit(main())
