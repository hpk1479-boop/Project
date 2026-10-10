from ..contracts import KernelResult,NativeCell,PercentileError
from ..profiles import freeze_source_profile,SOURCE_HASHES
from ..series import SourceStateStore,SeriesAdapter
from ..lifecycle import source_call_plan

OSCILLATOR_BUFFERS=('lower','upper','smooth','arrow_lower','arrow_upper','basis','color','regime_upper','regime_lower')
PRICE_BUFFERS=('upper','lower','ma1','ma2','ma3','hma','arrow_lower','arrow_upper','basis','color','regime_upper','regime_lower','ehlers')

class SourceKernel:
    family=''
    def __init__(self,profile=None):
        self.profile=profile or freeze_source_profile(self.family)
        if self.profile.family!=self.family or self.profile.source_hash!=SOURCE_HASHES[self.family]:raise PercentileError('E_SOURCE_DRIFT',self.family)
        # Direct SourceProfile construction must not bypass supported input checks.
        freeze_source_profile(self.family,self.profile.params,self.profile.seed_policy)
        self.state=SourceStateStore(PRICE_BUFFERS if self.family=='PRICE' else OSCILLATOR_BUFFERS)
        self._price_cache = {}
        self._close_bars = ()
        self._close_cells = None
        self.incremental_percentiles = True
        self._band_window = None
    def snapshot(self):return self.state.snapshot()
    def restore(self,data):
        self.state.restore(data)
        self._band_window = None  # derived index is rebuilt, not a second native state
    def _begin(self,bars,event):
        self.state.begin(bars,event);self.event=event;self.bars=SeriesAdapter(bars)
        return source_call_plan(self.family,event.R,event.P,self.profile.params)
    def price(self,name,shift):
        if not 0<=shift<len(self.bars):return NativeCell.unknown(f'{name}:SOURCE_ARRAY_BOUNDS:{shift}')
        bar=self.bars[shift]
        key = (name, getattr(bar, 'bar_id', None))
        cached = self._price_cache.get(key) if self.state.freeze_versions and not isinstance(bar, dict) else None
        if cached is not None and cached[0] is bar:
            return cached[1]
        value=bar[name] if isinstance(bar,dict) else getattr(bar,name)
        if value is None:return NativeCell.unknown(f'{name}:INPUT_UNAVAILABLE')
        quality=bar.get('quality','COMPLETE_PREFIX') if isinstance(bar,dict) else getattr(bar,'quality','COMPLETE_PREFIX')
        result = NativeCell.from_float(value,'MARKET_INPUT',() if quality=='COMPLETE_PREFIX' else ('MARKET_'+quality,))
        if self.state.freeze_versions and not isinstance(bar, dict):
            self._price_cache[key] = (bar, result)
        return result
    def _result(self,returned,plan,extra=None):
        self.state.last_return=returned
        if self.state.freeze_versions:
            from ..frozen_buffers import TrackedCells
            buffers = {name: a.freeze() for name, a in self.state.buffers.items()}
            if self.family == 'PRICE':
                bars = tuple(self.bars.values)
                if len(bars) != len(self._close_bars) or self._close_cells is None:
                    self._close_cells = TrackedCells(self.price('close', i) for i in range(self.event.R))
                else:
                    for i, (old, new) in enumerate(zip(reversed(self._close_bars), reversed(bars))):
                        if old is not new:
                            self._close_cells[i] = self.price('close', i)
                self._close_bars = bars
                buffers['raw'] = self._close_cells.freeze()
            else:
                buffers['raw'] = self.state.raw.freeze()
        else:
            buffers={name:tuple(a) for name,a in self.state.buffers.items()}
            buffers['raw']=tuple(self.state.raw) if self.family!='PRICE' else tuple(self.price('close',i) for i in range(self.event.R))
        if self.family=='PRICE':buffers['smooth']=buffers['ehlers']
        metadata={'family':self.family,'limit':plan.limit,'allocated':self.state.allocated,'raw_array_size':len(self.state.raw),
                  'seed_policy':self.profile.seed_policy,'numeric_backend':self.profile.math_backend_id,'parity_status':'UNVERIFIED',
                  'ehlers_limit':plan.ehlers_limit,'hma_limit':plan.hma_limit,'raw_limit':plan.raw_limit,
                  'initialization_gap':None if self.state.initialization_gap is None else dict(self.state.initialization_gap)}
        if extra:metadata.update(extra)
        return KernelResult(self.event,returned,buffers,tuple(self.state.writes),metadata)
