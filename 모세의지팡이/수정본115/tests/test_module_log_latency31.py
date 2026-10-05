"""Offline UI regression checks for prompt module log navigation."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import module_status_ui as ui


def snapshot(name, **options):
    return {
        'modules': {key: {'status': '정상', 'last': None, 'errors': 0,
                          'monitoring': True} for key in ui.MODULES},
        'pipe_connected': True, 'pipe_seen': True,
        'telegram_connected': True, 'telegram_attempted': True,
        'enabled_specials': ui.SPECIAL_MODULES,
        'lines': [(1, name + ' 핵심 로그')],
        'trace_enabled': True,
        'detail_enabled': bool(options.get('detail', False)),
    }


class ModuleLogLatencyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Tk display unavailable: ' + str(exc))
        self.root.withdraw()

    def tearDown(self):
        if hasattr(self, 'root'):
            self.root.destroy()

    def until(self, condition, timeout=0.7):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            self.root.update()
            if condition():
                return time.monotonic() - start
            time.sleep(0.01)
        self.fail('UI did not update promptly')

    def test_open_switch_and_detail_are_immediate(self):
        calls = []

        def fake(_root, name, **options):
            calls.append((name, options))
            return snapshot(name, **options)

        with patch.object(ui, 'read_snapshot', fake):
            win = ui.open_window(self.root, ROOT / 'Part1', initial='OZ')
            text = next(w for w in win.winfo_children() if isinstance(w, tk.Text))
            self.until(lambda: 'OZ 핵심 로그' in text.get('1.0', 'end'))
            win.select_module('KIM')
            self.until(lambda: 'KIM 핵심 로그' in text.get('1.0', 'end'))
            self.assertNotIn('OZ 핵심 로그', text.get('1.0', 'end'))
            detail = next(w for w in win.winfo_children() if isinstance(w, ttk.Frame))
            checkbox = next(w for w in detail.winfo_children()
                            if isinstance(w, ttk.Checkbutton) and w.cget('text') == '상세 로그 보기')
            checkbox.invoke()
            self.until(lambda: any(name == 'KIM' and opts.get('detail') is True
                                   for name, opts in calls))
            win.destroy()

    def test_late_response_cannot_replace_new_selection(self):
        started = threading.Event()
        release = threading.Event()

        def fake(_root, name, **options):
            if name == 'OZ':
                started.set()
                release.wait(1)
            return snapshot(name, **options)

        with patch.object(ui, 'read_snapshot', fake):
            win = ui.open_window(self.root, ROOT / 'Part1', initial='OZ')
            text = next(w for w in win.winfo_children() if isinstance(w, tk.Text))
            self.until(started.is_set)
            win.select_module('KIM')
            release.set()
            self.until(lambda: 'KIM 핵심 로그' in text.get('1.0', 'end'))
            self.assertNotIn('OZ 핵심 로그', text.get('1.0', 'end'))
            win.destroy()

    def test_control_reuses_one_main_log_window(self):
        source = ROOT / 'Part1/OZ_SYSTEM CONTROL.pyw'
        spec = importlib.util.spec_from_file_location(
            'control_log_latency31', source,
            loader=importlib.machinery.SourceFileLoader('control_log_latency31', str(source)))
        module = importlib.util.module_from_spec(spec)
        with patch('shutil.rmtree'), patch.object(ui, 'watch_status', lambda *_args: None), \
                patch.object(ui, 'read_snapshot', lambda _root, name, **opts: snapshot(name, **opts)):
            spec.loader.exec_module(module)
            control = module.MosesController(self.root)
            control.module_buttons['OZ'].invoke()
            windows = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertEqual(len(windows), 1)
            control.module_buttons['KIM'].invoke()
            self.root.update()
            self.assertEqual([w for w in self.root.winfo_children()
                              if isinstance(w, tk.Toplevel)], windows)
            self.assertIn('[김비서]', windows[0].title())


if __name__ == '__main__':
    unittest.main()
