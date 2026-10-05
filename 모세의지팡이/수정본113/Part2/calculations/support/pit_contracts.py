from dataclasses import asdict, is_dataclass, dataclass, fields
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import struct

ROOT = Path(__file__).resolve().parents[1]
HMA_SOURCE_SHA = 'f011f76291acc856772ab77357b661472a6571f66183e249e9b05312ba88f2b4'
PROFILE = 'PIT_TICK_V1'
TIMEFRAMES = dict(zip(('1m','2m','3m','4m','5m','6m','10m','12m','15m','20m','30m',
                      '1h','2h','3h','4h','6h','8h','12h','1d'),
                     (60,120,180,240,300,360,600,720,900,1200,1800,3600,7200,10800,14400,21600,28800,43200,86400)))

class PitError(ValueError):
    def __init__(self, code, detail=''):
        self.code = code
        super().__init__(code + (': ' + detail if detail else ''))

_CANONICAL_ATOMS = frozenset((str, int, bool, type(None)))


@dataclass
class _CanonicalBox:
    value: object


@lru_cache(maxsize=256)
def _field_names(cls):
    return tuple(f.name for f in fields(cls))


def _canonical_dataclass(value):
    kind = type(value)
    if kind in _CANONICAL_ATOMS: return value
    if kind is float: return {'binary64': struct.pack('<d', value).hex()}
    if is_dataclass(value) and not isinstance(value, type):
        result = {name: _canonical_dataclass(getattr(value, name))
                  for name in _field_names(kind)}
        return dict(sorted(result.items()))
    if kind in (list, tuple): return [_canonical_dataclass(v) for v in value]
    if kind is dict and all(type(k) is str for k in value):
        result = {k: _canonical_dataclass(v) for k, v in value.items()}
        return dict(sorted(result.items()))
    # Retain stdlib behavior for exotic container/deepcopy objects.
    return canonical(asdict(_CanonicalBox(value))['value'])


def canonical(value):
    if type(value) in _CANONICAL_ATOMS: return value
    if is_dataclass(value):
        if isinstance(value, type): value = asdict(value)
        else: return _canonical_dataclass(value)
    if isinstance(value, float): return {'binary64':struct.pack('<d',value).hex()}
    if isinstance(value, dict): return {str(k):canonical(v) for k,v in sorted(value.items())}
    if isinstance(value, (list,tuple)): return [canonical(v) for v in value]
    return value

def digest(value):
    return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

