"""Evaluate readinto candidates only; settings.file_hash is never edited by this tool."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Part2'))
from event_backtest.settings import file_hash
OUT = ROOT / '검증결과/가상진입최적화44'


def readinto_hash(path, buffer_size):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        buffer = bytearray(buffer_size)
        view = memoryview(buffer)
        while (size := stream.readinto(buffer)):
            h.update(view[:size])
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=7)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error('At least three samples are required.')
    report = {'python': platform.python_version(), 'platform': platform.system(),
              'scope': 'Warm filesystem cache, local synthetic files, not real disk throughput.',
              'settings_modified_by_tool': False, 'samples': args.samples, 'cases': []}
    functions = [('current_4MiB_read', file_hash),
                 ('attempt1_4MiB_readinto', lambda p: readinto_hash(p, 4 * 1024 * 1024)),
                 ('attempt2_256KiB_readinto', lambda p: readinto_hash(p, 256 * 1024))]
    sizes = [(0, 250), (4096, 250), (256 * 1024 + 17, 100),
             (4 * 1024 * 1024 + 17, 20), (64 * 1024 * 1024 + 17, 2),
             (256 * 1024 * 1024 + 17, 1)]
    block = bytes(range(256)) * 4096
    with tempfile.TemporaryDirectory(prefix='hash44-') as folder:
        for size, iterations in sizes:
            path = Path(folder) / 'synthetic.bin'
            reference = hashlib.sha256()
            with path.open('wb') as stream:
                remaining = size
                while remaining:
                    part = block[:min(remaining, len(block))]
                    stream.write(part)
                    reference.update(part)
                    remaining -= len(part)
            expected = reference.hexdigest()
            samples = {name: [] for name, _ in functions}
            for name, function in functions:
                assert function(path) == expected, name
            for index in range(args.samples):
                # Rotate the first candidate and reverse alternately to reduce order bias.
                order = functions[index % 3:] + functions[:index % 3]
                if index % 2:
                    order = list(reversed(order))
                for name, function in order:
                    began = time.perf_counter()
                    for _ in range(iterations):
                        actual = function(path)
                    elapsed = time.perf_counter() - began
                    assert actual == expected
                    samples[name].append(elapsed / iterations)
            medians = {name: statistics.median(values) for name, values in samples.items()}
            baseline = medians[functions[0][0]]
            row = dict(bytes=size, iterations=iterations, sha256=expected,
                       seconds_per_file=samples, median_seconds_per_file=medians,
                       reduction_percent={name: (1 - value / baseline) * 100
                                          for name, value in medians.items()})
            report['cases'].append(row)
            print(size, row['reduction_percent'], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'hash_measurements.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
