"""Shared SWEEP detectors; thresholds and lifecycle stay in strategy_SWEEP."""
from .subscription_cache import cached_subscriptions
from .model import Kind,Subscriptions,Resolution
from .domain_support import accepts_command,FactPort,subscription_command,emit_facts,plain
from .sweep_runtime import SweepRuntime
from .restoration import sweep_state


class EventList:
    def __init__(self):self.events=[]
    def publish(self,event):self.events.append(plain(event))


class SweepProcessor:
    name='SWEEP_STATE'
    def __init__(self,module,protocol,compat,oz,*,symbols=(),initial=(),sigma=3.):
        self.module=module;self.protocol=protocol;self.compat=compat;self.oz=oz
        self.symbols=tuple(symbols);self.initial=tuple(initial);self.sigma=sigma
    def subscriptions(self):
        return cached_subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('SWEEP_WATCH', 'CANCEL_SWEEP', 'RESET_SWEEP', 'SWEEP_QUERY'))
    def on_event(self,event,board,state):
        if 'startup_files' in state:sweep_state(self.module,self.protocol,state,state.pop('startup_files'))
        watches=state.setdefault('watches',{p['watch_id']:dict(p) for p in self.initial})
        command=subscription_command(event,state,'SWEEP')
        if command:
            action=command.get('action');wid=command.get('watch_id')
            if action=='SWEEP_WATCH':watches[wid]=plain(command)
            elif action=='CANCEL_SWEEP':watches.pop(wid,None)
            elif action=='RESET_SWEEP':watches.clear();state['subscription_owners']={}
            state.setdefault('__board__',{})['registry']=watches
            if action!='SWEEP_QUERY':return
        if event.kind!=Kind.MARKET_BUNDLE and not command:return
        symbol=event.payload['symbol'];port=FactPort(self.protocol,'SWEEP',state.setdefault('stream',{}))
        events=EventList()
        if 'runtime' not in state:state['runtime']=SweepRuntime(state.setdefault('core',{}))
        core=state['runtime'];core.bind(board,port,events,event.source_time/1000)
        if command:
            core.run_query(plain(command))
            state['__board__']={'events':port.events,'external_events':events.events,
                                'publication':event.engine_seq,'symbol':symbol,'registry':watches}
            return
        # A market publication may advance only its own symbol's lifecycle.
        # Keep other-symbol detectors until that symbol's next observation.
        keep=set(watches)|{wid for wid,detector in core.detectors.items() if detector.spec.symbol!=symbol}
        core.cleanup(keep)
        for payload in watches.values():
            if payload['symbol']!=symbol:continue
            spec=self.module.SweepSpec.from_payload(payload)
            if spec is not None:core.run_spec_once(spec)
        state['__board__']={'events':port.events,'external_events':events.events,
                            'publication':event.engine_seq,'symbol':symbol,'registry':watches}


class SweepConsumer:
    name='SWEEP'
    def subscriptions(self):
        return cached_subscriptions(kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL),processor_states=('SWEEP_STATE',),resolution=Resolution.TICK)
    def accepts_event(self,event):
        return accepts_command(event,('SWEEP_QUERY',))
    def on_event(self,event,board,state,emit):
        current=board.processor('SWEEP_STATE')
        if current.get('publication')==event.engine_seq:
            emit_facts(emit,event.payload['symbol'],'SWEEP',current['events'],prepared=True)
