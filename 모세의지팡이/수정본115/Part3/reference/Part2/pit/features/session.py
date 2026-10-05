from dataclasses import dataclass
from ..contracts import PitError,digest,TIMEFRAMES
from ..models import FeatureValue
from .hma_open import SourceHMAOpenKernel

@dataclass(frozen=True)
class FeatureSpec:
    symbol: str
    timeframe: str
    period: int
    name: str='HMA_OPEN'
    source_field: str='OPEN'
    seed_policy: str='STRICT_COMPLETE_OBSERVED_WINDOW'

class FeatureSession:
    def __init__(self,owner):
        if not owner:raise PitError('E_ID_COLLISION','owner')
        self.owner=owner;self._specs={};self._windows={};self._signatures={};self._forming={};self._token=None
    def bind(self,spec):
        if self._token is not None:raise PitError('E_UNDECLARED_DEPENDENCY','bind before execution')
        if (spec.name,spec.source_field,spec.symbol,spec.seed_policy)!=('HMA_OPEN','OPEN','XAUUSD+','STRICT_COMPLETE_OBSERVED_WINDOW') or spec.period not in (6,17) or spec.timeframe not in TIMEFRAMES:
            raise PitError('E_FEATURE_NOT_AVAILABLE',repr(spec))
        handle=digest(('FEATURE',self.owner,spec,SourceHMAOpenKernel.version,SourceHMAOpenKernel.source_hash))
        self._specs[handle]=spec
        return handle
    def advance(self,view):
        token=view.token
        if self._token:
            if token==self._token:return
            if token.market_epoch!=self._token.market_epoch or token.source_ordinal<=self._token.source_ordinal or token.now_ns<self._token.now_ns:
                raise PitError('E_OBSERVER_ORDER','session cannot rewind or change core')
        for handle,spec in self._specs.items():
            bars=view.bars(spec.timeframe)
            self._forming[handle]=bool(bars and bars[-1].state=='FORMING')
            signature=tuple((b.bar_id,b.open,b.quality,b.gap_before) for b in bars)
            if self._signatures.get(handle)==signature:continue
            values=SourceHMAOpenKernel.calculate(tuple(b.open if b.quality=='COMPLETE_PREFIX' else None for b in bars),spec.period)
            required=SourceHMAOpenKernel.warmup(spec.period);window=[]
            for i,(bar,value) in enumerate(zip(bars,values)):
                tail=bars[max(0,i-required+1):i+1];reason=None
                if len(tail)<required:reason='INSUFFICIENT_HISTORY'
                elif any(b.quality!='COMPLETE_PREFIX' for b in tail):reason='PARTIAL_SEED' if any(b.quality.startswith('PARTIAL') for b in tail) else 'SOURCE_GAP'
                elif any(b.gap_before for b in tail[1:]):reason='SOURCE_GAP'
                elif value is None:reason='UNDEFINED_INITIALIZATION'
                window.append((bar.bar_id,None if reason else value,reason))
            self._windows[handle]=tuple(window);self._signatures[handle]=signature
        self._token=token
    def _check(self,handle,token):
        if handle not in self._specs:raise PitError('E_UNDECLARED_DEPENDENCY','foreign/unknown handle')
        if token!=self._token:raise PitError('E_FUTURE_READ','feature token differs')
    def window(self,handle,token,row_count=650,include_forming=True):
        self._check(handle,token)
        if type(row_count)!=int or not 0<row_count<=650:raise PitError('E_RESOURCE_LIMIT','feature window')
        raw=self._windows[handle]
        if not include_forming and self._forming[handle]:raw=raw[:-1]
        return tuple(FeatureValue(handle,b,v,'UNAVAILABLE' if reason else 'SOURCE_PARITY_VALID_DOMAIN',reason,
                                 token.source_ordinal,token.prefix_commitment) for b,v,reason in raw[-row_count:])
    def value(self,handle,token):
        values=self.window(handle,token,1)
        if values:return values[-1]
        return FeatureValue(handle,'',None,'UNAVAILABLE','INSUFFICIENT_HISTORY',token.source_ordinal,token.prefix_commitment)
    def state_hash(self):return digest((self.owner,self._token,self._specs,self._windows,self._forming))
