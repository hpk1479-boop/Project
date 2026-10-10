"""Primitive Watch condition consumer, with no service loop or persistent I/O."""
from .subscription_cache import cached_subscriptions
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,command_of,plain,emit_facts
from .watch_lifetime import WatchRuntime


class WatchConditionConsumer:
    name='WATCH_CONDITIONS'
    def __init__(self,oz,compat,config,*,symbols=()):
        self.oz=oz;self.compat=compat;self.config=dict(config);self.symbols=tuple(symbols)
    def subscriptions(self):
        return cached_subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),facts=('WONBI_BANDS',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('GENERIC_WATCH', 'CANCEL_GENERIC', 'RESET_ALL'))
    def on_event(self,event,board,state,emit):
        command=command_of(event)
        if event.kind!=Kind.MARKET_BUNDLE and command is None:return
        if 'runtime' not in state:state['runtime']=WatchRuntime(self.oz,self.config,state.pop('startup_files',{}))
        events=state['runtime'].process(event,board,command)
        emit_facts(emit,event.payload['symbol'],'WATCH',events,prepared=True)
