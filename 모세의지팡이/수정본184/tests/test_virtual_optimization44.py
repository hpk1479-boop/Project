"""Revision44: fixed targets, observation-local input, and ratio-independent counts."""
from pathlib import Path
import csv
import json
import socket
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/optimization44'))
from support44 import alert, frame, write_alerts, config, oz_policy, PROJECTED
from event_backtest import virtual_entry as ve
from event_backtest import virtual_source
from event_backtest.cancellation import Cancelled


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network is allowed in synthetic validation.')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def oz_entry(alerts, spread=0.):
    """Every fixture alert is an OZ signal of an OZ recipe's entry."""
    return ve.VirtualEntry(alerts, spread, oz_policy())


def observe_rows(calc, rows, *, projected=False, end=86_400_000):
    for i, row in enumerate(rows):
        calc.observe(row[0] * 1000, {'1m': frame(rows[:i + 1], projected=projected)}, end_ms=end)
    return calc


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
@pytest.mark.parametrize('spread', [0., .125, 1.])
@pytest.mark.parametrize('projected', [False, True])
def test_targets_use_exact_existing_formula_after_entry(direction, spread, projected):
    fast, slow = (9., 8.) if direction == 'LONG' else (11., 12.)
    stop = 8. if direction == 'LONG' else 12.
    rows = [(0, 10., 11., 9., fast, slow), (60, 10., 11., 9., fast, slow)]
    calc = observe_rows(oz_entry([alert(direction=direction, stop=stop)], spread), rows,
                        projected=projected)
    trade = calc.trades[0]
    entry = 10. + spread if direction == 'LONG' else 10.
    risk = abs(entry - stop)
    assert trade['status'] == 'ENTERED' and trade['entry_time'] == 60_000
    assert (trade['entry_price'], trade['risk'], trade['stop_price']) == (entry, risk, stop)
    assert trade['targets'] == {rr: entry + (1 if direction == 'LONG' else -1) * rr * risk
                               for rr in ve.RATIOS}
    # Precomputed internal targets must not leak into the unclosed CSV columns.
    summaries, details = calc.results()
    assert all(row['unclosed'] == 1 for row in summaries)
    assert all(row['target'] is None and row['result'] == 'UNCLOSED' for row in details)


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_target_prices_prepared_once_not_per_closed_minute(monkeypatch, direction):
    # 178 uses decimal source prices: verify one-time preparation, not float.__rmul__ calls.
    prepared = []
    decimal = ve.Decimal
    def counting_decimal(value):
        prepared.append(value)
        return decimal(value)
    monkeypatch.setattr(ve, 'Decimal', counting_decimal)
    fast, slow = (9., 8.) if direction == 'LONG' else (11., 12.)
    calc = oz_entry([alert(direction=direction, stop=0. if direction == 'LONG' else 20.)])
    rows = [(i * 60, 10., 11., 9., fast, slow) for i in range(10)]
    observe_rows(calc, rows[:2])
    at_entry = list(prepared)
    assert at_entry
    for i in range(2, len(rows)):
        calc.observe(rows[i][0] * 1000, {'1m': frame(rows[:i + 1])}, end_ms=86_400_000)
    assert calc.trades[0]['status'] == 'ENTERED'
    assert not calc.trades[0]['exits'] and len(calc.open) == 1
    assert prepared == at_entry


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['stop_and_target', 'target_only'])
def test_stop_priority_and_independent_ratio_exits(direction, kind):
    fast, slow = (9., 8.) if direction == 'LONG' else (11., 12.)
    stop = 8. if direction == 'LONG' else 12.
    high, low = ((21., -1.) if kind == 'stop_and_target'
                 else ((13., 9.) if direction == 'LONG' else (11., 7.)))
    rows = [(0, 10., 11., 9., fast, slow), (60, 10., high, low, fast, slow),
            (120, 10., 11., 9., fast, slow)]
    calc = observe_rows(oz_entry([alert(direction=direction, stop=stop)]), rows)
    summaries, details = calc.results()
    if kind == 'stop_and_target':
        assert all(x['losses'] == 1 and x['total_r'] == -1 for x in summaries)
        assert not calc.open and 'targets' not in calc.trades[0]
    else:
        assert [x['wins'] for x in summaries] == [1, 1, 0, 0, 0, 0, 0, 0, 0]
        assert len(calc.open) == 1 and len(calc.trades[0]['targets']) == 9
    assert all(x['exit_time'] == 60_000 for x in details if x['result'] in ('WIN', 'LOSS'))


