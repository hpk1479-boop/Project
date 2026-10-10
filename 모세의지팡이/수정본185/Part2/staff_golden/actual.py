"""Add a verified, real Strategy Tester sample to the S0 baseline bundle."""
import hashlib
import json
import logging
from pathlib import Path

from part1_host import capture, runtime
from .contracts import Recorder
from .record import timed, metric_summary
from .scenario import INDICATORS


def record_actual(part1, source, out):
    source, out = Path(source), Path(out)
    info = capture.parse_capture_manifest(source)
    first = min(next(capture._read_records(f.path, f.records))[1] for f in info['feeds'])
    out.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(out / 'golden.sqlite')
    capture._sequence = 0
    metrics, errors, publications = {}, [], 0
    digest = hashlib.sha256()
    old_logging = logging.root.manager.disable
    try:
        with runtime.Part1Runtime(symbols=[info['symbol']], start_epoch=first - 1,
                                  specials=['SPECIAL7'], part1_root=Path(part1),
                                  log_level=logging.CRITICAL) as rt:
            logging.disable(logging.CRITICAL)
            for second, tf, wire in capture.iter_publications(source):
                rt.clock.set(second)
                timed(metrics, 'actual_parser', rt.publish, wire)
                digest.update(wire)
                request = {'symbol': info['symbol'], 'timeframes': [tf],
                           'indicators': list(INDICATORS) + ['SMA20', 'HMA17']}
                reply = timed(metrics, 'actual_request', rt.staff_data_request, request)
                key = f'{second}/{tf}'
                recorder.add(key, reply)
                if 'error' in reply:
                    errors.append({'key': key, 'error': reply['error']})
                publications += 1
                if publications % 1000 == 0:
                    print('actual golden publications: ' + str(publications), flush=True)
            recorder.add('health/final', rt._staff_handle({'kind': 'SOURCE_HEALTH',
                         'symbol': info['symbol'], 'timeframes': [f.timeframe for f in info['feeds']]}))
            result = recorder.close()
            result.update({'provenance': 'ACTUAL_MT5_STRATEGY_TESTER', 'symbol': info['symbol'],
                           'source_sha256': rt.source_hashes, 'publications': publications,
                           'wire_sha256': digest.hexdigest(), 'errors': errors,
                           'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in source.iterdir() if p.is_file()},
                           'timing': metric_summary(metrics)})
        (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    finally:
        logging.disable(old_logging)
