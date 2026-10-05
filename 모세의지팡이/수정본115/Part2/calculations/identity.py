"""Content identity; never includes an installation/drive path."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import platform


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def file_hashes(root: Path | None = None) -> dict[str, str]:
    root = root or Path(__file__).resolve().parent
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.mq5') and '__pycache__' not in p.parts}


def calculation_hash(feature: str = 'BUNDLE') -> str:
    # Conservative full dependency closure, including source MQL and adapters.
    payload = {'feature': feature, 'schema': 'CANONICAL_CALCULATION_V1', 'files': file_hashes()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def environment_identity() -> dict:
    import numpy
    import pandas
    return {'python': platform.python_version(), 'numpy': numpy.__version__,
            'pandas': pandas.__version__, 'float': 'IEEE754_BINARY64', 'byteorder': 'little'}
