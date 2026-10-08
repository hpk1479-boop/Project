"""Zero-copy Snapshot input, integer time, named lazy OZ-only facts.

No pandas, history slicing, profile state or I/O. One view per feed/publication.
"""
import numpy as np
from staff_schema import PIPE_VALUE_COLUMNS
from .common import finite_number,percentile_states

COLUMNS={name:i for i,name in enumerate(PIPE_VALUE_COLUMNS)}
OZ_FACT_DEFINITIONS={name:{'owner':'oz_engine.market','invalidation':'publication'} for name in (
    'row','percentile_states','hma_cross_observation','bars_since','pre_cross_extreme',
    'one_way','bo_break','regime','open_vs_hma6','hma_aligned','trigger_hits','neckline',
    'atr_series','closed_hma_cross_observation','closed_view')}


class MarketRow:
    __slots__=('view','index')
    def __init__(self,view,index):self.view=view;self.index=index
    def get(self,name,default=None):
        v=self.view;i=self.index
        if name=='time':return int(v.time[i])
        if name=='volume':return int(v.snapshot.volume[i])
        if name in ('wonbi_mid','wonbi_upper','wonbi_lower'):return float(v.wonbi()[name][i])
        if name in COLUMNS:return float(v.values[i,COLUMNS[name]])
        if name in ('atr_14','ATR14_GENERAL'):return float(v.atr()[i])
        prefix,_,suffix=name.partition('_regime_')
        if suffix=='zone':
            cols={'price':('price_hma_6','price_regime_basis'),'RSI':('RSI_val','RSI_basis'),
                  'STO':('STO_val','STO_basis'),'DI':('DI_val','DI_basis')}.get(prefix)
            if cols is None:return default
            value=self.get(cols[0]);lo=self.get(prefix+'_regime_lower');hi=self.get(prefix+'_regime_upper')
            if not all(finite_number(x) for x in (value,lo,hi)):return float('nan')
            return -1. if value<lo else 1. if value>hi else 0.
        return default


