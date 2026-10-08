"""Second cancel targets only its owned task; saving and other sessions survive."""
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs, backtest_sequences, unified_backtest
from verification.test_backtest_jobs67 import make_job, record, isolated_engine, wait_status
from verification.test_exit_backtest102 import fake_force, row
from verification.test_backtest_routes67 import handler


@pytest.fixture(autouse=True)
def isolate_state(monkeypatch):
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SESSION_CONTEXTS', {})
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {})
    monkeypatch.setattr(backtest_sequences, 'begin_shutdown', lambda: None)


def test_first_cancel_preserves_partial_result_and_reports_stopping(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '4200', 'action': 'run'})
    (owner.folder / 'result.json').write_bytes(b'{"partial":"preserve"}')
    result = jobs.stop(owner.id, **context)
    assert '종료 중' in result['message']
    assert record(owner)['phase'] == 'run'
    assert record(owner)['cancel_requested']
    assert not record(owner).get('forced_shutdown')
    assert (owner.folder / 'stop.request').is_file()
    assert (owner.folder / 'result.json').read_bytes() == b'{"partial":"preserve"}'
    state = jobs.status(owner.id, **context)
    assert state['cancel_requested'] and state['active']
    assert not jobs._closing


def test_force_requires_prior_cancel_and_boolean_choice(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    call = Mock(side_effect=AssertionError('unexpected kill'))
    monkeypatch.setattr(jobs, '_force_job', call)
    with pytest.raises(ValueError, match='먼저'):
        jobs.stop(owner.id, force=True, **context)
    with pytest.raises(ValueError, match='선택'):
        jobs.stop(owner.id, force='true', **context)
    call.assert_not_called()


def test_single_force_leaves_other_job_and_mt5_session_untouched(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '91000', 'action': 'run'})
    live, terminated, _ = fake_force(monkeypatch, rows={
        900: row(900, 90000), 42: row(42, 91000, 900),
        43: row(43, 92000, 42), 990: row(990, 99000), 991: row(991, 99100, 990)})
    other_folder = owner.folder.parent / ('b' * 32)
    other_folder.mkdir()
    other = {**record(owner), 'id': other_folder.name,
             'result_path': 'runs/' + other_folder.name + '/result.json',
             'supervisor': {'pid': 990, 'created': '99000', 'run_id': 'other'},
             'process': {'pid': 991, 'created': '99100', 'action': 'run'}}
    jobs._write(other_folder, other)
    original = (other_folder / 'job.json').read_bytes()
    saved = b'{"partial":"exact bytes"}'
    (owner.folder / 'result.json').write_bytes(saved)
    jobs.stop(owner.id, **context)
    result = jobs.stop(owner.id, force=True, **context)
    assert result['ok'] and result['forced']
    assert set(terminated) == {900, 42, 43}
    assert set(live) == {990, 991}
    assert (other_folder / 'job.json').read_bytes() == original
    assert (owner.folder / 'result.json').read_bytes() == saved
    assert record(owner)['forced_shutdown']
    assert not jobs._closing
    with jobs._accept_work():
        pass


def test_single_force_refuses_unverified_job_owner(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    _, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)}, owner_valid=False)
    jobs.stop(owner.id, **context)
    with pytest.raises(ValueError, match='관리 실행 ID'):
        jobs.stop(owner.id, force=True, **context)
    assert not terminated


