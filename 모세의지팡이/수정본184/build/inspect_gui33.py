from pathlib import Path
import sys,json,tkinter as tk,time
from tkinter import ttk
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest import gui,ui_model
from event_backtest.segment_control import Segments
from event_backtest.system import deny_network
deny_network()

def descendants(widget):
    for c in widget.winfo_children():yield c;yield from descendants(c)

def inspect(root):
    from PIL import ImageGrab
    root.geometry('1180x810+20+30');root.update()
    widgets=list(descendants(root));slots=[w for w in widgets if isinstance(w,Segments)]
    target=next(s for s in slots if len(s.choices)==2);mode=next(s for s in slots if len(s.choices)==3)
    entry=next(w for w in widgets if isinstance(w,tk.Text) and int(w.cget('height'))==2)
    assert not any(isinstance(w,ttk.Radiobutton) for w in widgets)
    combos=[w for w in widgets if isinstance(w,ttk.Combobox)]
    assert all('downarrow' not in str(ttk.Style(root).layout(w.cget('style'))).lower() for w in combos)
    assert not any(isinstance(w,ttk.Entry) and w.get()=='BACKTEST' for w in widgets)
    def shot(name):
        root.lift();root.update();time.sleep(.3)
        import ctypes
        user32=ctypes.windll.user32;user32.GetParent.argtypes=[ctypes.c_void_p];user32.GetParent.restype=ctypes.c_void_p
        ImageGrab.grab(window=user32.GetParent(root.winfo_id())).save(OUT/name)
    mode.choose(0);root.update();shot('part2_alert.png')
    target.choose(1);mode.choose(1);root.update();entry.delete('1.0','end');entry.insert('1.0','골드 1분 상단 원비 터치 계속 알려줘')
    spread=next(w for w in widgets if isinstance(w,ttk.Entry) and w.cget('width')==10)
    spread.delete(0,'end');spread.insert(0,'37')
    assert spread.winfo_ismapped()
    mode.choose(0);root.update();assert not spread.winfo_ismapped()
    mode.choose(1);root.update();assert spread.get()=='37' and spread.winfo_ismapped()
    shot('part2_watch_virtual.png')
    rebuild=next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='초기화 후 녹화')
    mode.choose(2);rebuild.invoke();root.update();assert rebuild.instate(['selected'])
    mode.choose(0);mode.choose(2);root.update();assert rebuild.instate(['selected'])
    assert not spread.winfo_ismapped();shot('part2_build.png')
    (OUT/'gui_checks.json').write_text(json.dumps({'arrowless_selectors':2,'watch_rows':2,'recipient_hidden':True,'segments':[2,3],'hidden_values_preserved':True},indent=2),encoding='utf-8')
    root.destroy()

with patch.object(tk.Tk,'mainloop',inspect),patch.object(ui_model,'save',lambda *a,**k:None):
    gui.main()
