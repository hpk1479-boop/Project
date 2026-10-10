"""Generate candidate responses once; compare against unchanged S0 SQLite files."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from staff_s1_evidence import ROOT, OUT, S0, frozen_guard


def main():
    frozen_guard()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    capture = next((S0 / 'actual_mt5').glob('capture_*'))
    commands = [
        ('synthetic', ['record', '--part1', str(ROOT / 'Part1'), '--out', str(OUT / 'synthetic')], (0,)),
        ('synthetic_compare', ['compare', str(S0 / 'golden_run1/golden.sqlite'),
            str(OUT / 'synthetic/golden.sqlite'), '--out', str(OUT / 'synthetic_compare.json')], (0,)),
        ('actual', ['actual', '--part1', str(ROOT / 'Part1'), '--capture', str(capture),
            '--out', str(OUT / 'actual')], (1,)),  # S0's 239 D1 unavailable responses are expected.
        ('actual_compare', ['compare', str(S0 / 'actual_run1/golden.sqlite'),
            str(OUT / 'actual/golden.sqlite'), '--out', str(OUT / 'actual_compare.json')], (0,)),
    ]
    results = []
    for name, command, expected_codes in commands:
        print('START G2 ' + name, flush=True)
        with (OUT / (name + '.log')).open('x', encoding='utf-8') as log:
            done = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'staff_golden', *command],
                cwd=ROOT / 'Part2', env=env, stdout=log, stderr=subprocess.STDOUT)
        results.append({'name': name, 'returncode': done.returncode})
        (OUT / 'g2_execution.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print('END G2 ' + name + ' rc=' + str(done.returncode), flush=True)
        if done.returncode not in expected_codes:
            return 1
    frozen_guard()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
