"""A departed web-like launcher cannot take the isolated job's stdout with it.

Fake Part2 tasks and the real maintenance helper run. No calculation engine, warehouse DB,
MT5, Ollama or current user settings are used by this process smoke test.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_jobs as jobs


def fake_project(root):
    package = root / 'Part2/event_backtest'
    package.mkdir(parents=True)
    (root / 'Part1/program').mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'warehouse_cleanup.py').write_bytes(
        (ROOT / 'Part2/event_backtest/warehouse_cleanup.py').read_bytes())
    (package / 'progress_view.py').write_text(textwrap.dedent('''\
        class ProgressView:
            def __init__(self):
                self.phase = 'fake replay'
                self.build = self.virtual = {}
                self.replay = {'processed': 0, 'total': 100}
                self.lines = []
                self.warnings = []
            def accept(self, event):
                if event.get('event') == 'FAKE_PROGRESS':
                    self.replay = {'processed': event['processed'], 'total': 100}
                    self.lines.append('fake progress ' + str(event['processed']))
            def remaining(self):
                return 'fake test only'
        '''), encoding='utf-8')
    (package / '__main__.py').write_text(textwrap.dedent('''\
        import argparse
        import json
        from pathlib import Path
        import time

        parser = argparse.ArgumentParser()
        parser.add_argument('action', choices=('plan', 'run'))
        parser.add_argument('--scenario', required=True)
        parser.add_argument('--warehouse', required=True)
        parser.add_argument('--session-id', required=True)
        parser.add_argument('--skip-cleanup', action='store_true')
        parser.add_argument('--approved-token')
        parser.add_argument('--rebuild', action='store_true')
        args = parser.parse_args()
        folder = Path(args.warehouse) / 'runs' / args.session_id
        def emit(event):
            line = json.dumps(event)
            print(line, flush=True)
            if args.action == 'run':
                with (folder / 'progress.jsonl').open('a', encoding='utf-8') as output:
                    output.write(line + '\\n')
        if args.action == 'plan':
            emit({'event': 'COMPLETE', 'result': {'record': [], 'reuse': [],
                'convert': [], 'estimate': {}, 'approval_token': 'fake-plan'}})
            raise SystemExit(0)
        deadline = time.monotonic() + 30
        count = 0
        while not (folder / 'stop.request').is_file():
            if time.monotonic() >= deadline:
                emit({'event': 'ERROR', 'message': 'fake test watchdog expired'})
                raise SystemExit(1)
            count += 1
            emit({'event': 'FAKE_PROGRESS', 'processed': count})
            time.sleep(.1)
        result = {'status': 'CANCELLED', 'run_id': args.session_id,
            'scenario': json.loads(Path(args.scenario).read_text('utf-8')),
            'result_path': 'runs/' + args.session_id + '/result.json',
            'fake_processed': count}
        (folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        emit({'event': 'COMPLETE', 'result': result})
        '''), encoding='utf-8')


def wait_for(predicate, description, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError(description)


def remember_owned(folder, identities):
    """Only records in this test's fresh temporary warehouse are eligible."""
    try:
        record = json.loads((folder / 'job.json').read_text('utf-8'))
    except (OSError, ValueError):
        return
    for name in ('supervisor', 'process'):
        identity = record.get(name)
        if (isinstance(identity, dict) and type(identity.get('pid')) is int
                and identity['pid'] > 0 and str(identity.get('created', '')).isdigit()):
            identities[(identity['pid'], identity['created'])] = identity


def alive(identity):
    return jobs.process_identity(identity['pid']) == {
        'pid': identity['pid'], 'created': identity['created']}


