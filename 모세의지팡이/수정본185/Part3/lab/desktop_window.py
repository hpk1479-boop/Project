"""Show the existing localhost MOSES UI in a dedicated WebView2 window."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from .server import LabServer
from . import runtime_lifecycle
from .backtest_jobs import ShutdownPending, ForceShutdownRequested
from .desktop_instance import DesktopInstance, notify_duplicate


def _apply_window_icon():
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    icon = str(Path(__file__).resolve().parents[1] / 'web/moses.ico')
    handles = [user32.LoadImageW(None, icon, 1, size, size, 0x10) for size in (16, 32)]
    _apply_window_icon.handles = handles
    @callback_type
    def apply(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            for kind, handle in enumerate(handles):
                if handle:
                    user32.SendMessageW(hwnd, 0x80, kind, handle)
        return True
    user32.EnumWindows(apply, 0)


def _show_close_error(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, 'MOSES · 엔진 종료 실패', 0x10)


def _show_close_warning(result):
    warnings = result.get('warnings', ()) if isinstance(result, dict) else ()
    if warnings:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, '\n'.join(warnings), 'MOSES · 종료 기록 확인', 0x30)


def _confirm_force_close(window):
    message = ('강제 종료하시겠습니까?\n\n'
               '아직 저장되지 않은 결과가 유실될 수 있습니다.')
    if os.name != 'nt':
        dialog = getattr(window, 'create_confirmation_dialog', None)
        return bool(callable(dialog) and dialog('MOSES · 강제 종료', message))
    import ctypes
    # Yes / No, default No. Closing this question also keeps normal saving.
    return ctypes.windll.user32.MessageBoxW(None, message, 'MOSES · 강제 종료', 0x124) == 6


def _exit_application():
    """An explicit force close must not wait for writers or atexit hooks."""
    os._exit(0)


class _EngineCloser:
    def __init__(self, window):
        self.window = window
        self.thread = None
        self.ready = False
        self.lock = threading.Lock()
        self.waiting = False
        self.dialog_open = False
        self.choice_thread = None
        self.forcing = False
        self.force_thread = None
        self.interrupt = threading.Event()

    def _notify(self, message, *, failed=False):
        """UI feedback must never hold up saving or engine termination."""
        def update():
            title = 'THE STAFF OF MOSES' if failed else 'THE STAFF OF MOSES · 종료 확인 중…'
            try:
                setter = getattr(self.window, 'set_title', None)
                if callable(setter):
                    setter(title)
            except Exception:
                pass
            try:
                evaluate = getattr(self.window, 'evaluate_js', None)
                if callable(evaluate):
                    evaluate('if (typeof window.mosesWindowClosing === "function") '
                             'window.mosesWindowClosing(' + json.dumps(message, ensure_ascii=False)
                             + ', ' + json.dumps(failed) + ');')
            except Exception:
                pass
        threading.Thread(target=update, name='MOSES close feedback', daemon=True).start()

    def closing(self):
        with self.lock:
            if self.ready:
                return True
            if (self.thread is None or not self.thread.is_alive()) and not self.forcing:
                self.interrupt.clear()
                self.waiting = True
                self.thread = threading.Thread(target=self._stop_and_close,
                                               name='MOSES engine shutdown', daemon=True)
                self.thread.start()
            elif self.waiting and not self.dialog_open and not self.forcing:
                self.dialog_open = True
                self.choice_thread = threading.Thread(target=self._choose_while_waiting,
                                                      name='MOSES shutdown choice', daemon=True)
                self.choice_thread.start()
        # Keep the GUI responsive until engine termination has been verified.
        return False

    def _choose_while_waiting(self):
        try:
            confirmed = _confirm_force_close(self.window)
            with self.lock:
                if confirmed and self.waiting and not self.ready and not self.forcing:
                    self.forcing = True
                    self.interrupt.set()
                    self.force_thread = threading.Thread(target=self._force_and_close,
                        name='MOSES force shutdown', daemon=True)
                    self.force_thread.start()
        except Exception as exc:
            self._notify('강제 종료 확인창을 열지 못했습니다. X를 눌러 다시 시도하세요. ' + str(exc), failed=True)
        finally:
            with self.lock:
                self.dialog_open = False

    def _force_and_close(self):
        try:
            runtime_lifecycle.force_shutdown()
            with self.lock:
                self.ready = True
                self.waiting = False
            _exit_application()
        except Exception as exc:
            with self.lock:
                self.forcing = False
            self._notify('강제 종료를 완료하지 못했습니다. X를 눌러 다시 시도하세요. ' + str(exc), failed=True)
            _show_close_error(str(exc))

    def _stop_and_close(self):
        try:
            runtime_lifecycle.prepare_shutdown()
            self._notify('종료 중입니다. 기록과 결과를 저장하고 있습니다. X를 다시 누르면 강제 종료할 수 있습니다.')
            while True:
                try:
                    result = runtime_lifecycle.shutdown(force_requested=self.interrupt)
                    break
                except ForceShutdownRequested:
                    return  # The independently running force worker owns exit.
                except ShutdownPending as exc:
                    with self.lock:
                        if self.forcing or self.ready:
                            return
                        self.waiting = True
                    self._notify(str(exc) + ' X를 다시 누르면 강제 종료할 수 있습니다.')
            # An already open question must be answered before normal teardown.
            while True:
                with self.lock:
                    if self.forcing or self.ready:
                        return
                    choice_thread = self.choice_thread if self.dialog_open else None
                    if choice_thread is None:
                        self.waiting = False
                        self.ready = True
                        break
                choice_thread.join()
            _show_close_warning(result)
            self.window.destroy()
        except Exception as exc:
            with self.lock:
                if self.forcing or self.ready:
                    return
            runtime_lifecycle.cancel_shutdown()
            self._notify('엔진 종료를 확인하지 못했습니다. X를 눌러 다시 시도하세요. ' + str(exc), failed=True)
            _show_close_error(str(exc))
            return
        finally:
            with self.lock:
                self.waiting = False

    def finish(self):
        if not self.ready:
            if self.thread is not None:
                self.thread.join()
            if not self.ready:
                # Also clean up if the GUI loop exits without the closing event.
                result = runtime_lifecycle.shutdown()
                _show_close_warning(result)
                self.ready = True


def _server(factory, port):
    try:
        return factory(port)
    except OSError:
        # Preserve the browser launcher's occupied-port behaviour.
        return factory(0)


def run(*, port=8763, webview=None, server_factory=LabServer, instance_factory=None):
    instance = (instance_factory or DesktopInstance)()
    if not instance.acquire():
        notify_duplicate(instance.window)
        return
    try:
        return _run_window(port=port, webview=webview, server_factory=server_factory)
    finally:
        instance.release()


def _run_window(*, port, webview, server_factory):
    """Stop jobs, saved live state and the local server before exiting."""
    if webview is None:
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError(
                "전용 창에 pywebview가 필요합니다. "
                "사용 중인 Python에 Part3/requirements-desktop.txt를 설치하세요."
            ) from exc

    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MOSES.TradingSystem')
    server = _server(server_factory, port)
    url = f"http://127.0.0.1:{server.server_port}/#token={server.token}"
    worker = threading.Thread(target=server.serve_forever, daemon=True,
                              name="MOSES localhost UI")
    closer = None
    try:
        worker.start()
        window = webview.create_window(
            "THE STAFF OF MOSES", url,
            width=1440, height=900, min_size=(1024, 680),
            resizable=True, background_color="#0b1423",
        )
        closer = _EngineCloser(window)
        if hasattr(window.events, 'loaded'):
            window.events.loaded += _apply_window_icon
        window.events.closing += closer.closing
        # Forcing EdgeChromium prevents an unexpected legacy MSHTML fallback.
        webview.start(gui="edgechromium", private_mode=True, debug=False)
    finally:
        try:
            if closer is not None:
                closer.finish()
        finally:
            if worker.is_alive():
                server.shutdown()
            server.server_close()
            if worker.ident is not None:
                worker.join(timeout=5)
