"""What a finished virtual-entry run shows, read from its trade rows (수정본165).

One period (all, a year or a month, in Korean time of the alert), the RR results side by side with the
usual evaluation metrics and their common grades, one RR's curves and tables, the hours chosen by
expectancy, and a compounding account. Everything is read from virtual_trades.csv; nothing here
replays or changes a trade.
"""
from __future__ import annotations

from array import array
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re

import numpy as np

from .analytics import RESULTS, _trade
from .virtual_entry import RATIOS

KST_MS = 9 * 3600 * 1000
HOUR_MS = 3600 * 1000
SLOT_MIN_TRADES = 30       # an hour is chosen only with at least this many closed trades
CHECK_SHARE = 0.7          # hours chosen on the first 70% of the period are checked on the rest
START_BALANCE = 10_000.0
RISK = 0.01                # 1R is 1% of the balance at entry
CURVE_POINTS = 1000        # curve points sent to the screen; extremes are kept
SHARPE_MONTHS = 6          # monthly Sharpe and Sortino need at least this many months
RF_GRADE_MONTHS = 12       # the recovery factor grows with the period; it is graded from a year up
SIGNIFICANT_T = 2.0        # the average R is told apart from chance from here
CODES = {name: index for index, name in enumerate(sorted(RESULTS))}
WIN, LOSS = CODES['WIN'], CODES['LOSS']
# Common rules of thumb for system tests: (grade, upper bound) pairs, a value below the bound gets the grade.
GRADES = {
    'profit_factor': (('poor', 1.0), ('weak', 1.5), ('good', 2.0), ('excellent', math.inf)),
    'recovery_factor': (('poor', 0.0), ('weak', 2.0), ('fair', 3.0), ('good', 5.0), ('excellent', math.inf)),
    'sqn': (('poor', 0.0), ('weak', 1.6), ('fair', 2.5), ('good', 3.0), ('excellent', math.inf)),
    'sharpe': (('poor', 0.0), ('weak', 1.0), ('good', 2.0), ('excellent', math.inf)),
    'sortino': (('poor', 0.0), ('weak', 1.0), ('fair', 2.0), ('good', 3.0), ('excellent', math.inf)),
}
SQN_SUSPECT = 7.0          # Van Tharp: a system test this good is usually over-fitted


