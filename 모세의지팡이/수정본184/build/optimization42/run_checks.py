"""Run selected Fact-adjacent regressions; retry failed nodes once, no code edits."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '검증결과/Fact최적화42'
GROUPS = {
    'event_e1': ['tests/test_event_e1.py'],
    'event_e2': ['tests/test_event_e2_boundaries.py', 'tests/test_event_e2_composition.py',
                 'tests/test_event_e2_domains.py', 'tests/test_event_e2_oz_pilot.py',
                 'tests/test_event_e2_startup.py'],
    'event_e3': ['tests/test_event_e3.py'],
    'event_perf1': ['tests/test_event_perf1.py'],
    'numpy_processors': ['tests/test_numpy_processors.py'],
    'engine_optimization': ['tests/test_engine_optimization.py'],
    'part2_runner': ['tests/test_part2_event_runner.py'],
    'fact42': ['tests/test_fact_optimization42.py'],
}


def portable_log(text):
    # Keep diagnostics usable after moving the project; no host paths are saved.
    replacements = ((str(ROOT), '.'), (str(ROOT.parent), 'workspace'),
                    (tempfile.gettempdir(), 'test-temp'), (sys.base_prefix, 'python-runtime'))
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def run(label, targets):
    cmd = [sys.executable, '-m', 'pytest', *targets, '-q', '-p', 'no:cacheprovider',
           '--tb=short', '--junitxml=' + (OUT / (label + '.xml')).relative_to(ROOT).as_posix()]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0')
    result = subprocess.run(cmd, cwd=ROOT, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    log = portable_log(result.stdout)
    (OUT / (label + '.log')).write_text(log, encoding='utf-8')
    xml_path = OUT / (label + '.xml')
    failures, counts = [], dict(passed=0, failed=0, skipped=0, errors=0)
    if xml_path.exists():
        xml_path.write_text(portable_log(xml_path.read_text(encoding='utf-8')), encoding='utf-8')
        for case in ET.parse(xml_path).iter('testcase'):
            status = 'passed'
            if case.find('failure') is not None: status = 'failed'
            elif case.find('error') is not None: status = 'errors'
            elif case.find('skipped') is not None: status = 'skipped'
            counts[status] += 1
            if status in ('failed', 'errors'):
                classname = case.get('classname', '')
                if classname:
                    filename = classname.replace('.', '/') + '.py'
                    failures.append(filename + '::' + case.get('name', ''))
                else:
                    failures.extend(targets)  # Import/collection error: rerun the module.
    return dict(exit_code=result.returncode, counts=counts, failures=failures,
                log=(OUT / (label + '.log')).relative_to(ROOT).as_posix())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'regression_results.json'
    results = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for label in sys.argv[1:] or list(GROUPS):
        row = run(label, GROUPS[label])
        if row['exit_code']:
            row['retry'] = run(label + '_retry', row['failures'] or GROUPS[label])
        results[label] = row
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label, row['counts'], 'retry=' + str(row.get('retry', {}).get('counts')), flush=True)


if __name__ == '__main__':
    main()
