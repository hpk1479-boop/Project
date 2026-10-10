"""R analytics for the existing virtual_trades.csv contract.

This module reads completed trade rows only. It never replays captures or
changes entry, exit, spread, or strategy decisions. The result screen's view
(result_analysis) follows its rules; no analytics.json is written after a run
(수정본170).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import csv
import math

from .virtual_entry import RATIOS


REQUIRED = frozenset({'signal_id', 'strategy', 'symbol', 'tf', 'alert_time',
                      'direction', 'entry_time', 'rr', 'result', 'exit_time', 'r'})
RESULTS = frozenset({'WIN', 'LOSS', 'UNCLOSED', 'UNCERTAIN', 'WAITING', 'BLOCKED', 'EXPIRED', 'PASS_ENV', 'PASS_RISK',
                     'PASS_CONDITION', 'PASS_DISTANCE'})


def _integer(value, field, *, optional=False):
    if optional and (value is None or value == ''):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f'거래 결과의 {field} 값이 올바르지 않습니다.') from None


def _number(value, field, *, optional=False):
    if optional and (value is None or value == ''):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'거래 결과의 {field} 값이 올바르지 않습니다.') from None
    if not math.isfinite(number):
        raise ValueError(f'거래 결과의 {field} 값이 유한하지 않습니다.')
    return number


def _trade(row):
    result = row['result']
    if result not in RESULTS:
        raise ValueError('지원하지 않는 거래 결과: ' + str(result))
    if row['direction'] not in ('LONG', 'SHORT'):
        raise ValueError('거래 방향은 LONG 또는 SHORT여야 합니다.')
    alert_time = _integer(row['alert_time'], 'alert_time')
    entry_time = _integer(row['entry_time'], 'entry_time', optional=True)
    exit_time = _integer(row['exit_time'], 'exit_time', optional=True)
    r = _number(row['r'], 'r', optional=True)
    rr = _number(row['rr'], 'rr')
    if rr <= 0 or alert_time < 0 or (entry_time is not None and entry_time < 0):
        raise ValueError('RR 또는 시각 값이 올바르지 않습니다.')
    entered = result in ('WIN', 'LOSS', 'UNCLOSED', 'UNCERTAIN')
    if entered != (entry_time is not None):
        raise ValueError('진입 여부와 entry_time이 일치하지 않습니다.')
    closed = result in ('WIN', 'LOSS')
    if closed != (exit_time is not None and r is not None):
        raise ValueError('청산 결과와 exit_time/r이 일치하지 않습니다.')
    if result == 'UNCERTAIN' and (exit_time is None or r is not None or exit_time < entry_time):
        raise ValueError('순서 미확정 결과는 확인 시각과 비어 있는 R이 필요합니다.')
    if (result == 'WIN' and r <= 0) or (result == 'LOSS' and r >= 0):
        raise ValueError('승패 결과와 R의 부호가 일치하지 않습니다.')
    if closed and exit_time < entry_time:
        raise ValueError('청산 시각이 진입 시각보다 빠릅니다.')
    return {'signal_id': row['signal_id'], 'strategy': row['strategy'],
            'symbol': row['symbol'], 'tf': row['tf'], 'alert_time': alert_time,
            'direction': row['direction'], 'entry_time': entry_time,
            'exit_time': exit_time, 'rr': rr, 'result': result, 'r': r}


def _summary(rows):
    alerts = len(rows)
    entered = [row for row in rows if row['entry_time'] is not None]
    wins = [row['r'] for row in rows if row['result'] == 'WIN']
    losses = [row['r'] for row in rows if row['result'] == 'LOSS']
    closed = len(wins) + len(losses)
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    total = gross_profit + gross_loss
    holding = [(row['exit_time'] - row['entry_time']) / 1000
               for row in rows if row['result'] in ('WIN', 'LOSS')]
    return {
        'alerts': alerts, 'total_trades': len(entered), 'wins': len(wins),
        'losses': len(losses), 'uncertain': sum(row['result'] == 'UNCERTAIN' for row in rows),
        'unclosed': sum(row['result'] == 'UNCLOSED' for row in rows),
        'win_rate': len(wins) / closed if closed else None,
        'total_r': total, 'gross_profit_r': gross_profit, 'gross_loss_r': gross_loss,
        'average_r': total / closed if closed else None,
        'average_win_r': gross_profit / len(wins) if wins else None,
        'average_loss_r': gross_loss / len(losses) if losses else None,
        'profit_factor': gross_profit / abs(gross_loss) if gross_loss < 0 else None,
        'expectancy_r': total / closed if closed else None,
        'average_holding_seconds': sum(holding) / len(holding) if holding else None,
        'max_holding_seconds': max(holding) if holding else None,
        'entry_rate': len(entered) / alerts if alerts else None,
        'passes': {'blocked': sum(row['result'] == 'BLOCKED' for row in rows),
                   'expired': sum(row['result'] == 'EXPIRED' for row in rows),
                   'environment': sum(row['result'] == 'PASS_ENV' for row in rows),
                   'condition': sum(row['result'] == 'PASS_CONDITION' for row in rows),
                   'distance': sum(row['result'] == 'PASS_DISTANCE' for row in rows),
                   'zero_stop_width': sum(row['result'] == 'PASS_RISK' for row in rows),
                   'waiting': sum(row['result'] == 'WAITING' for row in rows)},
    }


def _group(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {str(name): _summary(group) for name, group in sorted(groups.items())}


def _series(rows, key, label):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return [{label: name, **_summary(group)} for name, group in sorted(groups.items())]


def _curve_and_streaks(rows):
    closed = sorted((row for row in rows if row['result'] in ('WIN', 'LOSS')),
                    key=lambda row: (row['exit_time'], row['alert_time'], row['signal_id']))
    equity = peak = max_drawdown = 0.0
    win_run = loss_run = max_wins = max_losses = 0
    equity_curve = []
    drawdown_curve = []
    for row in closed:
        equity += row['r']
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        point = {'exit_time_ms': row['exit_time'], 'signal_id': row['signal_id']}
        equity_curve.append({**point, 'equity_r': equity})
        drawdown_curve.append({**point, 'drawdown_r': drawdown})
        if row['result'] == 'WIN':
            win_run += 1
            loss_run = 0
            max_wins = max(max_wins, win_run)
        else:
            loss_run += 1
            win_run = 0
            max_losses = max(max_losses, loss_run)
    return ({'max_drawdown_r': max_drawdown,
             'recovery_factor': equity / max_drawdown if max_drawdown > 0 else None},
            {'max_consecutive_wins': max_wins, 'max_consecutive_losses': max_losses},
            equity_curve, drawdown_curve)


def analyze_rows(rows):
    """Return JSON-safe, independent analyses keyed by the recorded RR value."""
    by_rr = defaultdict(list)
    for source in rows:
        row = _trade(source)
        by_rr[row['rr']].append(row)
    result = {}
    for rr in sorted(set(RATIOS) | set(by_rr)):
        trades = by_rr[rr]
        drawdown, streaks, equity_curve, drawdown_curve = _curve_and_streaks(trades)
        result[f'{rr:.1f}'] = {
            'rr': rr, 'calendar_basis': 'alert_time UTC', 'summary': _summary(trades),
            'drawdown': drawdown, 'streaks': streaks,
            'by_direction': _group(trades, lambda row: row['direction']),
            'by_symbol': _group(trades, lambda row: row['symbol']),
            'by_timeframe': _group(trades, lambda row: row['tf']),
            'by_strategy': _group(trades, lambda row: row['strategy']),
            'monthly': _series(trades, lambda row: datetime.fromtimestamp(row['alert_time'] / 1000, timezone.utc).strftime('%Y-%m'), 'month'),
            'weekday': _series(trades, lambda row: datetime.fromtimestamp(row['alert_time'] / 1000, timezone.utc).weekday(), 'weekday'),
            'hourly': _series(trades, lambda row: datetime.fromtimestamp(row['alert_time'] / 1000, timezone.utc).hour, 'hour_utc'),
            'equity_r_curve': equity_curve, 'drawdown_curve': drawdown_curve,
        }
    return {'schema_version': 1, 'source': 'virtual_trades.csv',
            'rr_results': result}


def analyze_csv(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError('거래 결과 CSV 헤더가 없습니다.')
        rows = list(reader)
        if rows and not REQUIRED.issubset(reader.fieldnames):
            raise ValueError('거래 결과 CSV 필수 열이 없습니다: ' +
                             ', '.join(sorted(REQUIRED - set(reader.fieldnames))))
    return analyze_rows(rows)
