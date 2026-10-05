"""Compatibility alias; shared implementation lives outside Part3."""
import sys
from pathlib import Path
_root = str(Path(__file__).resolve().parents[3])
if _root not in sys.path:
    sys.path.insert(0, _root)
from common_ai import local_lora as _shared
sys.modules[__name__] = _shared
