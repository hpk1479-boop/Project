"""Verify ALL Part1 bytes and paths against the pre-edit uploaded baseline."""
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
expected = json.loads((ROOT / 'build/part1_immutable_sha256.json').read_text(encoding='utf8'))
actual = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in (ROOT / 'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts
          and 'results' not in p.parts and p.name != 'special_settings.json' and p.suffix != '.ex5'}
changes = {'added': sorted(set(actual) - set(expected)), 'removed': sorted(set(expected) - set(actual)),
           'modified': sorted(k for k in set(actual) & set(expected) if actual[k] != expected[k])}
print(json.dumps({'part1_files': len(actual), 'unchanged': actual == expected, **changes}, ensure_ascii=False, indent=2))
sys.exit(0 if actual == expected else 1)
