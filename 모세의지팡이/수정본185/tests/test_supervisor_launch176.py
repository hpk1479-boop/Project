"""A Windows venv launches a child; the supervisor must own that exact process."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from lab import backtest_jobs as jobs, backtest_supervisor as supervisor
from verification.test_backtest_jobs67 import make_job, isolated_engine, wait_status


def local_process(monkeypatch, pid=901, parent=900):
    monkeypatch.setattr(supervisor, 'os', SimpleNamespace(name='nt', getpid=lambda: pid, getppid=lambda: parent))


def test_exact_process_is_accepted_without_changing_ownership(tmp_path, monkeypatch):
    owner, _ = make_job(tmp_path, monkeypatch)
    local_process(monkeypatch, pid=900)
    before = owner.snapshot()['supervisor']
    owner.bind_process()
    assert owner.snapshot()['supervisor'] == before


def test_direct_child_binds_and_preserves_cancellation_and_run_id(tmp_path, monkeypatch):
    owner, _ = make_job(tmp_path, monkeypatch)
    local_process(monkeypatch)
    owner.update(cancel_requested=True, phase='cancelled')
    owner.bind_process()
    record = owner.snapshot()
    assert record['supervisor'] == {'pid': 901, 'created': '90100', 'run_id': 'test_owner'}
    assert record['phase'] == 'cancelled' and record['cancel_requested']
    owner.bind_process()  # Rechecking the now-bound process remains valid.


@pytest.mark.parametrize('parent,created', [(899, '89900'), (900, 'different'), (900, None)])
def test_unrelated_reused_or_exited_parent_cannot_claim_job(tmp_path, monkeypatch, parent, created):
    owner, _ = make_job(tmp_path, monkeypatch)
    local_process(monkeypatch, parent=parent)
    before = owner.snapshot()['supervisor']
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, handle=None:
                        {'pid': 901, 'created': '90100'} if pid == 901 else
                        {'pid': parent, 'created': created} if created else None)
    with pytest.raises(ValueError, match='현재 실행 기록'):
        owner.bind_process()
    assert owner.snapshot()['supervisor'] == before


def test_previous_launch_cannot_claim_a_new_run(tmp_path, monkeypatch):
    owner, _ = make_job(tmp_path, monkeypatch)
    local_process(monkeypatch)
    record = jobs._read(owner.folder)
    record['supervisor']['run_id'] = 'new_owner'
    jobs._write(owner.folder, record)
    with pytest.raises(ValueError, match='실행 ID'):
        owner.bind_process()
    assert jobs._read(owner.folder)['supervisor']['run_id'] == 'new_owner'


@pytest.mark.skipif(os.name != 'nt', reason='Windows CPython venv redirector')
def test_real_venv_plan_run_reconnect_and_safe_stop(tmp_path, monkeypatch):
    # jobs.start prepends the fake engine's paths. Restore them at teardown so later Windows
    # spawn workers import the real event_backtest package, not this fixture's minimal package.
    monkeypatch.syspath_prepend(str(ROOT / 'Part2'))
    binary_root = tmp_path / 'python_env'
    venv.EnvBuilder(with_pip=False).create(binary_root)
    binary = binary_root / 'Scripts/python.exe'
    project, warehouse = isolated_engine(tmp_path), tmp_path / 'store'
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': str(binary)}
    identifier = jobs.start({**context, 'kind': 'normal', 'scenario': {
        'symbol': 'TEST', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR', 'hold': True}})['job_id']
    try:
        running = wait_status(identifier, context, lambda row:
                              row['phase'] == 'run' and (row.get('progress') or {}).get('replay') == 25)
        assert running['active']
        record = jobs._read(warehouse / 'runs' / identifier)
        identity = jobs.process_identity(record['supervisor']['pid'])
        assert identity == {key: record['supervisor'][key] for key in ('pid', 'created')}
        assert jobs.reconnect(identifier, **context)['active']
        jobs.stop(identifier, **context)
        ended = wait_status(identifier, context, lambda row: row['phase'] == 'cancelled' and not row['active'])
        assert ended['result_ready']
        result = json.loads((warehouse / 'runs' / identifier / 'result.json').read_text('utf-8'))
        assert result['status'] == 'CANCELLED'
    finally:
        jobs.stop(identifier, **context)
        wait_status(identifier, context, lambda row: not row['active'])
