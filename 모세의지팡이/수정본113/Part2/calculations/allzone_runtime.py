"""Independent historical OZ runtime: frames + token + explicit environment.

No monitor/controller, wall clock, network, subscriptions or LIVE persistence.
A caller must supply point-in-time PRICE/RSI/STO/DI and mapped frames. Missing
upstream capabilities are not invented by this engine. External SWEEP/ATR
qualification is a separate input contract, not silently reimplemented here.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
import pandas as pd
from generic_backtest.contracts import GenericError
from .allzone_support.inputs import ObservationGuard,validate_frame,frame_signature,exact_digest,legacy_identity
from .allzone_support.array_frame import ArrayFrame,from_validated_frame
from .allzone import (_OZRules, IndicatorEpisode, TF_MAP, PERCENTILES,
    _resolve_oz_modes, _observed_encode, _observed_decode)


_BASE_ACCELERATOR_FACTORY = None


def configure_base_acceleration(factory=None):
    """Set an application-owned numerical adapter; no DB/MT5 imports here."""
    global _BASE_ACCELERATOR_FACTORY
    _BASE_ACCELERATOR_FACTORY = factory


class _HistoricalWatchAdapter:
    """Expose the LIVE watch qualification method against historical environment input."""
    def __init__(self, engine):
        self.engine = engine

    def validate_external_true_b0(self, symbol, tf, direction, validation_mode, trigger_mode, true_b0):
        # The LIVE method asks the Watch controller here. Historical mode receives
        # the same qualification explicitly from the caller-owned OZEnvironment.
        return self.engine._external_eligible(tf, direction, true_b0)


@dataclass(frozen=True)
class OZEnvironment:
    # Pure, caller-owned historical eligibility. NOT a LIVE WATCH handle.
    identities: frozenset = frozenset()
    external_required: bool = False
    external_qualified: bool = False
    qualified_true_b0: float | None = None


class HistoricalOZEngine(_OZRules):
    version='BACKTEST_OZ_SOURCE_V1'
    _checkpoint_fields=('episodes','prev_states','prev_bar_time','candidates','percentile_candidates',
                        'alert_keys','bootstrapped','environment_identities','hma_cross_extremes')

    def __init__(self,symbol,validation_mode='NORMAL',trigger_mode='OZ',*,
                 max_bars=10,max_cross_bars=7,max_outin_bars=7,min_bars=1,
                 alert_long=True,alert_short=True,use_numpy=True):
        self.symbol=str(symbol)
        self.use_numpy=bool(use_numpy)
        self.numpy_stats={'array_frames':0,'reference_fallback_frames':0}
        self.validation_mode,self.trigger_mode=_resolve_oz_modes(validation_mode,trigger_mode)
        self.max_bars=int(max_bars);self.max_bars_after_hma_cross=int(max_cross_bars)
        self.max_bars_after_outin=int(max_outin_bars);self.min_bars=int(min_bars)
        self.alert_long=bool(alert_long);self.alert_short=bool(alert_short)
        self.base_tfs=list(TF_MAP)
        self.episodes={(tf,d,ind):IndicatorEpisode() for tf in self.base_tfs for d in ('LONG','SHORT') for ind in PERCENTILES}
        self.prev_states={};self.prev_bar_time={tf:None for tf in self.base_tfs}
        self.candidates={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.percentile_candidates={(tf,d,p):None for tf in self.base_tfs for d in ('LONG','SHORT') for p in PERCENTILES}
        self.alert_keys=set();self.bootstrapped=set()
        self.environment_identities={(tf,d):frozenset() for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.hma_cross_extremes={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self._resume_outin=set();self._resume_cross=set();self._resume_cross_seen={}
        self.watch=_HistoricalWatchAdapter(self)
        self.guard=ObservationGuard();self._environment={};self._token=None;self._events=[]
        self._base_accelerator = _BASE_ACCELERATOR_FACTORY(self) if _BASE_ACCELERATOR_FACTORY is not None else None

    @staticmethod
    def required_timeframes(base_tfs,validation_mode='NORMAL',trigger_mode='OZ'):
        vm,_=_resolve_oz_modes(validation_mode,trigger_mode)
        result=set()
        for tf in base_tfs:
            if tf not in TF_MAP:raise GenericError('E_ENGINE_INPUT','unsupported OZ base '+tf)
            result.add(tf)
            if vm=='NORMAL':result.update(TF_MAP[tf])
        return tuple(sorted(result))

    def _external_eligible(self,tf,direction,true_b0):
        env=self._environment.get((tf,direction),OZEnvironment())
        if not env.external_required:return True
        # Require a qualification for this exact structural price. No reuse of
        # an external permit for a different TRUE B0, no implicit ATR substitute.
        return (env.external_qualified and env.qualified_true_b0==true_b0)

    def _record(self,kind,tf,direction,payload):
        event={'kind':kind,'symbol':self.symbol,'timeframe':tf,'direction':direction,
            'validation_mode':self.validation_mode,'trigger_mode':self.trigger_mode,
            'observed_at_ns':self._token.now_ns,'source_ordinal':self._token.source_ordinal,
            'input_prefix':self._token.prefix_commitment,'payload':deepcopy(payload)}
        event['event_id']=exact_digest((self.version,self._token,kind,tf,direction,len(self._events),payload))
        self._events.append(event)

    def _cancel_base_candidate(self,tf,direction,reason):
        before=(self.candidates.get((tf,direction)) is not None or
                bool(self._active_percentile_candidates(tf,direction)) or
                self.hma_cross_extremes.get((tf,direction)) is not None)
        super()._cancel_base_candidate(tf,direction,reason)
        if before and self._token is not None:self._record('OZ_BASE_CANCELLED',tf,direction,{'reason':reason})

    def _register_percentile_candidate(self,tf,direction,percentile,b0_price,b0_time,trigger_time):
        result=super()._register_percentile_candidate(tf,direction,percentile,b0_price,b0_time,trigger_time)
        if self._token is not None:self._record('OZ_FAMILY_REGISTERED',tf,direction,{
            'percentile':percentile,'b0_price':b0_price,'b0_time':b0_time,'trigger_time':trigger_time})
        return result

    def _silent_consume_candidate(self,tf,direction,decision,reason):
        c=self.candidates.get((tf,direction))
        active=c is not None and not c.alerted and not c.completed_outside_window
        super()._silent_consume_candidate(tf,direction,decision,reason)
        if active and self._token is not None:self._record('OZ_SILENT_CONSUMED',tf,direction,{'reason':reason,'decision':decision})

    def _evaluate_local(self,data,tf,direction,delivery_succeeded):
        decision=self._candidate_completion_decision(data,tf,direction,require_external=True,commit_validation=True)
        if decision is None:return
        cand=self.candidates.get((tf,direction))
        if cand is None or cand.alerted or cand.completed_outside_window:return
        key=(tf,direction,decision.true_b0_time)
        if key in self.alert_keys:
            cand.alerted=True
            return
        if cand.completion_time is None:cand.completion_time=self._token.now_ns/1_000_000_000
        df=data.get(tf)
        px=df.iloc[-1].get('close') if df is not None and not df.empty else None
        from .allzone import finite_number
        if callable(delivery_succeeded):
            delivery_succeeded=delivery_succeeded(tf,direction,decision)
            if type(delivery_succeeded) is not bool:raise GenericError('E_ENGINE_INPUT','delivery acknowledgement must be bool')
        payload={'grade':decision.grade,'consensus_count':decision.consensus_count,
            'trigger_name':decision.trigger.trigger_name,
            'alert_identity':legacy_identity(self.symbol,tf,direction,self.validation_mode,self.trigger_mode,str(decision.true_b0_time)),
            'completion_time':cand.completion_time,
            'indicators_text':', '.join(decision.matched_trigger_contexts),
            'current_price':float(px) if finite_number(px) else None,'decision':decision,
            'local_delivery_succeeded':bool(delivery_succeeded)}
        self._record('OZ_COMPLETION_DECISION',tf,direction,payload)
        if delivery_succeeded:
            self.alert_keys.add(key);cand.alerted=True;self._clear_percentile_candidates(tf,direction)
            self._record('OZ_LOCAL_ALERT',tf,direction,payload)

    def observe(self,data,token,*,environments=None,delivery_succeeded=True):
        if not isinstance(data,Mapping):raise GenericError('E_ENGINE_INPUT','frame mapping')
        environment=dict(environments or {})
        for key,value in environment.items():
            if not isinstance(key,tuple) or len(key)!=2 or key[0] not in TF_MAP or key[1] not in ('LONG','SHORT') or not isinstance(value,OZEnvironment):
                raise GenericError('E_ENGINE_INPUT','explicit OZ environment')
            if not isinstance(value.identities,frozenset):raise GenericError('E_ENGINE_INPUT','immutable environment identities')
        for tf,frame in data.items():
            validate_frame(frame,token,tf,required=('hma_6','hma_17'),symbol=self.symbol)
        prepared=None
        if self.use_numpy:
            prepared={tf:from_validated_frame(frame) for tf,frame in data.items()}
        def signature_for(tf):
            frame=prepared[tf] if prepared is not None else None
            if prepared is not None and isinstance(frame,ArrayFrame):
                return frame_signature(data[tf],column_arrays=frame.arrays,dtype_names=frame.dtype_names)
            return frame_signature(data[tf])
        signature=exact_digest((tuple((tf,signature_for(tf)) for tf in sorted(data)),environment,bool(delivery_succeeded)))
        if not self.guard.check(token,signature):return ()
        self._token=token;self._environment=environment;self._events=[]
        if self.use_numpy:
            data=prepared
            for frame in data.values():
                self.numpy_stats['array_frames' if isinstance(frame,ArrayFrame) else 'reference_fallback_frames']+=1
        # Environment qualification and delivery remain on the original path.
        if self._base_accelerator is None:
            self._advance_base(data)
        else:
            self._base_accelerator.apply(self, data)
        for tf in self.base_tfs:
            if tf not in data:continue
            for direction in ('LONG','SHORT'):
                key=(tf,direction);previous=self.environment_identities.get(key,frozenset())
                current=environment.get(key,OZEnvironment()).identities
                if not current:
                    self.environment_identities[key]=frozenset()
                    self._probe_silent_completion(data,tf,direction,'NO_ENVIRONMENT')
                    continue
                fresh=not previous or previous.isdisjoint(current)
                if fresh and self._probe_silent_completion(data,tf,direction,'PREEXISTING_AT_ENVIRONMENT_ACTIVATION'):
                    self.environment_identities[key]=current
                    continue
                self.environment_identities[key]=current
                if direction=='LONG' and self.alert_long or direction=='SHORT' and self.alert_short:
                    self._evaluate_local(data,tf,direction,delivery_succeeded)
        self.guard.commit(token,signature)
        return tuple(deepcopy(self._events))

    def _advance_base(self, data):
        """Original source-ordered base state mutations; no delivery/Watch gate."""
        for tf in self.base_tfs:
            df=data.get(tf)
            if df is None:continue
            self._process_hma_cross(tf,df);self._process_out_in(tf,df)
            for direction in ('LONG','SHORT'):
                before={p:r.invalidated for p in PERCENTILES if (r:=self.percentile_candidates[(tf,direction,p)]) is not None}
                self._maintain_base_candidate(tf,direction,df)
                for p,was in before.items():
                    r=self.percentile_candidates[(tf,direction,p)]
                    if r is not None and not was and r.invalidated:
                        self._record('OZ_FAMILY_EXPIRED',tf,direction,{'percentile':p,'trigger_time':r.trigger_time})

    @staticmethod
    def _bars_since_event(df,event_time):
        from .allzone_support.array_frame import ArrayFrame
        if isinstance(df,ArrayFrame):return df.bars_since(event_time)
        return _OZRules._bars_since_event(df,event_time)

    @staticmethod
    def _one_way_reason(df,direction,hma_cross_time):
        from .allzone_support.array_frame import ArrayFrame
        if isinstance(df,ArrayFrame):return df.one_way_reason(direction,hma_cross_time)
        return _OZRules._one_way_reason(df,direction,hma_cross_time)

    def snapshot_state(self):
        return deepcopy({name:getattr(self,name) for name in self._checkpoint_fields})

    def mark_observation_gap(self):
        """Source restart guard, not a rewind/replay-checkpoint operation."""
        self._resume_outin=set(self.prev_states)
        self._resume_cross=set(self.base_tfs)
