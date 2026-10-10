"""Final read-only source review and portable diagnostic-path normalization."""
from pathlib import Path
import ast, hashlib, importlib.util, json, pstats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/shared_oz_composer_input'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
read = lambda path: json.loads(path.read_text('utf-8'))
before = read(OUT / 'before_sources.json')
changed = []
for folder in ('Part1/program', 'Part2/event_backtest'):
    for path in sorted((ROOT / folder).rglob('*.py')):
        name = path.relative_to(ROOT).as_posix()
        if before.get(name) != sha(path):
            ast.parse(path.read_bytes(), filename=name)
            changed.append(name)
spec = importlib.util.spec_from_file_location('integrity', ROOT / 'Part1/audit/source_integrity.py')
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)
verified = integrity.verify_sources()
(OUT / 'integrity_verification.json').write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding='utf-8')
existing = read(OUT / 'integrity_existing_comparison.json')
existing['after'] = verified['integrity_errors']
existing['new_errors'] = sorted(set(existing['after']) - set(existing['before']))
(OUT / 'integrity_existing_comparison.json').write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
assert not existing['new_errors'], existing['new_errors']
inventory = read(ROOT / 'build/part1_immutable_sha256.json')
mismatches = [name for name, digest in inventory.items() if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
assert not mismatches, mismatches
protected = read(OUT / 'protected_files.json')
for item in protected:
    item['unchanged'] = sha(ROOT / item['file']) == sha(ROOT.parent / '수정본31' / item['file'])
(OUT / 'protected_files.json').write_text(json.dumps(protected, ensure_ascii=False, indent=2), encoding='utf-8')
assert all(item['unchanged'] for item in protected)

def portable(name):
    if not isinstance(name, str):
        return name
    value = name.replace('\\', '/')
    normalized = ROOT.as_posix()
    if value.lower().startswith(normalized.lower() + '/'):
        return './' + value[len(normalized) + 1:]
    if len(value) > 2 and value[1:3] == ':/':
        marker = '/lib/'
        pos = value.lower().find(marker)
        return '<python-runtime>/' + (value[pos + 1:] if pos >= 0 else Path(value).name)
    return value

# pstats filenames are diagnostic metadata, never executable sources. Preserve
# all counters/timings while making the saved profile portable.
profiles = []
for path in list(OUT.glob('*.pstats')) + list(OUT.glob('*/profile.pstats')):
    stats = pstats.Stats(str(path))
    renamed = {}
    for key, (cc, nc, tt, ct, callers) in stats.stats.items():
        newkey = (portable(key[0]), key[1], key[2])
        renamed[newkey] = (cc, nc, tt, ct, {(portable(k[0]), k[1], k[2]): v for k, v in callers.items()})
    stats.stats = renamed
    stats.dump_stats(str(path))
    output = path.with_suffix('.txt')
    if path.name == 'profile.pstats':
        output = path.with_name('profile.txt')
    with output.open('w', encoding='utf-8') as handle:
        stats.stream = handle
        stats.files = [path.relative_to(ROOT).as_posix()]
        stats.sort_stats('cumulative').print_stats(50)
    profiles.append(path.relative_to(ROOT).as_posix())
result = {'syntax_checked': changed, 'new_integrity_errors': existing['new_errors'],
          'immutable_inventory_mismatches': mismatches, 'protected_files': len(protected),
          'profiles_normalized': profiles}
(OUT / 'final_source_review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
