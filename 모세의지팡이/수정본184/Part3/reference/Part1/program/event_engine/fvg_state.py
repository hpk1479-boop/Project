"""FVG lifecycle state owned by the engine; canonical functions only."""
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,FactPort,subscription_command,emit_facts
from .market import select
from .fvg_runtime import FVGRuntime
from .restoration import fvg_state


class FVGProcessor:
    name='FVG_STATE'
    def __init__(self,module,protocol,*,symbols=(),initial=()):
        self.module=module;self.protocol=protocol;self.symbols=tuple(symbols);self.initial=tuple(initial)
    def subscriptions(self):
        return Subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('FVG_WATCH', 'CANCEL_FVG', 'RESET_FVG', 'FVG_QUERY'))
    def on_event(self,event,board,state):
        if 'startup_files' in state:fvg_state(self.module,self.protocol,state,state.pop('startup_files'))
        if 'runtime' not in state:state['runtime']=FVGRuntime(state.setdefault('core',{}))
        core=state['runtime']
        watches=state.setdefault('watches',{f'initial:{s}:{tf}':(s,tf) for s,tf in self.initial})
        command=subscription_command(event,state,'FVG')
        if command:
            action=command.get('action');key=command.get('watch_id')
            if action=='FVG_WATCH':watches[key]=(command['symbol'],command['source_tf'])
            elif action=='CANCEL_FVG':watches.pop(key,None)
            elif action=='RESET_FVG':watches.clear();state['subscription_owners']={}
            elif action=='FVG_QUERY':
                port=FactPort(self.protocol,'FVG',state.setdefault('stream',{}))
                core.bind(board,port)
                core.run_query(dict(command))
                state['__board__']={'events':port.events,'publication':event.engine_seq,'symbol':event.payload['symbol']}
            return
        if event.kind!=Kind.MARKET_BUNDLE:return
        symbol=event.payload['symbol']; port=FactPort(self.protocol,'FVG',state.setdefault('stream',{}))
        core.bind(board,port)
        for tf in dict.fromkeys(tf for sym,tf in watches.values() if sym==symbol):
            raw=select(board,symbol,tf)
            if raw is None:continue
            result=core.evaluate(symbol,tf,raw)
            if result is not None:core.process_watch_result(result)
        state['__board__']={'events':port.events,'publication':event.engine_seq,'symbol':symbol}


class FVGConsumer:
    name='FVG'
    def subscriptions(self):
        return Subscriptions(kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),processor_states=('FVG_STATE',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('FVG_QUERY',))
    def on_event(self,event,board,state,emit):
        current=board.processor('FVG_STATE')
        if current.get('publication')==event.engine_seq:
            emit_facts(emit,event.payload['symbol'],'FVG',current['events'])
