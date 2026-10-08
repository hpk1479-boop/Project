"""Opt-in composition boundary for unchanged Watch/SPECIAL decision methods.

Construction dependencies are explicit. A Kernel is engine-owned mutable state;
its deepcopy contract is the in-memory checkpoint contract, with fresh locks and
ports and no references back to the pre-checkpoint manager.
"""
from copy import deepcopy
from pathlib import PurePath
from types import ModuleType,FunctionType,MethodType
from types import SimpleNamespace
import json
import threading
from collections import deque
from domain_clock import event_scope
from domain_memory import memory_scope
from event_engine.frames import BoardFrames
from event_engine.domain_support import plain
from event_commands import ExternalCommandPending
from event_composer_inputs import ComposerInputs
from command_interpreter import resolve_command_language_path
from telegram_routing import private_alert_chats


class CommandPort:
    def __init__(self,kernel):self.kernel=kernel
    def push(self,payload):
        item=deepcopy(payload)
        if item.get('symbol') not in (None,'',self.kernel.symbol):return
        from event_selection import command_family
        family=command_family(item);plan=self.kernel.selection
        if family and plan is not None and not plan.unrestricted and family not in plan.families:
            raise ValueError('선택되지 않은 감시/미선언 의존관계: '+family)
        item.setdefault('issued_at',self.kernel.timestamp*1000000)
        self.kernel.commands.append(item)


class StaffPort:
    def __init__(self,kernel):self.kernel=kernel
    def request(self,symbol,tfs,indicators=None,**extra):
        if symbol!=self.kernel.symbol or self.kernel.board is None:return None
        try:return self.kernel.legacy_frames().select(symbol,tfs,indicators,**extra)
        except (RuntimeError,ValueError):return None
    def health(self,symbol,tfs):
        board=self.kernel.board
        result={}
        for tf in tfs:
            key=(symbol,tf)
            if board is None or key not in board.feeds:
                result[tf]={'status':'UNAVAILABLE'};continue
            snap=board.snapshot(symbol,tf)
            source_status=board.health.get(symbol,{}).get('status','FRESH')
            age=max(0,(self.kernel.timestamp-board.observed[key])/1000)
            stale=float(self.kernel.config.get('STAFF_STALE_SEC','30'))
            result[tf]={'status':source_status if source_status!='FRESH' else ('STALE' if age>stale else 'FRESH'),'source_epoch':snap.source_epoch,
                        'age_seconds':age,'stale_seconds':stale,'indicators':dict(snap.indicator_validity)}
        return result


class NotificationPort:
    """Domain output intents. IDs are logical references, never Telegram ACKs."""
    def __init__(self,kernel):
        self.kernel=kernel;self.sequence=0;self.deliveries={}
        self.expiry=deque();self.delivery_times={};self.clock=0.
    def advance_time(self,seconds):
        self.clock=max(self.clock,float(seconds))
        while self.expiry and self.expiry[0][0]<self.clock-3*86400:
            stamp,key=self.expiry.popleft()
            if self.delivery_times.get(key)==stamp:
                self.delivery_times.pop(key,None);self.deliveries.pop(key,None)
    def send(self,text,chat_id=None,event_id=None,sent_message_ids=None,reply_to_message_id=None,
             signal_context=None,**kwargs):
        from event_commands import LOGICAL_MESSAGE_BASE
        target=str(chat_id or self.kernel.config.get('TELEGRAM_CHAT_ID',''))
        # No alert room: a broadcast still goes out when 1:1 command chats opted in to live alerts.
        if not target and not private_alert_chats(self.kernel.config,'live'):return False
        key=(event_id,target,reply_to_message_id) if event_id else None
        if key and key in self.deliveries:
            if sent_message_ids is not None:sent_message_ids.append(self.deliveries[key])
            return True
        self.sequence+=1;token=LOGICAL_MESSAGE_BASE+self.sequence
        context=getattr(getattr(self.kernel,'manager',None),'_delivery_context',None)
        raw=(getattr(context,'oz_event',None) or self.kernel.current_event or {}) if signal_context is None else signal_context
        source=raw.get('signal_source') or ('OZ' if raw.get('kind')=='FINAL_ALERT' and raw.get('strategy')=='OZ' else 'NOTICE')
        self.kernel.messages.append({'message':str(text),'recipients':[target],
            'direction':str(raw.get('direction') or ''),'event_time':raw.get('event_time',self.kernel.timestamp/1000),
            'source_tf':raw.get('source_tf',raw.get('tf','')),'kind':raw.get('kind','CONDITION_NOTIFICATION'),
            'event_id':event_id,'message_token':token,'reply_to_message_id':reply_to_message_id,
            'signal_price':raw.get('current_price'),
            'signal_source':source,
            **{k:raw.get(k) for k in ('signal_tf','env_tf','neckline_price','neckline_time_ms') if raw.get(k) is not None},
            **({k:raw.get(k) for k in ('signal_strategy','market_event_id','source_spec_id','b0_price','b0_time','grade','validation_mode','trigger_mode','trigger_name','profile','profile_id','trigger','final_trigger')}
               if raw.get('signal_strategy') or source=='OZ' else {})})
        if key:
            self.deliveries[key]=token;self.delivery_times[key]=self.clock;self.expiry.append((self.clock,key))
        if sent_message_ids is not None:sent_message_ids.append(token)
        return True


