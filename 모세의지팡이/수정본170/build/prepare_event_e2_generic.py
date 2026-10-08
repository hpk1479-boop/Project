from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/monitor_OZ.py';s=p.read_text('utf-8')
start=s.index('class GenericConditionMonitor:');end=s.index('\nclass OZCommandFileWorker',start)
section=s[start:end]
old='''    def __init__(self, symbol: str, config: dict[str, str], controller: GenericWatchController):'''
assert old in section
section=section.replace(old,'''    def __init__(self, symbol: str, config: dict[str, str], controller: GenericWatchController, *, staff_client=None, event_state=None):''')
# Preserve the original constructor's defaults; only replace external client and state slots.
section=section.replace('self.client = StaffClient(', 'self.client = staff_client if staff_client is not None else StaffClient(',1)
lines=section.splitlines()
state_names=('last_bar','touch_state','percentile_state','known_ids')
for i,line in enumerate(lines):
    if any(line.strip().startswith('self.'+name) and '=' in line for name in state_names):
        # Constructor only; subsequent updates are unchanged.
        if i>25:break
        left,right=line.split('=',1);name=left.strip().split('.')[1].split(':')[0]
        if name in state_names:lines[i]=left.split(':')[0]+'= state.setdefault('+repr(name)+', '+right.strip()+')'
pos=next(i for i,line in enumerate(lines) if line.strip().startswith('self.symbol ='))
lines.insert(pos,'        state = {} if event_state is None else event_state')
section='\n'.join(lines)+'\n'
run=section.index('    def run(self, stop_event: threading.Event) -> None:')
body_start=section.index('                _, watches =',run)
body_end=section.index('            except Exception:\n                logging.exception("[%s] 조건 Watch 처리 오류"',body_start)
body=section[body_start:body_end]
body=body.replace('''                if not watches:
                    stop_event.wait(self.loop_sleep)
                    continue''','''                if not watches:
                    return''')
body='\n'.join(line[8:] if line.startswith('        ') else line for line in body.splitlines())
replacement='''    def evaluate_once(self) -> None:
        """One committed observation; the canonical Watch decision body."""
'''+body+'''

    def run(self, stop_event: threading.Event) -> None:
        logging.info("🟦 [조건 Watch 대기] %s", self.symbol)
        while not stop_event.is_set():
            try:
                self.evaluate_once()
            except Exception:
                logging.exception("[%s] 조건 Watch 처리 오류", self.symbol)
            stop_event.wait(self.loop_sleep)


'''
section=section[:run]+replacement
s=s[:start]+section+s[end:]
p.write_text(s,encoding='utf-8')
print('Generic Watch single-observation body extracted unchanged')