def terminate_owned(identity):
    """Failure cleanup pins and verifies FILETIME before terminating that handle."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00101001, False, identity['pid'])
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise OSError(ctypes.get_last_error(), 'cannot clean up test-owned process')
    try:
        if jobs.process_identity(identity['pid'], handle) != {
                'pid': identity['pid'], 'created': identity['created']}:
            return
        if not kernel.TerminateProcess(handle, 1):
            raise OSError(ctypes.get_last_error(), 'cannot terminate test-owned process')
        assert kernel.WaitForSingleObject(handle, 3000) == 0, 'test-owned process did not exit'
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != 'nt', reason='Windows detached launcher/FILETIME contract')
def test_job_survives_launcher_exit_and_reconnects_then_stops(tmp_path):
    project = tmp_path / '이동할 가짜 프로젝트'
    warehouse = tmp_path / '임시 가짜 창고'
    fake_project(project)
    stale = warehouse / 'build_runs' / ('b' * 32)
    stale.mkdir(parents=True)
    (stale / 'original_0').write_bytes(b'restored old build backup')
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    launcher_code = textwrap.dedent('''\
        import json
        from pathlib import Path
        import sys
        sys.path.insert(0, sys.argv[1])
        from lab import backtest_jobs as jobs
        result = jobs.start({'warehouse': Path(sys.argv[2]),
            'project_root': Path(sys.argv[3]), 'python_executable': sys.executable,
            'kind': 'normal', 'scenario': {'symbol': 'FAKE', 'start': '2025-09-01',
                'end': '2025-09-02', 'mode': 'BAR', 'strategies': ['FAKE']},
            'adapter': {}, 'plan_only': False, 'auto_run': True})
        print(json.dumps(result), flush=True)
        ''')
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    launcher = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-c', launcher_code,
        str(ROOT / 'Part3'), str(warehouse), str(project)], cwd=project,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    identifier = None
    identities = {}
    try:
        output, errors = launcher.communicate(timeout=10)
        assert launcher.returncode == 0, errors
        assert not stale.exists(), 'copied cache must be cleaned before the new job is published'
        identifier = json.loads(output)['job_id']
        folder = warehouse / 'runs' / identifier

        def running():
            remember_owned(folder, identities)
            state = jobs.reconnect(identifier, **context)
            count = ((state.get('progress') or {}).get('replay') or {}).get('processed', 0)
            return state if state['phase'] == 'run' and state['active'] and count >= 1 else None

        first = wait_for(running, 'fake replay did not remain active after launcher exit')
        first_count = first['progress']['replay']['processed']
        def advances():
            state = running()
            return state if state and state['progress']['replay']['processed'] > first_count else None
        state = wait_for(advances, 'detached stdout/progress stopped when its launcher exited')
        assert launcher.poll() == 0 and state['recoverable']
        row = json.loads((folder / 'job.json').read_text('utf-8'))
        assert row['supervisor']['pid'] != launcher.pid
        assert row['process']['pid'] != launcher.pid
        assert alive(row['supervisor']) and alive(row['process'])
        persisted = (folder / 'job.json').read_text('utf-8')
        assert str(project) not in persisted and str(warehouse) not in persisted
        assert 'FAKE_PROGRESS' in jobs.log(identifier, **context)['text']
        assert jobs.stop(identifier, **context)['ok']

        def cancelled():
            state = jobs.reconnect(identifier, **context)
            return state if state['phase'] == 'cancelled' and state['result_ready'] else None
        wait_for(cancelled, 'cooperative stop did not publish the partial cancellation result')
        result = json.loads((folder / 'result.json').read_text('utf-8'))
        assert result['status'] == 'CANCELLED' and result['run_id'] == identifier
        assert result['fake_processed'] > first_count
        assert result['result_path'] == 'runs/' + identifier + '/result.json'
        assert (folder / 'stop.request').is_file()
        wait_for(lambda: all(not alive(row) for row in identities.values()),
                 'detached supervisor or fake replay process remained after safe stop')
        assert not jobs.reconnect(identifier, **context)['active']
    finally:
        if launcher.poll() is None:
            launcher.kill()  # The exact Popen handle, never a PID query.
            launcher.communicate(timeout=3)
        # A failed assertion cannot leave this test's temporary children behind.
        folders = list((warehouse / 'runs').glob('*/job.json')) if (warehouse / 'runs').exists() else []
        for path in folders:
            remember_owned(path.parent, identities)
            (path.parent / 'stop.request').write_text('STOP\n', encoding='ascii')
        deadline = time.monotonic() + 3
        while any(alive(row) for row in identities.values()) and time.monotonic() < deadline:
            time.sleep(.05)
        for row in identities.values():
            if alive(row):
                terminate_owned(row)
