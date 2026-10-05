"""The post-run analyzer uses existing virtual trade rows without replaying data."""
from __future__ import annotations

from pathlib import Path
import csv
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))
from event_backtest.analytics import analyze_csv, analyze_rows, analyze_completed_run


def trade(signal, *, rr=1.0, result='WIN', r=None, alert=0, entry=60_000,
          exit=120_000, direction='LONG', strategy='SPECIAL1', symbol='XAUUSD+', tf='1m'):
    if result in ('WAITING', 'BLOCKED', 'EXPIRED', 'PASS_RISK'):
        entry = exit = None
    elif result == 'UNCLOSED':
        exit = None
    if r is None and result == 'WIN':
        r = rr
    elif r is None and result == 'LOSS':
        r = -1.0
    return dict(signal_id=signal, strategy=strategy, symbol=symbol, tf=tf,
                alert_time=alert, direction=direction, entry_time=entry,
                entry_price=10 if entry is not None else None, stop_price=9,
                rr=rr, result=result, exit_time=exit, r=r, target=None)


def test_empty_one_trade_all_wins_and_all_losses():
    empty = analyze_rows([])['rr_results']
    assert len(empty) == 9
    assert empty['1.0']['summary']['total_trades'] == 0
    assert empty['1.0']['summary']['profit_factor'] is None
    assert empty['1.0']['equity_r_curve'] == []

    single = analyze_rows([trade('a')])['rr_results']['1.0']
    assert single['summary']['total_trades'] == 1
    assert single['summary']['wins'] == 1
    assert single['summary']['profit_factor'] is None
    assert single['drawdown']['max_drawdown_r'] == 0
    assert single['drawdown']['recovery_factor'] is None
    assert single['streaks']['max_consecutive_wins'] == 1
    assert single['summary']['average_holding_seconds'] == 60

    losses = analyze_rows([trade('a', result='LOSS'),
                           trade('b', result='LOSS', alert=1, exit=180_000)])['rr_results']['1.0']
    assert losses['summary']['profit_factor'] == 0
    assert losses['streaks']['max_consecutive_losses'] == 2
    assert losses['drawdown']['max_drawdown_r'] == 2


def test_rr_experiments_are_independent_and_drawdown_uses_exit_order():
    rows = [trade('a', result='WIN', exit=120_000),
            trade('b', result='LOSS', alert=1, exit=180_000),
            trade('c', result='LOSS', alert=2, exit=240_000),
            trade('d', result='WIN', alert=3, exit=300_000),
            trade('e', result='WIN', alert=4, exit=360_000),
            trade('a', rr=1.5, result='LOSS', exit=150_000)]
    result = analyze_rows(rows)['rr_results']
    one = result['1.0']
    assert one['summary']['alerts'] == 5
    assert one['summary']['total_trades'] == 5
    assert one['summary']['total_r'] == 1
    assert one['summary']['gross_profit_r'] == 3
    assert one['summary']['gross_loss_r'] == -2
    assert one['summary']['profit_factor'] == 1.5
    assert one['summary']['win_rate'] == .6
    assert one['summary']['expectancy_r'] == .2
    assert [point['equity_r'] for point in one['equity_r_curve']] == [1, 0, -1, 0, 1]
    assert [point['drawdown_r'] for point in one['drawdown_curve']] == [0, 1, 2, 1, 0]
    assert one['drawdown'] == {'max_drawdown_r': 2, 'recovery_factor': .5}
    assert one['streaks'] == {'max_consecutive_wins': 2, 'max_consecutive_losses': 2}
    assert result['1.5']['summary']['alerts'] == 1
    assert result['1.5']['summary']['total_r'] == -1


def test_unclosed_passes_and_all_group_totals():
    rows = [trade('a', result='UNCLOSED'),
            trade('b', result='BLOCKED', direction='SHORT', strategy='SPECIAL2',
                  symbol='BTCUSD', tf='5m', alert=86_400_000),
            trade('c', result='PASS_RISK', alert=2 * 86_400_000),
            trade('d', result='WAITING', alert=3 * 86_400_000),
            trade('e', result='LOSS', direction='SHORT', strategy='SPECIAL2',
                  symbol='BTCUSD', tf='5m', alert=4 * 86_400_000)]
    result = analyze_rows(rows)['rr_results']['1.0']
    summary = result['summary']
    assert summary['alerts'] == 5
    assert summary['total_trades'] == 2
    assert summary['unclosed'] == 1
    assert summary['entry_rate'] == .4
    assert summary['passes'] == {'blocked': 1, 'expired': 0, 'zero_stop_width': 1, 'waiting': 1}
    assert summary['average_holding_seconds'] == 60
    for name in ('by_direction', 'by_symbol', 'by_timeframe', 'by_strategy',
                 'monthly', 'weekday', 'hourly'):
        groups = result[name].values() if isinstance(result[name], dict) else result[name]
        assert sum(group['alerts'] for group in groups) == 5, name
        groups = result[name].values() if isinstance(result[name], dict) else result[name]
        assert sum(group['total_trades'] for group in groups) == 2, name
    json.dumps(result, allow_nan=False)