@pytest.mark.parametrize('stamp,closed', [(119_999, False), (120_000, True), (120_001, True)])
def test_m1_close_boundary_is_not_advanced_by_precomputed_targets(stamp, closed):
    rows = [(0, 10., 11., 9., 9., 8.), (60, 10., 30., 7., 9., 8.)]
    calc = observe_rows(oz_entry([alert()]), rows)
    calc.observe(stamp, {'1m': frame(rows)}, end_ms=86_400_000)
    assert bool(calc.trades[0]['exits']) == closed


@pytest.mark.parametrize('end', [59_999, 60_000, 60_001])
def test_entry_end_boundary_remains_exclusive(end):
    rows = [(0, 10., 11., 9., 9., 8.), (60, 10., 11., 9., 9., 8.)]
    calc = observe_rows(oz_entry([alert()]), rows, end=end)
    assert (calc.trades[0]['entry_time'] == 60_000) == (end > 60_000)


@pytest.mark.parametrize('case', ['risk', 'waiting'])
def test_no_target_preparation_for_non_entries(case):
    # Waiting: the confirmation candle closed below HMA6 (11).
    first = (0, 10., 11., 9., 9., 8.) if case == 'risk' else (0, 10., 11., 9., 11., 8.)
    calc = observe_rows(oz_entry([alert(stop=10. if case == 'risk' else 8.)]),
                        [first, (60, 10., 11., 9., 9., 8.)])
    expected = {'risk': 'PASS_RISK', 'waiting': 'WAITING'}[case]
    assert calc.trades[0]['status'] == expected and 'targets' not in calc.trades[0]


@pytest.mark.parametrize('field', ['open', 'hma_6', 'high', 'low'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 1.e101])
def test_existing_invalid_native_values_still_raise(field, bad):
    item = frame([(0, 10., 11., 9., 9., 8.), (60, 10., 11., 9., 9., 8.)], projected=True)
    # Row 0 is the confirmation candle: its OHLC and its HMA6 are what the entry reads.
    item.values[0, PROJECTED.index(field)] = bad
    calc = oz_entry([alert()])
    with pytest.raises(ValueError):
        calc.observe(60_000, {'1m': item}, end_ms=86_400_000)


@pytest.mark.parametrize('missing', ['alert_tf', 'm1'])
def test_missing_required_feed_is_not_hidden(missing):
    calc = oz_entry([alert(tf='5m')])
    data = frame([(0, 10., 11., 9., 9., 8.)])
    with pytest.raises(ValueError, match='타임프레임|1분봉'):
        calc.observe(0, {'1m': data} if missing == 'alert_tf' else {'5m': data}, end_ms=100_000)


def test_common_summary_counts_are_per_strategy_and_refreshed_each_call():
    calc = oz_entry([])
    rows = [
        ('Z', 'ENTERED', {1.: dict(result='WIN', r=1., exit_time=120_000, target=12.)}),
        ('A', 'BLOCKED', {}), ('Z', 'PASS_RISK', {}), ('A', 'WAITING', {}),
        ('A', 'ENTERED', {1.: dict(result='LOSS', r=-1., exit_time=120_000, target=12.)}),
        ('Z', 'ENTERED', {}),
    ]
    for i, (strategy, status, exits) in enumerate(rows):
        entered = status == 'ENTERED'
        calc.trades.append(dict(alert=alert(str(i), strategy=strategy), status=status,
                                entry_time=60_000 if entered else None, entry_price=10. if entered else None,
                                stop_price=8., exits=exits,policy={'mode':'CONFIRM'},blocked_checks=0))
    summary, details = calc.results(partial=True)
    assert [s['strategy'] for s in summary] == ['A'] * 9 + ['Z'] * 9
    for s in summary[:9]:
        assert (s['alerts'], s['entries'], s['pass_blocked'], s['pass_risk'], s['waiting']) == (3, 1, 1, 0, 1)
    for s in summary[9:]:
        assert (s['alerts'], s['entries'], s['pass_blocked'], s['pass_risk'], s['waiting']) == (3, 2, 0, 1, 0)
    assert summary[0]['losses'] == 1 and summary[0]['average_r'] == -1.
    assert summary[9]['wins'] == 1 and summary[9]['unclosed'] == 1
    assert summary[1]['win_rate'] is None and summary[1]['average_r'] is None
    assert [d['signal_id'] for d in details] == [str(i) for i in range(6) for _ in ve.RATIOS]
    calc.trades[3]['status'] = 'BLOCKED'
    again, _ = calc.results(partial=True)
    assert again[0]['pass_blocked'] == 2 and again[0]['waiting'] == 0
    assert summary[0]['pass_blocked'] == 1 and summary[0]['waiting'] == 1