class Trades:
    """One run's trade rows as arrays: one row per alert and RR, validated like analytics.analyze_rows."""

    def __init__(self, rows):
        alert, entry, exit_, rr, r = array('q'), array('q'), array('q'), array('d'), array('d')
        result, short = array('b'), array('b')
        signals, seen = [], {key: {} for key in ('strategy', 'symbol', 'tf')}
        codes = {key: array('i') for key in seen}
        for source in rows:
            row = _trade(source)
            alert.append(row['alert_time'])
            entry.append(-1 if row['entry_time'] is None else row['entry_time'])
            exit_.append(-1 if row['exit_time'] is None else row['exit_time'])
            rr.append(row['rr'])
            r.append(math.nan if row['r'] is None else row['r'])
            result.append(CODES[row['result']])
            short.append(row['direction'] == 'SHORT')
            signals.append(row['signal_id'])
            for key, names in seen.items():
                codes[key].append(names.setdefault(row[key], len(names)))
        self.alert = np.array(alert, dtype=np.int64)
        self.entry = np.array(entry, dtype=np.int64)
        self.exit = np.array(exit_, dtype=np.int64)
        self.rr = np.array(rr, dtype=np.float64)
        self.r = np.array(r, dtype=np.float64)
        self.result = np.array(result, dtype=np.int8)
        self.short = np.array(short, dtype=bool)
        order = {value: index for index, value in enumerate(sorted(set(signals)))}
        self.signal = np.fromiter((order[value] for value in signals), np.int64, len(signals))
        self.codes = {key: np.array(values, dtype=np.int32) for key, values in codes.items()}
        self.labels = {key: sorted(names, key=names.get) for key, names in seen.items()}
        kst = self.alert + KST_MS
        self.month = kst.astype('datetime64[ms]').astype('datetime64[M]').astype(np.int64)
        self.hour = (kst // HOUR_MS) % 24
        self.tables = {}   # period -> its RR table, computed once for these rows (수정본170)

    def __len__(self):
        return len(self.alert)


def load_trades(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        return Trades(csv.DictReader(handle))


def _month(index):
    return f'{1970 + int(index) // 12:04d}-{int(index) % 12 + 1:02d}'


def _value(number):
    """JSON-safe: a finite float, or None."""
    return float(number) if number is not None and math.isfinite(number) else None


def periods(trades):
    months = [_month(index) for index in np.unique(trades.month)]
    return {'years': sorted({month[:4] for month in months}), 'months': months}


def period_window(period):
    """[start, end) in UTC milliseconds of a year or a month in Korean time; None for the whole run."""
    if period in (None, '', 'all'):
        return None
    kst = timezone(timedelta(milliseconds=KST_MS))
    if re.fullmatch(r'\d{4}', str(period)):
        start, end = datetime(int(period), 1, 1, tzinfo=kst), datetime(int(period) + 1, 1, 1, tzinfo=kst)
    elif re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(period)):
        year, month = int(period[:4]), int(period[5:])
        start = datetime(year, month, 1, tzinfo=kst)
        end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=kst)
    else:
        raise ValueError('기간은 전체, 연도(YYYY) 또는 월(YYYY-MM)로 고르세요.')
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _period(trades, period):
    """Rows in the period, and its months from the run's first to its last alert (Korean time)."""
    if not len(trades):
        return np.zeros(0, bool), []
    first, last = int(trades.month.min()), int(trades.month.max())
    if period in (None, '', 'all'):
        low, high = first, last
    elif re.fullmatch(r'\d{4}', str(period)):
        low = (int(period) - 1970) * 12
        high = low + 11
    elif re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(period)):
        low = high = (int(period[:4]) - 1970) * 12 + int(period[5:]) - 1
    else:
        raise ValueError('기간은 전체, 연도(YYYY) 또는 월(YYYY-MM)로 고르세요.')
    if high < first or low > last:
        raise ValueError('이 실행에 없는 기간입니다.')
    return (trades.month >= low) & (trades.month <= high), list(range(max(low, first), min(high, last) + 1))


def rr_keys(trades):
    return [f'{value:.1f}' for value in sorted(set(RATIOS) | set(np.unique(trades.rr).tolist()))]


def summary(trades, rows):
    """The same values as analytics.analyze_rows's summary, for the given rows."""
    result = trades.result[rows]
    win, loss = result == WIN, result == LOSS
    wins, losses = trades.r[rows][win], trades.r[rows][loss]
    closed = len(wins) + len(losses)
    profit, loss_total = float(wins.sum()), float(losses.sum())
    total = profit + loss_total
    done = win | loss
    held = (trades.exit[rows][done] - trades.entry[rows][done]) / 1000
    entered = int((trades.entry[rows] >= 0).sum())
    alerts = len(rows)
    count = lambda name: int((result == CODES[name]).sum())
    return {
        'alerts': alerts, 'total_trades': entered, 'wins': len(wins), 'losses': len(losses),
        'uncertain': count('UNCERTAIN'), 'unclosed': count('UNCLOSED'),
        'win_rate': len(wins) / closed if closed else None,
        'total_r': total, 'gross_profit_r': profit, 'gross_loss_r': loss_total,
        'average_r': total / closed if closed else None,
        'average_win_r': profit / len(wins) if len(wins) else None,
        'average_loss_r': loss_total / len(losses) if len(losses) else None,
        'profit_factor': profit / abs(loss_total) if loss_total < 0 else None,
        'expectancy_r': total / closed if closed else None,
        'average_holding_seconds': float(held.mean()) if len(held) else None,
        'max_holding_seconds': float(held.max()) if len(held) else None,
        'entry_rate': entered / alerts if alerts else None,
        'passes': {'blocked': count('BLOCKED'), 'expired': count('EXPIRED'), 'environment': count('PASS_ENV'),
                   'zero_stop_width': count('PASS_RISK'), 'waiting': count('WAITING')},
    }


def _closed(trades, rows):
    """Closed trades of the rows in exit order (as analytics: exit, alert, then signal ID)."""
    result = trades.result[rows]
    done = rows[(result == WIN) | (result == LOSS)]
    return done[np.lexsort((trades.signal[done], trades.alert[done], trades.exit[done]))]


