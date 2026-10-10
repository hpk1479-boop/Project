"""Developer supervisor: prepare, build in a child, then remove build caches."""
from __future__ import annotations

from contextlib import contextmanager
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from .builder import BuildSettings, _portable
from .license_runtime import canonical


def workspace(root):
    root = Path(root).resolve()
    tools = root / '통합설치'
    if not tools.is_dir() or tools.is_symlink() or tools.resolve() != tools:
        raise RuntimeError('배포 도구 폴더를 확인할 수 없습니다.')
    temporary = tools / '.buildtmp'
    if temporary.is_symlink() or (temporary.exists() and temporary.resolve() != temporary):
        raise RuntimeError('배포 준비 폴더의 외부 연결을 허용하지 않습니다.')
    temporary.mkdir(exist_ok=True)
    return tools, temporary


@contextmanager
def release_lock(root, name='release'):
    if name not in {'release', 'worker'}:
        raise ValueError('잘못된 배포 잠금 이름')
    _, temporary = workspace(root)
    target = temporary / (name + '.lock')
    if target.is_symlink() or (target.exists() and target.resolve() != target):
        raise RuntimeError('배포 잠금 파일의 외부 연결을 허용하지 않습니다.')
    stream = target.open('a+b')
    if not target.stat().st_size:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        stream.close()
        raise RuntimeError('이 수정본에서 배포 작업이 이미 진행 중입니다.') from error
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == 'nt':
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class ProcessTree:
    """A Windows job also ends child processes if the supervisor is killed."""
    def __init__(self, process, *, suspended=False):
        self.handle = None
        if os.name != 'nt':
            return
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                        ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Limits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', Counters),
                        ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        limits = Limits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if (not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or
                not self.kernel.AssignProcessToJobObject(handle, int(process._handle))):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        if suspended:
            self._resume(process.pid)

    def _resume(self, pid):
        # Popen closes the initial thread handle. Find that suspended thread
        # before it executes anything, after the process is assigned to the job.
        from ctypes import wintypes
        class ThreadEntry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                        ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG),
                        ('dwFlags', wintypes.DWORD)]
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenThread.restype = wintypes.HANDLE
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    handle = self.kernel.OpenThread(2, False, entry.th32ThreadID)
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.kernel.ResumeThread(handle) == 0xffffffff:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.kernel.CloseHandle(handle)
                    return
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError('배포 준비 프로세스의 실행을 시작하지 못했습니다.')
        except BaseException:
            self.close()
            raise
        finally:
            self.kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def environment(root):
    tools, temporary = workspace(root)
    values = os.environ.copy()
    values.update(PYTHONPATH=str(tools), PYTHONDONTWRITEBYTECODE='1',
                  PYTHONUTF8='1', PYTHONUNBUFFERED='1',
                  PIP_DISABLE_PIP_VERSION_CHECK='1', TEMP=str(temporary), TMP=str(temporary))
    return values


def stream_process(args, root, emit, *, check=True, timeout=1800):
    process = subprocess.Popen([str(arg) for arg in args], cwd=root, env=environment(root),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                               errors='replace', creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                                               | (4 if os.name == 'nt' else 0)))
    tree = None
    lines = queue.Queue()
    tail = []
    def read_lines():
        try:
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)
    try:
        tree = ProcessTree(process, suspended=(os.name == 'nt'))
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        finished = False
        while not finished:
            if time.monotonic() >= deadline:
                raise RuntimeError('배포 준비 또는 생성 시간이 초과되었습니다.')
            try:
                line = lines.get(timeout=.2)
            except queue.Empty:
                continue
            if line is None:
                finished = True
            elif line:
                text = _portable(line, root)
                tail = (tail + [text])[-12:]
                emit(text)
        code = process.wait(timeout=max(1, deadline - time.monotonic()))
        if check and code:
            raise RuntimeError('배포 단계 실패: ' + '\n'.join(tail))
        return code
    finally:
        if process.poll() is None:
            if tree:
                tree.close()
            process.kill()
            process.wait(timeout=10)
        if tree:
            tree.close()
        process.stdout.close()


