"""170: waiting outside the engine, cut without changing what a backtest computes.

- No analytics.json after a run: the result screen computes its analysis from the trades.
- The physical core count is asked of Windows directly (the PowerShell query took over a second).
- The backtest screen's options load no pandas; the frame functions used instead give the same values.
- A strategy entry is one copy of the cached entry, the same as before.
- Trade pages continue the reading where the last page stopped; the rows are those read from the start.
- A period's RR table is computed once; another RR of the period reuses it.
- job.json keeps the plan without each reused recording's whole record; an unchanged progress is not
  written again; the list does not parse an unchanged finished result again.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import runner, system, workflow
from event_backtest.settings import scenario
from test_verify_once170 import REQUEST, store  # noqa: F401  (fixture)


@pytest.mark.skipif(os.name != 'nt', reason='Windows core records')
def test_the_physical_core_count_is_the_one_windows_reports():
    reported = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
                                        '(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum'],
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=60)
    assert system.physical_cores() == int(reported.strip())
    assert 1 <= system.physical_cores() <= os.cpu_count()


def test_without_the_windows_records_half_the_logical_processors(monkeypatch):
    def unavailable():
        raise OSError('no records')
    monkeypatch.setattr(system, '_windows_cores', unavailable)
    assert system.physical_cores() == max(1, (os.cpu_count() or 2) // 2)


def test_the_backtest_options_load_no_pandas():
    code = ('import sys; sys.path.insert(0, %r); from lab import unified_backtest; unified_backtest.options(); '
            'print("pandas" in sys.modules)') % str(ROOT / 'Part3')
    done = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == 'False'


def test_the_frame_functions_read_every_frame_as_indicator_facts_does():
    import command_interpreter
    import indicator_facts
    from event_backtest import base_frames, virtual_contract
    assert base_frames.tf_seconds is command_interpreter.tf_seconds
    assert virtual_contract.normalize_tf is command_interpreter.normalize_tf
    assert set(command_interpreter.MT5_TIMEFRAMES) == set(indicator_facts.MT5_TIMEFRAMES)
    values = [*indicator_facts.MT5_TIMEFRAMES, '1분', '15분', '1시간', '4시간', '1일', '24h', '60m', ' 5m ', '5M', '2H',
              '', None, 'SIGNAL', 'ENV', 'SOURCE', 'FINAL', 5]
    for value in values:
        assert command_interpreter.normalize_tf(value) == indicator_facts.normalize_tf(value), value
        assert command_interpreter.tf_seconds(value) == indicator_facts.tf_seconds(value), value


def test_a_strategy_entry_is_its_own_copy_of_the_cached_entry():
    from strategy_recipe import registry
    names = registry.list_presets()
    builtin = registry.builtin_entries()
    for name in names:
        entry = registry.preset_entry(name)
        assert entry == builtin.get(name, entry)
    first = registry.preset_entry(names[0])
    first['recipe']['strategy_intent']['steps'] = 'changed'
    builtin[names[0]]['name'] = 'changed'
    again = registry.preset_entry(names[0])
    assert again['recipe']['strategy_intent'].get('steps') != 'changed' and again['name'] != 'changed'
    assert registry.builtin_entries()[names[0]] == again
    with pytest.raises(ValueError, match='등록되지 않은 preset'):
        registry.preset_entry('NO_SUCH_PRESET')


def test_a_virtual_entry_run_writes_no_analysis_file(store, monkeypatch):
    root, rows, hashed = store
    run_id = 'c' * 32
    folder = root / 'runs' / run_id
    finished = {'run_id': run_id, 'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY',
                'virtual_entry': {'trades_csv': f'runs/{run_id}/virtual_trades.csv', 'summary': []}}

    def run(s, warehouse, **options):
        folder.mkdir(parents=True)
        (folder / 'virtual_trades.csv').write_text('run_id,signal_id\n', encoding='utf-8')
        (folder / 'result.json').write_text(json.dumps(finished), encoding='utf-8')
        return dict(finished)
    monkeypatch.setattr(runner, 'run', run)
    result = workflow.execute(scenario(**REQUEST), root, yes=True, cleanup=False)
    assert result == finished and 'analytics_path' not in result
    assert not (folder / 'analytics.json').exists()
    assert json.loads((folder / 'result.json').read_text('utf-8')) == finished


# ---- result screen: trade pages and RR tables ---------------------------------------------------

def trade_file(path, rows, *, quoted_break=False, odd_lines=True):
    import csv
    fields = ['run_id', 'signal_id', 'strategy', 'symbol', 'tf', 'alert_time', 'direction', 'entry_time', 'entry_price',
              'stop_price', 'rr', 'result', 'exit_time', 'r', 'target']
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, (rr, alert) in enumerate(rows):
            writer.writerow({'run_id': 'x', 'signal_id': f's{index}', 'strategy': 'S\nP' if quoted_break and index == 3 else 'S',
                             'symbol': 'XAUUSD+', 'tf': '1m', 'alert_time': alert, 'direction': 'LONG', 'entry_time': alert + 60000,
                             'entry_price': 10, 'stop_price': 9, 'rr': rr, 'result': 'WIN' if index % 3 else 'LOSS',
                             'exit_time': alert + 120000, 'r': rr if index % 3 else -1, 'target': ''})
        if odd_lines:
            handle.write('\r\n')                                   # an empty line, skipped as csv.DictReader skips it
            handle.write('x,short_row\r\n')                        # fewer values than columns: None for the rest
    return path


def from_start(path, rr, offset, limit, period):
    """The 169 method: read from the first row every time."""
    import csv
    from event_backtest.result_analysis import period_window
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab.backtest_results import _wanted
    chosen, window, rows, matched = (None if rr is None else float(rr)), period_window(period), [], 0
    with path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            if not _wanted(row, chosen, window):
                continue
            if matched >= offset:
                rows.append(row)
                if len(rows) > limit:
                    break
            matched += 1
    return rows[:limit], offset + limit if len(rows) > limit else None


def result_files(tmp_path, trades):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab.backtest_results import ResultFiles
    from event_backtest.settings import warehouse_path
    folder = tmp_path / 'runs' / ('e' * 32)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'result.json').write_text(json.dumps({'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY',
        'virtual_entry': {'trades_csv': trades.relative_to(tmp_path).as_posix()}}), encoding='utf-8')
    return ResultFiles(tmp_path, folder, warehouse_path)


@pytest.mark.parametrize('quoted_break', [False, True])
def test_trade_pages_are_the_rows_read_from_the_start(tmp_path, quoted_break):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import backtest_results
    month = 30 * 86400 * 1000
    rows = [(rr, 1735689600000 + index * 3600000 + (index % 7) * month) for index in range(700) for rr in (1.0, 2.0, 3.5)]
    trades = trade_file(tmp_path / 'trades.csv', rows, quoted_break=quoted_break)
    files = result_files(tmp_path, trades)
    backtest_results._SCANS.clear()
    for rr, period in (('2.0', 'all'), ('1.0', '2025'), ('3.5', '2025-03'), (None, 'all'), ('9.0', 'all')):
        for offset in (0, 100, 200, 600, 2000, 2100, 0, 100):
            page = files.trades(rr, offset, 100, period)
            expected, following = from_start(trades, rr, offset, 100, period)
            assert page['rows'] == expected and page['next_offset'] == following, (rr, period, offset)


def test_a_next_page_continues_and_an_earlier_page_reads_nothing(tmp_path):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import backtest_results
    trades = trade_file(tmp_path / 'trades.csv', [(rr, 1735689600000 + index * 3600000)
                                                   for index in range(1000) for rr in (1.0, 2.0)])
    files = result_files(tmp_path, trades)
    backtest_results._SCANS.clear()
    files.trades('2.0', 0, 100, 'all')
    (scan,) = backtest_results._SCANS.values()
    first = scan.position
    files.trades('2.0', 100, 100, 'all')
    second = scan.position
    assert first < second < trades.stat().st_size                  # read on from the first page, not to the end
    files.trades('2.0', 0, 100, 'all')
    assert scan.position == second                                 # an earlier page reads nothing more


def test_a_periods_rr_table_is_computed_once(tmp_path, monkeypatch):
    from event_backtest import result_analysis as ra
    trades = ra.load_trades(trade_file(tmp_path / 'trades.csv', [(rr, 1735689600000 + index * 86400000)
                                                                  for index in range(400) for rr in (1.0, 2.0, 3.0)],
                                       odd_lines=False))
    fresh = ra.load_trades(tmp_path / 'trades.csv')
    calls = []
    original = ra.rr_table
    monkeypatch.setattr(ra, 'rr_table', lambda *a: calls.append(1) or original(*a))
    first = ra.view(trades, 'all', '2.0')
    second = ra.view(trades, 'all', '3.0')
    table = ra.table_view(trades, 'all')
    assert len(calls) == 1 and first['rr_table'] == second['rr_table'] == table['rr_table']
    ra.view(trades, '2025', '2.0')
    assert len(calls) == 2
    monkeypatch.setattr(ra, 'rr_table', original)
    assert second == ra.view(fresh, 'all', '3.0') and table['default_rr'] == ra.view(fresh, 'all')['default_rr']
    second['rr_table'][0]['values']['total_r'] = 'changed'          # a shown table is a copy
    assert ra.view(trades, 'all', '3.0')['rr_table'] == ra.view(fresh, 'all', '3.0')['rr_table']


# ---- job record, progress writes and the list ---------------------------------------------------

def test_job_json_keeps_the_plan_without_each_reused_recording_record():
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import backtest_jobs
    from lab.backtest_supervisor import stored_plan
    reuse = [{'capture_id': f'c{i}', 'start': f'2025-{i:02d}-01', 'end': f'2025-{i + 1:02d}-01', 'files': {'a': 'b' * 64},
              'observed_days': ['2025-01-02'] * 20, 'path': 'captures/x'} for i in range(1, 11)]
    plan = {'record': [{'start': '2026-01-01', 'end': '2026-02-01'}], 'convert': [], 'reuse': reuse,
            'estimate': {'recording_seconds': 1}, 'approval_token': 't', 'period_adjustment': None}
    kept = stored_plan(plan)
    assert kept['reuse'] == [{key: row[key] for key in ('capture_id', 'start', 'end')} for row in reuse]
    assert {key: value for key, value in kept.items() if key != 'reuse'} == {key: value for key, value in plan.items() if key != 'reuse'}
    assert backtest_jobs._revision(kept) == backtest_jobs._revision(plan)
    assert backtest_jobs._plan_public({'phase': 'confirm', 'plan': kept}) == backtest_jobs._plan_public({'phase': 'confirm', 'plan': plan})
    assert len(json.dumps(kept)) * 3 < len(json.dumps(plan))


def test_an_unchanged_progress_is_not_written_again():
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab.backtest_supervisor import Supervisor
    owner = Supervisor.__new__(Supervisor)
    import threading
    owner.view_lock, owner.last_progress_write, owner.last_progress = threading.RLock(), 0.0, None
    written = []
    owner.update = lambda **changes: written.append(changes['progress'])

    class View:
        phase, build, replay, virtual, lines, warnings = '재생', 100., 10., 0., ['a'], []

        def remaining(self):
            return '1분'
    owner.view = View()
    owner._progress(force=False)
    owner.last_progress_write = 0.0
    owner._progress(force=False)                                   # the same progress
    assert len(written) == 1
    owner.view.replay = 11.
    owner.last_progress_write = 0.0
    owner._progress(force=False)
    owner._progress(force=True)                                    # an end event always writes
    assert [row['replay'] for row in written] == [10., 11., 11.]


def test_the_list_reads_an_unchanged_finished_result_once(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import backtest_jobs
    folder = tmp_path / ('f' * 32)
    folder.mkdir()
    path = folder / 'result.json'
    path.write_text(json.dumps({'status': 'COMPLETE', 'scenario': {'strategies': ['SPECIAL1']}}), encoding='utf-8')
    reads = []
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **k: reads.append(self.name) or original(self, *a, **k))
    backtest_jobs._LEGACY.clear()
    first = backtest_jobs._legacy(folder)                           # just written: read every time
    backtest_jobs._legacy(folder)
    assert reads.count('result.json') == 2
    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))
    backtest_jobs._legacy(folder)
    again = backtest_jobs._legacy(folder)
    assert reads.count('result.json') == 3 and again['phase'] == first['phase'] == 'complete'
    again['scenario']['strategies'].append('changed')               # a copy
    assert backtest_jobs._legacy(folder)['scenario']['strategies'] == ['SPECIAL1']
    path.write_text(json.dumps({'status': 'CANCELLED', 'scenario': {}}), encoding='utf-8')
    assert backtest_jobs._legacy(folder)['phase'] == 'cancelled'