def execute_fake(tmp_path, monkeypatch, alerts, observations, *, cancel=lambda: None,
                 emit=lambda *args: None, scenario=None):
    closed = []
    def stream(*args, check=lambda: None, **kwargs):
        try:
            for item in observations:
                check()
                yield item
        finally:
            closed.append(True)
    monkeypatch.setattr(virtual_source, 'shared_observations', stream)
    path = write_alerts(tmp_path / 'alerts.csv', alerts)
    result = ve.calculate(path, [], tmp_path, scenario or config(), {'POINT_XAUUSD+': '.01'},
                          tmp_path, cancel=cancel, emit=emit)
    return result, closed


@pytest.mark.parametrize('symbol_count', [1, 2])
def test_each_signal_keeps_its_own_state_and_the_input_is_unchanged(tmp_path, monkeypatch, symbol_count):
    symbols = ['XAUUSD+', 'OTHER'][:symbol_count]
    alerts = [alert(str(i), symbol=symbols[i % symbol_count], stop=0.) for i in range(6)]
    feeds = {(symbol, '1m'): frame([(0, 10., 11., 9., 9., 8.)]) for symbol in symbols}
    source_values = dict(feeds)
    seen = []
    original = ve.VirtualEntry.observe
    def observing(self, stamp, inputs, **kwargs):
        seen.append((self, inputs))
        return original(self, stamp, inputs, **kwargs)
    monkeypatch.setattr(ve.VirtualEntry, 'observe', observing)
    result, closed = execute_fake(tmp_path, monkeypatch, alerts, [(0, feeds)])
    assert result['observed_signals'] == 6 and result['read_bundles'] == 1 and closed == [True]
    for symbol in symbols:
        members = [(c, f) for c, f in seen if c.alerts[0]['symbol'] == symbol]
        assert len({id(c.views) for c, f in members}) == len(members)
        assert len({id(c.trades[0]) for c, f in members}) == len(members)
    assert dict(feeds) == source_values


def test_feed_cache_refreshes_on_same_stamp_and_mutable_registry(tmp_path, monkeypatch):
    original = ve.VirtualEntry.observe
    retained = []
    def observing(self, stamp, inputs, **kwargs):
        retained.append((stamp, inputs))
        return original(self, stamp, inputs, **kwargs)
    monkeypatch.setattr(ve.VirtualEntry, 'observe', observing)
    def observations():
        registry = {('XAUUSD+', '1m'): frame([(0, 10., 11., 9., 9., 8.)])}
        yield 0, registry
        registry[('XAUUSD+', '1m')] = frame([(0, 10.25, 11., 9., 9., 8.)])
        yield 0, registry  # same time, a new ROW revision
    execute_fake(tmp_path, monkeypatch, [alert('a'), alert('b')], observations())
    first, second = retained[0][1], retained[2][1]
    assert first is retained[1][1] and second is retained[3][1] and first is not second
    assert first['1m'].values[0, 0] == 10. and second['1m'].values[0, 0] == 10.25


