"""A backtest result is offered once Part2 has written it whole, a virtual-entry result once its request
has ended.

Part2 writes each run's result.json after that run's virtual entry; a request with several base frames
finishes its runs one after another and holds the others until it has ended (수정본163). The screen
reads the result as soon as the status says it is ready and never reads it again. Since 수정본165 the
screen computes its analysis from the run's trades, and since 수정본170 no analysis file follows
result.json: a complete virtual-entry result opens when the job's processes have ended, as before,
without waiting for an analysis.
"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'Part3')]
from lab import backtest_jobs


def job(folder, *, active):
    return {'folder': folder, 'active': active, 'phase': 'run' if active else 'complete', 'kind': 'normal',
            'message': '', 'cancel_requested': False, 'progress': None, 'plan': None, 'identity_warning': False,
            'scenario': {'strategies': ['SPECIAL1'], 'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01',
                         'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY'}}


def write(folder, **fields):
    data = {'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY', **fields}
    (folder / 'result.json').write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def folder(tmp_path):
    return tmp_path


def test_no_result_file_is_not_ready(folder):
    assert backtest_jobs._result_ready(job(folder, active=True)) is False
    assert backtest_jobs._result_ready(job(folder, active=False)) is False


def test_a_virtual_entry_result_opens_when_its_request_has_ended(folder):
    write(folder)
    assert backtest_jobs._result_ready(job(folder, active=True)) is False
    assert backtest_jobs._result_ready(job(folder, active=False)) is True


@pytest.mark.parametrize('fields', [{'status': 'CANCELLED'}, {'result_mode': 'ALERT_ONLY'}, {'build_only': True},
                                    {'status': 'FAILED'}])
def test_other_written_results_are_ready_at_once(folder, fields):
    write(folder, **fields)
    assert backtest_jobs._result_ready(job(folder, active=True)) is True


def test_a_half_written_result_is_not_ready_while_running(folder):
    (folder / 'result.json').write_text('{"status": "COMPLE', encoding='utf-8')
    assert backtest_jobs._result_ready(job(folder, active=True)) is False
    assert backtest_jobs._result_ready(job(folder, active=False)) is True


def test_when_ready_the_screen_has_its_analysis_from_the_trades(tmp_path, monkeypatch):
    """What Part2's run leaves (its trades, then result.json) is all the result screen needs."""
    import csv
    from event_backtest.settings import warehouse_path
    from lab.backtest_results import ResultFiles
    run_id = 'b' * 32
    folder = tmp_path / 'runs' / run_id
    folder.mkdir(parents=True)
    row = dict(signal_id='a', strategy='SPECIAL1', symbol='XAUUSD+', tf='1m', alert_time=0, direction='LONG',
               entry_time=60_000, entry_price=10, stop_price=9, rr=1.0, result='WIN', exit_time=120_000, r=1.0, target=None)
    with (folder / 'virtual_trades.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['run_id', *row])
        writer.writeheader()
        writer.writerow({'run_id': run_id, **row})
    current = {'active': True}
    monkeypatch.setattr(backtest_jobs, 'get', lambda identifier, **context: job(folder, **current))
    assert backtest_jobs.status('x')['result_ready'] is False
    result = {'run_id': run_id, 'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY',
              'virtual_entry': {'trades_csv': f'runs/{run_id}/virtual_trades.csv', 'summary': [{'rr': 1.0}]}}
    (folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')          # what the run leaves last
    assert backtest_jobs.status('x')['result_ready'] is False                           # its processes are ending
    current['active'] = False
    assert backtest_jobs.status('x')['result_ready'] is True
    analysis, state, message = ResultFiles(tmp_path, folder, warehouse_path).analysis()
    assert state == 'ready' and len(analysis['rr_table']) == 9 and message == ''
    assert analysis['rr'] == '1.0' and analysis['rr_table'][0]['summary']['wins'] == 1
    assert not (folder / 'analytics.json').exists()