class Services:
    root=PurePath('/event-domain')
    alias_path=resolve_command_language_path()
    def __init__(self,kernel):
        self.kernel=kernel;self.staff=StaffPort(kernel)
        self.profiles=kernel.modules['event_profiles'];self.trigger_map=kernel.trigger_map
        self.restore_state=True
    def interpreter(self,manager):
        cls=self.kernel.modules['command_interpreter'].CommandInterpreter
        value=cls.__new__(cls)
        value.config=manager.system_config;value.aliases_path=self.alias_path
        value.allowed_symbols_provider=manager._allowed_symbols
        value.command_aliases=deepcopy(self.kernel.aliases)
        value._last_alias_check=0.;value._last_alias_mtime=None
        return value


def composer_type(module):
    class EventComposer(module.ComposerManager):
        def _scan_special_strategies(self):
            for plugin in self.event_services.kernel.plugins:
                key=plugin.__name__
                if key not in self.special_modules:
                    plugin.register(self);self.special_modules[key]=plugin
            self._special_initial_scan_done=True
            plan=self.event_services.kernel.selection
            if plan is not None and plan.official:
                self.register_special_bundle(strategies=[v for n,v in plan.official if n.startswith('OFFICIAL:')],
                    timed_chains=[v for n,v in plan.official if n.startswith('CHAIN:')])
    return EventComposer


def symbol_restore_files(files,symbol):
    """Partition only symbol-bearing collections, leaving canonical decoding intact.

    Each symbol has its own source clock. In particular startup reconciliation
    must never interpret another symbol's saved subscriptions as orphans.
    Original polling bytes remain untouched; malformed inputs still reach the
    canonical restore function and retain its fallback/error handling.
    """
    result=dict(files)
    for name in ('composer_private_watches.json','composer_timed_chains.json',
                 'composer_fvg_created_watches.json','composer_active_oz_watches.json',
                 'trend_watch_state.json','fvg_watch_state.json','sweep_watch_state.json',
                 'oz_external_liquidity_state.json','oz_manual_watch_state.json','oz_generic_watch_state.json'):
        if name not in result:continue
        try:
            data=json.loads(result[name])
            if not isinstance(data,dict):continue
            for key in ('watches','chains','specs'):
                collection=data.get(key)
                def belongs(item):
                    if name in ('trend_watch_state.json','fvg_watch_state.json') and isinstance(item,(list,tuple)):
                        return not item or item[0]==symbol
                    return not isinstance(item,dict) or item.get('symbol',symbol)==symbol
                if isinstance(collection,list):data[key]=[item for item in collection if belongs(item)]
                elif isinstance(collection,dict):data[key]={k:v for k,v in collection.items() if belongs(v)}
            result[name]=json.dumps(data,ensure_ascii=False)
        except (ValueError,TypeError):pass
    return result


