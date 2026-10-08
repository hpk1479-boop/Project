"""INDICATOR consumer, retaining canonical trend/metric decision functions."""
from domain_clock import event_scope
from .subscription_cache import cached_subscriptions
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,FactPort,subscription_command,emit_facts,plain
from .indicator_runtime import IndicatorRuntime
from .restoration import indicator_state


class IndicatorConsumer:
    name='INDICATOR'
    def __init__(self,module,protocol,compat,oz,*,symbols=(),initial=(),sigma=3.):
        self.module=module;self.protocol=protocol;self.compat=compat;self.oz=oz
        self.symbols=tuple(symbols);self.initial=tuple(initial);self.sigma=sigma
    def subscriptions(self):
        return cached_subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),facts=('ATR14_GENERAL',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('TREND_WATCH', 'CANCEL_TREND', 'RESET_TREND', 'TREND_QUERY'))
    def on_event(self,event,board,state,emit):
        if 'startup_files' in state:indicator_state(self.module,self.protocol,state,state.pop('startup_files'))
        watches=state.setdefault('watches',{f'initial:{s}:{tf}':{'symbol':s,'source_tf':tf,'requested_fields':[]} for s,tf in self.initial})
        command=subscription_command(event,state,'TREND')
        if command:
            action=command.get('action');wid=command.get('watch_id')
            if action=='TREND_WATCH':watches[wid]=plain(command)
            elif action=='CANCEL_TREND':watches.pop(wid,None)
            elif action=='RESET_TREND':watches.clear();state['subscription_owners']={}
            if action!='TREND_QUERY':return
        if event.kind!=Kind.MARKET_BUNDLE and not command:return
        symbol=event.payload['symbol'];active={}
        for item in watches.values():
            if item['symbol']==symbol:
                fields=active.setdefault(item['source_tf'],[])
                fields.extend(f for f in item.get('requested_fields',()) if f not in fields)
        if not active and not command:return
        port=FactPort(self.protocol,'TREND',state.setdefault('stream',{}))
        if 'runtime' not in state:
            state['runtime']=IndicatorRuntime(state.setdefault('core',{}),state.setdefault('pending',{}))
        core=state['runtime'];core.bind(board,port,event.source_time/1000)
        with event_scope(event.source_time,self.name,state.setdefault('clock',{})):
            if command:core.query(plain(command))
            else:core.watch(symbol,active)
        emit_facts(emit,symbol,'TREND',port.events,prepared=True)
