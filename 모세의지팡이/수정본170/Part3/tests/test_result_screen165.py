"""165: the result screen is computed on the server from the run's trade rows, kept for the last runs opened."""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / name) for name in ('Part3', 'Part2', 'Part1/program', 'verification')]
from lab import backtest_results, backtest_sequences, unified_backtest
from event_backtest import result_analysis
from dashboard63_fixtures import create_case


@pytest.fixture
def run(tmp_path):
    backtest_results._TRADES.clear()
    job, _ = create_case(tmp_path / '창고', 'normal')
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        yield job


def test_the_result_carries_the_screen_not_the_stored_analytics(run):
    shown = unified_backtest.result(run['id'])
    assert 'analytics' not in shown and shown['analytics_status'] == 'ready'
    screen = shown['analysis']
    assert screen['rr'] == screen['default_rr'] and screen['period'] == 'all'
    assert {'rr_table', 'detail', 'slots', 'accounts', 'periods'} <= set(screen)
    assert screen['periods']['months'] == ['2026-01', '2026-02', '2026-03', '2026-04']
    assert 'analytics' in {file['kind'] for file in shown['files']}        # still an original file
    assert unified_backtest.download(run['id'], 'analytics').is_file()


def test_another_period_or_rr_is_computed_on_request(run):
    screen = unified_backtest.analysis(run['id'], '2026-02', '1')
    assert screen['period'] == '2026-02' and screen['rr'] == '1.0'
    [row] = [item for item in screen['rr_table'] if item['rr'] == '1.0']
    assert row['trades'] == 2                                                # 10 and 20 February
    for period, rr in (('2026-13', None), ('2025', None), ('all', '7'), ('all', 'x')):
        with pytest.raises(ValueError):
            unified_backtest.analysis(run['id'], period, rr)


def test_parsed_trades_are_kept_until_the_file_changes(run, monkeypatch, tmp_path):
    loads = []
    real = result_analysis.load_trades
    monkeypatch.setattr(result_analysis, 'load_trades', lambda path: loads.append(path) or real(path))
    unified_backtest.analysis(run['id'])
    unified_backtest.analysis(run['id'], '2026')
    unified_backtest.result(run['id'])
    assert len(loads) == 1
    trades = run['folder'] / 'virtual_trades.csv'
    with trades.open('a', encoding='utf-8', newline='') as handle:
        handle.write('99,SPECIAL1,XAUUSD+,1m,1767225600000,LONG,1767225601000,2000,1990,1.0,WIN,1767225660000,1.0\n')
    screen = unified_backtest.analysis(run['id'], rr='1.0')
    assert len(loads) == 2 and [row for row in screen['rr_table'] if row['rr'] == '1.0'][0]['trades'] == 13
    others = []
    for index in (1, 2):
        job, _ = create_case(tmp_path / f'other{index}', 'normal', index)
        others.append(job)
    with patch.dict(unified_backtest.JOBS, {job['id']: job for job in [run, *others]}, clear=True):
        for job in others:
            unified_backtest.analysis(job['id'])
        assert len(backtest_results._TRADES) == backtest_results._KEEP


def test_trade_pages_follow_the_korean_period(run):
    page = unified_backtest.trades(run['id'], '1.0', 0, 100, '2026-02')
    assert [row['signal_id'] for row in page['rows']] == ['4', '5']          # 2026-02-10 and 02-20, Korean time
    assert [row['signal_id'] for row in unified_backtest.trades(run['id'], '1.0', 0, 100, 'all')['rows']] == \
        [str(index) for index in range(12)]
    with pytest.raises(ValueError):
        unified_backtest.trades(run['id'], '1.0', 0, 100, '2026-2')


def test_a_sequence_keeps_its_rr_summaries(run):
    summary = backtest_sequences._summary(unified_backtest.result(run['id']))
    stored = json.loads((run['folder'] / 'analytics.json').read_text('utf-8'))['rr_results']
    assert set(summary['by_rr']) == set(stored)
    for key, item in stored.items():
        for name, value in item['summary'].items():
            assert summary['by_rr'][key][name] == (pytest.approx(value) if isinstance(value, float) else value)


def test_the_screen_after_moving_the_warehouse(run, tmp_path):
    before = unified_backtest.result(run['id'])
    moved = tmp_path / '옮긴 창고'
    shutil.copytree(run['warehouse'], moved)
    job = {**run, 'warehouse': moved, 'folder': moved / 'runs' / run['id']}
    with patch.dict(unified_backtest.JOBS, {run['id']: job}, clear=True):
        assert unified_backtest.result(run['id']) == before
        shutil.rmtree(run['warehouse'])                                       # nothing reads the old place
        assert unified_backtest.analysis(run['id'], '2026-03')['period'] == '2026-03'
