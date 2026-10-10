"""165: the result screen's values, read from a run's trade rows (Part2 result_analysis)."""
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import result_analysis as ra
from event_backtest.analytics import analyze_rows

FIELDS = ['signal_id', 'strategy', 'symbol', 'tf', 'alert_time', 'direction', 'entry_time', 'rr', 'result', 'exit_time', 'r']
DAY = 86400 * 1000
HOUR = 3600 * 1000
BASE = int(datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 09:00 KST


def row(signal, rr, result, r=None, alert=BASE, entry=None, exit_=None, direction='LONG', strategy='SPECIAL1',
        symbol='XAUUSD+', tf='1m'):
    entered = result in ('WIN', 'LOSS', 'UNCLOSED', 'UNCERTAIN')
    if entered and entry is None:
        entry = alert
    if result in ('WIN', 'LOSS', 'UNCERTAIN') and exit_ is None:
        exit_ = (entry or alert) + 60_000
    return {'signal_id': str(signal), 'strategy': strategy, 'symbol': symbol, 'tf': tf, 'alert_time': str(alert),
            'direction': direction, 'entry_time': '' if entry is None else str(entry), 'rr': str(rr), 'result': result,
            'exit_time': '' if exit_ is None else str(exit_), 'r': '' if r is None else str(r)}


def write(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return ra.load_trades(path)


def closed(rr, values, start=BASE, step=HOUR, **extra):
    """Closed trades of one RR in time order, one per `step`."""
    return [row(f'{rr}-{index}', rr, 'WIN' if value > 0 else 'LOSS', value, start + index * step, **extra)
            for index, value in enumerate(values)]


def test_summary_matches_the_stored_analytics_for_every_result_kind(tmp_path):
    rng = random.Random(165)
    kinds = ['WIN', 'LOSS', 'UNCLOSED', 'UNCERTAIN', 'WAITING', 'BLOCKED', 'EXPIRED', 'PASS_ENV', 'PASS_RISK']
    rows = []
    for index in range(400):
        rr = rng.choice([1.0, 2.5])
        kind = rng.choice(kinds)
        r = rr if kind == 'WIN' else -1.0 if kind == 'LOSS' else None
        alert = BASE + rng.randrange(0, 90 * DAY)
        exit_ = alert + rng.randrange(60_000, DAY) if kind in ('WIN', 'LOSS', 'UNCERTAIN') else None
        rows.append(row(index, rr, kind, r, alert, exit_=exit_, direction=rng.choice(['LONG', 'SHORT'])))
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    stored = analyze_rows(rows)['rr_results']
    shown = ra.view(trades)
    assert [item['rr'] for item in shown['rr_table']] == list(stored)
    for item in shown['rr_table']:
        before = stored[item['rr']]
        for key, value in before['summary'].items():
            assert item['summary'][key] == (pytest.approx(value) if isinstance(value, float) else value), key
        assert item['values']['max_drawdown_r'] == pytest.approx(before['drawdown']['max_drawdown_r'])
        assert item['values']['max_consecutive_losses'] == before['streaks']['max_consecutive_losses']


def test_metrics_follow_their_definitions(tmp_path):
    values = [2, -1, -1, 2, -1, 2, 2, -1]
    trades = write(tmp_path / 'virtual_trades.csv', closed(2.0, values))
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '2.0']
    shown = item['values']
    deviation = math.sqrt(sum((value - 0.5) ** 2 for value in values) / 7)
    assert shown['total_r'] == 4 and shown['average_r'] == 0.5 and shown['win_rate'] == 0.5
    assert shown['profit_factor'] == 2.0                     # 8 R won / 4 R lost
    assert shown['max_drawdown_r'] == 2.0                    # equity 2 -> 0
    assert shown['recovery_factor'] == 2.0                   # 4 R / 2 R
    assert shown['max_consecutive_losses'] == 2
    assert shown['sqn'] == pytest.approx(math.sqrt(8) * 0.5 / deviation)
    assert shown['t'] == pytest.approx(math.sqrt(8) * 0.5 / deviation)
    assert shown['kelly'] == pytest.approx(0.5 - 0.5 / 2.0)  # p - q / payoff
    assert not item['significant'] and item['grades']['profit_factor'] == 'excellent'


def test_sqn_counts_at_most_a_hundred_trades_and_t_counts_them_all(tmp_path):
    values = [3, -1, -1] * 70
    trades = write(tmp_path / 'virtual_trades.csv', closed(3.0, values, step=60_000))
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '3.0']
    mean, deviation = np.mean(values), np.std(values, ddof=1)
    assert item['values']['sqn'] == pytest.approx(10 * mean / deviation)
    assert item['values']['t'] == pytest.approx(math.sqrt(210) * mean / deviation)
    assert item['significant'] == (item['values']['t'] >= 2)


