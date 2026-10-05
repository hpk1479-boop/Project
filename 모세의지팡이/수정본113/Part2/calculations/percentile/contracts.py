"""Native source contracts; undefined storage is not a floating-point value."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Any
import math
import struct

EMPTY_VALUE = float.fromhex('0x1.fffffffffffffp+1023')
ALGORITHM_VERSION = 'PIT_PERCENTILE_SOURCE_PORT_V1'

class PercentileError(ValueError):
    def __init__(self, code, detail=''):
        self.code = code
        super().__init__(f'{code}: {detail}')

def float_bits(value):
    return struct.unpack('<Q', struct.pack('<d', value))[0]

def bits_float(bits):
    return struct.unpack('<d', struct.pack('<Q', bits))[0]

@dataclass(frozen=True)
class NativeCell:
    bits: int | None
    origin: str = 'SOURCE_WRITTEN'
    taint: tuple[str, ...] = ()
    last_write_event: str | None = None
    @classmethod
    def from_float(cls, value, origin='SOURCE_WRITTEN', taint=(), last_write_event=None):
        return cls(float_bits(float(value)), origin, tuple(taint), last_write_event)
    @classmethod
    def unknown(cls, reason='UNDEFINED_SOURCE_STATE'):
        return cls(None, 'UNSPECIFIED_SOURCE_STORAGE', (reason,))
    @property
    def value(self): return None if self.bits is None else bits_float(self.bits)
    @property
    def available(self): return self.bits is not None
    @property
    def source_defined(self): return self.available and not self.taint
    @property
    def numeric_class(self):
        v = self.value
        if v is None: return 'UNDEFINED_STORAGE'
        if v == EMPTY_VALUE: return 'EMPTY_VALUE'
        if math.isnan(v): return 'NAN'
        if math.isinf(v): return 'INFINITY'
        if v == 0: return 'NEGATIVE_ZERO' if self.bits >> 63 else 'ZERO'
        return 'FINITE'
    @property
    def status(self):
        if not self.available: return 'UNDEFINED_SOURCE_STATE'
        if self.taint: return 'STATE_CONDITIONED' if any('CAPTURED' in t for t in self.taint) else 'UNVERIFIED_DEPENDENCY'
        return 'EXPLICIT_EMPTY' if self.numeric_class == 'EMPTY_VALUE' else 'SOURCE_DEFINED'

@dataclass(frozen=True)
class SourceProfile:
    family: str
    source_hash: str
    inputs: tuple[tuple[str, Any], ...]
    source_file: str = ''
    algorithm_version: str = ALGORITHM_VERSION
    precision_mode: str = 'grid100'
    seed_policy: str = 'SOURCE_DEFINED_ONLY'
    math_backend_id: str = 'PYTHON_SCALAR_FLOAT64_UNVERIFIED_NATIVE_MATH'
    calculation_policy: str = 'PIT_TICK'
    parity_scope: str = 'UNVERIFIED'
    @property
    def params(self): return dict(self.inputs)

@dataclass(frozen=True)
class CalcEvent:
    calc_event_id: str
    rates_total: int
    prev_calculated: int
    history_epoch: str = '0'
    source_ordinal: int = 0
    market_asof_token: Any = None
    point: float = 0.01
    reset_reason: str | None = None
    @property
    def R(self): return self.rates_total
    @property
    def P(self): return self.prev_calculated

@dataclass(frozen=True)
class KernelWrite:
    buffer: str
    shift: int
    cell: NativeCell
    pass_name: str

@dataclass(frozen=True)
class KernelWriteSet:
    writes: tuple[KernelWrite, ...] = ()
    returned: int = 0

@dataclass(frozen=True)
class KernelResult:
    event: CalcEvent
    returned: int
    buffers: Mapping[str, tuple[NativeCell, ...]]
    writes: tuple[KernelWrite, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self):
        from .frozen_buffers import FrozenCells
        object.__setattr__(self, 'buffers', MappingProxyType({k:v if isinstance(v, FrozenCells) else tuple(v) for k,v in self.buffers.items()}))
        object.__setattr__(self, 'metadata', MappingProxyType(dict(self.metadata)))
    def cell(self, name, shift=0):
        values = self.buffers.get(name, ())
        return values[shift] if 0 <= shift < len(values) else NativeCell.unknown('OUT_OF_RANGE')

@dataclass(frozen=True)
class ParityScope:
    status: str = 'UNVERIFIED'
    seed_policy: str = 'SOURCE_DEFINED_ONLY'
    raw_dependency_status: str = 'UNVERIFIED'
    numeric_status: str = 'UNVERIFIED_NATIVE_MATH'

@dataclass(frozen=True)
class FeatureBundle:
    family: str
    result: KernelResult
    profile: SourceProfile
    def cell(self, name, shift=0): return self.result.cell(name, shift)
