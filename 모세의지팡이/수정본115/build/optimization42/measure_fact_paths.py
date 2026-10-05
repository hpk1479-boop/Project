"""Local Fact-path timings, not an end-to-end recorded-market benchmark.

Run from the revision42 project with --original pointing to revision41.
All saved paths are project-relative. No production source is changed.
"""
from pathlib import Path
import argparse
import gc
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'Part1/program'))
import numpy as np
import pandas as pd
import event_engine.facts as current
from event_engine.model import FeedSnapshot
from staff_schema import PIPE_VALUE_COLUMNS

KEY = ('MEASURE42', '1m')
COLS = {v: i for i, v in enumerate(PIPE_VALUE_COLUMNS)}
NAMES = ('ATR14_GENERAL', 'WONBI_BANDS')


def snap(seq=1, *, change=0., shift=0):
    times = np.arange(650, dtype='<i8') * 60 + 1756684800 + shift
    values = np.full((650, len(COLS)), 100., dtype='<f8')
    values[:, COLS['open']] = 100 + np.arange(650) * .01
    values[:, COLS['high']] = values[:, COLS['open']] + 2
    values[:, COLS['low']] = values[:, COLS['open']] - 2
    values[:, COLS['close']] = values[:, COLS['open']] + .5
    values[:, COLS['wonbi_upper']] = values[:, COLS['open']] + 3
    values[:, COLS['wonbi_lower']] = values[:, COLS['open']] - 3
    values[-1, COLS['high']] += change
    return FeedSnapshot(times, np.ones(650, dtype='<i8'), values, seq, 'measure42:1')


def seeded(module, snapshot):
    cache = module.EventFacts()
    cache.invalidate({KEY: snapshot})
    for name in NAMES:
        cache.get(*KEY, snapshot, name, 'measurement42')
    return cache


def jobs(module, first, second, updates):
    reuse = seeded(module, first)
    hits = seeded(module, first)
    change_a, change_b = {KEY: first}, {KEY: second}
    def invalidation():
        for i in range(1200):
            reuse.invalidate(change_a if i % 2 else change_b)
    def cached_read():
        for _ in range(20000):
            hits.get(*KEY, first, 'ATR14_GENERAL', 'measurement42')
    def dependencies():
        for _ in range(20000):
            module.dependencies('ATR14_GENERAL')
    def columns():
        for _ in range(50000):
            module.array(first, 'wonbi_upper')
    def mixed():
        cache = module.EventFacts()
        for _ in range(10):
            for snapshot in updates:
                cache.invalidate({KEY: snapshot})
                for name in NAMES:
                    cache.get(*KEY, snapshot, name, 'measurement42')
                cache.get(*KEY, snapshot, 'ATR14_GENERAL', 'measurement42')
    return {'unchanged_invalidation_1200': invalidation,
            'cached_atr_reads_20000': cached_read,
            'dependency_queries_20000': dependencies,
            'column_views_50000': columns,
            'mixed_updates_and_reads_240': mixed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error('at least 3 repetitions are required')
    original_path = args.original / 'Part1/program/event_engine/facts.py'
    if not original_path.is_file():
        parser.error('original project does not contain event_engine/facts.py')
    spec = importlib.util.spec_from_file_location('event_engine._original_facts42', original_path)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    first, second = snap(), snap(2)
    updates = [snap(i + 1, change=(0., 0., 0.5, 2.)[i % 4], shift=(i // 8) * 60)
               for i in range(24)]
    modules = {'original41': original, 'revision42': current}
    tasks = {label: jobs(mod, first, second, updates) for label, mod in modules.items()}
    counts = {}
    equal = np.array_equal
    for label, mod in modules.items():
        cache = seeded(mod, first)
        counter = [0]
        def counted(*a, **kw):
            counter[0] += 1
            return equal(*a, **kw)
        try:
            np.array_equal = counted
            cache.invalidate({KEY: second})
        finally:
            np.array_equal = equal
        counts[label] = counter[0]
    # No profiler/counters are enabled during timing. Alternate A/B order.
    measurements = {}
    for name in tasks['original41']:
        for label in modules:
            tasks[label][name]()
        times = {label: [] for label in modules}
        for rep in range(args.repeats):
            order = tuple(modules) if rep % 2 == 0 else tuple(reversed(modules))
            for label in order:
                gc.collect()
                start = perf_counter()
                tasks[label][name]()
                times[label].append(perf_counter() - start)
        medians = {label: statistics.median(values) for label, values in times.items()}
        measurements[name] = {'samples_seconds': times, 'median_seconds': medians,
            'median_reduction_percent': 100 * (1 - medians['revision42'] / medians['original41'])}
        print(name, {k: round(v, 6) for k, v in medians.items()},
              round(measurements[name]['median_reduction_percent'], 2), flush=True)
    report = {'scope': 'Synthetic local Fact paths only; not complete backtest speed or actual MT5 replay.',
        'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__,
                        'system': platform.system(), 'processor': platform.processor(),
                        'hash_seed': os.environ.get('PYTHONHASHSEED', 'random')},
        'rows': 650, 'repeats': args.repeats, 'timing_order': 'AB, BA, AB, BA, AB (alternating)',
        'source_sha256': {'original41': hashlib.sha256(original_path.read_bytes()).hexdigest(),
                         'revision42': hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest()},
        'unchanged_snapshot_comparisons': counts, 'measurements': measurements}
    out = ROOT / '검증결과/Fact최적화42/measurements.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
