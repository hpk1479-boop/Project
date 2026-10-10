"""Regenerate STAFF_Wire_Schema.mqh (Wire kinds incl. APPEND) and its EA source build hash.

Same hash rule as generate_schema_final.py: every .mq5/.mqh in Part1/program/MT5 except the
generated header itself. The identity goes to this revision's evidence folder.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
FOLDER = PROGRAM / 'MT5'
spec = importlib.util.spec_from_file_location('schema_generator', PROGRAM / 'staff_schema.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
digest = hashlib.sha256()
inputs = []
for path in sorted(FOLDER.iterdir()):
    if path.suffix not in ('.mq5', '.mqh') or path.name == 'STAFF_Wire_Schema.mqh':
        continue
    name, raw = path.name.encode(), path.read_bytes()
    digest.update(len(name).to_bytes(4, 'little') + name + len(raw).to_bytes(8, 'little') + raw)
    inputs.append({'name': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
build = digest.hexdigest()
(FOLDER / 'STAFF_Wire_Schema.mqh').write_text(module.generated_mqh(build), encoding='utf-8')
out = ROOT / '검증결과/revision152/ea'
out.mkdir(parents=True, exist_ok=True)
(out / 'ea_build_identity.json').write_text(json.dumps(
    {'build_sha256': build, 'schema_id': module.WIRE_SCHEMA_ID, 'inputs': inputs}, indent=2) + '\n', encoding='utf-8')
print('schema', hex(module.WIRE_SCHEMA_ID), 'build', build)