def _longest(flags):
    if not flags.any():
        return 0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], flags.astype(np.int8), [0]))))
    return int((edges[1::2] - edges[::2]).max())


def _drawdown(r):
    """Equity in R and the drawdown from its peak, both starting from zero."""
    equity = np.cumsum(r)
    return equity, np.maximum.accumulate(np.maximum(equity, 0.0)) - equity


def grade(name, value):
    if value is None or name not in GRADES:
        return None
    return next(label for label, bound in GRADES[name] if value < bound or (bound == 0.0 and value <= 0.0))


def metrics(trades, rows, months):
    """The usual system-test metrics of the rows' closed trades (R units, exit order)."""
    order = _closed(trades, rows)
    r = trades.r[order]
    n = len(r)
    if not n:
        return {'trades': 0, 'max_drawdown_r': 0.0, 'recovery_factor': None, 'max_consecutive_wins': 0,
                'max_consecutive_losses': 0, 'sqn': None, 't': None, 'sharpe': None, 'sortino': None,
                'payoff': None, 'kelly': None}
    total = float(r.sum())
    _, drawdown = _drawdown(r)
    deepest = float(drawdown.max())
    mean = total / n
    deviation = float(r.std(ddof=1)) if n > 1 else 0.0
    sqn = t = None
    if deviation > 0:
        sqn = math.sqrt(min(n, 100)) * mean / deviation
        t = math.sqrt(n) * mean / deviation
    sharpe = sortino = None
    if len(months) >= SHARPE_MONTHS:
        monthly = np.bincount(trades.month[order] - months[0], weights=r, minlength=len(months))[:len(months)]
        spread = float(monthly.std(ddof=1))
        if spread > 0:
            sharpe = float(monthly.mean()) / spread * math.sqrt(12)
        downside = math.sqrt(float(np.mean(np.minimum(monthly, 0.0) ** 2)))
        if downside > 0:
            sortino = float(monthly.mean()) / downside * math.sqrt(12)
    gains, pains = r[r > 0], r[r < 0]
    payoff = kelly = None
    if len(gains) and len(pains):
        payoff = float(gains.mean()) / -float(pains.mean())
        p = len(gains) / n
        kelly = p - (1 - p) / payoff
    return {'trades': n, 'max_drawdown_r': deepest, 'recovery_factor': total / deepest if deepest > 0 else None,
            'max_consecutive_wins': _longest(r > 0), 'max_consecutive_losses': _longest(r < 0),
            'sqn': sqn, 't': t, 'sharpe': sharpe, 'sortino': sortino, 'payoff': payoff, 'kelly': kelly}


def rr_table(trades, mask, months):
    """Every RR side by side: the values ranked on screen, their grades, and whether the edge is real."""
    rows = []
    for key in rr_keys(trades):
        chosen = np.flatnonzero(mask & (trades.rr == float(key)))
        base, measured = summary(trades, chosen), metrics(trades, chosen, months)
        values = {'total_r': base['total_r'], 'average_r': base['average_r'], 'win_rate': base['win_rate'],
                  'profit_factor': base['profit_factor'], **{name: measured[name] for name in (
                      'recovery_factor', 'sqn', 'sharpe', 'sortino', 'max_drawdown_r',
                      'max_consecutive_losses', 'kelly', 't')}}
        values = {name: _value(value) if isinstance(value, float) else value for name, value in values.items()}
        grades = {name: grade(name, values[name]) for name in GRADES}
        if len(months) < RF_GRADE_MONTHS:
            grades['recovery_factor'] = None
        rows.append({'rr': key, 'trades': measured['trades'], 'values': values, 'grades': grades,
                     'significant': values['t'] is not None and values['t'] >= SIGNIFICANT_T,
                     'suspect': values['sqn'] is not None and values['sqn'] >= SQN_SUSPECT,
                     'summary': {name: _value(value) if isinstance(value, float) else value
                                 for name, value in base.items()}})
    return rows


def best_rr(rows):
    """The largest total R among the RR whose average R is told apart from chance, else among all."""
    candidates = [row for row in rows if row['trades']] or rows
    trusted = [row for row in candidates if row['significant']] or candidates
    return max(trusted, key=lambda row: (row['values']['total_r'] or 0.0, -float(row['rr'])))['rr'] if trusted else None


