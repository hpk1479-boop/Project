"""Offline layout and interaction checks for the Part1 Canvas control screen."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import module_status_ui as status_ui
from modern_widgets import GradientHeader, GradientPage, ModernButton, ModernCard, ModuleStatusRow


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class Part1CanvasTests(unittest.TestCase):
    def setUp(self):
        try:self.root = tk.Tk()
        except tk.TclError as exc:self.skipTest('Tk unavailable: ' + str(exc))
        source = ROOT / 'Part1/OZ_SYSTEM CONTROL.pyw'
        spec = importlib.util.spec_from_file_location(
            'control_canvas31', source,
            loader=importlib.machinery.SourceFileLoader('control_canvas31', str(source)))
        module = importlib.util.module_from_spec(spec)
        self.render = []
        with patch.object(status_ui, 'watch_status',
                          lambda _root, _part1, render:self.render.append(render)):
            spec.loader.exec_module(module)
            self.controller = module.MosesController(self.root)
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'root'):
            try:self.root.destroy()
            except tk.TclError:pass

    def test_dashboard_uses_two_by_four_cards_and_resizes(self):
        c = self.controller
        self.assertEqual(tuple(c.module_buttons), status_ui.MAIN_MODULES)
        self.assertTrue(all(isinstance(row, ModuleStatusRow)
                            for row in c.module_buttons.values()))
        self.assertTrue(all(isinstance(button, ModernButton)
                            for button in (c.start_btn, c.stop_btn, c.special_btn)))
        self.assertEqual(sum(isinstance(w, GradientPage) for w in descendants(self.root)), 1)
        self.assertEqual(sum(isinstance(w, GradientHeader) for w in descendants(self.root)), 1)
        self.assertEqual(sum(isinstance(w, ModernCard) for w in descendants(self.root)), 2)
        self.assertFalse(any(isinstance(w, tk.Button) for w in descendants(self.root)))
        for index, name in enumerate(status_ui.MAIN_MODULES):
            placement = c.module_buttons[name].grid_info()
            self.assertEqual((int(placement['row']), int(placement['column'])),
                             (index // 2, index % 2))
        before = c.module_buttons['OZ'].winfo_width()
        self.assertGreater(before, 300)
        self.assertGreaterEqual(c.module_buttons['OZ'].winfo_height(), 68)
        self.root.geometry('1020x930')
        self.root.update()
        self.assertGreater(c.module_buttons['OZ'].winfo_width(), before + 70)
        self.root.geometry('740x650')
        self.root.update()
        last_row = c.module_buttons['KIM']
        row_bottom = last_row.winfo_rooty() + last_row.winfo_height()
        self.assertLess(row_bottom, self.root.winfo_rooty() + self.root.winfo_height())
        self.assertGreaterEqual(last_row.winfo_width(), 280)

    def test_commands_disabled_state_and_status_render(self):
        c = self.controller
        self.assertIs(c.start_btn.cget('command').__self__, c)
        self.assertIs(c.stop_btn.cget('command').__self__, c)
        self.assertIs(c.special_btn.cget('command').__self__, c)
        called = []
        c.start_btn.configure(command=lambda:called.append('start'))
        c.set_busy(True)
        c.start_btn.invoke()
        self.assertEqual(called, [])
        c.set_busy(False)
        c.start_btn.invoke()
        self.assertEqual(called, ['start'])
        data = {'modules':{name:{'status':'정상','last':1.0,'errors':0,
                                 'monitoring':True} for name in status_ui.MAIN_MODULES},
                'pipe_connected':True,'pipe_seen':True,
                'telegram_attempted':True,'telegram_connected':True}
        self.render[0](data,None)
        self.assertIn('연결중',c.module_buttons['OZ'].cget('text'))
        self.assertEqual(c.module_buttons['OZ'].cget('fg'),
                         status_ui.STATE_COLORS['연결중'])

    def test_three_status_labels_preserve_existing_conditions(self):
        modules = {name:{'status':'정상','last':1.0,'errors':0,
                         'monitoring':True} for name in status_ui.MAIN_MODULES}
        data = {'modules':modules,'pipe_connected':True,'pipe_seen':True,
                'telegram_attempted':True,'telegram_connected':True,
                'enabled_specials':('SPECIAL1',)}
        self.assertEqual(set(status_ui.STATE_COLORS), {'연결 대기','연결중','오류'})
        self.assertEqual(status_ui.display_state('OZ', data), '연결중')
        modules['OZ']['monitoring'] = False
        self.assertEqual(status_ui.display_state('OZ', data), '연결 대기')
        modules['OZ']['status'] = '지연'
        self.assertEqual(status_ui.display_state('OZ', data), '오류')
        self.assertEqual(status_ui.display_state('KIM', data), '연결중')
        data['telegram_connected'] = False
        self.assertEqual(status_ui.display_state('KIM', data), '오류')


if __name__ == '__main__':
    unittest.main()
