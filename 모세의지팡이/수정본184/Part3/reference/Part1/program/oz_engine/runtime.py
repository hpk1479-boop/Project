"""Engine-owned OZ lifetime, dependency scheduling and explicit checkpoints."""
from copy import deepcopy
import json,logging
import numpy as np
from domain_clock import event_scope
from domain_memory import memory_scope
from event_engine.domain_support import plain
from .common import PROFILE_KEYS,TF_MAP,ExternalLiquiditySpec
from .controllers import ExternalLiquidityController,OZWatchController
from .profile import OZProfile
from .market import OZMarketView


class SourceClock:
    def __init__(self,state):self.state=state;self.milliseconds=0
    @property
    def seconds(self):return self.milliseconds/1000
    def identity_ns(self):
        value=max(self.milliseconds*1000000,self.state.get('logical_identity_ns',-1)+1)
        self.state['logical_identity_ns']=value
        return value


class Collector:
    def __init__(self):self.events=[]
    def __call__(self,event):
        self.events.append(plain(event));return {'ok':True,'delivered':True}


class GenericBoundary:
    def reset_all(self,**kwargs):return None


def same_feed(before,after):
    return before is after or (before is not None and before.source_epoch==after.source_epoch
        and before.indicator_validity==after.indicator_validity
        and all(np.array_equal(getattr(before,key),getattr(after,key),equal_nan=True)
                for key in ('time','volume','values')))


def ready(board,symbol,tf):
    if (symbol,tf) not in board.feeds:return False
    if board.health.get(symbol,{}).get('status') in ('STALE','UNAVAILABLE','RECONNECT'):return False
    if board.source_time-board.observed[(symbol,tf)]>30000:return False
    snapshot=board.snapshot(symbol,tf)
    return len(snapshot.time)>=3 and np.isfinite(snapshot.values[:,:4]).all()


