"""S2-only provenance. Original revisions, goldens and policies remain frozen."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s2'
S0 = ROOT / '검증결과/staff_s0'
S1 = ROOT / '검증결과/staff_s1'
SOURCE = ROOT.parent / '수정본8'


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def frozen_guard():
    manifest = read(OUT / 's1_frozen_manifest.json')
    differences = []
    expected = {r['path'] for r in manifest}
    actual = {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file()}
    differences.extend('original inventory: ' + p for p in sorted(expected ^ actual))
    allowed_tests = {'Part2/validation_suite/test_oz_fvg_optimization.py', 'tests/sparse_events/test_events.py'}
    policies = {'build/staff_performance_policy.json', 'build/staff_performance_policy.py',
                'build/staff_performance_protocol.py', '성능규칙_S1_S8.md'}
    protected_count = 0
    for item in manifest:
        rel = item['path']
        if not (SOURCE/rel).is_file() or sha(SOURCE/rel) != item['sha256']:
            differences.append('original changed: '+rel)
        protected = (rel.startswith(('검증결과/staff_s0/', '검증결과/staff_s1/', 'Part1/audit/fixtures/'))
                     or rel in policies or 'expected' in Path(rel).name.lower()
                     or (Path(rel).name.startswith('test_') and rel not in allowed_tests))
        if protected:
            protected_count += 1
            if not (ROOT/rel).is_file() or sha(ROOT/rel) != item['sha256']:
                differences.append('protected copy changed: '+rel)
    # Existing assertion functions stay exact; only their fixture/host boundary changes.
    for rel in allowed_tests:
        def tests(path):
            return {n.name:ast.dump(n) for n in ast.parse(path.read_bytes()).body
                    if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
        if tests(SOURCE/rel) != tests(ROOT/rel):
            differences.append('existing test assertions changed: '+rel)
    report = {'stage':'S2','unchanged':not differences,'original_files':len(manifest),
              'protected_copied_files':protected_count,'differences':differences,
              'manifest_sha256':sha(OUT/'s1_frozen_manifest.json')}
    write(OUT/'frozen_guard.json', report)
    assert not differences, differences
    return report


def provenance():
    before = {r['path']:r['sha256'] for r in read(OUT/'s1_frozen_manifest.json')}
    changes = []
    for prefix in ('Part1/program', 'Part2', 'Part3', 'tests'):
        for path in (ROOT/prefix).rglob('*'):
            if not path.is_file() or path.suffix not in ('.py','.mq5','.mqh'):
                continue
            rel = path.relative_to(ROOT).as_posix()
            after = sha(path)
            if before.get(rel) != after:
                changes.append({'file':rel,'before_sha256':before.get(rel),'after_sha256':after})
    write(OUT/'source_delta.json', {'stage':'S2','baseline':'whole frozen revision8/S1','changes':changes})
    return changes


if __name__ == '__main__':
    frozen_guard()
    print('S2 changes:',len(provenance()))
