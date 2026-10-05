"""Part2 dashboard layout and existing control wiring, without starting a run."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))
sys.path.insert(0, str(ROOT / 'Part1' / 'program'))


def descendants(widget):
    return [child for direct in widget.winfo_children()
            for child in (direct, *descendants(direct))]


class Part2DashboardTest(unittest.TestCase):
    def test_dashboard_and_controls(self):
        from event_backtest import gui, ui_model
        from modern_widgets import ModernButton, ModernCard

        state = {'target_mode': 'SPECIAL',
                 'specials': {'SPECIAL1': {'enabled': True, 'trigger': None,
                                          'time_filters': None}},
                 'watch': {'text': '골드 1분 올존 계속 알려줘', 'chat_id': 'TEST'}}

        def inspect(root):
            try:
                root.update_idletasks()
                items = descendants(root)
                text = lambda item: str(item.cget('text')) if 'text' in item.keys() else ''
                labels = [text(item) for item in items]

                self.assertGreaterEqual(root.winfo_width(), 1000)
                cards = [item for item in items if isinstance(item, ModernCard)]
                self.assertEqual(len(cards), 8)
                for label in ('종목', '시작일 UTC', '종료일 UTC 미포함',
                              '재생 모드', '데이터', '주의 사항', '실행 로그', '결과 요약'):
                    self.assertIn(label, labels)
                self.assertEqual(labels.count('📅'), 2)

                buttons = [item for item in items if isinstance(item, ModernButton)]
                self.assertEqual({item.cget('text') for item in buttons},
                                 {'▶  백테스트 실행', '›  상세 로그 보기', '›  결과 보기'})
                run = next(item for item in buttons
                           if item.cget('text') == '▶  백테스트 실행')
                self.assertEqual(run.cget('state'), 'normal')
                self.assertTrue(callable(run.cget('command')))
                run.configure(state='disabled')
                self.assertEqual(run.cget('state'), 'disabled')
                run.configure(state='normal')
                for button in buttons:
                    self.assertTrue(callable(button.cget('command')))
                    button._enter(None)
                    self.assertTrue(button._hover)
                    button._press(None)
                    self.assertTrue(button._pressed)
                    button._leave(None)
                    button.configure(state='disabled')
                    button._press(None)
                    self.assertFalse(button._pressed)
                    button.configure(state='normal')

                self.assertEqual(int(ttk.Style(root).lookup(
                    'Dashboard.Horizontal.TProgressbar', 'thickness')), 18)

                radios = [item for item in items if isinstance(item, ttk.Radiobutton)]
                next(item for item in radios if text(item) == 'WATCH 모드').invoke()
                watch = next(item for item in items
                             if item.winfo_class() == 'TLabelframe'
                             and text(item).startswith('WATCH 모드'))
                self.assertEqual(watch.winfo_manager(), 'grid')
                trade_time = next(item for item in descendants(watch)
                                  if text(item) == '거래시간')
                self.assertEqual(str(trade_time.cget('state')), 'disabled')
                next(item for item in radios if text(item) == 'SPECIAL 모드').invoke()

                build = next(item for item in items if isinstance(item, ttk.Checkbutton)
                             and text(item) == '데이터 구축')
                rebuild = next(item for item in items if isinstance(item, ttk.Checkbutton)
                               and text(item) == '초기화 후 녹화')
                self.assertEqual(str(rebuild.cget('state')), 'disabled')
                build.invoke()
                self.assertEqual(str(rebuild.cget('state')), 'normal')
                build.invoke()
                self.assertEqual(str(rebuild.cget('state')), 'disabled')
                self.assertEqual(state['watch']['text'], '골드 1분 올존 계속 알려줘')

                root.geometry('1000x770')
                root.update_idletasks()
                for card in cards:
                    self.assertLessEqual(card.winfo_rooty() + card.winfo_height(),
                                         root.winfo_rooty() + root.winfo_height())
            finally:
                root.destroy()

        with patch.object(ui_model, 'load', return_value=state), \
             patch.object(ui_model, 'save'), \
             patch.object(tk.Tk, 'mainloop', inspect):
            gui.main()


if __name__ == '__main__':
    unittest.main()