def test_reused_pids_survive_single_force(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(cancel_requested=True, process={'pid': 42, 'created': '91000', 'action': 'run'})
    live, terminated, _ = fake_force(monkeypatch, rows={
        900: row(900, 99000), 42: row(42, 99100, 900)})
    assert jobs.stop(owner.id, force=True, **context)['ok']
    assert not terminated and set(live) == {900, 42}


def test_force_during_cooperative_shutdown_bypasses_save_wait(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    _, terminated, _ = fake_force(monkeypatch, rows={900: row(900, 90000)})
    finished, failures = [], []
    def close():
        try:
            finished.append(jobs.shutdown(timeout=20, **context))
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=close)
    thread.start()
    deadline = time.monotonic() + 2
    while not record(owner)['cancel_requested'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert record(owner)['cancel_requested']
    start = time.monotonic()
    assert jobs.stop(owner.id, force=True, **context)['ok']
    thread.join(2)
    assert not thread.is_alive() and not failures and finished[0]['ok']
    assert time.monotonic() - start < 2
    assert terminated == [900]
    assert not (owner.folder / 'result.json').exists()


@pytest.mark.parametrize('force', [False, True])
def test_stop_route_passes_explicit_choice(monkeypatch, force):
    stop = Mock(return_value={'ok': True})
    monkeypatch.setattr(unified_backtest, 'stop', stop)
    request = handler('/api/mo/backtest/stop', {'job_id': 'a' * 32, 'force': force})
    request.do_POST()
    assert request.replies[-1] == (200, {'ok': True})
    stop.assert_called_once_with('a' * 32, force=force)


def test_unauthorized_force_route_cannot_terminate(monkeypatch):
    stop = Mock(side_effect=AssertionError('unexpected kill'))
    monkeypatch.setattr(unified_backtest, 'stop', stop)
    request = handler('/api/mo/backtest/stop', {'job_id': 'a' * 32, 'force': True}, token='wrong')
    request.do_POST()
    assert request.replies[-1][0] == 403
    stop.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows ownership handles')
def test_native_second_cancel_terminates_only_selected_stubborn_worker(tmp_path):
    project, warehouse = isolated_engine(tmp_path), tmp_path / 'store'
    main = project / 'Part2/event_backtest/__main__.py'
    main.write_text('''import argparse,json,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--scenario');p.add_argument('--warehouse');p.add_argument('--session-id');p.add_argument('--skip-cleanup',action='store_true');p.add_argument('--approved-token');a=p.parse_args();f=Path(a.warehouse)/'runs'/a.session_id
if a.action=='plan':
 print(json.dumps({'event':'COMPLETE','result':{'record':[],'convert':[],'estimate':{},'approval_token':'approved'}}),flush=True)
else:
 child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(30)'])
 (f/'owned-child').write_text(str(child.pid));print(json.dumps({'event':'RUN_START'}),flush=True)
 time.sleep(30)
''', encoding='utf-8')
    context = {'warehouse': warehouse, 'project_root': project,
               'python_executable': getattr(sys, '_base_executable', sys.executable)}
    params = {**context, 'kind': 'normal', 'scenario': {'symbol': 'TEST',
        'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR'}}
    identifiers = []
    try:
        for _ in range(2):
            identifier = jobs.start(params)['job_id']
            identifiers.append(identifier)
            folder = warehouse / 'runs' / identifier
            wait_status(identifier, context, lambda state: state['phase'] == 'run' and (folder / 'owned-child').is_file())
        selected, other = identifiers
        selected_folder = warehouse / 'runs' / selected
        child_identity = jobs.process_identity(int((selected_folder / 'owned-child').read_text()))
        other_job = jobs.get(other, **context)
        other_child = jobs.process_identity(int((other_job['folder'] / 'owned-child').read_text()))
        jobs.stop(selected, **context)
        assert jobs.get(selected, **context)['active']
        started = time.monotonic()
        assert jobs.stop(selected, force=True, **context)['ok']
        elapsed = time.monotonic() - started
        assert elapsed < 5
        assert jobs._alive(child_identity) is False
        assert jobs.get(other, **context)['active'] and jobs._alive(other_child) is True
        assert not jobs._closing
        assert not (selected_folder / 'result.json').exists()
        # 수정본162: no longer writes revision 111's evidence file on every run.
    finally:
        for identifier in identifiers:
            if jobs.get(identifier, **context)['active']:
                jobs.stop(identifier, **context)
                jobs.stop(identifier, force=True, **context)
