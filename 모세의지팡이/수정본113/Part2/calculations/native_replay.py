"""Independently recompute native calls from BAR snapshots, never tick arrays.

This preserves the actual OnCalculate R/P sequence and native prestate instead
of treating final completed bars as earlier forming-bar values.
"""
from __future__ import annotations
from .percentile_band import make_kernel,export_result
from .state_codec import encode,decode,digest
from .bar_observations import BarObservationDecoder


def plain(value):
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value


class NativeReplayCompiler:
    def __init__(self,family):
        self.family=family;self.kernel=None;self.reference=None;self.parameters=None;self.events=[];self.event_numbers={};self.calls=0;self.bar_decoder=BarObservationDecoder()
    def step(self,expected,packet):
        from pit.models import AsOfToken
        from pit.features.percentile.contracts import CalcEvent
        parameters=packet['parameters']
        if self.kernel is None:
            self.parameters=parameters;self.kernel=make_kernel(self.family,parameters);self.reference=make_kernel(self.family,parameters)
            self.reference.incremental_percentiles=False
        if self.parameters!=parameters:raise ValueError('PERCENTILE_PARAMETER_CHANGED')
        bars=self.bar_decoder.restore(packet['bar_delta']) if 'bar_delta' in packet else decode(packet['bars'])
        event=decode(packet['event'])
        if not isinstance(event,CalcEvent):raise ValueError('PERCENTILE_EVENT_TYPE')
        if list(expected)!=[event.R,event.P,event.source_ordinal,event.point]:raise ValueError('PERCENTILE_INVOCATION_MISMATCH')
        reset=event.P==0 and (event.reset_reason or self.kernel.state.history_epoch!=event.history_epoch)
        if event.R!=len(bars) or (event.P!=self.kernel.state.last_return and not reset):raise ValueError('PERCENTILE_R_P_SEQUENCE_MISMATCH')
        token=event.market_asof_token
        if isinstance(token,AsOfToken):
            for bar in bars:
                if bar.open_ns>token.now_ns or bar.last_ordinal>token.source_ordinal:raise ValueError('PERCENTILE_FUTURE_BAR')
                if bar.state=='COMPLETED' and (bar.nominal_end_ns>token.now_ns or (bar.complete_at_order is not None and bar.complete_at_order>token.source_ordinal)):
                    raise ValueError('PERCENTILE_FUTURE_COMPLETED_BAR')
        result=self.kernel.invoke(bars,event);reference=self.reference.invoke(bars,event)
        if digest(encode(export_result(result)))!=digest(encode(export_result(reference))):raise ValueError('PERCENTILE_FULL_ARRAY_REFERENCE_MISMATCH')
        number=len(self.events);self.events.append(event.calc_event_id);self.event_numbers[event.calc_event_id]=number
        def cell(v):
            if v.last_write_event is not None and v.last_write_event not in self.event_numbers:raise ValueError('PERCENTILE_EVENT_REFERENCE')
            return [v.bits,v.origin,list(v.taint),self.event_numbers.get(v.last_write_event)]
        self.calls+=1
        return {'returned':result.returned,'metadata':plain(dict(result.metadata)),
                'buffers':{name:[cell(c) for c in rows[:2]] for name,rows in result.buffers.items()}}
