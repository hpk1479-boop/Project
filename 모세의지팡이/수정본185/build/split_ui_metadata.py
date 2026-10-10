from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part1/program/special_ui.py';s=p.read_text('utf-8')
start=s.index('SESSIONS=');end=s.index('def trigger_dialog(')
model='"""Read-only host settings vocabulary; no GUI or engine side effects."""\nimport ast,calendar,datetime as dt,json,re\nfrom pathlib import Path\nimport oz_profiles\n\n'+s[start:end]
(p.parent/'special_settings_model.py').write_text(model,encoding='utf-8')
header='"""Host dialogs backed by canonical settings vocabulary."""\nimport copy\nimport tkinter as tk\nfrom tkinter import ttk,messagebox\nimport oz_profiles\nfrom special_settings_model import SESSIONS,discover,source_defaults,gui_dates,time_summary,time_env\n\n'
p.write_text(header+s[end:],encoding='utf-8')
# Calendar UI uses the same standard date implementation.
s=p.read_text('utf-8').replace('import copy\n','import copy,calendar,datetime as dt\n')
p.write_text(s,encoding='utf-8')
p=ROOT/'Part2/event_backtest/runner.py';s=p.read_text('utf-8').replace('from special_ui import source_defaults,SESSIONS','from special_settings_model import source_defaults,SESSIONS')
p.write_text(s,encoding='utf-8')
