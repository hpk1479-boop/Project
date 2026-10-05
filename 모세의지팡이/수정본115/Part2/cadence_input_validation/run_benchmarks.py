"""Reproduce sequential legacy/current measurements after equivalence tests.

The original erroneous close guard cannot run a close backtest. Both arms here
use the corrected guard and differ ONLY in the original versus optimized input
supplier. Original SPECIAL requirements, worker isolation, cadence and strategy
source remain unchanged. Existing disk caches are disabled in BOTH arms.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('cadence_input_validation/local_measurements'))
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--skip-tests', action='store_true', help='Use only after the equivalence tests have already passed')
    args = p.parse_args()
    if args.rounds < 1:
        p.error('--rounds must be positive')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env.pop('PYTHONPATH', None)
    if not args.skip_tests:
        subprocess.run([sys.executable, '-m', 'pytest', 'cadence_input_validation', 'conditional_validation', '-q'],
                       cwd=root, env=env, check=True)
    def run(tag, argv):
        with (output/(tag+'.log')).open('w', encoding='utf-8') as log:
            subprocess.run([sys.executable, *argv, '--output', str(output/(tag+'.json'))],
                           cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        print(tag, flush=True)
    for number in range(args.rounds):
        for plugin in ('WATCH_OZ', 'WATCH_BAR'):
            for arm in (('legacy', 'current') if number % 2 == 0 else ('current', 'legacy')):
                run(f'{plugin}_50000_r{number}_{arm}', ['cadence_input_validation/benchmark.py', '--plugin', plugin,
                                                     '--reader', arm, '--rows', '50000'])
    for plugin in ('WATCH_OZ',):
        for arm in ('legacy', 'current'):
            run(f'{plugin}_50000_timings_{arm}', ['cadence_input_validation/benchmark.py', '--plugin', plugin,
                                                 '--reader', arm, '--rows', '50000', '--timings'])
            run(f'{plugin}_5000_profile_{arm}', ['cadence_input_validation/benchmark.py', '--plugin', plugin,
                                               '--reader', arm, '--rows', '5000', '--profile'])
    for arm in ('legacy', 'current'):
        run(f'WATCH_BAR_200000_{arm}', ['cadence_input_validation/benchmark.py', '--plugin', 'WATCH_BAR',
                                       '--reader', arm, '--rows', '200000'])
    archives = [p for p in (root/'generic_cache/raw').iterdir() if (p/'manifest.json').exists()]
    if len(archives) != 1:
        raise RuntimeError('Expected the single supplied native archive; select --archive explicitly with benchmark_reader.py otherwise.')
    for arm in ('legacy', 'current'):
        run('full_reader_'+arm, ['cadence_input_validation/benchmark_reader.py', '--reader', arm, '--archive', str(archives[0])])
    subprocess.run([sys.executable, 'cadence_input_validation/aggregate_results.py', str(output)], cwd=root, env=env, check=True)

if __name__ == '__main__':
    main()