def _curve(times, values, limit=CURVE_POINTS):
    """At most `limit` points in time order, keeping each segment's lowest and highest point."""
    n = len(values)
    if n > limit:
        keep = {0, n - 1}
        edges = np.linspace(0, n, (limit - 2) // 2 + 1).astype(int)
        for low, high in zip(edges[:-1], edges[1:]):
            if high > low:
                part = values[low:high]
                keep.update((low + int(part.argmin()), low + int(part.argmax())))
        index = np.array(sorted(keep))
    else:
        index = np.arange(n)
    return [[int(times[i]), round(float(values[i]), 6)] for i in index]


def detail(trades, rows):
    """One RR in the period: curves in exit order, then monthly, hourly and group tables (Korean time)."""
    order = _closed(trades, rows)
    equity, drawdown = _drawdown(trades.r[order])
    times = trades.exit[order]
    monthly = [{'month': _month(index), **summary(trades, rows[trades.month[rows] == index])}
               for index in np.unique(trades.month[rows])]
    hourly = [{'hour': int(hour), **summary(trades, rows[trades.hour[rows] == hour])}
              for hour in np.unique(trades.hour[rows])]
    groups = {}
    for key, title in (('direction', 'by_direction'), ('strategy', 'by_strategy'),
                       ('symbol', 'by_symbol'), ('tf', 'by_timeframe')):
        if key == 'direction':
            codes, labels = trades.short[rows].astype(np.int32), ['LONG', 'SHORT']
        else:
            codes, labels = trades.codes[key][rows], trades.labels[key]
        groups[title] = {labels[int(code)]: summary(trades, rows[codes == code]) for code in np.unique(codes)}
    return {'equity': _curve(times, equity), 'drawdown': _curve(times, drawdown),
            'monthly': monthly, 'hourly': hourly, **groups}


def _brief(trades, rows):
    """Trades, win rate, average and total R of the rows' closed trades: the comparison of hours."""
    r = trades.r[rows][(trades.result[rows] == WIN) | (trades.result[rows] == LOSS)]
    n = len(r)
    return {'trades': n, 'win_rate': float((r > 0).mean()) if n else None,
            'average_r': float(r.mean()) if n else None, 'total_r': float(r.sum())}


def _good_hours(trades, closed):
    count = np.bincount(trades.hour[closed], minlength=24)
    total = np.bincount(trades.hour[closed], weights=trades.r[closed], minlength=24)
    return [hour for hour in range(24) if count[hour] >= SLOT_MIN_TRADES and total[hour] > 0]


def ranges(hours):
    """Chosen hours as [start, end) ranges, joined over midnight."""
    chosen = sorted(set(hours))
    if len(chosen) == 24:
        return [[0, 24]]
    spans = []
    for hour in chosen:
        if spans and spans[-1][1] == hour:
            spans[-1][1] = hour + 1
        else:
            spans.append([hour, hour + 1])
    if len(spans) > 1 and spans[0][0] == 0 and spans[-1][1] == 24:
        spans[0][0] = spans.pop()[0]
    return spans


def time_slots(trades, rows):
    """Hours with a positive average R and enough trades, compared with all hours.

    The same rule applied to the first 70% of the period is checked on the remaining 30%: choosing and
    scoring hours on the same trades always flatters them.
    """
    result = trades.result[rows]
    closed = rows[(result == WIN) | (result == LOSS)]
    hours = trades.hour[closed]
    count = np.bincount(hours, minlength=24)
    total = np.bincount(hours, weights=trades.r[closed], minlength=24)
    won = np.bincount(hours, weights=(trades.r[closed] > 0).astype(np.float64), minlength=24)
    table = [{'hour': hour, 'trades': int(count[hour]),
              'win_rate': float(won[hour] / count[hour]) if count[hour] else None,
              'average_r': float(total[hour] / count[hour]) if count[hour] else None,
              'total_r': float(total[hour])} for hour in range(24)]
    chosen = _good_hours(trades, closed)
    picked = closed[np.isin(hours, chosen)]
    check = None
    if len(closed):
        first, last = int(trades.alert[closed].min()), int(trades.alert[closed].max())
        split = first + int((last - first) * CHECK_SHARE)
        early, late = closed[trades.alert[closed] < split], closed[trades.alert[closed] >= split]
        early_hours = _good_hours(trades, early)
        check = {'split_ms': split, 'hours': early_hours, 'ranges': ranges(early_hours),
                 'all': _brief(trades, late), 'chosen': _brief(trades, late[np.isin(trades.hour[late], early_hours)])}
    return {'min_trades': SLOT_MIN_TRADES, 'table': table, 'hours': chosen, 'ranges': ranges(chosen),
            'all': _brief(trades, closed), 'chosen': _brief(trades, picked), 'check': check}, picked


def account(trades, closed, fraction):
    """A START_BALANCE account where 1R is `fraction` of the realized balance at entry, booked at exit."""
    n = len(closed)
    if not n or fraction is None or fraction <= 0:
        return None
    times = np.concatenate((trades.exit[closed], trades.entry[closed]))
    kinds = np.concatenate((np.zeros(n, np.int8), np.ones(n, np.int8)))   # exits first at the same moment
    ids = np.concatenate((np.arange(n), np.arange(n)))
    risk = [None] * n
    r = trades.r[closed]
    balance = top = START_BALANCE
    deepest, holding, most = 0.0, 0, 0
    for event in np.lexsort((ids, kinds, times)):
        trade = int(ids[event])
        if risk[trade] is None:   # its entry; an exit at the entry's own moment enters it first
            risk[trade] = balance * fraction
            holding += 1
            most = max(most, holding)
        if kinds[event] == 0:
            balance += risk[trade] * float(r[trade])
            holding -= 1
            top = max(top, balance)
            deepest = max(deepest, (top - balance) / top)
    return {'fraction': fraction, 'trades': n, 'final': balance, 'profit': balance - START_BALANCE,
            'return': balance / START_BALANCE - 1, 'max_drawdown': deepest, 'max_open': most}


def accounts(trades, rows, picked):
    """The account for all hours and for the chosen hours, at 1% and at a quarter of the Kelly fraction."""
    result = trades.result[rows]
    closed = rows[(result == WIN) | (result == LOSS)]
    shown = {}
    for name, chosen in (('all', closed), ('chosen', picked)):
        kelly = metrics(trades, chosen, [])['kelly'] if len(chosen) else None
        shown[name] = {'one_percent': account(trades, chosen, RISK),
                       'quarter_kelly': account(trades, chosen, kelly / 4 if kelly and kelly > 0 else None),
                       'kelly': _value(kelly)}
    return shown


def _period_table(trades, period):
    """(rows in the period, its months, its RR table). The table depends on the period only, so it is
    computed once per loaded run and period: another RR of the same period reuses it (수정본170)."""
    mask, months = _period(trades, period)
    key, tables = period or 'all', vars(trades).setdefault('tables', {})
    if key not in tables:
        tables[key] = rr_table(trades, mask, months)
    return mask, months, deepcopy(tables[key])


def table_view(trades, period='all'):
    """The RR table of a period alone: the screen picks the RR by its own ranking, then asks for that RR."""
    _, months, table = _period_table(trades, period)
    return {'basis': 'KST alert time', 'periods': periods(trades), 'period': period or 'all', 'months': len(months),
            'rr_table': table, 'default_rr': best_rr(table)}


def view(trades, period='all', rr=None):
    """The whole result screen for one period and one RR; the RR defaults to best_rr."""
    mask, months, table = _period_table(trades, period)
    default = best_rr(table)
    keys = [row['rr'] for row in table]
    if rr not in (None, ''):
        try:
            rr = f'{float(rr):.1f}'
        except (TypeError, ValueError):
            raise ValueError('RR을 확인하세요.') from None
        if rr not in keys:
            raise ValueError('RR을 확인하세요.')
    chosen = rr or default
    rows = np.flatnonzero(mask & (trades.rr == float(chosen))) if chosen else np.zeros(0, np.int64)
    slots, picked = time_slots(trades, rows)
    return {'basis': 'KST alert time', 'periods': periods(trades), 'period': period or 'all',
            'months': len(months), 'rr_table': table, 'default_rr': default, 'rr': chosen,
            'detail': detail(trades, rows), 'slots': slots, 'accounts': accounts(trades, rows, picked),
            'start_balance': START_BALANCE}