class OZRuntime:
    def __init__(self,oz,config,memory=None,*,selection=None):
        self.oz=oz;self.config=dict(config);self.memory=dict(memory or {})
        self.selection=selection
        self.clock=SourceClock({});self.collector=Collector();self.registry={}
        with memory_scope(self.memory):self.sender=oz.DomainEventSender(config,transport=self.collector)
        def initial(name):
            if name not in self.memory:return None
            try:return json.loads(self.memory[name])
            except (ValueError,TypeError):
                logging.exception('[OZ 상태 복원 실패] %s',name)
                return None
        self.external=ExternalLiquidityController(self.clock,sweep_registry=self.registry,
            initial=initial('oz_external_liquidity_state.json'))
        self.watch=OZWatchController(self.sender,self.external,self.clock,
            initial=initial('oz_manual_watch_state.json'))
        self.external._load_state();self.watch._load_state()
        self.profiles={};self.previous={};self.signatures={};self.last_evaluated={};self.ready_profiles=set()
        self.views_created=0;self.evaluations=0;self.force_all=False;self.bootstrapped=False
        self.external_revision=-1;self.external_memory=self.memory.get('oz_external_liquidity_state.json')

    def bootstrap(self,board,events):
        if self.bootstrapped:return
        for wid,item in board.processor('SWEEP_STATE').get('registry',{}).items():
            self.registry[wid]=ExternalLiquiditySpec(wid,item['symbol'],item['source_tf'])
        self.external._bootstrap_sweep_registry()
        for event in events:self.watch.apply_external_event(plain(event))
        self.bootstrapped=True

    def _profile(self,symbol,vm,tm):
        key=(symbol,vm,tm)
        if key not in self.profiles:
            profile=OZProfile(symbol,self.config,self.sender,self.watch,vm,tm,source_time=self.clock.seconds,
                base_tfs=None if self.selection is None else self.selection.timeframes(vm,tm))
            if profile.checkpoint_name in self.memory:
                profile.restore_observed_checkpoint(json.loads(self.memory[profile.checkpoint_name]))
            self.profiles[key]=profile
        return self.profiles[key]

    def _environment(self,profile,tf):
        signatures=[];sources=set()
        for wid,w in self.watch._watches.items():
            if (w.symbol not in (None,profile.symbol) or tf not in w.timeframes
                or (w.validation_mode,w.trigger_mode)!=(profile.validation_mode,profile.trigger_mode)):continue
            external_id=self.watch._external_id_for_watch_locked(w)
            ext=self.external._specs.get(external_id)
            if ext is not None:sources.add(ext.source_tf)
            signatures.append((wid,tuple(vars(w).items()),self.external.watch_signature(external_id)))
        return tuple(signatures),sources

    def _market(self,event,board):
        self.validate_selection()
        symbol=event.payload['symbol']
        sweep=board.processor('SWEEP_STATE')
        if sweep.get('publication')==event.engine_seq:
            for item in sweep.get('external_events',()):self.watch.apply_external_event(plain(item))
        views={};changed=set()
        for sym,tf in board.feeds:
            if self.selection is not None and tf not in self.selection.feeds:continue
            if sym!=symbol or not ready(board,symbol,tf):continue
            snapshot=board.snapshot(symbol,tf)
            if not same_feed(self.previous.get((symbol,tf)),snapshot):changed.add(tf)
            views[tf]=OZMarketView(snapshot,atr_provider=lambda tf=tf:board.fact('ATR14_GENERAL',symbol,tf),
                                   wonbi_provider=lambda tf=tf:board.fact('WONBI_BANDS',symbol,tf))
        self.views_created+=len(views)
        for vm,tm in (PROFILE_KEYS if self.selection is None else self.selection.profiles):
            profile=self._profile(symbol,vm,tm);profile._source_time=self.clock.seconds
            revision,tfs=self.watch.snapshot_for_symbol(symbol,vm,tm)
            if revision!=profile.watch_generation:profile._reset_watch_state(tfs,revision)
            profile_key=(symbol,vm,tm)
            needed=set(profile.base_tfs)
            if vm=='NORMAL':needed.update(x for tf in profile.base_tfs for x in TF_MAP[tf])
            elif profile.use_regime:needed.update(TF_MAP[tf][1] for tf in profile.base_tfs)
            needed.update(self.watch.external_source_tfs_for_profile(symbol,vm,tm))
            if not needed.issubset(views):
                self.ready_profiles.discard(profile_key);self.last_evaluated[profile_key]=()
                continue
            newly_ready=profile_key not in self.ready_profiles
            self.ready_profiles.add(profile_key)
            selected=[]
            for tf in profile.base_tfs:
                key=(symbol,vm,tm,tf);signature,sources=self._environment(profile,tf)
                dependencies={tf}|sources
                if vm=='NORMAL':dependencies.update(TF_MAP[tf])
                elif profile.use_regime:dependencies.add(TF_MAP[tf][1])
                if self.force_all or newly_ready or dependencies & changed or self.signatures.get(key)!=signature:
                    selected.append(tf)
                self.signatures[key]=signature
            self.last_evaluated[(symbol,vm,tm)]=tuple(selected)
            if selected:
                profile.run_once(revision,tfs,views,selected)
                self.evaluations+=len(selected)
        # A temporarily unavailable TF becomes changed when it returns.
        for key in tuple(self.previous):
            if key[0]==symbol:self.previous.pop(key)
        self.previous.update({(symbol,tf):view.snapshot for tf,view in views.items()})

    def process(self,event,board,command=None,startup_events=()):
        self.clock.milliseconds=event.source_time;self.collector.events=[]
        with event_scope(event.source_time,'OZ_STATE',self.clock.state),memory_scope(self.memory):
            self.bootstrap(board,startup_events)
            if command is not None:
                action=command.get('action')
                if action=='SWEEP_WATCH':
                    self.registry[command['watch_id']]=ExternalLiquiditySpec(command['watch_id'],command['symbol'],command['source_tf'])
                elif action=='CANCEL_SWEEP':self.registry.pop(command.get('watch_id'),None)
                elif action=='RESET_SWEEP':self.registry.clear()
                worker=self.oz.OZCommandHandler.__new__(self.oz.OZCommandHandler)
                worker.controller=self.watch;worker.generic=GenericBoundary();worker.config=self.config
                worker._apply(plain(command))
                self.validate_selection()
            else:self._market(event,board)
        # Composer's existing read-only projection uses this JSON envelope.
        # Only changed external qualification is encoded, never profile state.
        if self.external_revision!=self.external.revision:
            self.external_memory=json.dumps(self.external.export_payload(),ensure_ascii=False)
            self.external_revision=self.external.revision
        return {'publication':event.engine_seq,'symbol':event.payload['symbol'],
                'events':self.collector.events,'external_memory':self.external_memory}

    def validate_selection(self):
        if self.selection is None:return
        for watch in self.watch._watches.values():
            for tf in watch.timeframes:self.selection.require(tf,watch.validation_mode,watch.trigger_mode)
        for spec in self.external._specs.values():
            if spec.source_tf not in self.selection.feeds:
                raise ValueError('undeclared OZ external source: '+spec.source_tf)

    def export_files(self):
        files=dict(self.memory)
        files['oz_manual_watch_state.json']=json.dumps(self.watch.export_payload(),ensure_ascii=False)
        files['oz_external_liquidity_state.json']=json.dumps(self.external.export_payload(),ensure_ascii=False)
        for (symbol,vm,tm),profile in self.profiles.items():
            files[profile.checkpoint_name]=json.dumps({'version':1,'time_unit':'epoch_seconds','symbol':symbol,
                'profile':[vm,tm],'observed':profile.export_event_state()},ensure_ascii=False)
        return files

    def __deepcopy__(self,memo):
        # Called only by explicit engine checkpoints, not market dispatch.
        other=OZRuntime(self.oz,self.config,deepcopy(self.memory,memo),selection=self.selection);memo[id(self)]=other
        other.clock.state=deepcopy(self.clock.state,memo);other.clock.milliseconds=self.clock.milliseconds
        other.registry.update(deepcopy(self.registry,memo))
        for name in ('_specs','_states','_invalidated','revision','_fingerprint'):
            setattr(other.external,name,deepcopy(getattr(self.external,name),memo))
        for name in ('_watches','_revision','dirty'):setattr(other.watch,name,deepcopy(getattr(self.watch,name),memo))
        for key,profile in self.profiles.items():
            clone=OZProfile(*key[:1],self.config,other.sender,other.watch,*key[1:],source_time=profile._source_time,base_tfs=profile.base_tfs)
            clone.restore_event_state(profile.export_event_state());other.profiles[key]=clone
        other.previous=dict(self.previous)  # Snapshot arrays are immutable.
        for name in ('signatures','last_evaluated','ready_profiles','views_created','evaluations','force_all','bootstrapped','external_revision','external_memory'):
            setattr(other,name,deepcopy(getattr(self,name),memo))
        return other
