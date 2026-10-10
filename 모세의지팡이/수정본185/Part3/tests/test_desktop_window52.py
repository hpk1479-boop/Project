"""The desktop shell must use WebView2 and close its localhost server."""

from __future__ import annotations

import sys
import os
import runpy
import threading
import tempfile
import unittest
import urllib.request
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch


PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))

from lab import desktop_window
from lab import backtest_jobs, catalog, runtime_lifecycle, unified_live
from lab.ai.model_runtime import RUNTIME
from common_ai import client as common_client


class FakeClosing:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWebview:
    def __init__(self, error=None):
        self.error = error
        self.window = None
        self.start_kwargs = None
        self.events = SimpleNamespace(closing=FakeClosing())
        self.closed = False
        self.closed_event = threading.Event()

    def create_window(self, *args, **kwargs):
        self.window = (args, kwargs)
        return self

    def destroy(self):
        self.closed = all(handler() is not False for handler in self.events.closing.handlers)
        if self.closed:
            self.closed_event.set()

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        url = self.window[0][1]
        with urllib.request.urlopen(url.split("#", 1)[0], timeout=5) as response:
            assert response.status == 200
            assert b"MOSES" in response.read()
        if self.error:
            raise self.error
        assert len(self.events.closing.handlers) == 1
        assert self.events.closing.handlers[0]() is False
        assert self.closed_event.wait(5)


class DesktopWindowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='moses desktop fixture ')
        self.addCleanup(temporary.cleanup)
        project = patch.object(catalog, 'ROOT', Path(temporary.name) / 'Part3')
        project.start()
        self.addCleanup(project.stop)
        self.ai_client = Mock(spec=['shutdown', 'close'])
        # Lifecycle fixtures must not interact with a user's open desktop.
        isolated = patch.object(desktop_window, 'DesktopInstance', return_value=SimpleNamespace(
            acquire=lambda: True, release=lambda: None))
        isolated.start()
        self.addCleanup(isolated.stop)
        # Desktop lifecycle tests must never stop real running engines.
        for owner, name, result in (
            (common_client, 'Client', self.ai_client),
            (runtime_lifecycle, 'prepare_shutdown', None),
            (backtest_jobs, 'shutdown', {'ok': True, 'warnings': []}),
            (RUNTIME, 'shutdown', None),
            (desktop_window, '_show_close_error', None),
            (desktop_window, '_show_close_warning', None),
        ):
            replacement = patch.object(owner, name, return_value=result)
            replacement.start()
            self.addCleanup(replacement.stop)
        for name, value in (('_closing', False), ('_SESSION_CONTEXTS', {}), ('_SHUTDOWN_PENDING', {})):
            replacement = patch.object(backtest_jobs, name, value, create=True)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.shutdown = patch.object(unified_live, 'shutdown')
        self.stop_engines = self.shutdown.start()
        self.addCleanup(self.shutdown.stop)

    def test_integrated_launcher_uses_desktop_window(self):
        old_cwd = Path.cwd()
        old_path = sys.path[:]
        try:
            with patch.object(desktop_window, "run") as start:
                runpy.run_path(str(PART3.parent / "START_MOSES.pyw"))
            start.assert_called_once_with()
        finally:
            os.chdir(old_cwd)
            sys.path[:] = old_path

    def test_webview2_uses_existing_local_server_and_closes_it(self):
        gui = FakeWebview()
        servers = []

        def factory(port):
            server = desktop_window.LabServer(port)
            servers.append(server)
            return server

        with patch("webbrowser.open", side_effect=AssertionError("browser opened")):
            desktop_window.run(port=0, webview=gui, server_factory=factory)

        url = gui.window[0][1]
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        self.assertTrue(url.endswith("/#token=" + servers[0].token))
        self.assertEqual(gui.start_kwargs["gui"], "edgechromium")
        self.assertTrue(gui.start_kwargs["private_mode"])
        self.assertFalse(gui.start_kwargs["debug"])
        self.assertEqual(gui.window[1]["min_size"], (1024, 680))
        self.assertEqual(servers[0].fileno(), -1)
        self.assertTrue(gui.closed)
        self.stop_engines.assert_called_once_with()

    def test_gui_failure_also_closes_local_server(self):
        gui = FakeWebview(error=RuntimeError("WebView2 unavailable"))
        servers = []

        def factory(port):
            server = desktop_window.LabServer(port)
            servers.append(server)
            return server

        with self.assertRaisesRegex(RuntimeError, "WebView2 unavailable"):
            desktop_window.run(port=0, webview=gui, server_factory=factory)
        self.assertEqual(servers[0].fileno(), -1)
        self.stop_engines.assert_called_once_with()

    def test_missing_pywebview_does_not_start_server(self):
        with patch.dict(sys.modules, {"webview": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-desktop.txt"):
                desktop_window.run(server_factory=lambda port: self.fail("server started"))


if __name__ == "__main__":
    unittest.main()
