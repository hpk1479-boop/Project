"""Frozen test-only oracles copied from the supplied current Part2, not an old release.

Never imported by production. Market source SHA256: a0314543d53d5e4098ee5d39e5d68702fc228ce0c58ae0e4e2abf5e96127f6ce
The inherited methods are unchanged; only the optimized function is overridden.
"""
from dataclasses import replace
import struct
from pit.models import BarState
from generic_backtest.market import GenericCandleBook
from generic_backtest.canonical import finite, identity

def quote_values(tick):
    def number(bits): return struct.unpack('<d', struct.pack('<Q', bits))[0]
    return {'bid': number(tick.bid_bits), 'ask': number(tick.ask_bits), 'last': number(tick.last_bits),
            'volume': tick.volume, 'volume_real': number(tick.volume_real_bits),
            'flags': tick.flags, 'time_msc': tick.time_msc, 'time': tick.time_sec}


class BaselineCandleBook(GenericCandleBook):
    def _apply_tick(self, tick, prefix, gap=False):
        quote = quote_values(tick)
        field, mask = ('bid',2) if self.price_event == 'BID' else ('last',8)
        if gap:
            self.forming = {tf: replace(b, quality='UNKNOWN_GAP', gap_before=True) for tf,b in self.forming.items()}
        if not tick.flags & mask: return ()
        price = quote[field]
        if not finite(price) or price <= 0:
            self.forming = {tf: replace(b, quality='UNKNOWN_GAP') for tf,b in self.forming.items()}
            return ()
        now = tick.time_msc*1000000
        intervals = {}
        for tf in self.closed:
            old = self.forming.get(tf)
            if old is not None and old.open_ns <= now < old.nominal_end_ns:
                intervals[tf] = (old.open_ns, old.nominal_end_ns)
                self.interval_cache_hits += 1
            else:
                intervals[tf] = self.calendar.interval_for(self.symbol,tf,now)
        for tf,(start,end) in intervals.items():
            old = self.forming.get(tf)
            if old and old.open_ns == start:
                self.forming[tf] = replace(old, high=max(old.high,price), low=min(old.low,price),close=price,
                    tick_volume=old.tick_volume+1,last_ordinal=tick.source_ordinal,prefix=prefix,revision=old.revision+1)
            else:
                if old:
                    self.closed[tf].append(replace(old,state='COMPLETED',complete_at_order=tick.source_ordinal))
                    self._closed_views[tf] = tuple(self.closed[tf])
                self.forming[tf] = BarState(identity((tick.stream_id,self.symbol,tf,start)),self.symbol,tf,start,end,
                    price,price,price,price,1,tick.source_ordinal,tick.source_ordinal,prefix,1,'FORMING',
                    'UNKNOWN_GAP' if gap else 'PARTIAL_START' if start<self.start_ns else 'COMPLETE_PREFIX',gap)
        return tuple(intervals)


# Frozen HMA source SHA256: 22fb34f7356b993c2f5072625e3ed84b1c8defe92d16e6b167c990608c9f34fb
import math
import sys
from pit.contracts import HMA_SOURCE_SHA,PitError

class BaselineHMAOpenKernel:
    version='HMA_EA_OPEN_PIT_V1'
    source_hash=HMA_SOURCE_SHA
    @staticmethod
    def wma_at(values,idx,length):
        if idx<length-1 or length<=0:return None
        numerator=0.0;denominator=0.0;weight=1
        for k in range(idx-length+1,idx+1):
            value=values[k]
            if value is None or value==sys.float_info.max or not math.isfinite(value):return None
            numerator+=value*float(weight)
            denominator+=float(weight);weight+=1
        return numerator/denominator if denominator else None
    @classmethod
    def calculate(cls,values,period):
        if period not in (6,17):raise PitError('E_FEATURE_NOT_AVAILABLE','Only source OPEN HMA6/17')
        half=max(1,period//2);root=max(1,int(math.sqrt(float(period))))
        raw=[None]*len(values);out=[None]*len(values)
        for i in range(len(values)):
            a=cls.wma_at(values,i,half);b=cls.wma_at(values,i,period)
            if a is not None and b is not None:raw[i]=2.0*a-b
        for i in range(len(values)):out[i]=cls.wma_at(raw,i,root)
        return tuple(out)
    @staticmethod
    def warmup(period):return period+int(math.sqrt(period))-1
