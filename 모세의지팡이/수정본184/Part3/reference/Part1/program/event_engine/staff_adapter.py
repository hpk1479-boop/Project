"""Opt-in pipe/STAFF boundary. Never installed by the polling application.

The caller owns the STAFF cache and pipe lifecycle. receive_one can be passed a
real pipe reader or the same bytes in tests. Publication completes in STAFF
before a single MARKET_BUNDLE enters the engine's ingress.
"""
import staff_schema as wire
from .model import Kind, FeedSnapshot


class StaffIngressAdapter:
    def __init__(self, cache, ingress):
        self.cache = cache; self.ingress = ingress

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
        feeds={tf:FeedSnapshot(s.time,s.volume,s.values,s.seq,s.source_epoch,s.indicator_validity)
               for tf,s in publication.feeds.items()}
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