class OZMarketView:
    def __init__(self,snapshot,*,atr_provider=None,wonbi_provider=None,end=None):
        # end=-1: the same immutable arrays without the forming bar (no copy).
        self.snapshot=snapshot
        self.time=snapshot.time if end is None else snapshot.time[:end]
        self.values=snapshot.values if end is None else snapshot.values[:end]
        self._atr_provider=atr_provider;self._atr=None;self._memo={};self._rows={}
        self._wonbi_provider=wonbi_provider;self._wonbi=None
    def __len__(self):return len(self.time)
    @property
    def empty(self):return not len(self)
    @property
    def columns(self):return ('time','volume',*PIPE_VALUE_COLUMNS,'atr_14')
    def row(self,index):
        i=index if index>=0 else len(self)+index
        if not 0<=i<len(self):raise IndexError(index)
        if i not in self._rows:self._rows[i]=MarketRow(self,i)
        return self._rows[i]
    @property
    def live(self):return self.row(-1)
    def column(self,name):
        if name in ('wonbi_mid','wonbi_upper','wonbi_lower'):return self.wonbi()[name]
        return self.time if name=='time' else self.values[:,COLUMNS[name]]
    def wonbi(self):
        if self._wonbi is None:
            if self._wonbi_provider is None:
                from indicator_facts import wonbi_bands
                self._wonbi=wonbi_bands(*(self.values[:,COLUMNS[k]] for k in ('open_band_4_mid','wonbi_upper','wonbi_lower')))
            else:self._wonbi=self._wonbi_provider()
        return self._wonbi
    def memo(self,key,compute):
        name=key[0] if isinstance(key,tuple) else key
        if name not in OZ_FACT_DEFINITIONS:raise KeyError('unregistered OZ Fact: '+name)
        if key not in self._memo:self._memo[key]=compute()
        return self._memo[key]
    def atr(self):
        if self._atr is None:
            if self._atr_provider is None:raise RuntimeError('ATR14_GENERAL Fact provider required')
            self._atr=self._atr_provider()
        return self._atr
    def atr_series(self,period):
        """Wilder ATR for any period, from the one shared TR/RMA definition.

        Period 14 is the existing ATR14_GENERAL Fact itself; other periods use the
        same ``true_range_array`` and ``rma_array`` as ``parameterized_atr_fact``.
        """
        if period==14:return self.atr()
        def compute():
            from indicator_facts import true_range_array,rma_array
            return rma_array(true_range_array(self.column('high'),self.column('low'),self.column('close')),period)
        return self.memo(('atr_series',period),compute)
    def index_of(self,when):
        if when is None:return None
        i=int(np.searchsorted(self.time,int(when)))
        return i if i<len(self) and int(self.time[i])==int(when) else None
    def nearest_index(self,when,tolerance=.5):
        i=int(np.searchsorted(self.time,when))
        choices=[x for x in (i-1,i) if 0<=x<len(self)]
        if not choices:return None
        pick=min(choices,key=lambda x:abs(int(self.time[x])-when))
        return pick if abs(int(self.time[pick])-when)<=tolerance else None
    def bars_since(self,when):
        i=self.index_of(when);return None if i is None else len(self)-1-i
    def neckline(self,direction,left_time):
        """Opposite extreme between this pattern's B0 and its alert bar.

        Both endpoints are excluded. In particular the unfinished right-hand
        alert bar never supplies a future high/low for the fixed neckline.
        """
        def compute():
            left=self.index_of(left_time)
            if left is None or left+1>=len(self)-1:return None
            values=self.column('high' if direction=='LONG' else 'low')[left+1:-1]
            positions=np.flatnonzero(np.isfinite(values))[::-1]
            if not len(positions):return None
            pick=np.argmax(values[positions]) if direction=='LONG' else np.argmin(values[positions])
            index=int(positions[pick])+left+1
            return float(self.column('high' if direction=='LONG' else 'low')[index]),int(self.time[index])*1000
        return self.memo(('neckline',direction,left_time),compute)
    def percentile_states(self,row=-1):return percentile_states(self.row(row))
    def regime(self,percentile,row=-1):
        prefix='price' if percentile=='PRICE' else percentile
        r=self.row(row);return r.get(prefix+'_regime_zone'),r.get(prefix+'_regime_slope')
    def hma_cross(self):
        if len(self)<2:return None
        prev,live=self.row(-2),self.live
        values=tuple(r.get(c) for r in (prev,live) for c in ('hma_6','hma_17'))
        return (*values,live.get('time')) if all(finite_number(x) for x in values) else None
    def closed_hma_cross(self,index):
        """HMA6/17 of closed bar ``index`` and the bar before it, and that bar's time."""
        if not 1<=index<len(self)-1:return None
        prev,closed=self.row(index-1),self.row(index)
        values=tuple(r.get(c) for r in (prev,closed) for c in ('hma_6','hma_17'))
        return (*values,closed.get('time')) if all(finite_number(x) for x in values) else None
    def closed_view(self,index):
        """This publication as it stood while closed bar ``index`` was still forming."""
        provider=self._atr_provider;end=index+1
        return OZMarketView(self.snapshot,end=end,wonbi_provider=None,
                            atr_provider=None if provider is None else (lambda:self.atr()[:end]))
    def pre_cross_extreme(self,direction):
        if len(self)<2:return None
        h6=self.column('hma_6')[:-1];h17=self.column('hma_17')[:-1]
        valid=np.isfinite(h6)&np.isfinite(h17)
        aligned=h6<=h17 if direction=='LONG' else h6>=h17
        stops=np.flatnonzero(~(valid&aligned));first=int(stops[-1]+1) if len(stops) else 0
        prices=self.column('low' if direction=='LONG' else 'high')[first:-1]
        positions=np.flatnonzero(np.isfinite(prices))[::-1]
        if not len(positions):return None
        pick=np.argmin(prices[positions]) if direction=='LONG' else np.argmax(prices[positions])
        i=int(positions[pick]);return float(prices[i]),int(self.time[first+i])
    def one_way(self,direction,cross_time):
        if len(self)<4 or cross_time is None:return None
        if np.any(self.time[-4:-1]<=cross_time):return None
        opens=self.column('open')[-4:-1];closes=self.column('close')[-4:-1]
        if not np.isfinite(opens).all() or not np.isfinite(closes).all():return None
        if direction=='LONG' and np.all(closes>opens):return 'ONE_WAY_UP'
        if direction=='SHORT' and np.all(closes<opens):return 'ONE_WAY_DOWN'
        return None
    def candle_pullback(self,direction,cross_time):
        if len(self)<5 or cross_time is None:return None
        o=self.column('open')[-4:-1];c=self.column('close')[-4:-1]
        if not np.isfinite(o).all() or not np.isfinite(c).all():return None
        before,first=map(int,self.time[-4:-2])
        if direction=='LONG':
            engulf=c[0]>o[0] and c[1]<o[1] and o[1]>=c[0] and c[1]<=o[0]
            if engulf and c[2]<o[2] and first>cross_time:return 'ENGULF_PLUS_BEAR'
            if np.all(c<o) and before>cross_time:return 'THREE_BEAR'
        else:
            engulf=c[0]<o[0] and c[1]>o[1] and o[1]<=c[0] and c[1]>=o[0]
            if engulf and c[2]>o[2] and first>cross_time:return 'ENGULF_PLUS_BULL'
            if np.all(c>o) and before>cross_time:return 'THREE_BULL'
        return None
    def hma6_turn(self,direction,cross_time):
        if len(self)<5 or cross_time is None:return False
        h=self.column('hma_6');vals=h[[-3,-5,-2,-4]]
        if not np.isfinite(vals).all() or int(self.time[-2])<=cross_time:return False
        prev=vals[0]>vals[1];now=vals[2]>vals[3]
        return bool(prev and not now) if direction=='LONG' else bool(not prev and now)
    def touch(self,level):
        low,high=self.live.get('low'),self.live.get('high')
        return all(finite_number(x) for x in (low,high,level)) and low<=level<=high
    def bo_break(self,direction,level,cross_time):
        if len(self)<2 or not finite_number(level) or cross_time is None or int(self.time[-1])<=cross_time:return False
        column=self.column('low' if direction=='LONG' else 'high');prev,live=column[-2:]
        if not finite_number(prev) or not finite_number(live):return False
        return bool(prev>=level and live<level) if direction=='LONG' else bool(prev<=level and live>level)
    def open_vs_hma6(self,direction):
        o,h=self.live.get('open'),self.live.get('hma_6')
        return finite_number(o) and finite_number(h) and (o>h if direction=='LONG' else o<h)
    def hma_aligned(self,direction):
        h6,h17=self.live.get('hma_6'),self.live.get('hma_17')
        return finite_number(h6) and finite_number(h17) and (h6>h17 if direction=='LONG' else h6<h17)
    def extreme_since(self,when,side):
        i=int(np.searchsorted(self.time,when))
        values=self.column(side)[i:];valid=values[np.isfinite(values)]
        if not len(valid):return None
        return float(np.min(valid) if side=='low' else np.max(valid))