def test_monthly_sharpe_and_sortino_need_six_months_and_count_empty_months(tmp_path):
    def month(year, number):
        return int(datetime(year, number, 10, tzinfo=timezone.utc).timestamp() * 1000)
    sums = {(2025, 1): 3, (2025, 2): -1, (2025, 3): 0, (2025, 4): 2, (2025, 5): 2, (2025, 6): -1, (2025, 7): 1}
    rows = []
    for (year, number), total in sums.items():
        if total:
            rows += closed(1.0, [total], start=month(year, number))
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '1.0']
    monthly = np.array(list(sums.values()), dtype=float)     # March has no trade: a zero month
    assert item['values']['sharpe'] == pytest.approx(monthly.mean() / monthly.std(ddof=1) * math.sqrt(12))
    downside = math.sqrt(np.mean(np.minimum(monthly, 0) ** 2))
    assert item['values']['sortino'] == pytest.approx(monthly.mean() / downside * math.sqrt(12))
    rows = [entry for entry in rows if int(entry['alert_time']) < month(2025, 6)]
    trades = write(tmp_path / 'short.csv', rows)
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '1.0']
    assert item['values']['sharpe'] is None and item['values']['sortino'] is None   # five months


@pytest.mark.parametrize('name,value,expected', [
    ('profit_factor', 0.99, 'poor'), ('profit_factor', 1.0, 'weak'), ('profit_factor', 1.49, 'weak'),
    ('profit_factor', 1.5, 'good'), ('profit_factor', 2.0, 'excellent'),
    ('recovery_factor', -0.5, 'poor'), ('recovery_factor', 0.0, 'poor'), ('recovery_factor', 1.9, 'weak'),
    ('recovery_factor', 2.0, 'fair'), ('recovery_factor', 3.0, 'good'), ('recovery_factor', 5.0, 'excellent'),
    ('sqn', 0.0, 'poor'), ('sqn', 1.59, 'weak'), ('sqn', 1.6, 'fair'), ('sqn', 2.5, 'good'), ('sqn', 3.0, 'excellent'),
    ('sharpe', 0.0, 'poor'), ('sharpe', 0.5, 'weak'), ('sharpe', 1.0, 'good'), ('sharpe', 2.0, 'excellent'),
    ('sortino', 0.5, 'weak'), ('sortino', 1.0, 'fair'), ('sortino', 2.0, 'good'), ('sortino', 3.0, 'excellent'),
    ('profit_factor', None, None), ('win_rate', 0.6, None)])
def test_grades_use_the_common_bounds(name, value, expected):
    assert ra.grade(name, value) == expected


def test_the_recovery_factor_is_graded_from_a_year_and_a_great_sqn_is_suspect(tmp_path):
    pattern = [1] * 7 + [-1]                                   # average 0.75, deviation about 0.71: SQN over 10
    trades = write(tmp_path / 'virtual_trades.csv', closed(1.0, pattern * 15, step=DAY))   # four months
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '1.0']
    assert item['values']['recovery_factor'] is not None and item['grades']['recovery_factor'] is None
    assert item['values']['sqn'] >= ra.SQN_SUSPECT and item['suspect']
    trades = write(tmp_path / 'year.csv', closed(1.0, pattern * 50, step=DAY))
    [item] = [entry for entry in ra.view(trades)['rr_table'] if entry['rr'] == '1.0']
    assert item['grades']['recovery_factor'] is not None


