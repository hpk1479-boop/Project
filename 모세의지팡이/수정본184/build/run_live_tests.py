"""Run fresh SPECIAL1 tests in disjoint groups and preserve every invocation.

No original tests/reports are invoked. Run from any directory:
    python -B build/run_live_tests.py
"""
from __future__ import annotations
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'A': ['tests/live_parity/test_source_and_standalone.py'],
    'B': ['tests/live_parity/test_observation_replay.py', '-k', 'nonzero or close_is_only or eof'],
    'C': ['tests/live_parity/test_observation_replay.py', '-k',
          'retry or duplicate_receipt or final_session or minute_inclusive or checkpoint'],
    'D': ['tests/live_parity/test_observation_replay.py', '-k',
          'not (nonzero or close_is_only or eof or retry or duplicate_receipt or final_session or minute_inclusive or checkpoint)'],
}


def main() -> int:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out = ROOT / 'reports/live_parity/reruns' / stamp
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    cases, seen, runs = [], set(), []
    failed = False
    for group, args in GROUPS.items():
        xml = out / f'group_{group}.xml'
        command = [sys.executable, '-B', '-m', 'pytest', *args, '-q', '--tb=short',
                   '--junitxml=' + str(xml)]
        completed = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / f'group_{group}.txt').write_text(completed.stdout, encoding='utf8')
        print(completed.stdout, end='')
        runs.append({'group': group, 'command': command, 'returncode': completed.returncode})
        failed |= completed.returncode != 0
        if not xml.exists():
            failed = True
            continue
        for case in ET.parse(xml).getroot().iter('testcase'):
            key = case.get('classname', '') + '::' + case.get('name', '')
            if key in seen:
                raise RuntimeError('Overlapping test groups: ' + key)
            seen.add(key)
            skipped, failure, error = case.find('skipped'), case.find('failure'), case.find('error')
            status = 'SKIPPED' if skipped is not None else 'FAILED' if failure is not None or error is not None else 'PASSED'
            item = {'id': key, 'group': group, 'status': status}
            if skipped is not None:
                item['reason'] = skipped.get('message') or skipped.text
            cases.append(item)
    summary = {'actual_live_parity': 'NOT_VERIFIED', 'execution': 'disjoint groups',
               'runs': runs, 'total': len(cases), 'cases': cases,
               **{name.lower(): sum(c['status'] == name for c in cases)
                  for name in ('PASSED', 'FAILED', 'SKIPPED')}}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print('Results:', out)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
