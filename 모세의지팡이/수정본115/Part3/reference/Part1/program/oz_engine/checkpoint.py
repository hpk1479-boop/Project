"""Explicit checkpoint codec; old timestamp envelopes become epoch seconds."""
from dataclasses import is_dataclass
from . import common

CLASSES={c.__name__:c for c in (common.IndicatorEpisode,common.PercentileCandidate,common.Candidate,
    common.FinalTriggerDecision,common.CandidateCompletionDecision,common.ExternalLiquiditySpec,
    common.ExternalLiquidityState,common.WatchSpec)}

def encode(value):
    if is_dataclass(value):return {'type':type(value).__name__,'value':encode(vars(value))}
    if isinstance(value,dict):return {'type':'dict','value':[[encode(k),encode(v)] for k,v in value.items()]}
    if isinstance(value,(tuple,set,frozenset,list)):return {'type':type(value).__name__,'value':[encode(v) for v in value]}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise TypeError('unsupported OZ checkpoint value: '+type(value).__name__)

def decode(value):
    if not isinstance(value,dict):return value
    kind,raw=value['type'],value['value']
    if kind=='timestamp':return common.epoch(raw)
    if kind=='dict':return {decode(k):decode(v) for k,v in raw}
    if kind in ('tuple','set','frozenset','list'):return {'tuple':tuple,'set':set,'frozenset':frozenset,'list':list}[kind](decode(x) for x in raw)
    return CLASSES[kind](**decode(raw))