def test_periods_are_korean_time(tmp_path):
    last_of_2024 = int(datetime(2024, 12, 31, 14, 59, 59, 999000, tzinfo=timezone.utc).timestamp() * 1000)
    first_of_2025 = last_of_2024 + 1                               # 2025-01-01 00:00 KST
    trades = write(tmp_path / 'virtual_trades.csv', closed(1.0, [1], start=last_of_2024) +
                   [row('b', 1.0, 'LOSS', -1, first_of_2025)])
    assert ra.periods(trades) == {'years': ['2024', '2025'], 'months': ['2024-12', '2025-01']}
    assert ra.period_window('2025') == (first_of_2025, int(datetime(2025, 12, 31, 15, tzinfo=timezone.utc).timestamp() * 1000))
    for period, total in (('2024', 1), ('2024-12', 1), ('2025', -1), ('2025-01', -1), ('all', 0)):
        [item] = [entry for entry in ra.view(trades, period)['rr_table'] if entry['rr'] == '1.0']
        assert item['values']['total_r'] == total and item['trades'] == (2 if period == 'all' else 1)
    for period in ('2025-13', '25', '2025-1', '2023', '2026-01'):
        with pytest.raises(ValueError):
            ra.view(trades, period)


def test_the_best_rr_is_the_largest_total_r_the_edge_supports():
    def entry(rr, total, significant, trades=10):
        return {'rr': rr, 'trades': trades, 'values': {'total_r': total}, 'significant': significant}
    assert ra.best_rr([entry('1.0', 30, False), entry('2.0', 20, True), entry('3.0', 25, True)]) == '3.0'
    assert ra.best_rr([entry('1.0', 30, False), entry('2.0', 20, False)]) == '1.0'
    assert ra.best_rr([entry('1.0', 20, True), entry('2.0', 20, True)]) == '1.0'   # a tie: the smaller RR
    assert ra.best_rr([entry('1.0', 0, False, 0), entry('2.0', -5, False)]) == '2.0'


def at(day, hour_kst, minute=0):
    return BASE + day * DAY + (hour_kst - 9) * HOUR + minute * 60_000


def test_hours_with_a_positive_average_and_enough_trades_are_chosen(tmp_path):
    rows = []
    plan = {9: (30, 0.5), 10: (29, 1.0), 11: (40, -0.2), 23: (30, 0.1), 0: (30, 0.1)}
    for hour, (count, average) in plan.items():
        for index in range(count):
            r = average + 1.5 if index % 2 == 0 else average - 1.5   # a win and a loss averaging `average`
            rows.append(row(f'{hour}-{index}', 2.0, 'WIN' if r > 0 else 'LOSS', r, at(index, hour), direction='LONG'))
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    slots = ra.view(trades, rr='2.0')['slots']
    assert slots['hours'] == [0, 9, 23] and slots['ranges'] == [[23, 1], [9, 10]]   # 23-01, joined over midnight
    chosen = [r for hour in (0, 9, 23) for r in [float(item['r']) for item in rows if item['signal_id'].startswith(f'{hour}-')]]
    assert slots['chosen']['trades'] == 90 and slots['chosen']['total_r'] == pytest.approx(sum(chosen))
    assert slots['all']['trades'] == sum(count for count, _ in plan.values())
    table = {item['hour']: item for item in slots['table']}
    assert table[10]['trades'] == 29 and table[11]['average_r'] == pytest.approx(-0.2)


def test_the_check_chooses_on_the_first_seventy_percent_and_scores_the_rest(tmp_path):
    rows = []
    for day in range(100):
        late = day >= 70
        # 09 KST wins early and loses late; 15 KST the other way round.
        for hour, r in ((9, -1.0 if late else 1.0), (15, 1.0 if late else -1.0)):
            rows.append(row(f'{day}-{hour}', 1.0, 'WIN' if r > 0 else 'LOSS', r, at(day, hour)))
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    check = ra.view(trades, rr='1.0')['slots']['check']
    assert check['hours'] == [9] and check['ranges'] == [[9, 10]]
    assert check['chosen']['average_r'] == -1.0 and check['chosen']['trades'] == 30
    assert check['all']['average_r'] == 0.0 and check['all']['trades'] == 60


