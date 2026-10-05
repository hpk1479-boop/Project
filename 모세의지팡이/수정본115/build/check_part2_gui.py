"""Construct the minimal screen without starting MT5 or network services."""
from pathlib import Path
import json,sys,tkinter as tk
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.gui import main
original=tk.Tk.mainloop
seen=[]
def inspect(root,*args,**kwargs):
    def walk(widget):
        for child in widget.winfo_children():
            try:
                text=child.cget('text')
                if text:seen.append(str(text))
            except tk.TclError:pass
            walk(child)
    root.withdraw();root.update_idletasks();walk(root);root.destroy()
tk.Tk.mainloop=inspect
try:main()
finally:tk.Tk.mainloop=original
assert all(t in seen for t in ('녹화 준비','실행','누락 조각 확인','창고 선택'))
out={'construction_ok':True,'required_controls':True,'mt5_started':False,'network_used':False}
(R/'검증결과/part2_connection/gui_smoke.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print('GUI construction PASS')
