"""Close-only regressions, using fake identities and an isolated dummy worker."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs, backtest_sequences
from verification.test_backtest_jobs67 import make_job, record, isolated_engine, wait_status


@pytest.fixture(autouse=True)
def isolate_close_state(monkeypatch):
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SESSION_CONTEXTS', {})
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {})
    monkeypatch.setattr(backtest_sequences, 'begin_shutdown', lambda: None)


@pytest.mark.skipif(os.name != 'nt', reason='Windows identity fallback')
@pytest.mark.parametrize('actual,expected', [
    ({'pid': 900, 'created': 'new'}, False),
    ({'pid': 900, 'created': '90000'}, True),
    (None, False),
])
def test_access_denied_uses_creation_identity_instead_of_assuming_live(monkeypatch, actual, expected):
    def denied(*_args):
        raise PermissionError(5, 'denied')
    monkeypatch.setattr(jobs, 'process_identity', denied)
    monkeypatch.setattr(jobs, '_windows_identity_fallback', lambda pid: actual)
    assert jobs._alive({'pid': 900, 'created': '90000'}) is expected


@pytest.mark.skipif(os.name != 'nt', reason='Windows identity fallback')
def test_both_identity_queries_fail_keeps_unknown(monkeypatch):
    def denied(*_args):
        raise PermissionError(5, 'denied')
    monkeypatch.setattr(jobs, 'process_identity', denied)
    monkeypatch.setattr(jobs, '_windows_identity_fallback', denied)
    assert jobs._alive({'pid': 900, 'created': '90000'}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows identity fallback')
@pytest.mark.parametrize('created', ['', 'unknown', None, '-123', '0'])
def test_missing_creation_cannot_be_proved_by_pid_presence(monkeypatch, created):
    monkeypatch.setattr(jobs, 'process_identity', lambda *_args: (_ for _ in ()).throw(PermissionError(5, 'denied')))
    monkeypatch.setattr(jobs, '_windows_identity_fallback', lambda pid: {'pid': pid, 'created': '1234'})
    assert jobs._alive({'pid': 900, 'created': created}) is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows identity fallback')
def test_protected_reused_pid_does_not_hold_close_or_change_saved_result(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='error')
    saved = b'{"status":"FAILED","partial":"keep exact bytes"}'
    (owner.folder / 'result.json').write_bytes(saved)
    before = (owner.folder / 'job.json').read_bytes()
    monkeypatch.setattr(jobs, 'process_identity', lambda *_args: (_ for _ in ()).throw(PermissionError(5, 'denied')))
    monkeypatch.setattr(jobs, '_windows_identity_fallback', lambda pid: {'pid': pid, 'created': 'new'})
    assert jobs.shutdown(timeout=0, **context)['ok']
    assert not jobs.get(owner.id, **context)['active']
    assert (owner.folder / 'job.json').read_bytes() == before
    assert (owner.folder / 'result.json').read_bytes() == saved


def test_wait_can_be_interrupted_without_reopening_start_gate(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    interrupt = threading.Event()
    original_get = jobs.get
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        if len(calls) >= 3:
            interrupt.set()
        return original_get(*args, **kwargs)
    monkeypatch.setattr(jobs, 'get', get)
    started = time.monotonic()
    with pytest.raises(jobs.ForceShutdownRequested):
        jobs.shutdown(timeout=45, force_requested=interrupt, **context)
    assert time.monotonic() - started < 2
    assert jobs._closing
    assert record(owner)['cancel_requested']
    with pytest.raises(ValueError, match='종료 중'):
        with jobs._accept_work():
            pytest.fail('closed gate admitted work')
    jobs.cancel_shutdown()
    assert not jobs._closing


def fake_force(monkeypatch, *, rows, owner_valid=True, orphan_valid=True):
    terminated, opened = [], []
    live = dict(rows)
    @contextmanager
    def hold(identity):
        opened.append((identity['pid'], identity['created']))
        actual = live.get(identity['pid'])
        if actual and actual['created'] == identity['created']:
            yield {'identity': {'pid': actual['pid'], 'created': actual['created']}}
        else:
            yield None
    def terminate(held, deadline):
        identity = held['identity']
        assert live[identity['pid']]['created'] == identity['created']
        terminated.append(identity['pid'])
        live.pop(identity['pid'])
    monkeypatch.setattr(jobs, '_hold_force_process', hold)
    monkeypatch.setattr(jobs, '_windows_process_snapshot', lambda: dict(live))
    monkeypatch.setattr(jobs, '_verify_force_owner', lambda *_args: owner_valid)
    monkeypatch.setattr(jobs, '_verify_force_orphan', lambda *_args: orphan_valid)
    monkeypatch.setattr(jobs, '_terminate_held', terminate)
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, handle=None:
                        {'pid': pid, 'created': live[pid]['created']} if pid in live else None)
    return live, terminated, opened


def row(pid, created, parent_pid=0):
    return {'pid': pid, 'created': str(created), 'parent_pid': parent_pid}


def test_force_only_verified_tree_including_multiprocessing_descendants(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '91000', 'action': 'run'})
    saved = b'{"partial":"preserve"}'
    (owner.folder / 'result.json').write_bytes(saved)
    live, terminated, opened = fake_force(monkeypatch, rows={
        900: row(900, 90000), 42: row(42, 91000, 900),
        43: row(43, 92000, 42), 44: row(44, 93000, 43),
        50: row(50, 89000, 42), 99: row(99, 94000, 8)})
    result = jobs.force_shutdown(**context)
    assert result['ok'] and result['warnings']
    assert set(terminated) == {900, 42, 43, 44}
    assert set(live) == {50, 99}
    assert 50 not in {pid for pid, _created in opened}
    assert record(owner)['phase'] == 'cancelled' and record(owner)['forced_shutdown']
    assert (owner.folder / 'result.json').read_bytes() == saved


def test_reused_owner_and_child_are_never_terminated(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='error')
    owner.update(process={'pid': 42, 'created': '4200', 'action': 'run'})
    before = (owner.folder / 'job.json').read_bytes()
    live, terminated, _ = fake_force(monkeypatch, rows={
        900: row(900, 99000), 42: row(42, 99100, 900), 43: row(43, 99200, 42)})
    assert jobs.force_shutdown(**context)['ok']
    assert not terminated and len(live) == 3
    assert (owner.folder / 'job.json').read_bytes() == before


@pytest.mark.parametrize('phase', ['complete', 'error'])
def test_force_stops_lingering_owned_process_without_changing_completed_record(tmp_path, monkeypatch, phase):
    owner, context = make_job(tmp_path, monkeypatch, phase=phase)
    saved = b'{"status":"COMPLETE","details":[1,2]}'
    (owner.folder / 'result.json').write_bytes(saved)
    before = (owner.folder / 'job.json').read_bytes()
    _, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)})
    assert jobs.force_shutdown(**context)['ok'] and terminated == [900]
    assert (owner.folder / 'job.json').read_bytes() == before
    assert (owner.folder / 'result.json').read_bytes() == saved


def test_unverified_job_run_id_refuses_force_before_any_kill(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    _, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)}, owner_valid=False)
    with pytest.raises(RuntimeError, match='관리 실행 ID'):
        jobs.force_shutdown(**context)
    assert not terminated and not record(owner).get('forced_shutdown')


def test_wrong_parent_refuses_force_before_any_kill(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '91000', 'action': 'run'})
    _, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000), 42: row(42, 91000, 99)})
    with pytest.raises(RuntimeError, match='해당 실행'):
        jobs.force_shutdown(**context)
    assert not terminated


def test_orphan_needs_independent_job_identity(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '91000', 'action': 'run'})
    _, terminated, _ = fake_force(monkeypatch, rows={42: row(42, 91000, 900)}, orphan_valid=False)
    with pytest.raises(RuntimeError, match='해당 실행'):
        jobs.force_shutdown(**context)
    assert not terminated


def test_changed_record_between_inventory_and_force_cannot_kill(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    job = jobs.get(owner.id, **context)
    owner.update(supervisor={**job['supervisor'], 'run_id': 'new_owner'})
    _, terminated, opened = fake_force(monkeypatch, rows={900: row(900, 90000)})
    with pytest.raises(ValueError, match='소유 정보가 변경'):
        jobs._force_job(job, time.monotonic() + 5)
    assert not opened and not terminated


def test_worker_born_at_termination_boundary_is_found_through_pinned_parent(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    live, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)})
    original = jobs._terminate_held
    def terminate(held, deadline):
        if held['identity']['pid'] == 900:
            live[42] = row(42, 91000, 900)
        original(held, deadline)
    monkeypatch.setattr(jobs, '_terminate_held', terminate)
    assert jobs.force_shutdown(**context)['ok']
    assert terminated == [900, 42] and not live


def test_reused_parent_in_late_snapshot_cannot_claim_unrelated_child(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    live, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)})
    original = jobs._terminate_held
    def terminate(held, deadline):
        original(held, deadline)
        live[900] = row(900, 99000)
        live[42] = row(42, 99100, 900)
    monkeypatch.setattr(jobs, '_terminate_held', terminate)
    assert jobs.force_shutdown(**context)['ok']
    assert terminated == [900] and set(live) == {900, 42}


def test_child_born_after_parent_exit_is_not_owned(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    live, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)})
    original = jobs._terminate_held
    def terminate(held, deadline):
        original(held, deadline)
        held['exited'] = 91000
        live[42] = row(42, 92000, 900)
    monkeypatch.setattr(jobs, '_terminate_held', terminate)
    assert jobs.force_shutdown(**context)['ok']
    assert terminated == [900] and set(live) == {42}


def test_force_cancels_confirmation_without_launching_or_deleting_files(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='confirm')
    owner.update(supervisor=None, plan={'approval_token': 'pending'})
    fake_force(monkeypatch, rows={})
    result = jobs.force_shutdown(**context)
    assert result['ok'] and result['warnings']
    assert record(owner)['phase'] == 'cancelled'
    assert record(owner)['cancel_requested']
    assert (owner.folder / 'web_scenario.json').exists()
    jobs.cancel_shutdown()
    with pytest.raises(ValueError, match='확인 대기'):
        jobs.confirm(owner.id, **context)


def test_force_finishes_pending_save_check_without_fabricating_result(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='cancelled')
    owner.update(supervisor=None)
    fake_force(monkeypatch, rows={})
    key = (str(context['warehouse']).casefold(), owner.id)
    jobs._SHUTDOWN_PENDING[key] = (context, True)
    assert not jobs.active_jobs(**context)
    assert jobs.shutdown_pending_jobs()[0]['id'] == owner.id
    with pytest.raises(RuntimeError, match='결과 저장'):
        jobs.shutdown(timeout=0, **context)
    assert key in jobs._SHUTDOWN_PENDING
    result = jobs.force_shutdown(**context)
    assert result['ok'] and result['warnings']
    assert not jobs._SHUTDOWN_PENDING
    assert not (owner.folder / 'result.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows pinned handles')
def test_native_snapshot_identity_matches_exact_process_creation():
    actual = jobs.process_identity(os.getpid())
    fallback = jobs._windows_identity_fallback(os.getpid())
    assert fallback == actual


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows pinned handles')
def test_native_force_isolated_dummy_supervisor_worker_and_grandchild(tmp_path):
    project, warehouse = isolated_engine(tmp_path), tmp_path / 'store'
    script = project / 'Part2/event_backtest/__main__.py'
    script.write_text('''import argparse,json,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--scenario');p.add_argument('--warehouse');p.add_argument('--session-id');a=p.parse_args();f=Path(a.warehouse)/'runs'/a.session_id
if a.action=='plan':
 print(json.dumps({'event':'COMPLETE','result':{'record':[],'convert':[],'estimate':{},'approval_token':'approved'}}),flush=True)
else:
 child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(15)'])
 (f/'dummy.child').write_text(str(child.pid));(f/'worker.started').write_text('started')
 print(json.dumps({'event':'RUN_START'}),flush=True)
 time.sleep(15)
''', encoding='utf-8')
    # The test runner may be a venv redirector; use its actual interpreter so
    # the independently owned fixture PID is the supervisor itself.
    context = {'warehouse': warehouse, 'project_root': project,
               'python_executable': getattr(sys, '_base_executable', sys.executable)}
    created = jobs.start({**context, 'kind': 'normal', 'scenario': {'symbol': 'TEST',
        'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR'}})
    identifier, folder = created['job_id'], warehouse / 'runs' / created['job_id']
    try:
        wait_status(identifier, context, lambda status: status['phase'] == 'run' and
                    (folder / 'dummy.child').exists())
        grandchild = int((folder / 'dummy.child').read_text())
        grandchild_identity = jobs.process_identity(grandchild)
        assert grandchild_identity
        result = jobs.force_shutdown(timeout=5, **context)
        assert result['ok'] and result['warnings']
        assert jobs._alive(grandchild_identity) is False
        status = jobs.status(identifier, **context)
        assert status['phase'] == 'cancelled' and not status['active']
        assert jobs._read(folder)['forced_shutdown']
    finally:
        # This inventory is confined to the fixture warehouse and identities.
        if jobs.active_jobs(**context):
            jobs.force_shutdown(timeout=5, **context)