class CompositionKernel:
    def __init__(self,modules,plugins,config,aliases,owner,symbol,timestamp,*,trigger_map=None,state_files=None,selection=None,backtest=False):
        self.modules=modules;self.plugins=tuple(plugins);self.config=dict(config)
        self.selection=selection;self.backtest=backtest
        self.aliases=deepcopy(aliases);self.owner=owner;self.symbol=symbol;self.timestamp=timestamp
        self.trigger_map=dict(trigger_map or {})
        self.initial_files=symbol_restore_files(state_files or {},symbol)
        self.memory=dict(self.initial_files);self.clock={};self.history={};self.commands=[];self.messages=[];self.current_event=None
        self.board=None;self.frames=None;self.services=Services(self)
        self._cached_frames=None;self._cached_signature=None
        self.inputs=ComposerInputs()
        self.queue=CommandPort(self);self.notifier=NotificationPort(self)
        try:
            output_state=json.loads(self.initial_files.get('event_notification_port.json','{}'))
            self.notifier.sequence=int(output_state.get('sequence',0))
            self.notifier.deliveries={tuple(key):value for key,value in output_state.get('deliveries',[])}
            self.notifier.clock=float(output_state.get('clock',timestamp/1000))
            stamps={tuple(key):value for key,value in output_state.get('delivery_times',[])}
            self.notifier.delivery_times={key:stamps.get(key,self.notifier.clock) for key in self.notifier.deliveries}
            self.notifier.expiry=deque(sorted(((stamp,key) for key,stamp in self.notifier.delivery_times.items()),key=lambda item:item[0]))
        except (ValueError,TypeError):pass
        self._manager_class=composer_type(modules['event_composer_domain'])
        with event_scope(timestamp,owner+':'+symbol,self.clock),memory_scope(self.memory):
            self.manager=self._manager_class(self.config,threading.Event(),self.queue,
                None,notifier=self.notifier,event_services=self.services)
            # One symbol has one source clock and checkpoint. Definitions for
            # other symbols remain in their own kernel, never advance here.
            self.manager.official_specs={k:v for k,v in self.manager.official_specs.items() if v.symbol==symbol}
            self.manager.official_chain_specs={k:v for k,v in self.manager.official_chain_specs.items() if v.symbol==symbol}
            m=self.manager
            own=set(m.official_chain_specs)
            m._config_chain_state={k:v for k,v in m._config_chain_state.items() if k in own}
            m._config_chain_active={k:v for k,v in m._config_chain_active.items() if v.get('symbol')==symbol}
            m._active_children={k:v for k,v in m._active_children.items() if v.get('symbol')==symbol}
            # Canonical SPECIAL modules register all their configured symbols.
            # Only this symbol's runtime may receive its source clock; retain
            # shared condition handlers, which have no symbol attribute.
            m._special_watch_handlers={k:v for k,v in m._special_watch_handlers.items()
                                       if getattr(v,'symbol',symbol)==symbol}
            m._special_watch_event_handlers=[v for v in m._special_watch_event_handlers
                                            if getattr(v,'symbol',symbol)==symbol]
        self.startup_reconciled=False

    def legacy_frames(self):
        # Only unchanged SPECIAL plugin APIs still request legacy chart frames.
        # Composer's own condition updates read Snapshot arrays directly.
        board=self.board
        signature=tuple((key,id(snapshot)) for key,snapshot in board.feeds.items())
        if signature!=self._cached_signature or self._cached_frames is None:
            self._cached_frames=BoardFrames(board,self.modules['staff_compat'],self.modules['monitor_OZ'],
                                sigma=float(self.config.get('WONBI_SIGMA',3)),history=self.history,role="composer")
            self._cached_signature=signature
        self.frames=self._cached_frames;self.frames.board=board
        return self.frames

    def wants_fact(self,raw):
        if raw.get('kind')!='FACT_SNAPSHOT':return True
        if raw.get('symbol')!=self.symbol:return False
        family=raw.get('strategy')
        if family not in ('TREND','FVG','SWEEP'):return True
        m=self.manager
        desired=m._desired_subscriptions_locked() if m._subscription_dirty else m._engine_subscriptions
        return any(v.get('symbol')==self.symbol and v.get('source_tf')==raw.get('source_tf')
            and (family!='SWEEP' or v.get('watch_id')==raw.get('watch_id'))
            for v in desired.get(family,{}).values())

    def export_memory(self):
        result=dict(self.memory)
        for records in (self.manager._receipts,self.manager._fact_revisions,self.manager._signature_records):
            result[records.path.name]=records.export_json()
        providers=self.manager._strategy_state_providers
        if providers:result['strategy_recipe_state.json']=json.dumps({key:provider.checkpoint() for key,provider in providers.items()})
        return result

    def step(self,event,board,raw=None,command=None):
        self.timestamp=event.source_time;self.board=board;self.current_event=raw
        m=self.manager;reply=None
        m._receipts.advance_time(self.timestamp/1000)
        self.notifier.advance_time(self.timestamp/1000)
        try:
            with event_scope(self.timestamp,self.owner+':'+self.symbol,self.clock),memory_scope(self.memory,skip_checkpoint_only=self.backtest):
                if not self.startup_reconciled:
                    # Equivalent to strategy-worker startup PING. Canonical
                    # methods reconcile existing ids and resend current watches.
                    for family in ('TREND','FVG','SWEEP'):
                        if self.selection is None or family in self.selection.families:m._refresh_engine_family_after_ping(family)
                    if self.selection is None or 'OZ' in self.selection.families:m._refresh_oz_after_ping()
                    self.startup_reconciled=True
                oz=board.processor('OZ_STATE') if 'OZ_STATE' in board.processor_states else {}
                if oz.get('external_memory') is not None:
                    self.memory['oz_external_liquidity_state.json']=oz['external_memory']
                if command is not None:
                    try:
                        m.handle_command(command['text'],str(command['chat_id']),message_id=command.get('message_id'),
                            reply_to_message_id=command.get('reply_to_message_id'),_gemini_retry=command.get('_external_reply',False))
                    except ExternalCommandPending as pending:
                        self.messages.append({'type':'EXTERNAL_REQUEST','service':'gemini','text':pending.text,'command':deepcopy(command)})
                elif raw is not None:
                    reply=m._handle_event(raw)  # Consumer supplied an owned plain payload.
                    if str(reply.get('error') or '').startswith('special_oz_hook_failed'):
                        raise RuntimeError(reply['error'])
                else:
                    if self.timestamp/1000-getattr(m,'_last_watch_confirmation_retry',0.)>=10.:
                        m._last_watch_confirmation_retry=self.timestamp/1000
                        if m._watch_message_links:m._retry_watch_confirmations()
                    if m.timed_chains:m._maintain_timed_chains()
                    if m.official_chain_specs:m._maintain_config_timed_chains()
                    if event.kind.value=='MARKET_BUNDLE' and (m._all_specs_locked() or m.timed_chains
                            or m.official_chain_specs or m._special_watch_handlers):
                        legacy_conditions=bool(m._all_specs_locked() or m.timed_chains or m.official_chain_specs)
                        if not legacy_conditions:
                            m._poll_strategy_handlers()
                        elif self.selection is None or self.selection.unrestricted:
                            m._update_wonbi();m._update_percentile();m._update_ma_state();m._update_local_chain_events()
                        else:
                            caps=self.selection.capabilities
                            if 'WONBI' in caps or 'WATCH' in caps:m._update_wonbi()
                            if 'PERCENTILE' in caps or 'WATCH' in caps:m._update_percentile()
                            if 'MA' in caps or 'WATCH' in caps:m._update_ma_state()
                            if 'CHAINS' in caps or 'WATCH' in caps:m._update_local_chain_events()
                        # Fact handlers still evaluate immediately in their
                        # original order. Market reevaluation needs a registered
                        # condition's feed (including its liveness/time boundary).
                        with m._lock:
                            specs=m._all_specs_locked()
                            if any(s.symbol==self.symbol and any(c.tf in event.payload['feeds'] for c in s.conditions) for s in specs):
                                m._evaluate_symbol_locked(self.symbol)
                if m._subscription_dirty:m._sync_engine_subscriptions(force_refresh=False)
        finally:
            if self._cached_frames is not None:self._cached_frames.board=None
            self.board=None;self.frames=None;self.current_event=None
        commands,messages=self.commands,self.messages
        self.commands=[];self.messages=[]
        return commands,messages,reply

    def deadlines(self):
        values=[]
        for provider in self.manager._strategy_state_providers.values():values.extend(provider.deadlines())
        for chain in self.manager.timed_chains.values():
            values.extend(v for v in (chain.stage_deadline,chain.active_until,chain.start_at) if isinstance(v,(int,float)))
        for item in self.manager._config_chain_active.values():
            values.extend(v for k,v in item.items() if k in ('expires_at','active_until','completion_deadline','stage_deadline') and isinstance(v,(int,float)))
        if any((not v.get('confirmation_message_id') and v.get('confirmation_text') and v.get('status')=='active')
               or v.get('status')=='cancel_requested' or v.get('cancel_confirmation_pending')
               for v in self.manager._watch_message_links.values()):
            values.append(getattr(self.manager,'_last_watch_confirmation_retry',self.timestamp/1000)+10.)
        return sorted({int(v*1000) for v in values if int(v*1000)>=self.timestamp})

    def __deepcopy__(self,memo):
        clone=type(self)(self.modules,self.plugins,self.config,self.aliases,self.owner,self.symbol,self.timestamp,
                         trigger_map=self.trigger_map,state_files={},selection=self.selection,backtest=self.backtest)
        memo[id(self)]=clone;memo[id(self.manager)]=clone.manager
        # Capabilities must point at the new engine state, never the old kernel.
        for old,new in ((self.services,clone.services),(self.services.staff,clone.services.staff),
                        (self.queue,clone.queue),(self.notifier,clone.notifier)):
            memo[id(old)]=new
        memo[id(self.modules)]=self.modules
        for module in tuple(self.modules.values())+self.plugins:memo[id(module)]=module
        lock_types=(type(threading.Lock()),type(threading.RLock()))
        visited=set()
        def capabilities(old,new=None):
            if id(old) in visited:return
            visited.add(id(old))
            if isinstance(old,ModuleType):memo[id(old)]=old;return
            if isinstance(old,lock_types):
                memo[id(old)]=new if isinstance(new,lock_types) else threading.RLock();return
            if isinstance(old,threading.local):
                memo[id(old)]=threading.local();return
            if isinstance(old,FunctionType):
                memo[id(old)]=new if isinstance(new,FunctionType) else old;return
            if isinstance(old,dict):
                for key,value in old.items():capabilities(value,new.get(key) if isinstance(new,dict) else None)
            elif isinstance(old,(list,tuple,set,frozenset)):
                for value in old:capabilities(value)
            elif hasattr(old,'__dict__') and not isinstance(old,(type,MethodType)):
                for key,value in vars(old).items():
                    capabilities(value,vars(new).get(key) if hasattr(new,'__dict__') else None)
        capabilities(self.manager,clone.manager)
        for key,value in vars(self.manager).items():
            if key.startswith('_Thread') or key in ('_target','_args','_kwargs','_tstate_lock','_started','_initialized','_ident','_native_id','_is_stopped','_daemonic','_name','_stderr','_invoke_excepthook','_handle'):
                continue
            setattr(clone.manager,key,deepcopy(value,memo))
        clone.memory=deepcopy(self.memory,memo);clone.clock=deepcopy(self.clock,memo)
        clone.history=deepcopy(self.history,memo)
        clone.inputs=deepcopy(self.inputs,memo)
        clone.commands=deepcopy(self.commands,memo);clone.messages=deepcopy(self.messages,memo)
        clone.notifier.sequence=self.notifier.sequence
        clone.notifier.deliveries=deepcopy(self.notifier.deliveries,memo)
        clone.notifier.delivery_times=deepcopy(self.notifier.delivery_times,memo)
        clone.notifier.expiry=deepcopy(self.notifier.expiry,memo)
        clone.notifier.clock=self.notifier.clock
        clone.startup_reconciled=self.startup_reconciled
        return clone
