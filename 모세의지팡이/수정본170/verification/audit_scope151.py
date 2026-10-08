"""Audit only product/shared source and the explicitly inherited fixes; no Lab imports."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '검증결과/revision151'
EXPECTED = {'Part1/program/THE STAFF OF MOSES.py', 'Part1/program/watch_array_facts.py',
            'Part1/program/strategy_recipe/port.py', 'Part2/event_backtest/delta.py',
            'Part2/event_backtest/bridge.py'}
INHERITED = ['Part2/event_backtest/virtual_entry.py', 'Part3/tests/test_ai_model_settings57.py',
             'Part3/tests/test_ai_no_default59.py']


if __name__ == '__main__':
    manifest = json.loads((EVIDENCE/'copy_manifest.json').read_text('utf-8'))['files']
    roots = {'Part1', 'Part2', 'settings', 'common_ai', 'moses_language'}
    extensions = {'.py', '.pyw', '.json', '.mq5', '.mqh', '.html', '.js', '.css', '.ini', '.toml', '.txt'}
    hashes, changed, missing = {}, [], []
    for name, digest in manifest.items():
        path = Path(name)
        if path.parts[0] not in roots or path.suffix.lower() not in extensions:
            continue
        target = ROOT/path
        if not target.is_file():
            missing.append(name)
            continue
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        hashes[name] = current
        if current != digest:
            changed.append(name)
    inherited = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == manifest[name]
                 for name in INHERITED}
    valid = set(changed) == EXPECTED and not missing and all(inherited.values())
    report = {'valid':valid, 'checked_source_files':len(hashes), 'changed':sorted(changed),
              'missing':missing, 'inherited_150_fixes_unchanged':inherited, 'hashes':hashes,
              'note':'No original files are written. Part3 Lab/prototype source is not examined.'}
    (EVIDENCE/'scope_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key != 'hashes'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if valid else 1)