def prepare_environment(root, emit=print):
    if sys.version_info[:2] not in {(3, 12), (3, 13)}:
        raise RuntimeError('배포 도구에는 Python 3.12 또는 3.13이 필요합니다.')
    tools, _ = workspace(root)
    directory = tools / '.buildenv'
    if directory.is_symlink() or (directory.exists() and directory.resolve() != directory):
        raise RuntimeError('빌드 환경의 외부 연결을 허용하지 않습니다.')
    worker = directory / 'Scripts' / 'python.exe'
    if not worker.is_file():
        emit('배포 도구 준비 중…')
        stream_process([sys.executable, '-B', '-m', 'venv', str(directory)], root, emit)
    checker = tools / 'releasekit' / 'check_build_environment.py'
    if stream_process([worker, '-B', checker], root, lambda _: None, check=False):
        emit('배포 도구 다운로드 중…')
        stream_process([worker, '-B', '-m', 'pip', '--disable-pip-version-check',
                        'install', '--no-cache-dir', '--timeout', '60', '--retries', '2',
                        '-r', tools / 'releasekit' / 'requirements-build.txt'], root, emit)
        stream_process([worker, '-B', checker], root, emit)
    emit('배포 도구 준비 완료')
    return worker


def run_release(root, settings, *, emit=print, metaeditor=None):
    root = Path(root).resolve()
    settings.validate()
    from .cleanup import cleanup_build_files
    from .prerequisites import ensure_webview2
    with release_lock(root):
        # A leftover worker must finish before we touch its build environment.
        with release_lock(root, 'worker'):
            pass
        _, temporary = workspace(root)
        token = uuid.uuid4().hex
        result_path = temporary / ('build_' + token + '.json')
        final = report = evidence = None
        try:
            worker = prepare_environment(root, emit)
            ensure_webview2(root, emit=emit)
            emit('Setup 파일 생성 및 검증 중…')
            args = [worker, '-B', '-m', 'releasekit.build_worker', '--version', settings.version,
                    '--expires', settings.expiry_date, '--install-minutes', settings.install_minutes,
                    '--nonce', token, '--parent-pid', os.getpid(), '--result', result_path.name]
            if metaeditor:
                args.extend(['--metaeditor', metaeditor])
            stream_process(args, root, emit, timeout=3600)
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('schema') != 1 or result.get('nonce') != token or result.get('passed') is not True:
                raise RuntimeError('배포 생성 완료 결과를 확인하지 못했습니다.')
            final, report, evidence = verified_result(root, result)
            emit('Setup 생성 완료: ' + final.relative_to(root).as_posix())
        finally:
            # The subprocess job has already ended all child processes here.
            with release_lock(root, 'worker'):
                cleanup = cleanup_build_files(root, emit=emit)
            result_path.unlink(missing_ok=True)
            if report is not None:
                report['cleanup'] = cleanup
                (evidence / 'build_summary.json').write_bytes(canonical(report))
        return final, report


def verified_result(root, result):
    """Tie the completion record to the actual installer and on-disk proofs."""
    try:
        candidate = result['report']
        final = root / result['installer']
        validation = candidate['release_validation']
        proof = root / validation['report']
        if (not isinstance(candidate, dict) or not isinstance(validation, dict)
                or candidate['installer'] != result['installer']
                or candidate['source_unchanged'] is not True or validation['passed'] is not True
                or final.suffix.lower() != '.exe' or not final.resolve().is_relative_to(root / '배포')
                or not final.is_file() or final.is_symlink()
                or proof.name != 'release_validation.json'
                or not proof.resolve().is_relative_to(root / '검증결과' / 'license96')
                or proof.is_symlink()):
            raise ValueError('invalid completed artifact')
        disk = json.loads((proof.parent / 'build_summary.json').read_text(encoding='utf-8'))
        checks = json.loads(proof.read_text(encoding='utf-8'))
        summary = proof.parent / 'build_summary.json'
        if (disk != candidate or checks.get('passed') is not True
                or summary.is_symlink() or not summary.resolve().is_relative_to(proof.parent.resolve())
                or len(checks.get('checks', [])) != 3
                or {check.get('mode') for check in checks['checks']} != {'imports', 'http', 'gui'}
                or any(check.get('passed') is not True or not check.get('checks') for check in checks['checks'])
                or any(row.get('passed') is not True for check in checks['checks'] for row in check['checks'])
                or hashlib.sha256(final.read_bytes()).hexdigest() != candidate['installer_sha256']):
            raise ValueError('mismatched completed proof')
        return final, candidate, proof.parent
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        raise RuntimeError('완성된 Setup 파일과 검증 기록을 확인하지 못했습니다.') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--expires', required=True)
    parser.add_argument('--install-minutes', type=int, default=10)
    parser.add_argument('--metaeditor')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    run_release(root, BuildSettings(args.version, args.expires, args.install_minutes),
                emit=lambda line: print(line, flush=True), metaeditor=args.metaeditor)


if __name__ == '__main__':
    main()
