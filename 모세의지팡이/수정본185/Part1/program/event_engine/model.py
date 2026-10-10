"""Immutable public messages. All domain times are integer Unix milliseconds."""
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from types import MappingProxyType
from collections.abc import Mapping
import hashlib
import json
import numpy as np
from staff_schema import PIPE_VALUE_COLUMNS


class Kind(str, Enum):
    MARKET_BUNDLE = 'MARKET_BUNDLE'
    FEED_GAP = 'FEED_GAP'
    FEED_HEALTH = 'FEED_HEALTH'
    TIMER = 'TIMER'
    COMMAND = 'COMMAND'
    EXTERNAL_REPLY = 'EXTERNAL_REPLY'
    CONFIG = 'CONFIG'
    SIGNAL = 'SIGNAL'
    STRATEGY_ERROR = 'STRATEGY_ERROR'


class Resolution(IntEnum):
    BAR_CLOSE = 0
    CONDITION = 1
    TICK = 2


@dataclass(frozen=True)
class FrozenMap(Mapping):
    """Owned, recursively sealed mapping; external mapping proxies are not trusted."""
    _data: object

    def __post_init__(self):
        object.__setattr__(self, '_data', MappingProxyType({k:freeze(v) for k,v in self._data.items()}))
    def __getitem__(self, key):return self._data[key]
    def __iter__(self):return iter(self._data)
    def __len__(self):return len(self._data)
    def __deepcopy__(self, memo):return self


class FrozenTuple(tuple):
    __slots__ = ()
    def __new__(cls, values):return tuple.__new__(cls, (freeze(v) for v in values))


def same_immutable_array(left, right):
    """Identical views of one bytes owner are equal without scanning elements."""
    if left.shape != right.shape or left.dtype != right.dtype or left.strides != right.strides:
        return False
    def owner(value):
        while True:
            if isinstance(value, np.ndarray) and value.base is not None: value = value.base
            elif isinstance(value, memoryview): value = value.obj
            else: return value
    backing = owner(left)
    return (isinstance(backing, bytes) and backing is owner(right)
            and left.__array_interface__['data'][0] == right.__array_interface__['data'][0])


def freeze(value):
    if type(value) in (FrozenMap, FrozenTuple):
        return value
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise TypeError('object arrays are not immutable event values')
        owner = value
        while True:
            if isinstance(owner,np.ndarray) and owner.base is not None:owner=owner.base
            elif isinstance(owner,memoryview):owner=owner.obj
            else:break
        if isinstance(owner, bytes):
            return value.view()  # Independent shape header, immutable byte owner.
        return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)
    if isinstance(value, Mapping):
        return FrozenMap(value)
    if isinstance(value, (list, tuple)):
        return FrozenTuple(value)
    if isinstance(value, FeedSnapshot):
        return value  # Constructor already seals all arrays and metadata.
    if value is None or isinstance(value, (str, bytes, bool, int, float, np.generic)):
        return value
    raise TypeError('unsupported mutable payload: ' + type(value).__name__)


@dataclass(frozen=True)
class FeedSnapshot:
    time: np.ndarray
    volume: np.ndarray
    values: np.ndarray
    seq: int = 0
    source_epoch: str = ''
    indicator_validity: object = field(default_factory=dict)

    def __post_init__(self):
        for key in ('time', 'volume', 'values', 'indicator_validity'):
            object.__setattr__(self, key, freeze(getattr(self, key)))
        if self.time.ndim != 1 or self.volume.shape != self.time.shape or self.values.shape != (len(self.time), len(PIPE_VALUE_COLUMNS)):
            raise ValueError('invalid Snapshot shape')
        if not len(self.time) or np.any(self.time[1:] <= self.time[:-1]):
            raise ValueError('Snapshot time must increase')


@dataclass(frozen=True)
class Input:
    source: str
    source_seq: int | None
    source_time: int
    kind: Kind
    payload: object
    engine_time: int

    def __post_init__(self):
        if not isinstance(self.source_time, int):
            raise TypeError('source_time must be integer milliseconds')
        object.__setattr__(self, 'kind', Kind(self.kind))
        object.__setattr__(self, 'payload', freeze(self.payload))


@dataclass(frozen=True)
class Event:
    engine_seq: int
    engine_time: int
    source: str
    source_seq: int | None
    source_time: int
    kind: Kind
    payload: object

    def __post_init__(self):
        if self.engine_seq < 1:
            raise ValueError('engine_seq must be positive')
        object.__setattr__(self, 'payload', freeze(self.payload))


@dataclass(frozen=True)
class Signal:
    symbol: str
    condition_key: str
    content: object
    strategy: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'content', freeze(self.content))


def signal_id(strategy, symbol, source_time, condition_key):
    raw = json.dumps([strategy, symbol, source_time, condition_key], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class TimerRequest:
    symbol: str
    due_time: int
    key: str = ''
    payload: object = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.due_time, int):
            raise TypeError('due_time must be integer milliseconds')
        object.__setattr__(self, 'payload', freeze(self.payload))


@dataclass(frozen=True)
class Boundary:
    timeframe: str
    left: str
    right: str | float
    closed_bar: bool = False


@dataclass(frozen=True)
class Subscriptions:
    symbols: tuple = ()
    timeframes: tuple = ()
    facts: tuple = ()
    processor_states: tuple = ()
    kinds: tuple = (Kind.MARKET_BUNDLE, Kind.TIMER)
    resolution: Resolution = Resolution.TICK
    boundaries: tuple = ()

    def __post_init__(self):
        for key in ('symbols', 'timeframes', 'facts', 'processor_states', 'kinds', 'boundaries'):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        object.__setattr__(self, 'resolution', Resolution(self.resolution))

    def accepts(self, event):
        if event.kind not in self.kinds:
            return False
        symbol = event.payload.get('symbol')
        if self.symbols and symbol not in self.symbols:
            return False
        if event.kind == Kind.MARKET_BUNDLE and self.timeframes:
            return any(tf in event.payload['feeds'] for tf in self.timeframes)
        return True
