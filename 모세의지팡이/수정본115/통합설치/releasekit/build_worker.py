"""Internal build child. Its venv can be deleted only after this process exits."""
import argparse
import ctypes
import os
from pathlib import Path
import re

from .builder import BuildSettings, build
from .license_runtime import canonical
from .workflow import release_lock, workspace


def parent_alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--expires', required=True)
    parser.add_argument('--install-minutes', type=int, required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--metaeditor')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}', args.nonce) or args.result != 'build_' + args.nonce + '.json':
        raise ValueError('잘못된 배포 완료 기록 이름')
    root = Path(__file__).resolve().parents[2]
    _, temporary = workspace(root)
    with release_lock(root, 'worker'):
        if not parent_alive(args.parent_pid):
            raise RuntimeError('배포 준비 창이 종료되어 생성을 중단했습니다.')
        final, report = build(root, BuildSettings(args.version, args.expires, args.install_minutes),
                              emit=lambda line: print(line, flush=True), metaeditor=args.metaeditor)
        record = {'schema': 1, 'nonce': args.nonce, 'passed': True,
                  'installer': final.relative_to(root).as_posix(), 'report': report}
        target = temporary / args.result
        pending = target.with_suffix('.part')
        pending.write_bytes(canonical(record))
        pending.replace(target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
