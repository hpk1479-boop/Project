"""Lossless, allowlisted data codec. No pickle, eval, imports from payloads.

Retains IEEE float bits, dtype, timezone, tuple/set keys and dataclass types.
Used only for calculation checkpoints / explicit numerical inputs.
"""
from __future__ import annotations
import base64
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import hashlib
import json
import struct
import numpy as np
import pandas as pd


def _classes():
    from .allzone import IndicatorEpisode, PercentileCandidate, Candidate, FinalTriggerDecision, CandidateCompletionDecision
    from .allzone_runtime import OZEnvironment
    from pit.models import AsOfToken, BarState
    from pit.features.percentile.contracts import NativeCell, CalcEvent
    return {c.__name__: c for c in (IndicatorEpisode, PercentileCandidate, Candidate,
        FinalTriggerDecision, CandidateCompletionDecision, OZEnvironment, AsOfToken, BarState, NativeCell, CalcEvent)}


def encode(value):
    if value is pd.NaT:return ['nat']
    if value is pd.NA:return ['pandas_na']
    if isinstance(value,pd.Timestamp):
        return ['timestamp',value.isoformat(),str(value.unit)]
    if isinstance(value,np.generic):
        return ['numpy_scalar',value.dtype.str,base64.b64encode(value.tobytes()).decode('ascii')]
    if type(value) is float:return ['f64',struct.pack('>d',value).hex()]
    if value is None or type(value) in (bool,int,str):return ['scalar',value]
    if isinstance(value,bytes):return ['bytes',base64.b64encode(value).decode('ascii')]
    if isinstance(value,pd.DataFrame):
        if type(value) is not pd.DataFrame:raise ValueError('CHECKPOINT_FRAME_SUBCLASS_UNSUPPORTED')
        if not value.columns.is_unique or not isinstance(value.index,pd.RangeIndex):
            raise ValueError('CHECKPOINT_FRAME_INDEX')
        return ['frame',list(value.columns),[encode(value[c].to_numpy()) for c in value.columns],
                [str(d) for d in value.dtypes],encode(dict(value.attrs)),
                [value.index.start,value.index.stop,value.index.step]]
    if isinstance(value,np.ndarray):
        if value.dtype.hasobject:return ['object_array',list(value.shape),[encode(x) for x in value.flat]]
        if value.dtype.fields:raise ValueError('CHECKPOINT_STRUCTURED_ARRAY_UNSUPPORTED')
        return ['array',value.dtype.str,list(value.shape),base64.b64encode(value.tobytes(order='C')).decode('ascii')]
    if is_dataclass(value) and not isinstance(value,type):
        name=type(value).__name__;registered=_classes().get(name)
        if registered is not type(value):raise ValueError('CHECKPOINT_CLASS_NOT_ALLOWED:'+name)
        return ['dataclass',name,[[f.name,encode(getattr(value,f.name))] for f in fields(value)]]
    if isinstance(value,Mapping):return ['mapping',[[encode(k),encode(v)] for k,v in value.items()]]
    if isinstance(value,(set,frozenset)):
        entries=[encode(x) for x in value];entries.sort(key=canonical)
        return ['frozenset' if isinstance(value,frozenset) else 'set',entries]
    if isinstance(value,(list,tuple)):return ['tuple' if isinstance(value,tuple) else 'list',[encode(x) for x in value]]
    raise TypeError('CHECKPOINT_TYPE_NOT_ALLOWED:'+type(value).__name__)


def decode(value):
    if not isinstance(value,list) or not value or not isinstance(value[0],str):raise ValueError('CHECKPOINT_LAYOUT')
    kind=value[0]
    if kind=='scalar':
        if len(value)!=2 or (value[1] is not None and type(value[1]) not in (bool,int,str)):raise ValueError('CHECKPOINT_SCALAR')
        return value[1]
    if kind=='nat':return pd.NaT
    if kind=='pandas_na':return pd.NA
    if kind=='timestamp':return pd.Timestamp(value[1]).as_unit(value[2])
    if kind=='f64':return struct.unpack('>d',bytes.fromhex(value[1]))[0]
    if kind=='bytes':return base64.b64decode(value[1],validate=True)
    if kind in ('array','numpy_scalar'):
        dtype=np.dtype(value[1])
        if dtype.hasobject or dtype.fields:raise ValueError('CHECKPOINT_UNSAFE_DTYPE')
        data=base64.b64decode(value[-1],validate=True)
        if len(data)>64*1024*1024:raise ValueError('CHECKPOINT_ARRAY_LIMIT')
        array=np.frombuffer(data,dtype=dtype).copy()
        return array[0] if kind=='numpy_scalar' else array.reshape(tuple(value[2]))
    if kind=='object_array':
        flat=[decode(x) for x in value[2]];a=np.empty(len(flat),dtype=object);a[:]=flat
        return a.reshape(tuple(value[1]))
    if kind=='frame':
        names,arrays,dtypes,attrs,index=value[1:]
        if len(names)!=len(set(names)) or not len(names)==len(arrays)==len(dtypes):raise ValueError('CHECKPOINT_FRAME_LAYOUT')
        columns={n:pd.Series(decode(a),dtype=d) for n,a,d in zip(names,arrays,dtypes)}
        frame=pd.DataFrame(columns);frame.index=pd.RangeIndex(*index);frame.attrs=decode(attrs)
        return frame
    if kind=='mapping':
        result={}
        for k,v in value[1]:
            key=decode(k)
            if key in result:raise ValueError('CHECKPOINT_DUPLICATE_KEY')
            result[key]=decode(v)
        return result
    if kind=='dataclass':
        cls=_classes().get(value[1])
        if cls is None:raise ValueError('CHECKPOINT_CLASS_NOT_ALLOWED')
        keys=[k for k,v in value[2]]
        if keys!=[f.name for f in fields(cls)]:raise ValueError('CHECKPOINT_CLASS_FIELDS')
        return cls(**{k:decode(v) for k,v in value[2]})
    if kind in ('tuple','list','set','frozenset'):
        seq=[decode(x) for x in value[1]]
        return {'tuple':tuple,'list':list,'set':set,'frozenset':frozenset}[kind](seq)
    raise ValueError('CHECKPOINT_TAG_NOT_ALLOWED:'+kind)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True)


def digest(value):return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()
