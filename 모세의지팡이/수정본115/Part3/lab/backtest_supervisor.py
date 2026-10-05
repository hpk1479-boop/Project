"""Independent stdout/progress owner; it never calculates a strategy result."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lab import backtest_jobs as jobs
else:
    from . import backtest_jobs as jobs


def projection(job):
    for path in (job['project_root'] / 'Part2', job['project_root'] / 'Part1/program'):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from event_backtest.progress_view import ProgressView
    view = ProgressView()
    view.build_only = job['scenario'].get('build_only', False)
    if job.get('plan'):
        view.accept({'event': 'BUILD_PLAN', **job['plan']})
    return view


def projection_snapshot(view):
    if view is None:
        return None
    return {'phase': view.phase, 'build': view.build, 'replay': view.replay,
            'virtual': view.virtual, 'remaining': view.remaining(),
            'lines': view.lines[-30:], 'warnings': view.warnings[-20:]}


class Supervisor:
    def __init__(self, identifier, warehouse, project_root, run_id, python_executable):
        self.id = jobs._identifier(identifier)
        self.warehouse = Path(warehouse).resolve()
        self.project_root = Path(project_root).resolve()
        self.python_executable = python_executable
        self.folder = jobs._folder(self.warehouse, identifier)
        self.run_id = run_id
        self.view = None
        self.view_lock = threading.RLock()
        self.process = None
        self.process_lock = threading.RLock()
        self.done = threading.Event()
        self.last_result = None
        self.last_error = None
        self.last_progress_write = 0.0

    def _read_locked(self):
        record = jobs._read(self.folder)
        if (record.get('supervisor') or {}).get('run_id') != self.run_id:
            raise ValueError('백테스트 관리 실행 ID가 변경되었습니다.')
        return record

    def snapshot(self):
        with jobs.job_lock(self.folder):
            record = self._read_locked()
        return {**record, 'folder': self.folder, 'warehouse': self.warehouse,
                'project_root': self.project_root, 'python_executable': self.python_executable,
                'scenario_path': self.folder / record['scenario_file']}

    def update(self, **changes):
        with jobs.job_lock(self.folder):
            record = self._read_locked()
            if record.get('cancel_requested') and changes.get('phase') in ('planning', 'plan', 'planned', 'confirm', 'starting'):
                changes['phase'] = 'cancelled'
            if record['phase'] == 'cancelled' and changes.get('phase') == 'error':
                changes.pop('phase')
                changes.pop('message', None)
            record.update(jobs._clean(changes, self.warehouse, self.project_root))
            jobs._write(self.folder, record)
        return record

    def _progress(self, *, force=False):
        now = time.monotonic()
        if not force and now - self.last_progress_write < .2:
            return
        with self.view_lock:
            value = projection_snapshot(self.view)
        if value is not None:
            self.update(progress=value)
        self.last_progress_write = now

    def check_control(self):
        """Cancellation and its acknowledged Popen boundary share job_lock."""
        with jobs.job_lock(self.folder):
            record = self._read_locked()
            if record.get('cancel_requested'):
                action = (record.get('process') or {}).get('action')
                with self.process_lock:
                    process = self.process
                    if action == 'plan' and process is not None and process.poll() is None:
                        process.terminate()  # Planning performs no MT5/partial run.
                    elif action == 'run':
                        (self.folder / 'stop.request').write_text('STOP\n', encoding='ascii')
        self._progress()

    def _control_loop(self):
        while not self.done.wait(.1):
            try:
                self.check_control()
            except OSError:
                # A transient file error must not abandon future stop checks.
                continue
            except ValueError:
                if self.done.is_set():
                    return
                continue

    def execute_action(self, action, approved=None):
        job = self.snapshot()
        # Preparation can be slow; the locked check below must run afterwards.
        view = projection(job) if action == 'run' else None
        args, overrides, cwd = jobs.command(job, action, approved=approved)
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
        env.update(overrides)
        process = None
        try:
            with jobs.job_lock(self.folder):
                record = self._read_locked()
                if record.get('cancel_requested'):
                    record['phase'] = 'cancelled'
                    jobs._write(self.folder, record)
                    return None
                process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                    encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                with self.process_lock:
                    self.process = process
                identity = jobs.process_identity(process.pid, getattr(process, '_handle', None))
                if identity is None:
                    # A fast completed child still has an owned stdout handle.
                    identity = {'pid': process.pid, 'created': ''}
                record['process'] = {**identity, 'action': action}
                if action == 'run':
                    record['run_started'] = True
                record['phase'] = action
                jobs._write(self.folder, record)
        except Exception:
            if process is not None:
                self._failed_publication(process, action)
            raise
        self.view = view
        self.last_result = self.last_error = None
        self.done.clear()
        monitor = threading.Thread(target=self._control_loop, daemon=True, name='backtest-control')
        monitor.start()
        try:
            with (self.folder / 'console.log').open('a', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(jobs._clean(line, self.warehouse, self.project_root))
                    log.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get('event') == 'COMPLETE':
                        self.last_result = event.get('result')
                    elif event.get('event') in ('ERROR', 'HISTORY_MISSING', 'CONFIRMATION_REQUIRED'):
                        self.last_error = event
                    if view is not None:
                        with self.view_lock:
                            view.accept(event)
                        self._progress(force=event.get('event') in ('COMPLETE', 'ERROR', 'HISTORY_MISSING'))
            return process.wait()
        finally:
            self.done.set()
            monitor.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            with self.process_lock:
                self.process = None
            self.update(process=None)

    def _failed_publication(self, process, action):
        """Keep ownership of the exact child when publication/identity fails."""
        if action == 'plan':
            if process.poll() is None:
                process.terminate()
        else:
            try:
                (self.folder / 'stop.request').write_text('STOP\n', encoding='ascii')
            except OSError:
                pass  # Retain stdout ownership even if the volume is offline.
        # Drain concurrently so a full stdout pipe cannot prevent safe-stop
        # checkpoints. Only this failed publication path has a force deadline.
        def drain():
            for line in process.stdout:
                try:
                    with (self.folder / 'console.log').open('a', encoding='utf-8') as log:
                        log.write(jobs._clean(line, self.warehouse, self.project_root))
                except OSError:
                    pass
        reader = threading.Thread(target=drain, daemon=True, name='backtest-failed-publication')
        reader.start()
        try:
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                # Popen retains the original Windows process handle, so this
                # PID cannot be reassigned while its tree is terminated.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                        capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
                else:
                    process.kill()
                process.wait(timeout=3)
            reader.join(timeout=2)
            if not reader.is_alive():
                process.stdout.close()
        finally:
            with self.process_lock:
                self.process = None

    def execute_run(self, approved=None):
        try:
            if self.snapshot().get('cancel_requested'):
                self.update(phase='cancelled')
                return
            code = self.execute_action('run', approved)
            job = self.snapshot()
            if code is None and job.get('cancel_requested'):
                self.update(phase='cancelled', returncode=None)
            elif code == 2 and (self.last_error or {}).get('event') == 'CONFIRMATION_REQUIRED':
                plan = {k: v for k, v in self.last_error.items() if k != 'event'}
                self.update(phase='confirm', plan=plan, returncode=code,
                            message='데이터 구축 계획이 변경되었습니다. 새 계획을 다시 확인하세요.')
            elif code:
                self.update(phase='error', returncode=code,
                    message=(self.last_error or {}).get('message', '백테스트를 완료하지 못했습니다.'))
            elif (self.last_result or {}).get('status') == 'CANCELLED':
                self.update(phase='cancelled', returncode=code)
            else:
                # A completed Part2 result remains complete even if a stop
                # arrived at the last output boundary.
                self.update(phase='complete', returncode=code)
        except Exception as exc:
            if self.snapshot()['phase'] != 'cancelled':
                self.update(phase='error', message=str(exc))

    def execute_plan(self):
        try:
            if self.snapshot().get('cancel_requested'):
                self.update(phase='cancelled')
                return
            code = self.execute_action('plan')
            job = self.snapshot()
            if job.get('cancel_requested'):
                self.update(phase='cancelled', returncode=code)
                return
            if code or not isinstance(self.last_result, dict):
                self.update(phase='error', returncode=code,
                    message=(self.last_error or {}).get('message', '데이터 구축 계획을 확인하지 못했습니다.'))
                return
            plan = self.last_result
            if not job.get('auto_run', True):
                self.update(phase='planned', plan=plan, returncode=code)
            elif plan.get('record'):
                self.update(phase='confirm', plan=plan, returncode=code)
            else:
                self.update(phase='starting', plan=plan)
                self.execute_run(None)
        except Exception as exc:
            if self.snapshot()['phase'] != 'cancelled':
                self.update(phase='error', message=str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser()
    for name in ('warehouse', 'project-root', 'job-id', 'run-id', 'python'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--action', choices=('plan', 'run'), required=True)
    args = parser.parse_args(argv)
    owner = Supervisor(args.job_id, args.warehouse, args.project_root, args.run_id, args.python)
    try:
        job = owner.snapshot()
        identity = jobs.process_identity(os.getpid())
        expected = job.get('supervisor') or {}
        if not identity or expected.get('pid') != identity['pid'] or expected.get('created') != identity['created']:
            raise ValueError('백테스트 관리 프로세스가 현재 실행 기록과 일치하지 않습니다.')
        if args.action == 'plan':
            owner.execute_plan()
        else:
            owner.execute_run((job.get('plan') or {}).get('approval_token'))
        return 1 if owner.snapshot()['phase'] in ('error', 'interrupted') else 0
    except Exception as exc:
        try:
            owner.update(phase='error', message=str(exc))
        except Exception:
            pass
        print(jobs._clean('백테스트 관리 오류: ' + str(exc), Path(args.warehouse), Path(args.project_root)), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
