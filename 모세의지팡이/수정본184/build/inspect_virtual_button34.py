from pathlib import Path
import sys,tkinter as tk,json,ctypes
from unittest.mock import patch
from PIL import ImageGrab
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/UI_가상진입_버튼'
stage=sys.argv[1] if len(sys.argv)>1 else 'before'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest import gui
from modern_widgets import ModernButton,ModernCard
from event_backtest.segment_control import Segments

def descendants(w):
    for c in w.winfo_children():
        yield c
        yield from descendants(c)

def inspect(root):
    root.update()
    widgets=list(descendants(root))
    run=next(w for w in widgets if isinstance(w,ModernButton) and '백테스트 실행' in w.cget('text'))
    stop=next(w for w in widgets if isinstance(w,ModernButton) and w.cget('text')=='■  정지')
    slot=next(w for w in widgets if isinstance(w,Segments) and any(v=='VIRTUAL_ENTRY' for _,v in w.choices))
    cards=[w for w in widgets if isinstance(w,ModernCard)]
    right=next(w for w in cards if run.winfo_rootx()>=w.winfo_rootx() and run.winfo_rootx()<w.winfo_rootx()+w.winfo_width() and run.winfo_rooty()>=w.winfo_rooty() and run.winfo_rooty()<w.winfo_rooty()+w.winfo_height())
    u32=ctypes.windll.user32;u32.GetParent.argtypes=[ctypes.c_void_p];u32.GetParent.restype=ctypes.c_void_p
    data={}
    for mode in ('ALERT_ONLY','VIRTUAL_ENTRY'):
        i=next(i for i,(_,v) in enumerate(slot.choices) if v==mode)
        slot.choose(i);root.update()
        data[mode]={'button_height':run.winfo_height(),'stop_height':stop.winfo_height(),'card_height':right.winfo_height(),'card_bottom':right.winfo_rooty()+right.winfo_height(),'button_bottom':run.winfo_rooty()+run.winfo_height()}
        ImageGrab.grab(window=u32.GetParent(root.winfo_id())).save(OUT/(stage+'_'+mode+'.png'))
    if stage=='after':
        rebuild=next(w for w in widgets if w.winfo_class()=='TCheckbutton' and w.cget('text')=='초기화 후 녹화')
        cycle=[]
        for mode in ('BUILD_ONLY','VIRTUAL_ENTRY','ALERT_ONLY','VIRTUAL_ENTRY',
                     'BUILD_ONLY','VIRTUAL_ENTRY','BUILD_ONLY','VIRTUAL_ENTRY'):
            slot.choose(next(i for i,(_,v) in enumerate(slot.choices) if v==mode));root.update()
            cycle.append({'mode':mode,'button_height':run.winfo_height(),'stop_height':stop.winfo_height(),
                          'rebuild_row_visible':bool(rebuild.master.winfo_manager())})
            assert run.winfo_height()==stop.winfo_height()==48
            assert bool(rebuild.master.winfo_manager())==(mode=='BUILD_ONLY')
        data['repeat_switches']=cycle
        ImageGrab.grab(window=u32.GetParent(root.winfo_id())).save(OUT/'after_build_to_virtual.png')
        root.geometry('1000x770');root.update()
        for mode in ('ALERT_ONLY','VIRTUAL_ENTRY','BUILD_ONLY'):
            slot.choose(next(i for i,(_,v) in enumerate(slot.choices) if v==mode));root.update()
            data['minimum_'+mode]={'button_height':run.winfo_height(),'stop_height':stop.winfo_height(),
                'button_bottom':run.winfo_rooty()+run.winfo_height(),
                'window_bottom':root.winfo_rooty()+root.winfo_height()}
            assert run.winfo_height()>=48 and stop.winfo_height()>=48
            assert data['minimum_'+mode]['button_bottom']<=data['minimum_'+mode]['window_bottom']
        assert data['ALERT_ONLY']['button_height']==data['VIRTUAL_ENTRY']['button_height']==48
    (OUT/(stage+'_sizes.json')).write_text(json.dumps(data,indent=2),encoding='utf8')
    root.destroy()
with patch.object(tk.Tk,'mainloop',inspect):gui.main()
