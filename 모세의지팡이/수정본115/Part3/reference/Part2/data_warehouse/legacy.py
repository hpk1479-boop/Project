import hashlib
import json
import math
import struct
from dataclasses import asdict, is_dataclass, dataclass, fields
from functools import lru_cache
from pathlib import Path


# Exact built-in leaves do not need dataclass/container introspection.  Subclasses
# deliberately take the original path (including their conversion semantics).
_JSON_ATOMS = frozenset((str, int, float, bool, type(None)))


@dataclass
class _AsdictBox:
    value: object


@lru_cache(maxsize=256)
def _field_names(cls):
    return tuple(f.name for f in fields(cls))


def _dataclass_plain(value):
    """Fuse asdict + plain for built-in payload trees, not numeric arithmetic.

    Container subclasses, non-string dictionary keys and custom leaves still
    use stdlib asdict/deepcopy.  Fresh lists/dicts are returned on every call.
    """
    kind = type(value)
    if kind in _JSON_ATOMS: return value
    if is_dataclass(value) and not isinstance(value, type):
        return {name: _dataclass_plain(getattr(value, name))
                for name in _field_names(kind)}
    if kind in (list, tuple): return [_dataclass_plain(v) for v in value]
    if kind is dict and all(type(k) is str for k in value):
        return {k: _dataclass_plain(v) for k, v in value.items()}
    return plain(asdict(_AsdictBox(value))['value'])


def plain(value):
    if type(value) in _JSON_ATOMS: return value
    if is_dataclass(value):
        if isinstance(value, type): return plain(asdict(value))
        return _dataclass_plain(value)
    if isinstance(value, dict): return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [plain(v) for v in value]
    return value


def _bits_of_plain(value):
    # plain() has already converted every nested dataclass/container.  Repeating
    # it at each depth used to rebuild the same subtrees over and over.
    if isinstance(value, float): return {'binary64': struct.pack('<d', value).hex()}
    if isinstance(value, dict): return {k: _bits_of_plain(v) for k, v in value.items()}
    if isinstance(value, list): return [_bits_of_plain(v) for v in value]
    return value


def bits(value):
    return _bits_of_plain(plain(value))


def encode(value):
    return (json.dumps(plain(value), sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def identity(value): return hashlib.sha256(encode(bits(value))).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    with Path(path).open('xb') as f: f.write(encode(value))


def finite(value): return type(value) in (int, float) and math.isfinite(value)
