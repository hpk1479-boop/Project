"""Compatibility import; generated strategies use the public common Recipe API."""
from pathlib import Path
import sys
_program = str(Path(__file__).resolve().parents[2] / 'Part1' / 'program')
if _program not in sys.path: sys.path.insert(0, _program)
from strategy_recipe.port import IntentPort
