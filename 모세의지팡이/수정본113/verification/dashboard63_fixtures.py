"""Synthetic stored results using the existing Part2 analytics contract (tests only)."""
from __future__ import annotations

import csv
import json
from pathlib import Path


CASES = ('normal', 'missing', 'sparse', 'zero', 'long_only', 'short_only')


def create_case(warehouse, case, index=0):
    from event_backtest.analytics import analyze_rows
    identifier = f'{index + 1:032x}'
    folder = Path(warehouse) / 'runs' / identifier
    folder.mkdir(parents=True)
    rows = []
    if case != 'zero':
        for i in range(12):
            direction = 'SHORT' if case == 'short_only' else 'LONG' if case == 'long_only' or i % 2 == 0 else 'SHORT'
            stamp = 1767225600000 + i * 10 * 86400000
            for rr in (1.0, 2.0):
                win = i % 4 in (0, 3)
                rows.append(dict(signal_id=str(i), strategy='SPECIAL1' if i % 3 else 'SPECIAL2',
                                 symbol='XAUUSD+' if i % 3 else 'EURUSD+', tf='15m' if i % 2 else '3m',
                                 alert_time=stamp, direction=direction, entry_time=stamp + 1000,
                                 entry_price=2000, stop_price=1990, rr=rr, result='WIN' if win else 'LOSS',
                                 exit_time=stamp + 60000, r=rr if win else -1.0))
    trades = folder / 'virtual_trades.csv'
    fields = list(rows[0]) if rows else ['signal_id', 'rr', 'result', 'r']
    with trades.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    (folder / 'virtual_summary.csv').write_text('strategy,rr,entries\nSPECIAL1,1.0,12\n', encoding='utf-8')
    (folder / 'alerts.csv').write_text('time_ms,strategy,symbol,tf,direction,message\n', encoding='utf-8')
    result = {'run_id': identifier, 'status': 'COMPLETE', 'status_label': '완료', 'result_mode': 'VIRTUAL_ENTRY',
              'alerts_csv': f'runs/{identifier}/alerts.csv',
              'virtual_entry': {'summary': [], 'trades_csv': f'runs/{identifier}/virtual_trades.csv',
                                'summary_csv': f'runs/{identifier}/virtual_summary.csv'}}
    if case != 'missing':
        analytics = analyze_rows(rows)
        if case == 'sparse':
            analytics['rr_results'] = {'1.0': {'summary': {'total_trades': 12, 'total_r': None},
                                              'monthly': None, 'by_direction': {'LONG': {}},
                                              'equity_r_curve': [{'exit_time_ms': None, 'equity_r': None}]}}
        result['analytics_path'] = f'runs/{identifier}/analytics.json'
        (folder / 'analytics.json').write_text(json.dumps(analytics, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return {'id': identifier, 'warehouse': Path(warehouse), 'folder': folder}, result
