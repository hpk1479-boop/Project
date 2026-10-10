"""Opt-in pipe/STAFF boundary. Never installed by the polling application.

The caller owns the STAFF cache and pipe lifecycle. receive_one can be passed a
real pipe reader or the same bytes in tests. Publication completes in STAFF
before a single MARKET_BUNDLE enters the engine's ingress.

server_time (수정본172): the broker clock of the bar times. Snapshots reach the
engine in real time, the same for LIVE and replay; STAFF itself keeps the times
it received. None passes them unchanged (tests that build their own bars).
"""
import numpy as np
import server_time as clock_rule
import staff_schema as wire
from .model import Kind, FeedSnapshot, FrozenMap


class StaffIngressAdapter:
    def __init__(self, cache, ingress, *, server_time=None, on_clock=None):
        self.cache = cache; self.ingress = ingress
        self._validity = {}
        self.server_time = server_time
        # on_clock(symbol, observed hours, set hours): LIVE publications keep showing another server offset.
        self.on_clock = on_clock
        self._times = {}; self._clock_misses = 0

    def _real_times(self, symbol, timeframe, times):
        """The snapshot's bar times in real time, converted once per STAFF time array."""
        if self.server_time is None or self.server_time.identity: return times
        key = (symbol, timeframe); cached = self._times.get(key)
        if cached is not None and cached[0] is times: return cached[1]
        converted = self.server_time.to_utc_array(times)
        # Byte-owned, so the Snapshot seals it without another copy.
        converted = np.frombuffer(converted.tobytes(), dtype=converted.dtype)
        self._times[key] = (times, converted)
        return converted

    def _check_clock(self, symbol, times, sent_ms):
        """A LIVE publication: the EA's real send time against its forming 1m bar on the server clock."""
        if not len(times) or not sent_ms: return
        hours = clock_rule.observed_offset(int(times[-1]), sent_ms)
        if hours is None: return
        expected = self.server_time.offset_at_utc(int(sent_ms) // 1000) // 3600
        if hours == expected: self._clock_misses = 0; return
        self._clock_misses += 1
        if self._clock_misses == clock_rule.CLOCK_MISSES: self.on_clock(symbol, hours, expected)

    def _sealed_validity(self, validity):
        """One sealed map per validity content; snapshots share it like any immutable value."""
        key = tuple(validity.items())
        sealed = self._validity.get(key)
        if sealed is None:
            if len(self._validity) >= 4096: self._validity.clear()
            sealed = self._validity[key] = FrozenMap(validity)
        return sealed

    def health(self, *, symbol, source_time, status, **details):
        self.ingress.post(Kind.FEED_HEALTH, source='staff', source_seq=None,
                          source_time=source_time, payload={'symbol': symbol, 'status': status, **details})

    def reconnect(self, *, symbol, source_time):
        self.cache.reconnect()
        self.health(symbol=symbol, source_time=source_time, status='RECONNECT')

    def _receive(self, *, raw=None, read_exact=None, source_time=None, allowed_symbols=None):
        try:
            publication=self.cache.receive_publication(raw=raw,read_exact=read_exact,
                source_time=source_time,require_observation=True,allowed_symbols=allowed_symbols)
        except wire.UnknownWireSchema as exc:
            self.health(symbol=exc.symbol,source_time=source_time or 0,status='UNAVAILABLE',error=str(exc))
            raise
        except Exception as exc:
            if getattr(exc,'staff_symbol',None):
                self.health(symbol=exc.staff_symbol,source_time=exc.staff_source_time,status='UNAVAILABLE',error=str(exc))
            raise
        packet=publication.packet
        if packet.kind in (wire.WIRE_HELLO,wire.WIRE_ACK):return publication.response
        timestamp=packet.sent_at_ms if source_time is None else source_time
        feeds={tf:FeedSnapshot(self._real_times(packet.symbol,tf,s.time),s.volume,s.values,s.seq,s.source_epoch,
                               self._sealed_validity(s.indicator_validity))
               for tf,s in publication.feeds.items()}
        if (source_time is None and self.on_clock is not None and self.server_time is not None
                and '1m' in publication.feeds):
            self._check_clock(packet.symbol,publication.feeds['1m'].time,packet.sent_at_ms)
        for gap in publication.gaps:
            self.ingress.post(Kind.FEED_GAP,source='staff',source_seq=gap['next_received_seq'],
                              source_time=timestamp,payload=gap)
        if feeds:
            self.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=packet.seq,
                              source_time=timestamp,payload={'symbol':packet.symbol,'feeds':feeds})
        return publication.response

    def publish(self, raw, *, source_time=None):
        return self._receive(raw=raw,source_time=source_time)

    def receive_one(self, read_exact, *, source_time=None, allowed_symbols=None):
        def exact(size):
            data=read_exact(size)
            if len(data)!=size:raise EOFError('truncated v2 pipe payload')
            return data
        return self._receive(read_exact=exact,source_time=source_time,allowed_symbols=allowed_symbols)