def test_uncertain_and_expired_are_separate_from_win_loss_unclosed():
    rows=[trade('a',result='UNCERTAIN'),trade('b',result='EXPIRED'),
          trade('c',result='WIN'),trade('d',result='LOSS'),trade('e',result='UNCLOSED')]
    result=analyze_rows(rows)['rr_results']['1.0']
    s=result['summary']
    assert (s['alerts'],s['total_trades'],s['wins'],s['losses'],s['uncertain'],s['unclosed'])==(5,4,1,1,1,1)
    assert s['win_rate']==.5 and s['average_r']==0 and s['total_r']==0
    assert s['passes']['expired']==1
    assert len(result['equity_r_curve'])==2 and s['average_holding_seconds']==60
    assert sum(g['uncertain'] for g in result['by_strategy'].values())==1


def test_existing_trade_csv_is_unchanged_and_path_is_relative(tmp_path):
    run_id = 'a' * 32
    folder = tmp_path / 'runs' / run_id
    folder.mkdir(parents=True)
    path = folder / 'virtual_trades.csv'
    row = trade('a')
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['run_id', *row])
        writer.writeheader()
        writer.writerow({'run_id': run_id, **row})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = {'run_id': run_id, 'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY',
              'virtual_entry': {'trades_csv': f'runs/{run_id}/virtual_trades.csv'}}
    analyzed = analyze_completed_run(result, tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert analyzed['analytics_path'] == f'runs/{run_id}/analytics.json'
    assert json.loads((folder / 'analytics.json').read_text('utf-8'))['rr_results']['1.0']['summary']['wins'] == 1
    assert json.loads((folder / 'result.json').read_text('utf-8'))['analytics_path'] == analyzed['analytics_path']
    relocated = tmp_path / 'relocated'
    shutil.copytree(tmp_path / 'runs', relocated / 'runs')
    moved_result = json.loads((relocated / 'runs' / run_id / 'result.json').read_text('utf-8'))
    assert json.loads((relocated / moved_result['analytics_path']).read_text('utf-8')) == analysis_from_run(analyzed, tmp_path)


def analysis_from_run(result, root):
    return json.loads((root / result['analytics_path']).read_text('utf-8'))


def test_not_complete_or_not_virtual_never_reads_or_writes(tmp_path):
    for status, mode in [('CANCELLED', 'VIRTUAL_ENTRY'), ('COMPLETE', 'ALERT_ONLY')]:
        result = {'status': status, 'result_mode': mode, 'run_id': 'a' * 32}
        assert analyze_completed_run(result, tmp_path) == result
    assert not list(tmp_path.rglob('*'))


def test_empty_csv_and_invalid_input(tmp_path):
    path = tmp_path / 'virtual_trades.csv'
    path.write_text('run_id,signal_id,strategy,alert_time,direction,entry_time,entry_price,stop_price,rr,result,exit_time,r\n', encoding='utf-8')
    assert analyze_csv(path)['rr_results']['5.0']['summary']['alerts'] == 0
    try:
        analyze_rows([trade('a', result='WIN', r=float('nan'))])
    except ValueError:
        pass
    else:
        raise AssertionError('non-finite R must be rejected')
    try:
        analyze_rows([trade('a', result='LOSS', r=1.0)])
    except ValueError:
        pass
    else:
        raise AssertionError('loss with positive R must be rejected')


def test_analysis_is_wired_after_the_runner_returns():
    workflow = (ROOT / 'Part2' / 'event_backtest' / 'workflow.py').read_text('utf-8')
    runner = (ROOT / 'Part2' / 'event_backtest' / 'runner.py').read_text('utf-8')
    assert workflow.index('result=run(') < workflow.index('analyze_completed_run(result,warehouse)')
    assert 'analyze_completed_run' not in runner


if __name__ == '__main__':
    from tempfile import TemporaryDirectory
    test_empty_one_trade_all_wins_and_all_losses()
    test_rr_experiments_are_independent_and_drawdown_uses_exit_order()
    test_unclosed_passes_and_all_group_totals()
    with TemporaryDirectory() as directory:
        test_existing_trade_csv_is_unchanged_and_path_is_relative(Path(directory))
    with TemporaryDirectory() as directory:
        test_not_complete_or_not_virtual_never_reads_or_writes(Path(directory))
    with TemporaryDirectory() as directory:
        test_empty_csv_and_invalid_input(Path(directory))
    test_analysis_is_wired_after_the_runner_returns()
    print('7 analytics tests passed')
