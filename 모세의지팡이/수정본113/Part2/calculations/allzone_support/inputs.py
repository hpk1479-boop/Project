"""Point-in-time ownership boundary for independently calculated market engines."""
from __future__ import annotations
from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
import struct
from collections.abc import Mapping
import numpy as np
import pandas as pd
from pit.models import AsOfToken
from generic_backtest.contracts import GenericError, TIMEFRAMES
from generic_backtest.fast.scalar_bits import bool_float_bytes


def exact_tree(value):
    """Typed canonical evidence. No NaN normalization or float tolerance."""
    if isinstance(value,pd.Timestamp):
        return {'timestamp':value.isoformat()}
    if isinstance(value,(float,np.floating)):
        return {'f64':struct.pack('>d',float(value)).hex()}
    if isinstance(value,(bool,np.bool_)): return bool(value)
    if isinstance(value,np.integer): return int(value)
    if is_dataclass(value): return exact_tree(asdict(value))
    if isinstance(value,Mapping):
        items=[(exact_tree(k),exact_tree(v)) for k,v in value.items()]
        items.sort(key=lambda pair:json.dumps(pair[0],ensure_ascii=False,sort_keys=True))
        return {'mapping':items}
    if isinstance(value,(set,frozenset)):
        return {'set':sorted([exact_tree(x) for x in value],key=lambda x:json.dumps(x,sort_keys=True))}
    if isinstance(value,(tuple,list)): return [exact_tree(x) for x in value]
    if value is None or isinstance(value,(str,int)): return value
    raise TypeError('Unserializable engine evidence: '+type(value).__name__)


def exact_digest(value):
    return hashlib.sha256(json.dumps(exact_tree(value),ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def legacy_identity(*parts):
    # Same public algorithm; no import of LIVE durable records/storage.
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True,
        default=str,separators=(',',':')).encode('utf-8')).hexdigest()


def validate_frame(frame, token, timeframe, *, required=(), symbol=None):
    """Validate an already as-of full window; never trim away a future row."""
    if not isinstance(token,AsOfToken) or timeframe not in TIMEFRAMES:
        raise GenericError('E_ENGINE_INPUT','token/timeframe')
    if not isinstance(frame,pd.DataFrame) or not frame.columns.is_unique:
        raise GenericError('E_ENGINE_INPUT','unique DataFrame columns required')
    if not isinstance(frame.index,pd.RangeIndex) or frame.index.start!=0 or frame.index.step!=1:
        raise GenericError('E_ENGINE_INPUT','RangeIndex from zero required')
    missing=set(('time','open','high','low','close','volume',*required))-set(frame.columns)
    if missing: raise GenericError('E_FEATURE_UNAVAILABLE',','.join(sorted(missing)))
    if frame.empty: return
    dtype=frame['time'].dtype
    if isinstance(dtype,np.dtype) and np.issubdtype(dtype,np.datetime64):
        stamps=frame['time'].to_numpy(dtype='datetime64[ns]').view('int64')
        if np.any(stamps==np.iinfo('int64').min) or np.any(stamps[1:]<=stamps[:-1]):
            raise GenericError('E_ENGINE_INPUT','bar timestamps')
    else:
        times=pd.to_datetime(frame['time'],errors='coerce')
        if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
            raise GenericError('E_ENGINE_INPUT','bar timestamps')
        stamps=np.array([pd.Timestamp(t).value for t in times],dtype='int64')
    if np.any(stamps>token.now_ns):raise GenericError('E_FUTURE_READ','bar open')
    if np.any(stamps[:-1]+TIMEFRAMES[timeframe]*1_000_000_000>token.now_ns):
        raise GenericError('E_FUTURE_READ','completed bar not yet closed')
    available=frame.attrs.get('available_at_ns')
    ordinal=frame.attrs.get('source_ordinal')
    if available is not None and available>token.now_ns or ordinal is not None and ordinal>token.source_ordinal:
        raise GenericError('E_FUTURE_READ','frame availability')
    if symbol is not None and frame.attrs.get('symbol',symbol)!=symbol:
        raise GenericError('E_ENGINE_INPUT','symbol binding')


def frame_signature(frame, *, column_arrays=None, dtype_names=None):
    # Guard identity is private, not an event ID. Encode numeric bytes directly
    # (including signed zero and NaN payloads) instead of allocating row dicts.
    h=hashlib.sha256()
    h.update(exact_digest((tuple(frame.columns),frame.attrs,tuple(str(x) for x in frame.dtypes) if dtype_names is None else dtype_names)).encode())
    for column in frame.columns:
        array=frame[column].to_numpy() if column_arrays is None else column_arrays[column]
        if array.dtype.hasobject:
            # State columns contain bools and historical NaNs. Encode scalar
            # types and exact float bits, never object pointer addresses. An
            # object column must not force every numeric cell through JSON.
            raw=bool_float_bytes(array)
            if raw is None:
                raw=exact_digest(array.tolist()).encode('ascii')
        else:raw=array.tobytes()
        h.update(struct.pack('<Q',len(raw)));h.update(raw)
    return h.hexdigest()



class ObservationGuard:
    """Monotone as-of calls; identical retry is read-only, changed retry is error."""
    def __init__(self): self.last_token=None;self.signature=None
    def check(self,token,signature):
        previous=self.last_token
        if previous is not None:
            if token.market_epoch!=previous.market_epoch:
                raise GenericError('E_ENGINE_EPOCH','new engine required')
            if token.now_ns<previous.now_ns or token.source_ordinal<previous.source_ordinal:
                raise GenericError('E_FUTURE_READ','observation order regression')
            if token.now_ns==previous.now_ns and token.source_ordinal==previous.source_ordinal and token.microstep<previous.microstep:
                raise GenericError('E_FUTURE_READ','microstep regression')
            if token==previous:
                if signature!=self.signature:raise GenericError('E_ENGINE_RETRY_COLLISION','same token changed input')
                return False
            if (token.now_ns,token.source_ordinal,token.microstep)==(previous.now_ns,previous.source_ordinal,previous.microstep):
                raise GenericError('E_ENGINE_RETRY_COLLISION','cursor identity changed')
        return True
    def commit(self,token,signature): self.last_token=token;self.signature=signature
