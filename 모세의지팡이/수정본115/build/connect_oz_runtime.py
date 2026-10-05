from pathlib import Path
p=Path('수정본20/Part1/program/event_engine/oz_processor.py')
s=p.read_text('utf-8');consumer=s[s.index('class OZConsumer:'):]
p.write_text('''"""Persistent NumPy OZ state; command/output envelopes remain unchanged."""
from oz_engine.runtime import OZRuntime
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,subscription_command,emit_facts


class OZProcessor:
    name='OZ_STATE'
    def __init__(self,oz,compat,config,*,symbols=()):
        self.oz=oz;self.config=dict(config);self.symbols=tuple(symbols)
    def subscriptions(self):
        return Subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),
            facts=('ATR14_GENERAL',),processor_states=('SWEEP_STATE',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('MANUAL_WATCH','CANCEL_MANUAL','RESET_ALL','SWEEP_WATCH','CANCEL_SWEEP','RESET_SWEEP'))
    def on_event(self,event,board,state):
        command=subscription_command(event,state,'SWEEP')
        if event.kind!=Kind.MARKET_BUNDLE and command is None:return
        if 'runtime' not in state:
            state['runtime']=OZRuntime(self.oz,self.config,state.pop('startup_files',{}))
        state['__board__']=state['runtime'].process(event,board,command,state.pop('startup_sweep_events',()))


'''+consumer,encoding='utf-8')
p=Path('수정본20/Part1/program/event_startup.py');s=p.read_text('utf-8')
a=s.index('    for (symbol,vm,tm),observed');b=s.index('    def save(',a)
s=s[:a]+"    oz_runtime=engine.processor_state.get('OZ_STATE',{}).get('runtime')\n    if oz_runtime is not None:files.update(oz_runtime.export_files())\n"+s[b:];p.write_text(s,encoding='utf-8')
p=Path('수정본20/Part1/program/oz_engine/runtime.py');s=p.read_text('utf-8');a=s.index('            # External qualification');b=s.index('            selected=[]',a);s=s[:a]+s[b:];p.write_text(s,encoding='utf-8')
