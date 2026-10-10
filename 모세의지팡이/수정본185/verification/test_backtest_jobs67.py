"""Common durable job, safe-stop and recovery contracts, using isolated inputs."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs, backtest_supervisor as supervisor


class Projection:
    phase = '준비'
    build = replay = virtual = 0
    lines = []
    warnings = []
    def accept(self, _event):
        pass
    def remaining(self):
        return '경과 확인 중'


def make_job(tmp_path, monkeypatch, *, phase='starting', auto_run=True):
    warehouse, project = tmp_path / 'store', tmp_path / 'project'
    folder = warehouse / 'runs' / ('a' * 32)
    folder.mkdir(parents=True)
    project.mkdir()
    record = {'version': 1, 'id': folder.name, 'kind': 'normal',
        'scenario': {'symbol': 'TEST', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR'},
        'scenario_file': 'web_scenario.json', 'adapter': {}, 'rebuild': False, 'auto_run': auto_run,
        'phase': phase, 'created_at': time.time(), 'updated_at': time.time(),
        'cancel_requested': False, 'plan': None, 'progress': None, 'message': '',
        'supervisor': {'pid': 900, 'created': '90000', 'run_id': 'test_owner'},
        'process': None, 'returncode': None, 'result_path': 'runs/' + folder.name + '/result.json'}
    jobs._atomic(folder / 'web_scenario.json', record['scenario'])
    jobs._write(folder, record)
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, handle=None: {'pid': pid, 'created': str(pid * 100)})
    monkeypatch.setattr(supervisor, 'projection', lambda job: Projection())
    monkeypatch.setattr(jobs, 'command', lambda job, action, approved=None: (['mock', action], {}, project))
    owner = supervisor.Supervisor(folder.name, warehouse, project, 'test_owner', sys.executable)
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    return owner, context


def record(owner):
    return jobs._read(owner.folder)


class Process:
    pid = 42
    def __init__(self, finish, result=None, code=0):
        self.finish = finish
        self.result = result
        self.code = code
        self.terminated = threading.Event()
        self.stdout = self.lines()
    def lines(self):
        assert self.finish.wait(5), 'mock stdout boundary was not released'
        if self.result is not None:
            yield json.dumps({'event': 'COMPLETE', 'result': self.result}) + '\n'
    def poll(self):
        return self.code if self.finish.is_set() else None
    def wait(self, timeout=None):
        assert self.finish.wait(timeout or 5)
        return self.code
    def terminate(self):
        self.terminated.set()
        self.finish.set()


def join(thread):
    thread.join(5)
    assert not thread.is_alive(), 'mock worker did not leave the tested boundary'


@pytest.mark.parametrize('phase', ['starting', 'confirm', 'planning'])
def test_cancelled_metadata_cannot_be_replaced_by_pending_plan_transition(tmp_path, monkeypatch, phase):
    owner, context = make_job(tmp_path, monkeypatch, phase=phase)
    jobs.stop(owner.id, **context)
    owner.update(phase='confirm', plan={'approval_token': 'new_plan'})
    assert record(owner)['phase'] == 'cancelled'
    owner.update(phase='starting')
    assert record(owner)['phase'] == 'cancelled'


def test_plan_completion_stop_gap_preserves_cancelled_and_never_launches_run(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='planning')
    def complete(_action, _approved=None):
        owner.last_result = {'record': [{'start': '2025-09-01'}], 'approval_token': 'new'}
        return 0
    monkeypatch.setattr(owner, 'execute_action', complete)
    original = owner.update
    def stop_before_update(**changes):
        if changes.get('phase') == 'confirm':
            jobs.stop(owner.id, **context)
        return original(**changes)
    monkeypatch.setattr(owner, 'update', stop_before_update)
    owner.execute_plan()
    assert record(owner)['phase'] == 'cancelled'


@pytest.mark.parametrize('action', ['plan', 'run'])
def test_identity_failure_keeps_child_owned_and_sends_safe_shutdown(tmp_path, monkeypatch, action):
    owner, _context = make_job(tmp_path, monkeypatch)
    finish = threading.Event()
    child = Process(finish, {'status': 'CANCELLED'})
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda *_a, **_k: child)
    def denied(_pid, _handle=None):
        raise OSError('identity failure')
    monkeypatch.setattr(jobs, 'process_identity', denied)
    if action == 'run':
        child.stdout = iter([json.dumps({'event': 'COMPLETE', 'result': {'status': 'CANCELLED'}}) + '\n'])
        class Stream:
            def __iter__(self):
                assert (owner.folder / 'stop.request').is_file()
                finish.set()
                yield from child.stdout_lines
            def close(self):
                pass
        child.stdout_lines = child.stdout
        child.stdout = Stream()
    with pytest.raises(OSError, match='identity failure'):
        owner.execute_action(action)
    assert child.poll() is not None and owner.process is None
    assert child.terminated.is_set() if action == 'plan' else (owner.folder / 'stop.request').is_file()


@pytest.mark.parametrize('value', ['../outside.json', '/outside.json', 'C:/outside.json', 'other.json'])
def test_persisted_scenario_path_cannot_escape_job(tmp_path, monkeypatch, value):
    owner, context = make_job(tmp_path, monkeypatch)
    row = record(owner)
    row['scenario_file'] = value
    jobs._write(owner.folder, row)
    with pytest.raises(ValueError, match='기록 형식'):
        jobs.get(owner.id, **context)


def test_reconnect_checks_creation_identity_instead_of_reused_pid(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, handle=None: {'pid': pid, 'created': 'different'})
    data = jobs.reconnect(owner.id, **context)
    assert not data['active'] and data['phase'] == 'interrupted'


def test_result_recovers_when_supervisor_died_after_part2_save(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    jobs._atomic(owner.folder / 'result.json', {'status': 'COMPLETE', 'scenario': record(owner)['scenario']})
    monkeypatch.setattr(jobs, 'process_identity', lambda *_a, **_k: None)
    assert jobs.reconnect(owner.id, **context)['phase'] == 'complete'
    assert jobs.status(owner.id, **context)['result_ready']


@pytest.mark.parametrize('unknown', [False, True])
def test_result_file_cannot_hide_live_or_unknown_orphan_child(tmp_path, monkeypatch, unknown):
    owner, context = make_job(tmp_path, monkeypatch, phase='run')
    owner.update(process={'pid': 42, 'created': '4200', 'action': 'run'})
    jobs._atomic(owner.folder / 'result.json', {'status': 'COMPLETE', 'scenario': record(owner)['scenario']})
    def identity(pid, handle=None):
        if pid == 900:
            return None
        if unknown:
            raise PermissionError('cannot query child')
        return {'pid': pid, 'created': '4200'}
    monkeypatch.setattr(jobs, 'process_identity', identity)
    data = jobs.status(owner.id, **context)
    assert data['phase'] == 'interrupted' and data['active'] and data['result_ready']
    assert jobs.stop(owner.id, **context)['ok']
    assert (owner.folder / 'stop.request').is_file()


def test_publication_failure_still_drains_owned_child_when_stop_write_fails(tmp_path, monkeypatch):
    owner, _context = make_job(tmp_path, monkeypatch)
    finish = threading.Event()
    child = Process(finish, {'status': 'COMPLETE'})
    class Stream:
        def __iter__(self):
            finish.set()
            yield json.dumps({'event': 'COMPLETE', 'result': {'status': 'COMPLETE'}}) + '\n'
        def close(self):
            pass
    child.stdout = Stream()
    real_write = Path.write_text
    def denied(path, *args, **kwargs):
        if path.name == 'stop.request':
            raise PermissionError('volume unavailable')
        return real_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', denied)
    owner.process = child
    owner._failed_publication(child, 'run')
    assert child.poll() == 0 and owner.process is None
    assert (owner.folder / 'console.log').is_file()


def test_plan_revision_is_pinned_and_stop_blocks_future_confirm(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='confirm')
    owner.update(plan={'approval_token': 'current', 'record': [{'start': '2025-09-01'}]})
    launches = []
    monkeypatch.setattr(jobs, '_launch', lambda *args: launches.append(args[-1]))
    with pytest.raises(ValueError, match='계획이 변경'):
        jobs.confirm(owner.id, plan_revision='old', **context)
    revision = jobs.status(owner.id, **context)['plan_revision']
    assert jobs.confirm(owner.id, plan_revision=revision, **context)['ok']
    assert launches == ['run'] and record(owner)['phase'] == 'starting'
    jobs.stop(owner.id, **context)
    with pytest.raises(ValueError, match='확인 대기'):
        jobs.confirm(owner.id, **context)


def test_plan_only_never_invokes_run_even_when_all_data_reusable(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch, phase='planning', auto_run=False)
    actions = []
    def planned(action, approved=None):
        actions.append(action)
        owner.last_result = {'record': [], 'approval_token': 'unchanged'}
        return 0
    monkeypatch.setattr(owner, 'execute_action', planned)
    owner.execute_plan()
    data = jobs.status(owner.id, **context)
    assert actions == ['plan'] and data['phase'] == 'planned' and data['plan'] is not None


def test_changed_build_plan_returns_to_confirmation(tmp_path, monkeypatch):
    owner, context = make_job(tmp_path, monkeypatch)
    def changed(action, approved=None):
        owner.last_error = {'event': 'CONFIRMATION_REQUIRED', 'record': [{'start': '2025-09-02'}], 'approval_token': 'new'}
        return 2
    monkeypatch.setattr(owner, 'execute_action', changed)
    owner.execute_run('old')
    data = jobs.status(owner.id, **context)
    assert data['phase'] == 'confirm' and data['plan']['record'][0]['start'] == '2025-09-02'
    assert 'approval_token' not in data['plan']


def isolated_engine(tmp_path):
    """An explicit fake CLI; no user engine, market data or network is used."""
    project = tmp_path / 'project'
    package = project / 'Part2/event_backtest'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    shutil.copyfile(ROOT / 'Part2/event_backtest/progress_view.py', package / 'progress_view.py')
    shutil.copyfile(ROOT / 'Part2/event_backtest/warehouse_cleanup.py', package / 'warehouse_cleanup.py')
    (package / '__main__.py').write_text('''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--scenario');p.add_argument('--warehouse');p.add_argument('--session-id');p.add_argument('--skip-cleanup',action='store_true');p.add_argument('--approved-token');p.add_argument('--rebuild',action='store_true');a=p.parse_args()
s=json.loads(Path(a.scenario).read_text());f=Path(a.warehouse)/'runs'/a.session_id
def emit(kind,**kw):print(json.dumps({'event':kind,**kw}),flush=True)
if a.action=='plan':
 emit('COMPLETE',result={'record':[{'start':s['start'],'end':s['end']}] if s.get('record') else [],'convert':[],'estimate':{},'approval_token':'approved'})
else:
 if s.get('record') and a.approved_token!='approved':raise SystemExit(2)
 emit('RUN_START',percent=0);emit('RUN_PROGRESS',percent=25);(f/'worker.started').write_text('started')
 if s.get('hold'):
  end=time.monotonic()+10
  while not (f/'release').exists() and not (f/'stop.request').exists() and time.monotonic()<end:time.sleep(.01)
 cancelled=(f/'stop.request').exists();result={'run_id':a.session_id,'status':'CANCELLED' if cancelled else 'COMPLETE','scenario':s,'result_path':'runs/'+a.session_id+'/result.json'}
 (f/'result.json').write_text(json.dumps(result));emit('COMPLETE',result=result)
''', encoding='utf-8')
    return project


def wait_status(identifier, context, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = jobs.status(identifier, **context)
        if predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError('isolated supervisor did not reach the expected status: ' + str(value))


def test_detached_supervisor_survives_new_server_and_cancels_with_partial_result(tmp_path):
    project, warehouse = isolated_engine(tmp_path), tmp_path / 'store'
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    created = jobs.start({**context, 'kind': 'normal', 'scenario': {'symbol': 'TEST', 'start': '2025-09-01',
        'end': '2025-09-08', 'mode': 'BAR', 'hold': True}})
    identifier = created['job_id']
    try:
        current = wait_status(identifier, context, lambda data: data['phase'] == 'run' and
                              (data.get('progress') or {}).get('replay') == 25)
        assert current['active'] and current['progress']['replay'] == 25
        # No JOBS/cache/session object is needed by a new web transport.
        fresh = jobs.reconnect(identifier, **context)
        assert fresh['job_id'] == identifier and fresh['active']
        assert jobs.recent(**context)['items'][0]['job_id'] == identifier
        assert jobs.stop(identifier, **context)['ok']
        ended = wait_status(identifier, context, lambda data: data['phase'] == 'cancelled' and not data['active'])
        assert ended['result_ready']
        result = json.loads((warehouse / 'runs' / identifier / 'result.json').read_text('utf-8'))
        assert result['status'] == 'CANCELLED'
        metadata = (warehouse / 'runs' / identifier / 'job.json').read_text('utf-8')
        assert str(tmp_path) not in metadata and 'C:' not in metadata
    finally:
        jobs.stop(identifier, **context)
        wait_status(identifier, context, lambda data: not data['active'])


def test_plan_approval_and_completed_result_reconnect_after_project_and_store_move(tmp_path):
    project, warehouse = isolated_engine(tmp_path), tmp_path / 'store'
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    created = jobs.start({**context, 'kind': 'normal', 'scenario': {'symbol': 'TEST', 'start': '2025-09-01',
        'end': '2025-09-08', 'mode': 'BAR', 'record': True}})
    identifier = created['job_id']
    ready = wait_status(identifier, context, lambda data: data['phase'] == 'confirm' and not data['active'])
    assert ready['plan']['record'] and not ready['result_ready']
    assert jobs.confirm(identifier, plan_revision=ready['plan_revision'], **context)['ok']
    complete = wait_status(identifier, context, lambda data: data['phase'] == 'complete' and not data['active'])
    assert complete['result_ready']
    destination = tmp_path / 'moved'
    destination.mkdir()
    shutil.move(warehouse, destination / 'store')
    shutil.move(project, destination / 'project')
    recovered = jobs.reconnect(identifier, warehouse=destination / 'store', project_root=destination / 'project',
                               python_executable=sys.executable)
    assert recovered['phase'] == 'complete' and recovered['result_ready']


@pytest.mark.parametrize('value', ['../escape', 'A' * 32, '0' * 31, None])
def test_job_ids_cannot_select_another_path(tmp_path, value):
    with pytest.raises(ValueError, match='실행 ID'):
        jobs.get(value, warehouse=tmp_path, project_root=tmp_path, python_executable=sys.executable)
