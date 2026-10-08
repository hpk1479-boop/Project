"""Watch/SPECIAL consumers delegate decisions to their canonical Part1 kernel."""
from .subscription_cache import cached_subscriptions
from .model import Kind,Subscriptions,Resolution,Signal,TimerRequest
from .domain_support import plain


class CompositionConsumer:
    # User-approved E2 policy: do not stop every SPECIAL after one family's
    # repeated errors. E3 individual consumers will retain the engine default.
    stop_after_errors=False
    def __init__(self,name,factory,route,*,symbols=(),selection=None):
        self.name=name;self.factory=factory;self.route=route;self.symbols=tuple(symbols);self.selection=selection
    def subscriptions(self):
        caps=self.selection.capabilities if self.selection is not None else {'WONBI','ATR','INDICATOR'}
        facts=tuple(name for flag,name in (('WONBI','WONBI_BANDS'),('ATR','ATR14_GENERAL')) if flag in caps)
        if 'INDICATOR' in caps and 'ATR14_GENERAL' not in facts:facts+=('ATR14_GENERAL',)
        return cached_subscriptions(symbols=self.symbols,kinds=(Kind.MARKET_BUNDLE,Kind.SIGNAL,Kind.TIMER,Kind.COMMAND,Kind.EXTERNAL_REPLY),
                             facts=facts,processor_states=(('OZ_STATE',) if self.selection is None or 'OZ_STATE' in self.selection.processors else ()),resolution=Resolution.TICK)
    def accepts_event(self,event):
        if event.kind==Kind.SIGNAL:
            content=event.payload.get('content',{})
            return content.get('type')=='DOMAIN_FACT' and (self.selection is None or self.selection.unrestricted or content.get('family') in self.selection.families)
        if event.kind==Kind.COMMAND:return 'text' in event.payload
        return True
    def on_event(self,event,board,state,emit):
        if event.kind==Kind.COMMAND and self.selection is not None and not self.selection.unrestricted:
            if not self.selection.permits_command(event.payload.get('strategy')):raise ValueError('명령 감시 전략을 시나리오에서 선택하고 command.strategy로 명시하세요.')
        symbol=event.payload.get('symbol')
        if not symbol:return
        kernels=state.setdefault('kernels',{})
        if symbol not in kernels:kernels[symbol]=self.factory(self.name,symbol,event.source_time)
        kernel=kernels[symbol];raw=None;command=None
        if event.kind==Kind.SIGNAL:
            content=event.payload['content']
            if content.get('type')!='DOMAIN_FACT':return
            if not kernel.wants_fact(content['event']):return
            raw=plain(content['event'])
            if content.get('family') in ('OZ','WATCH') and self.route(raw)!=self.name:return
        elif event.kind==Kind.EXTERNAL_REPLY:
            if 'command' not in event.payload:return
            request_id=event.payload.get('request_id')
            consumed=state.setdefault('external_replies',set())
            if request_id in consumed:return
            consumed.add(request_id)
            command=plain(event.payload['command'])
            command['text']=event.payload.get('canonical_text') or command['text']
            command['_external_reply']=True
        elif event.kind==Kind.COMMAND:
            if 'text' not in event.payload:return
            command=plain(event.payload)
        commands,messages,reply=kernel.step(event,board,raw,command)
        ordinal=state.get('output_sequence',0)
        for index,payload in enumerate(commands):
            ordinal+=1
            emit(Signal(symbol,f'command:{ordinal}',{'type':'WATCH_COMMAND','command':plain(payload)}))
        for index,message in enumerate(messages):
            ordinal+=1
            key=str(message.get('event_id') or f"notice:{ordinal}")
            emit(Signal(symbol,key,{'type':'NOTIFICATION',**plain(message)},strategy=message.get('signal_strategy')))
        state['output_sequence']=ordinal
        requested=state.setdefault('timers',set())
        if event.kind==Kind.TIMER:requested.discard((symbol,event.source_time))
        for due in kernel.deadlines():
            # Canonical chains retain expired identities to accept late facts.
            # Retaining that deadline must not re-arm the timer just consumed.
            if event.kind==Kind.TIMER and due<=event.source_time:continue
            key=(symbol,due)
            if key not in requested:
                requested.add(key);emit(TimerRequest(symbol,due,'composition_deadline'))
