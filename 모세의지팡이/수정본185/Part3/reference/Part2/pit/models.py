from dataclasses import dataclass
import struct
from .contracts import digest, PitError

RAW = struct.Struct('<qQQQQqIQ')  # float fields kept as their exact uint64 bits

@dataclass(frozen=True)
class TickRecord:
    stream_id: str
    source_ordinal: int
    time_sec: int
    bid_bits: int
    ask_bits: int
    last_bits: int
    volume: int
    time_msc: int
    flags: int
    volume_real_bits: int

    @classmethod
    def from_bytes(cls, stream, ordinal, raw): return cls(stream,ordinal,*RAW.unpack(raw))
    def to_bytes(self):
        return RAW.pack(self.time_sec,self.bid_bits,self.ask_bits,self.last_bits,
                        self.volume,self.time_msc,self.flags,self.volume_real_bits)
    @property
    def tick_id(self): return digest(('TICK',self.stream_id,self.source_ordinal))
    @property
    def bid(self): return struct.unpack('<d',struct.pack('<Q',self.bid_bits))[0]

@dataclass(frozen=True)
class AsOfToken:
    market_epoch: str
    microstep: int
    now_ns: int
    source_ordinal: int
    prefix_commitment: str
    boundary_version: str
    profile: str
    view_id: str

@dataclass(frozen=True)
class BarState:
    bar_id: str
    symbol: str
    timeframe: str
    open_ns: int
    nominal_end_ns: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    first_ordinal: int
    last_ordinal: int
    prefix: str
    revision: int
    state: str
    quality: str = 'COMPLETE_PREFIX'
    gap_before: bool = False
    complete_at_order: int | None = None
    seed_quality: str = 'REAL_TICK_PREFIX'

@dataclass(frozen=True)
class PitView:
    token: AsOfToken
    symbol: str
    quote: TickRecord | None
    feeds: tuple[tuple[str,tuple[BarState,...]],...]
    def bars(self,tf):
        for name,bars in self.feeds:
            if name==tf:return bars
        raise PitError('E_UNDECLARED_DEPENDENCY',tf)

@dataclass(frozen=True)
class MarketChange:
    token: AsOfToken
    changed_feeds: tuple[str,...]
    raw_tick_id: str

@dataclass(frozen=True)
class FeatureValue:
    feature_id: str
    bar_id: str
    value: float | None
    validity: str
    unavailable_reason: str | None
    input_cursor: int
    prefix_commitment: str
    implementation_version: str = 'HMA_EA_OPEN_PIT_V1'
