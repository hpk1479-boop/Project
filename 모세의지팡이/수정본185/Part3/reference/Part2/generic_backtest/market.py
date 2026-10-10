"""Parallel configurable market book; legacy PIT remains unchanged."""
import hashlib
import struct
from collections import deque
from dataclasses import replace
from pit.models import AsOfToken, BarState, PitView, MarketChange
from pit.clock import PitClock
from .canonical import identity, finite
from .contracts import GenericError


def quote_values(tick):
    # Reinterpret the same four uint64 payloads as float64 in one struct call.
    # No numeric conversion: NaN payloads, signed zero and quote order survive.
    bid,ask,last,volume_real = struct.unpack('<4d',struct.pack('<4Q',
        tick.bid_bits,tick.ask_bits,tick.last_bits,tick.volume_real_bits))
    return {'bid': bid, 'ask': ask, 'last': last,
            'volume': tick.volume, 'volume_real': volume_real,
            'flags': tick.flags, 'time_msc': tick.time_msc, 'time': tick.time_sec}


class GenericCandleBook:
    def __init__(self, symbol, calendar, start_ns, lookbacks, price_event='BID'):
        if price_event not in ('BID','LAST'): raise GenericError('E_PLUGIN_SCHEMA', 'chart price policy')
        self.symbol, self.calendar, self.start_ns = symbol, calendar, start_ns
        self.price_event = price_event
        self.closed = {tf: deque(maxlen=n) for tf,n in lookbacks.items()}
        self.forming = {}
        self._timeframes = tuple(sorted(self.closed))
        self._closed_views = {tf: () for tf in self.closed}
        self.interval_cache_hits = 0
        self.replay_tape = None

    def apply_tick(self, tick, prefix, gap=False):
        tape=self.replay_tape
        expected=(tick.source_ordinal,tick.time_msc,gap)
        saved=tape.read(expected) if tape else None
        if tape and tape.cache.hit:
            return self._apply_saved(tick,prefix,gap,saved)
        changed=self._apply_tick(tick,prefix,gap)
        if tape:
            fields=('open_ns','nominal_end_ns','open','high','low','close','tick_volume',
                    'first_ordinal','last_ordinal','revision','quality','gap_before')
            tape.append(expected,{'changed':list(changed),'forming':
                {tf:[getattr(b,f) for f in fields] for tf,b in self.forming.items()}})
        return changed

    def _apply_saved(self,tick,prefix,gap,saved):
        if gap:
            self.forming={tf:replace(b,quality='UNKNOWN_GAP',gap_before=True) for tf,b in self.forming.items()}
        for tf,row in saved['forming'].items():
            start,end,o,h,l,c,volume,first,last,revision,quality,gap_before=row
            old=self.forming.get(tf)
            if old and old.open_ns!=start:
                self.closed[tf].append(replace(old,state='COMPLETED',complete_at_order=tick.source_ordinal))
                self._closed_views[tf]=tuple(self.closed[tf])
            own_prefix=old.prefix if old and old.open_ns==start and old.last_ordinal==last else prefix
            self.forming[tf]=BarState(identity((tick.stream_id,self.symbol,tf,start)),self.symbol,tf,start,end,
                o,h,l,c,volume,first,last,own_prefix,revision,'FORMING',quality,gap_before)
        return tuple(saved['changed'])

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
                # Same immutable snapshot and arithmetic as replace(), without
                # per-tick dataclass field discovery/keyword-dict construction.
                # Keep every unchanged field, including completion/seed metadata.
                self.forming[tf] = BarState(
                    old.bar_id,old.symbol,old.timeframe,old.open_ns,old.nominal_end_ns,
                    old.open,max(old.high,price),min(old.low,price),price,
                    old.tick_volume+1,old.first_ordinal,tick.source_ordinal,prefix,
                    old.revision+1,old.state,old.quality,old.gap_before,
                    old.complete_at_order,old.seed_quality)
            else:
                if old:
                    self.closed[tf].append(replace(old,state='COMPLETED',complete_at_order=tick.source_ordinal))
                    self._closed_views[tf] = tuple(self.closed[tf])
                self.forming[tf] = BarState(identity((tick.stream_id,self.symbol,tf,start)),self.symbol,tf,start,end,
                    price,price,price,price,1,tick.source_ordinal,tick.source_ordinal,prefix,1,'FORMING',
                    'UNKNOWN_GAP' if gap else 'PARTIAL_START' if start<self.start_ns else 'COMPLETE_PREFIX',gap)
        return tuple(intervals)

    def view(self):
        return tuple((tf,self._closed_views[tf]+((self.forming[tf],) if tf in self.forming else ()))
                     for tf in self._timeframes)


class GenericMarketCore:
    def __init__(self, instrument, calendar, start_ns, lookbacks, namespace):
        self.instrument, self.calendar = instrument, calendar
        self.clock = PitClock(start_ns)
        self.epoch = identity(('GENERIC_MARKET',instrument,calendar.definition,start_ns,namespace))
        self.prefix = identity(('PREFIX',self.epoch))
        self.book = GenericCandleBook(instrument.broker_symbol,calendar,start_ns,lookbacks,instrument.chart_mode)
        self.ordinal = 0
        self.current = None

    def step(self, tick, gap=False):
        if tick.time_msc*1000000 < self.clock.now_ns or (self.current and tick.source_ordinal <= self.ordinal):
            raise GenericError('E_FUTURE_READ', 'tick order regression')
        self.prefix = hashlib.sha256(bytes.fromhex(self.prefix)+tick.to_bytes()).hexdigest()
        changed = self.book.apply_tick(tick,self.prefix,gap)
        self.clock.advance_to(tick.time_msc*1000000)
        self.ordinal = tick.source_ordinal
        fields = (self.epoch,self.ordinal,self.clock.now_ns,self.ordinal,self.prefix,self.calendar.revision,'GENERIC_TICK_PREFIX_V1')
        token = AsOfToken(*fields,identity(fields))
        self.current = PitView(token,self.instrument.broker_symbol,tick,self.book.view())
        return self.current, MarketChange(token,changed,tick.tick_id)
