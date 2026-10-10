"""Freeze original STAFF responses; never bypass its parser or request handler."""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd

from part1_host import capture, runtime
from part1_host.synthetic import TF_SECONDS, TIMEFRAMES
from .contracts import Recorder
from .scenario import (INDICATORS, MA_CASES, START, SYMBOL, WINDOW,
                       indicator_combinations, market)


def timed(metrics, key, fn, *args):
    wall, cpu = time.perf_counter(), time.process_time()
    value = fn(*args)
    metrics.setdefault(key, []).append((time.perf_counter() - wall, time.process_time() - cpu))
    return value


def metric_summary(metrics):
    return {key: {'calls': len(values), 'wall_s': sum(x[0] for x in values),
                  'cpu_s': sum(x[1] for x in values),
                  'p50_ms': float(np.percentile([x[0] for x in values], 50) * 1000),
                  'p95_ms': float(np.percentile([x[0] for x in values], 95) * 1000)}
            for key, values in metrics.items()}


def record(part1, out, *, quick=False, actual_capture=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(out / 'golden.sqlite')
    metrics = {}
    data = market()
    rt = runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START - 1, specials=['SPECIAL7'],
                              trigger_overrides={'SPECIAL7': '무지성 올존'}, part1_root=Path(part1),
                              log_level=logging.CRITICAL)
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    sequence = 0
    wire_hash = hashlib.sha256()
    writer = capture.CaptureWriter(out / 'synthetic_capture', SYMBOL, TIMEFRAMES)
    errors = 0

    def publish(tf, payload, observed):
        nonlocal sequence
        sequence += 1
        rt.clock.set(observed)
        wire = capture.pack_wire(SYMBOL, tf, *payload, snapshot=sequence)
        wire_hash.update(wire)
        timed(metrics, 'parser', rt.publish, wire)

    def request(name, tfs, indicators=(), history=0):
        nonlocal errors
        req = {'symbol': SYMBOL, 'timeframes': list(tfs), 'indicators': list(indicators),
               'watch_ma_history_rows': history}
        response = timed(metrics, 'request', rt.staff_data_request, req)
        errors += int('error' in response)
        recorder.add(name, response)

    try:
        # Keep all 240 seconds and all TF wire bytes, including not-ready high TFs.
        for offset in range(3 if quick else WINDOW):
            observed = START + offset
            for index, tf in enumerate(TIMEFRAMES):
                payload = data.payload(tf, observed)
                writer.write(index, observed, *payload)
                publish(tf, payload, observed)
                request(f'timeline/{offset:03}/{tf}', [tf], ('PRICE', 'RSI', 'STO', 'DI'))
            recorder.add(f'health/{offset:03}', rt._staff_handle(
                {'kind': 'SOURCE_HEALTH', 'symbol': SYMBOL, 'timeframes': list(TIMEFRAMES)}))
        writer.close()
        # The exhaustive matrix uses 650 ready rows for every TF. The values are
        # synthetic 1m fixture values, time-remapped per TF, NOT MT5-produced data.
        times, volumes, values = data.payload('1m', START + WINDOW - 1)
        matrix_epoch = START + WINDOW
        for tf in TIMEFRAMES:
            width = TF_SECONDS[tf]
            tt = (matrix_epoch // width * width) - np.arange(len(times) - 1, -1, -1) * width
            publish(tf, (tt, volumes, values), matrix_epoch)
        combinations = list(indicator_combinations())
        if quick:
            combinations = [(), INDICATORS]
        for mask, indicators in enumerate(combinations):
            for ma_index, names in enumerate(MA_CASES):
                for tf in TIMEFRAMES:
                    request(f'matrix/{mask:03}/{ma_index}/{tf}', [tf], indicators + names,
                            40 if names else 0)
            if mask % 32 == 0:
                print(f'golden matrix {mask + 1}/{len(combinations)}', flush=True)
        # Multi-TF ordering and opt-in MA history across new bars, beyond 650 rows.
        for index, tfs in enumerate((TIMEFRAMES, tuple(reversed(TIMEFRAMES)), ('1m', '5m', '1m'))):
            request(f'multi/{index}', tfs, INDICATORS + MA_CASES[-1])
        for offset in range(6):
            observed = matrix_epoch + (offset + 1) * 60
            tt = observed - np.arange(len(times) - 1, -1, -1) * 60
            vv = values.copy()
            vv[:, :4] += offset * 0.1
            publish('1m', (tt, volumes, vv), observed)
            request(f'history/{offset}', ['1m'], ('SMA651', 'WMA23', 'EMA37', 'HMA17'), 655)
        # Runtime sigma transitions are recorded before the later S7 semantic change.
        for sigma in (3.0, 2.5, 3.0):
            rt.staff_server.wonbi_state.set_sigma(sigma)
            request(f'sigma/{sigma}/{sequence}', ['1m'])
            sequence += 1
        # Optional real Strategy Tester sample, retained as distinct provenance.
        actual = {'status': 'NOT_AVAILABLE', 'reason': 'No real MT5 STAFF_PIPE_V1 sample supplied'}
        if actual_capture:
            info = capture.parse_capture_manifest(actual_capture)
            if info['symbol'] != SYMBOL:
                raise ValueError('S0 actual sample must be XAUUSD+ for this scenario')
            for i, (observed, tf, wire) in enumerate(capture.iter_publications(actual_capture)):
                rt.clock.set(observed)
                # Fresh sequence is needed after the preceding synthetic section.
                sequence += 1
                header = list(capture.PIPE_HEADER.unpack_from(wire))
                header[2] = sequence
                wire = capture.PIPE_HEADER.pack(*header) + wire[capture.PIPE_HEADER.size:]
                timed(metrics, 'actual_parser', rt.publish, wire)
                request(f'actual/{i}/{tf}', [tf], ('PRICE', 'RSI', 'STO', 'DI'))
            actual = {'status': 'RECORDED', 'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in Path(actual_capture).iterdir() if p.is_file()}}
        summary = recorder.close()
        summary.update({'format': 'staff-s0-v1', 'quick': quick, 'window_s': 3 if quick else WINDOW,
                        'symbol': SYMBOL, 'timeframes': list(TIMEFRAMES),
                        'indicator_combinations': len(combinations), 'ma_variants': MA_CASES,
                        'matrix_cases': len(combinations) * len(MA_CASES) * len(TIMEFRAMES),
                        'error_responses_recorded': errors, 'wire_sha256': wire_hash.hexdigest(),
                        'source_sha256': rt.source_hashes, 'actual_tester_sample': actual,
                        'synthetic_capture': 'Python CaptureWriter, not an MT5 execution',
                        'normalization': 'Only random STAFF session prefix; epoch counter retained',
                        'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                                        'pandas': pd.__version__, 'platform': platform.platform()},
                        'timing': metric_summary(metrics)})
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        return summary
    finally:
        for f in writer.files:
            if not f.closed:
                f.close()
        rt.close()
        logging.disable(previous_logging)