def test_the_account_risks_one_percent_of_the_balance_at_entry_and_books_at_exit(tmp_path):
    # The request's own example: 10,000 then +2R is 10,200, and the next 1R is 102.
    rows = [row('a', 2.0, 'WIN', 2.0, BASE, BASE, BASE + HOUR), row('b', 2.0, 'LOSS', -1.0, BASE + 2 * HOUR, BASE + 2 * HOUR, BASE + 3 * HOUR)]
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    account = ra.view(trades, rr='2.0')['accounts']['all']['one_percent']
    assert account['final'] == pytest.approx(10_098.0) and account['profit'] == pytest.approx(98.0)
    assert account['return'] == pytest.approx(0.0098) and account['max_drawdown'] == pytest.approx(102 / 10_200)
    assert account['max_open'] == 1 and account['trades'] == 2
    # Overlapping: the second trade enters before the first is booked, so its 1R is still 1% of 10,000.
    rows = [row('a', 2.0, 'WIN', 2.0, BASE, BASE, BASE + 3 * HOUR), row('b', 2.0, 'LOSS', -1.0, BASE + HOUR, BASE + HOUR, BASE + 4 * HOUR)]
    account = ra.view(write(tmp_path / 'overlap.csv', rows), rr='2.0')['accounts']['all']['one_percent']
    assert account['final'] == pytest.approx(10_100.0) and account['max_open'] == 2
    # A trade booked at the moment another enters: the booking comes first.
    rows = [row('a', 2.0, 'WIN', 2.0, BASE, BASE, BASE + HOUR), row('b', 2.0, 'WIN', 2.0, BASE + HOUR, BASE + HOUR, BASE + HOUR)]
    account = ra.view(write(tmp_path / 'touch.csv', rows), rr='2.0')['accounts']['all']['one_percent']
    assert account['final'] == pytest.approx(10_200.0 + 204.0) and account['max_open'] == 1


def test_a_quarter_kelly_account_and_no_account_without_an_edge(tmp_path):
    values = [2, -1, -1, 2, -1, 2, 2, -1]                   # Kelly 0.25: a quarter is 6.25%
    shown = ra.view(write(tmp_path / 'virtual_trades.csv', closed(2.0, values)), rr='2.0')['accounts']['all']
    assert shown['kelly'] == pytest.approx(0.25) and shown['quarter_kelly']['fraction'] == pytest.approx(0.0625)
    balance = 10_000.0
    for value in values:
        balance += balance * 0.0625 * value
    assert shown['quarter_kelly']['final'] == pytest.approx(balance)
    shown = ra.view(write(tmp_path / 'loss.csv', closed(2.0, [2, -1, -1, -1])), rr='2.0')['accounts']['all']
    assert shown['kelly'] < 0 and shown['quarter_kelly'] is None and shown['one_percent'] is not None


def test_curves_keep_their_extremes_within_the_point_limit():
    rng = np.random.default_rng(165)
    values = np.cumsum(rng.normal(size=20_000))
    times = np.arange(20_000) * 1000
    points = ra._curve(times, values)
    assert len(points) <= ra.CURVE_POINTS
    kept = {point[0] for point in points}
    assert {0, int(times[-1]), int(times[values.argmin()]), int(times[values.argmax()])} <= kept
    assert [point[0] for point in points] == sorted(kept)
    assert ra._curve(times[:10], values[:10]) == [[int(t), round(float(v), 6)] for t, v in zip(times[:10], values[:10])]


def test_the_view_is_json_safe_and_checks_its_rr(tmp_path):
    rows = closed(1.0, [1, -1, 1]) + closed(2.0, [2, -1, -1])
    trades = write(tmp_path / 'virtual_trades.csv', rows)
    shown = ra.view(trades)
    json.dumps(shown, allow_nan=False)
    assert shown['rr'] == shown['default_rr'] and len(shown['rr_table']) == 9
    assert ra.view(trades, rr='2')['rr'] == '2.0'
    for value in ('7', 'abc', '-1'):
        with pytest.raises(ValueError):
            ra.view(trades, rr=value)
    assert len(shown['detail']['equity']) == 3 and shown['detail']['monthly'][0]['month'] == '2026-01'
    assert shown['detail']['by_direction'] == {'LONG': shown['detail']['by_direction']['LONG']}


def test_a_run_without_trade_rows_still_has_a_view(tmp_path):
    trades = write(tmp_path / 'virtual_trades.csv', [])
    shown = ra.view(trades)
    json.dumps(shown, allow_nan=False)
    assert shown['periods'] == {'years': [], 'months': []} and all(item['trades'] == 0 for item in shown['rr_table'])
    assert shown['accounts']['all']['one_percent'] is None and shown['slots']['check'] is None
