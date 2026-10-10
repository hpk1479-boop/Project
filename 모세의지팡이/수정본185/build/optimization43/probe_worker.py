"""Real run_chunk with synthetic Wire and canonical WATCH; no SQL or MT5.

Missing DuckDB is bypassed only at import by a guard that raises on any SQL use.
Each invocation runs in a child process because worker write/network guards persist.
"""
from pathlib import Path
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile

from probe43_support import ROOT, synthetic_snapshot, make_capture, load_warehouse


def child(root):
    warehouse = load_warehouse()
    sys.modules['event_backtest.warehouse'] = warehouse
    from event_backtest.runner import run_chunk
    from event_backtest.settings import scenario
    config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+',
              'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'offline', 'WONBI_SIGMA': '3'}
    s = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-02', mode='TICK',
                 strategies=['WATCH'], overlap_trading_days=0,
                 commands=[{'strategy': 'WATCH', 'chat_id': 'offline',
                            'text': '골드 1분 상단 원비 터치 알려줘'}])
    results = []
    for transport in ('live', 'replay'):
        task = {'scenario': s, 'config': config, 'out': str(root / transport), 'run_id': 'probe43',
                'start': s['start'], 'end': s['end'], 'warm_start': s['start'],
                'captures': [{'path': 'capture'}], 'warehouse': str(root), 'transport': transport}
        result = run_chunk(task)
        assert result['bundles'] == 6 and not result['cancelled']
        with (root / transport / 'alerts.csv').open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        assert rows, 'canonical WATCH produced no notification'
        timings = result['processor_timings']
        assert timings and all(v['ns'] >= 0 and v['calls'] > 0 for v in timings.values())
        results.append({'bundles': result['bundles'], 'notifications': result['notifications'],
                        'alerts': result['alerts'], 'rows': rows,
                        'timing_calls': {k: v['calls'] for k, v in timings.items()},
                        'watch_dependencies': result['watch_dependencies']})
    assert results[0] == results[1], 'LIVE/replay worker results differ'
    try:
        (root / 'outside.txt').write_text('must fail', encoding='utf-8')
    except PermissionError:
        pass
    else:
        raise AssertionError('worker write boundary was lost')
    print('PROBE43_JSON=' + json.dumps({'worker': results[0], 'same_worker_reuse': True,
          'live_vs_replay_equal': True, 'outside_write_blocked': True,
          'sql_tested': False, 'duckdb_import_omitted': warehouse._validation_without_duckdb}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return
    from run_checks import portable_log
    out = ROOT / '검증결과/계측_유효성최적화43'
    out.mkdir(parents=True, exist_ok=True)
    variants = [(), ((-1, 'high', 108.),), ((-1, 'high', 108.),), (), ((-1, 'high', 110.),), ()]
    attempts = []
    for attempt in range(2):
        with tempfile.TemporaryDirectory(prefix='worker43_') as tmp:
            make_capture(Path(tmp) / 'capture', [synthetic_snapshot(i, c) for i, c in enumerate(variants, 1)])
            response = subprocess.run([sys.executable, '-X', 'utf8', '-B', __file__, '--child', tmp],
                                      cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                      capture_output=True, text=True, timeout=90)
        (out / f'worker_attempt{attempt+1}.log').write_text(
            portable_log(response.stdout + response.stderr), encoding='utf-8')
        attempts.append({'attempt': attempt+1, 'exit_code': response.returncode})
        if response.returncode == 0:
            payload = next(line.split('=', 1)[1] for line in response.stdout.splitlines() if line.startswith('PROBE43_JSON='))
            result = json.loads(payload)
            (out / 'worker_integration.json').write_text(json.dumps({'attempts': attempts, **result},
                ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'attempts': attempts, **result}, ensure_ascii=False, indent=2))
            return
        print(f'worker attempt {attempt+1} failed', flush=True)
    (out / 'worker_integration.json').write_text(json.dumps({'attempts': attempts, 'completed': False,
        'excluded': 'worker integration; see logs; no production workaround applied'}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(response.stderr)
    raise SystemExit(1)


if __name__ == '__main__':
    main()
