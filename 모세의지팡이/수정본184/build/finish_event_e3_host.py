from pathlib import Path
import ast
R=Path(__file__).resolve().parents[1];P=R/'Part1/program';old=R.parent/'수정본18/Part1/program/manager_KIM.py'
text=old.read_text('utf-8-sig');tree=ast.parse(text);lines=text.splitlines(True)
worker=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='EconomyWorker')
source=''.join(lines[worker.lineno-1:worker.end_lineno])
cls=ast.parse(source).body[0];init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
items=source.splitlines(True)
init_text='''    def __init__(self, config, stop_event, notifier, *, program, state_directory):
        super().__init__(name="EconomyHostInput", daemon=True)
        import shutil
        self.config=dict(config);self.stop_event=stop_event;self.notifier=notifier
        self.token=config.get('TELEGRAM_TOKEN','');self.chat_id=config.get('TELEGRAM_CHAT_ID','')
        self.fetch_interval=int(config.get('ECONOMY_FETCH_SEC','1800'))
        self.poll_interval=int(config.get('ECONOMY_POLL_SEC','30'))
        state=Path(state_directory);state.mkdir(parents=True,exist_ok=True)
        self.alerted_file=state/'economy_alerted.txt';self.briefing_file=state/'economy_briefing.txt'
        program=Path(program)
        alerted=Path(config.get('ECONOMY_ALERTED_FILE','alerted_events.txt'))
        if not alerted.is_absolute():alerted=program/alerted
        briefing=Path(config.get('ECONOMY_BRIEFING_FILE','last_briefing.txt'))
        choices=[briefing] if briefing.is_absolute() else [(program if briefing.parts[0]=='logs' else program/'logs')/briefing,program/briefing]
        for target,origins in [(self.alerted_file,[alerted]),(self.briefing_file,choices)]:
            if not target.exists():
                for origin in origins:
                    if origin.is_file():shutil.copyfile(origin,target);break
        self.next_fetch_monotonic=0.;self.calendar_status_logged=False
'''
items[init.lineno-1:init.end_lineno]=[init_text]
pieces=[]
for n in tree.body:
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('KST','CALENDAR_URL','INDICATOR_NAMES_KO') for t in n.targets):pieces.append(''.join(lines[n.lineno-1:n.end_lineno]))
    if isinstance(n,ast.FunctionDef) and n.name in ('format_indicator_title','get_futures_status'):pieces.append(''.join(lines[n.lineno-1:n.end_lineno]))
(P/'event_economy_host.py').write_text('''"""Existing economic calendar host service, outside market strategy dispatch.
Only the host performs HTTP/time/file I/O. Notifications enter the sequencer.
"""
from __future__ import annotations
import datetime as dt
import logging,threading,time
from pathlib import Path
from typing import Optional
import requests
'''+ '\n'.join(pieces)+'\n'+''.join(items)+'\n',encoding='utf-8')
# Remove economic host helpers from the Composer domain, not their formulas.
path=P/'event_composer_domain.py';s=path.read_text('utf-8');nodes=ast.parse(s);ls=s.splitlines(True);ranges=[]
for n in nodes.body:
    if isinstance(n,ast.FunctionDef) and n.name in ('format_indicator_title','get_futures_status'):ranges.append((n.lineno,n.end_lineno))
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('CALENDAR_URL','INDICATOR_NAMES_KO') for t in n.targets):ranges.append((n.lineno,n.end_lineno))
for a,b in sorted(ranges,reverse=True):del ls[a-1:b]
path.write_text(''.join(ls),encoding='utf-8')
path=R/'Part1/OZ_SYSTEM CONTROL.pyw';s=path.read_text('utf-8-sig')
s=s.replace('LEGACY_PROGRAMS = (\n    ("TREND(구버전)", "strategy_TREND.py"),\n)','LEGACY_PROGRAMS = ()')
s=s.replace('script_name == "manager_KIM.py"','script_name == "event_host.py"').replace('script == "manager_KIM.py"','script == "event_host.py"')
s=s.replace('(manager_KIM 시작)','(이벤트 엔진 시작)').replace('시스템의 6개 프로그램을 모두 종료하시겠습니까?','이벤트 엔진을 종료하시겠습니까?')
path.write_text(s,encoding='utf-8')
