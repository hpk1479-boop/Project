"""AB/BA microbenchmarks, not an end-to-end replay speed claim."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import platform
import statistics
import sys
import time

from probe43_support import ROOT, synthetic_snapshot, original_measure_consumer, original_market_functions
import numpy as np
import pandas as pd
from event_backtest.instrumentation import measure_consumer
from event_engine import market
from oz_engine.runtime import ready

OUT = ROOT / '검증결과/계측_유효성최적화43'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    old_wrap = original_measure_consumer(args.original)
    old_market, old_ready = original_market_functions(args.original)
    snap = synthetic_snapshot()
    b = SimpleNamespace(_frame_cache={}, feeds={('XAUUSD+', '1m'): snap},
                        health={}, source_time=1000, observed={('XAUUSD+', '1m'): 1000},
                        snapshot=lambda *a: snap,
                        fact=lambda *a: {'wonbi_upper': snap.values[:, market.COLUMNS['wonbi_upper']]})
    writer = SimpleNamespace(note_emission=lambda *a: None)

    def wrapper(factory, arity, count, emit=False):
        fn = (lambda e, b, s, output: output(1)) if emit else (lambda *a: None)
        measured = factory(fn, 'probe', writer, {})
        arguments = (None, None, None) if arity == 3 else (None, None, None, lambda value: None)
        for _ in range(count):
            measured(*arguments)

    def selection(module, required, count):
        b._frame_cache = {}
        module.select(b, 'XAUUSD+', '1m', required)
        for _ in range(count):
            module.select(b, 'XAUUSD+', '1m', required)

    def flow(module, check, count):
        for _ in range(count):
            b._frame_cache = {}
            check(b, 'XAUUSD+', '1m')
            module.select(b, 'XAUUSD+', '1m')
            module.select(b, 'XAUUSD+', '1m', ('WONBI',))
            for _ in range(8):
                check(b, 'XAUUSD+', '1m')
                module.select(b, 'XAUUSD+', '1m', ('EMA',))
                module.select(b, 'XAUUSD+', '1m', ('WONBI',))

    jobs = {
        'processor_3args_200000': (lambda: wrapper(old_wrap, 3, 200000), lambda: wrapper(measure_consumer, 3, 200000)),
        'consumer_4args_no_output_150000': (lambda: wrapper(old_wrap, 4, 150000), lambda: wrapper(measure_consumer, 4, 150000)),
        'consumer_4args_one_output_150000': (lambda: wrapper(old_wrap, 4, 150000, True), lambda: wrapper(measure_consumer, 4, 150000, True)),
        'cached_ohlc_only_select_20000': (lambda: selection(old_market, (), 20000), lambda: selection(market, (), 20000)),
        'cached_ema_select_20000': (lambda: selection(old_market, ('EMA',), 20000), lambda: selection(market, ('EMA',), 20000)),
        'mixed_300_publications_8_reuses': (lambda: flow(old_market, old_ready, 300), lambda: flow(market, ready, 300)),
    }
    timings = {}
    for label, pair in jobs.items():
        for fn in pair:
            fn()
        samples = [[], []]
        for repeat in range(7):
            for i in ([0, 1] if repeat % 2 == 0 else [1, 0]):
                began = time.perf_counter()
                pair[i]()
                samples[i].append(time.perf_counter() - began)
        before, after = map(statistics.median, samples)
        timings[label] = {'input42_seconds': before, 'revision43_seconds': after,
                          'time_reduction_pct': (1 - after / before) * 100,
                          'samples_input42': samples[0], 'samples_revision43': samples[1]}
        print(label, f'{before:.6f} -> {after:.6f} ({(1-after/before)*100:.2f}%)', flush=True)
    counts = {}
    for label, module, check in [('input42', old_market, old_ready), ('revision43', market, ready)]:
        calls = []
        def finite(values):
            calls.append(values.shape)
            return np.isfinite(values)
        previous = module.np
        module.np = SimpleNamespace(isfinite=finite)
        if label == 'input42':
            check.__globals__['np'] = module.np
        try:
            flow(module, check, 1)
        finally:
            module.np = previous
            if label == 'input42':
                check.__globals__['np'] = np
        counts[label] = {'full_ohlc_scans': calls.count((650, 4)),
                         'requested_row_scans': len(calls) - calls.count((650, 4)),
                         'total_scans': len(calls)}
    sources = {}
    for relative in ['Part2/event_backtest/runner.py', 'Part1/program/event_engine/market.py',
                     'Part1/program/oz_engine/runtime.py']:
        sources[relative] = {label: hashlib.sha256((base / relative).read_bytes()).hexdigest()
                             for label, base in [('input42', args.original), ('revision43', ROOT)]}
    result = {'scope': 'synthetic microbenchmarks only; no SQL/disk/process startup/real recording',
              'environment': {'python': platform.python_version(), 'platform': platform.system(),
                              'numpy': np.__version__, 'pandas': pd.__version__},
              'rows': 650, 'rounds': 7, 'order': 'alternating AB/BA after warm-up',
              'measurements': timings, 'scan_counts': counts, 'source_sha256': sources}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('scan_counts', counts)


if __name__ == '__main__':
    main()
