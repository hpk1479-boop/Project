"""Isolated STAFF-only timings; exclude generation, hashing, disk and startup."""
import gc
import json
import logging
import platform
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

from part1_host import capture, runtime
from .record import timed, metric_summary
from .scenario import INDICATORS, MA_CASES, START, SYMBOL, market


def benchmark(part1, out, rounds=3):
    data = market()
    payload = data.payload('1m', START + 239)
    reports = []
    old_logging = logging.root.manager.disable
    try:
        for round_index in range(rounds):
            with runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START + 239,
                                      specials=['SPECIAL7'], part1_root=Path(part1),
                                      log_level=logging.CRITICAL) as rt:
                logging.disable(logging.CRITICAL)
                wires = [capture.pack_wire(SYMBOL, '1m', *payload, snapshot=i + 1) for i in range(210)]
                for wire in wires[:10]:
                    rt.publish(wire)
                requests = [{'symbol': SYMBOL, 'timeframes': ['1m'], 'indicators': list(names)}
                            for names in ((), ('PRICE', 'RSI', 'STO', 'DI'), INDICATORS,
                                          INDICATORS + MA_CASES[-1])]
                for request in requests:
                    assert 'error' not in rt.staff_data_request(request)
                gc.collect()
                metrics = {}
                for wire in wires[10:]:
                    timed(metrics, 'parser_650_rows', rt.publish, wire)
                for index, request in enumerate(requests):
                    for _ in range(50):
                        reply = timed(metrics, f'request_{index}', rt.staff_data_request, request)
                        assert 'error' not in reply
                reports.append(metric_summary(metrics))
                print(f'benchmark {round_index + 1}/{rounds}', flush=True)
        keys = reports[0]
        result = {'scope': 'STAFF parser and handle only; offline transport, no OS pipe/ZMQ latency',
                  'rounds': reports, 'idle_run_required': True,
                  'request_groups': [[], ['PRICE', 'RSI', 'STO', 'DI'], list(INDICATORS),
                                     list(INDICATORS + MA_CASES[-1])],
                  'baseline': {key: {metric: statistics.median(r[key][metric] for r in reports)
                                     for metric in ('wall_s', 'cpu_s', 'p50_ms', 'p95_ms')}
                               for key in keys},
                  'source_sha256': rt.source_hashes,
                  'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                                  'pandas': pd.__version__, 'platform': platform.platform()}}
        Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    finally:
        logging.disable(old_logging)
