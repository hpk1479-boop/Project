"""Desktop launch lease only. It never starts or stops an engine."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

NAME = r'Global\MOSES.DesktopWindow.v1'
TITLE = 'THE STAFF OF MOSES'
_held = {}
_lock = threading.Lock()


def _kernel():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    return api


def existing_window():
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = (callback, wintypes.LPARAM)
    api.IsWindowVisible.argtypes = (wintypes.HWND,)
    api.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    found = []

    @callback
    def visit(handle, _):
        if not api.IsWindowVisible(handle):
            return True
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        title = ctypes.create_unicode_buffer(256)
        api.GetWindowTextW(handle, title, len(title))
        if title.value == TITLE or title.value.startswith(TITLE + ' ·'):
            found.append(int(handle))
            return False
        return True

    api.EnumWindows(visit, 0)
    return found[0] if found else None


def notify_duplicate(handle=None):
    if os.name != 'nt':
        print('MOSES가 이미 실행 중입니다. 기존 창을 사용해 주세요.')
        return
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    api.MessageBoxW(handle, 'MOSES가 이미 실행 중입니다.\n기존 창을 사용해 주세요.', 'MOSES · 중복 실행', 0x10030)


class DesktopInstance:
    def __init__(self, name=NAME):
        self.name, self.handle, self.owned, self.window = name, None, False, None

    def acquire(self):
        if self.owned:
            return True
        with _lock:
            if self.name in _held:
                self.window = existing_window()
                return False
            _held[self.name] = self
        try:
            if os.name == 'nt':
                import ctypes
                api = _kernel()
                self.handle = api.CreateMutexW(None, False, self.name)
                if not self.handle:
                    raise OSError(ctypes.get_last_error(), 'MOSES 실행 상태를 확인하지 못했습니다.')
                status = api.WaitForSingleObject(self.handle, 0)
                if status not in (0, 0x80):
                    if status != 0x102:
                        raise OSError(ctypes.get_last_error(), 'MOSES 실행 잠금을 확인하지 못했습니다.')
                    self.window = existing_window()
                    self.release()
                    return False
            else:
                import fcntl
                self.handle = open(Path(tempfile.gettempdir()) / (self.name.replace('\\', '_') + '.lock'), 'a+b')
                try:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.release()
                    return False
            self.owned = True
            self.window = existing_window()
            if self.window:
                self.release()
                return False
            return True
        except Exception:
            self.release()
            raise

    def release(self):
        if self.handle is not None:
            if os.name == 'nt':
                api = _kernel()
                if self.owned:
                    api.ReleaseMutex(self.handle)
                api.CloseHandle(self.handle)
            else:
                self.handle.close()
        self.handle, self.owned = None, False
        with _lock:
            if _held.get(self.name) is self:
                _held.pop(self.name)
