from pathlib import Path
import sys,json,tkinter as tk,ctypes,time
from tkinter import ttk
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.result_ui import open_result
from ui_theme import install
from PIL import ImageGrab
root=tk.Tk();install(root);root.withdraw()
def descendants(w):
    for c in w.winfo_children():yield c;yield from descendants(c)
results={}
for stage,letter in [('replay','a'),('virtual','b')]:
    warehouse=OUT/'stop_runs';path=warehouse/'runs'/(letter*32)/'result.json'
    data=json.loads(path.read_text('utf-8'));assert data['status']=='CANCELLED'
    win=open_result(root,path,warehouse);win.update()
    all_widgets=list(descendants(win));labels=[w.cget('text') for w in all_widgets if isinstance(w,ttk.Label)]
    table=next(w for w in all_widgets if isinstance(w,ttk.Treeview))
    assert any('중단됨(부분 결과)' in t for t in labels) and table.get_children()
    assert any('실제 처리 구간' in t for t in labels)
    win.update();time.sleep(.2)
    user32=ctypes.windll.user32;user32.GetParent.argtypes=[ctypes.c_void_p];user32.GetParent.restype=ctypes.c_void_p
    ImageGrab.grab(window=user32.GetParent(win.winfo_id())).save(OUT/('partial_'+stage+'.png'))
    results[stage]={'status_visible':True,'period_visible':True,'table_rows':len(table.get_children())}
    win.destroy()
root.destroy()
(OUT/'partial_windows.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(results)