@pytest.mark.parametrize('stop_at', [1, 3, 4, 5, 6, 7, 8, 11])
def test_cancel_checks_preserve_processed_prefix_and_close_source(tmp_path, monkeypatch, stop_at):
    checks = []
    def cancel():
        checks.append(True)
        return len(checks) >= stop_at
    data = frame([(0, 10., 11., 9., 9., 8.)])
    inputs = [alert(str(i)) for i in range(3)]
    result, closed = execute_fake(tmp_path, monkeypatch, inputs,
                                 [(0, {('XAUUSD+', '1m'): data}), (1_000, {('XAUUSD+', '1m'): data})],
                                 cancel=cancel)
    expected_observed = max(0, min(3, stop_at - 4))
    assert result['cancelled'] and result['observed_signals'] == expected_observed
    assert result['processed_signals'] == 0
    assert bool(closed) == (stop_at > 1)
    with (tmp_path / 'virtual_trades.csv').open(encoding='utf-8-sig') as stream:
        details = list(csv.DictReader(stream))
    assert [d['signal_id'] for d in details] == [str(i) for i in range(expected_observed) for _ in ve.RATIOS]


def test_empty_alerts_keep_selected_strategies_without_reading(tmp_path, monkeypatch):
    def source(*args, **kwargs):
        pytest.fail('An empty alert file must not open the recording stream.')
    monkeypatch.setattr(virtual_source, 'shared_observations', source)
    path = write_alerts(tmp_path / 'alerts.csv', [])
    result = ve.calculate(path, [], tmp_path, dict(config(), strategies=['ALL']),
                          {'POINT_XAUUSD+': '.01'}, tmp_path)
    assert result['read_bundles'] == 0 and result['eligible_signals'] == 0
    from strategy_recipe.registry import list_presets
    # One row per registered strategy and RR.
    assert len(result['summary']) == len(list_presets('Part2')) * len(ve.RATIOS)
    assert all(s['alerts'] == 0 and s['win_rate'] is None for s in result['summary'])


def test_full_calculate_independent_completion_duplicate_notice_and_csv_order(tmp_path, monkeypatch):
    source = [alert('first', stop=0., strategy='SPECIAL2'), alert('second'),
              alert('second'), dict(alert('notice'), direction='')]
    rows = [(0, 10., 11., 9., 9., 8.), (60, 10., 30., 7., 9., 8.),
            (120, 10., 11., 9., 9., 8.), (180, 10., 11., -1., 9., 8.),
            (240, 10., 11., 9., 9., 8.)]
    observations = [(r[0] * 1000, {('XAUUSD+', '1m'): frame(rows[:i + 1], projected=True)})
                    for i, r in enumerate(rows)]
    result, closed = execute_fake(tmp_path, monkeypatch, source, observations,
                                 scenario=dict(config(), strategies=['SPECIAL1', 'SPECIAL2', 'SPECIAL3']))
    assert result['eligible_signals'] == 2 and result['non_entry_notices'] == 1
    assert result['processed_signals'] == 2 and closed == [True]
    with (tmp_path / 'virtual_trades.csv').open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    assert [r['signal_id'] for r in rows] == ['first'] * 9 + ['second'] * 9
    assert all(r['result'] == 'LOSS' and r['exit_time'] == '60000' for r in rows[9:])
    assert rows[0]['result'] == 'WIN' and rows[0]['target'] == '20.0'
    assert rows[-1]['stop_price'] == '8.0'
    assert [s['strategy'] for s in result['summary']] == ['SPECIAL1'] * 9 + ['SPECIAL2'] * 9 + ['SPECIAL3'] * 9
    assert all(s['alerts'] == 0 for s in result['summary'][-9:])
    assert not any('targets' in r for r in rows)
    assert not Path(result['summary_csv']).is_absolute() and not Path(result['trades_csv']).is_absolute()


def test_unobserved_future_alert_still_fails_non_partial_calculation(tmp_path, monkeypatch):
    data = frame([(0, 10., 11., 9., 9., 8.)])
    with pytest.raises(ValueError, match='기간 끝'):
        execute_fake(tmp_path, monkeypatch, [alert('now'), alert('future', time_ms=600_000)],
                     [(0, {('XAUUSD+', '1m'): data})])
