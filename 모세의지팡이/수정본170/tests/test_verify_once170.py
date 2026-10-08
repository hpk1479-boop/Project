"""170: a backtest start hashes each stored recording once.

The plan (Part3's planning step) reads the catalog only: a recording is reusable when it is complete and
its files are present. The run hashes each recording once, right before replaying it; prepare's plan and
the runner use that result. A recording changed after the plan is found by the run, which asks for a new
approval instead of replaying it. The runner still hashes recordings nobody verified for it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import build_plan, runner, warehouse as wh, workflow
from event_backtest.build_plan import ConfirmationRequired
from event_backtest.settings import file_hash, scenario
import staff_schema as wire

REQUEST = dict(symbol='XAUUSD+', start='2025-08-25', end='2025-09-10', mode='BAR', strategies=['SPECIAL8'],
               overlap_trading_days=0)


def recording(root, key, start, end):
    """A stored monthly recording as the catalog lists it (its bytes are not read here)."""
    folder = root / 'captures' / key
    folder.mkdir(parents=True)
    (folder / 'capture.delta2').write_bytes(key.encode() * 50000)
    (folder / 'storage.json').write_text('{}', encoding='utf-8')
    (folder / 'complete.txt').write_text('complete', encoding='ascii')
    row = {'capture_id': key, 'symbol': 'XAUUSD+', 'mode': 'BAR', 'start': start, 'end': end, 'unit': 'MONTH',
           'ea_build_hash': 'test-ea', 'schema_id': wire.WIRE_SCHEMA_ID, 'timer_ms': 1000, 'storage': 'MSD2',
           'reconstruction_verified': True, 'path': 'captures/' + key, 'observed_days': [],
           'files': {p.name: file_hash(p) for p in folder.iterdir()}, 'tick_evidence': {'actual': 'REAL_TICKS'},
           'stored_bytes': sum(p.stat().st_size for p in folder.iterdir()), 'recorded_at': '2026-10-01T00:00:00+00:00'}
    catalog = wh.Warehouse(root)
    catalog.register(row)
    catalog.close()
    return row


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / 'w'
    rows = [recording(root, 'aug', '2025-08-01', '2025-09-01'), recording(root, 'sep', '2025-09-01', '2025-10-01')]
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(workflow, '_backtest_priority', lambda: None)
    hashed = []
    original = wh._capture_file_hash

    def counted(path):
        hashed.append(Path(path).parent.name + '/' + Path(path).name)
        return original(path)
    monkeypatch.setattr(wh, '_capture_file_hash', counted)
    return root, rows, hashed


def every_file(rows):
    return sorted(row['capture_id'] + '/' + name for row in rows for name in row['files'])


def test_the_plan_hashes_nothing_and_sees_a_missing_file(store):
    root, rows, hashed = store
    plan = workflow.proposal(scenario(**REQUEST), root, verify=False, cleanup=False)
    assert hashed == [] and sorted(c['capture_id'] for c in plan['reuse']) == ['aug', 'sep'] and plan['record'] == []
    (root / 'captures/sep/capture.delta2').unlink()
    plan = workflow.proposal(scenario(**REQUEST), root, verify=False, cleanup=False)
    assert hashed == [] and [c['capture_id'] for c in plan['reuse']] == ['aug']
    assert [piece['start'][:7] for piece in plan['record']] == ['2025-09']


def test_a_start_hashes_each_recording_once_and_the_runner_uses_it(store, monkeypatch):
    root, rows, hashed = store
    calls = []

    def run(s, warehouse, **options):
        calls.append(options)
        return {'status': 'COMPLETE', 'result_mode': 'ALERT_ONLY', 'run_id': 'r'}
    monkeypatch.setattr(runner, 'run', run)
    assert workflow.execute(scenario(**REQUEST), root, yes=True, cleanup=False)['status'] == 'COMPLETE'
    # The run's plan, prepare's plan and the runner: every file once in all.
    assert sorted(hashed) == every_file(rows)
    (options,) = calls
    assert options['verified'] is True and sorted(c['capture_id'] for c in options['captures']) == ['aug', 'sep']


def test_a_recording_changed_after_the_plan_is_not_replayed(store, monkeypatch):
    root, rows, hashed = store
    plan = workflow.proposal(scenario(**REQUEST), root, verify=False, cleanup=False)
    path = root / 'captures/sep/capture.delta2'
    path.write_bytes(path.read_bytes()[:-1] + b'!')              # same size, other bytes
    monkeypatch.setattr(runner, 'run', lambda *a, **k: pytest.fail('a changed recording was replayed'))
    with pytest.raises(ConfirmationRequired) as raised:
        workflow.execute(scenario(**REQUEST), root, approved_token=plan['approval_token'], cleanup=False)
    assert [piece['start'][:7] for piece in raised.value.plan['record']] == ['2025-09']


def test_the_runner_hashes_recordings_nobody_verified_for_it(store, monkeypatch):
    root, rows, hashed = store

    class Started(Exception):
        pass

    def started(*a, **k):
        raise Started
    monkeypatch.setattr(runner, '_Run', started)
    s = scenario(**REQUEST)
    with pytest.raises(Started):
        runner.run_many([s], root, captures=rows, sequential=True)
    assert sorted(hashed) == every_file(rows)
    hashed.clear()
    with pytest.raises(Started):
        runner.run_many([s], root, captures=rows, sequential=True, verified=True)
    assert hashed == []
    path = root / 'captures/aug/capture.delta2'
    path.write_bytes(path.read_bytes()[:-1] + b'!')
    with pytest.raises(ValueError, match='녹화 무결성 오류'):
        runner.run_many([s], root, captures=rows, sequential=True)


@pytest.mark.parametrize('action, verify', [('plan', False), ('missing', True)])
def test_the_command_line_plan_reads_the_catalog_only(tmp_path, monkeypatch, capsys, action, verify):
    import json
    from event_backtest import __main__ as cli
    seen = []
    monkeypatch.setattr(workflow, 'proposal', lambda s, warehouse, **options: seen.append(options) or {'record': []})
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(REQUEST), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['event_backtest', action, '--scenario', str(path), '--warehouse', str(tmp_path / 'w'),
                                      '--skip-cleanup'])
    assert cli.main() == 0
    assert seen == [{'rebuild': False, 'verify': verify, 'cleanup': False}]


def test_a_part3_job_runs_the_command_line_without_cleaning_again(tmp_path):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import backtest_jobs
    job = {'kind': 'normal', 'python_executable': 'python', 'scenario_path': tmp_path / 'web_scenario.json',
           'warehouse': tmp_path, 'id': 'a' * 32, 'project_root': ROOT}
    for action in ('plan', 'run'):
        args, _, _ = backtest_jobs.command(job, action)
        assert args[args.index('-m') + 1:args.index('-m') + 3] == ['event_backtest', action] and '--skip-cleanup' in args
