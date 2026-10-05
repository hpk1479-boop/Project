"""Compatibility alias; shared implementation lives outside Part3."""
import sys
from pathlib import Path
_root = str(Path(__file__).resolve().parents[3])
if _root not in sys.path:
    sys.path.insert(0, _root)
from common_ai import provider as _shared
from .schema import output_schema
_shared.DEFAULT_SCHEMA_FACTORY = output_schema
sys.modules[__name__] = _shared
