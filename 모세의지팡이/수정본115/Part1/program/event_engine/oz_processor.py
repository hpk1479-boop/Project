"""Persistent NumPy OZ state; command/output envelopes remain unchanged."""
from oz_engine.runtime import OZRuntime,Collector as MessageCollector
from .subscription_cache import cached_subscriptions
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,subscription_command,emit_facts


class OZProcessor:
    name='OZ_STATE'
    def __init__(self,oz,compat,config,*,symbols=(),selection=None,resource_keys=()):
        self.oz=oz;self.config=dict(config);self.symbols=tuple(symbols)
        self.selection=selection
        self.resource_keys=tuple(resource_keys)
    def subscriptions(self):
        return cached_subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),
            facts=('ATR14_GENERAL','WONBI_BANDS'),processor_states=('SWEEP_STATE',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('MANUAL_WATCH','CANCEL_MANUAL','RESET_ALL','SWEEP_WATCH','CANCEL_SWEEP','RESET_SWEEP'))
    def on_event(self,event,board,state):
        command=subscription_command(event,state,'SWEEP')
        if event.kind!=Kind.MARKET_BUNDLE and command is None:return
        if 'runtime' not in state:
            state['runtime']=OZRuntime(self.oz,self.config,state.pop('startup_files',{}),selection=self.selection)
        state['__board__']=state['runtime'].process(event,board,command,state.pop('startup_sweep_events',()))
        if self.resource_keys:
            state['__board__']['resources']=state['runtime'].resource_status(event.payload['symbol'],self.resource_keys)


class OZConsumer:
    name='OZ'
    def subscriptions(self):
        return cached_subscriptions(kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),processor_states=('OZ_STATE',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('MANUAL_WATCH', 'CANCEL_MANUAL', 'RESET_ALL', 'SWEEP_WATCH', 'CANCEL_SWEEP', 'RESET_SWEEP'))
    def on_event(self,event,board,state,emit):
        current=board.processor('OZ_STATE')
        if current.get('publication')==event.engine_seq:
            emit_facts(emit,event.payload['symbol'],'OZ',current['events'],prepared=True)
