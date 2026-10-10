"""Only substitute explicit external clock ports, never decision expressions."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'Part1/program'
names=['monitor_OZ.py','strategy_INDICATOR.py','strategy_SWEEP.py','strategy_FVG.py',
       'manager_KIM.py','watch_orchestrator.py','command_interpreter.py']
names += [str(p.relative_to(root)) for p in (root/'SPECIAL').glob('SPECIAL*.py')]
changes=[]
for name in names:
    p=root/name;s=p.read_text('utf-8');old=s
    for a,b in [('import time\n','from domain_clock import time\n'),
                ('import datetime as dt\n','from domain_clock import datetime as dt\n'),
                ('import datetime\n','from domain_clock import datetime\n'),
                ('import uuid\n','from domain_clock import uuid\n')]:
        s=s.replace(a,b)
    if s!=old:p.write_text(s,encoding='utf-8');changes.append(name)
print(changes)
