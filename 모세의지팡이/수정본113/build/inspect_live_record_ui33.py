from pathlib import Path
import ctypes,json,sys,tkinter as tk
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/live_daily_partition_virtual'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_backtest.system import deny_network
deny_network()
from ui_theme import install
from strategy_settings_view import live_strategy_dialog
import live_record_folder_ui as ui
from machine_roots import load_live_root,save_live_root

local=OUT/'ui_pc/roots.json';folder=OUT/'ui_records';folder.mkdir(exist_ok=True)
original=ui.LiveRecordFolderButton;buttons=[]
def make(parent):
    button=original(parent,loader=lambda:load_live_root(local),
        saver=lambda p:save_live_root(p,path=local,program=OUT/'ui_app/Part1/program'))
    buttons.append(button);return button
root=tk.Tk();install(root);root.geometry('1x1+0+0')
with patch.object(ui,'LiveRecordFolderButton',make):
    win=live_strategy_dialog(root,ROOT/'Part1/program/SPECIAL',{}, {},lambda values:None)
win.geometry('860x720+30+30');win.update();button=buttons[0]
assert button.tooltip_text()=='기록 폴더 미지정'
from PIL import ImageGrab
user32=ctypes.windll.user32;user32.GetParent.argtypes=[ctypes.c_void_p];user32.GetParent.restype=ctypes.c_void_p
def shot(name):
    win.lift();win.update();ImageGrab.grab(window=user32.GetParent(win.winfo_id())).save(OUT/name)
shot('live_folder_unset.png')
with patch.object(ui.filedialog,'askdirectory',return_value=str(folder)):
    button.choose()
assert button.tooltip_text()==str(folder.resolve())
button.enter();win.update();shot('live_folder_selected.png');button.leave()
(OUT/'gui_check.json').write_text(json.dumps({'picker_saved_to_test_computer_settings':True,
    'unset_tooltip':True,'selected_tooltip':True,'header_disk_button':True},indent=2),encoding='utf-8')
win.destroy();root.destroy()
