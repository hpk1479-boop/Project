"""Verify input preservation and exact authorized file scope against the input ZIP."""
from pathlib import Path
from zipfile import ZipFile
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '검증결과/계측_유효성최적화43'
MODIFIED = {'Part1/program/event_engine/market.py', 'Part1/program/oz_engine/runtime.py',
            'Part2/event_backtest/runner.py'}
ADDED_OPERATING = {'Part2/event_backtest/instrumentation.py'}
ADDED_TESTS = {'tests/test_worker_measurement43.py', 'tests/test_array_validity43.py',
               'tests/test_integration43.py'}


def ignored(name):
    path = Path(name)
    return any(part in ('검증결과', '__pycache__', '.pytest_cache') for part in path.parts) or path.suffix == '.pyc'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-zip', type=Path, required=True)
    args = parser.parse_args()
    with ZipFile(args.input_zip) as archive:
        before = {name.split('/', 1)[1]: sha(archive.read(name)) for name in archive.namelist()
                  if '/' in name and not name.endswith('/') and not ignored(name.split('/', 1)[1])}
    after = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in ROOT.rglob('*')
             if path.is_file() and not ignored(path.relative_to(ROOT).as_posix())}
    modified = sorted(name for name in before if name in after and before[name] != after[name])
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    assert set(modified) == MODIFIED, modified
    assert not missing, missing
    assert ADDED_OPERATING.issubset(added)
    for name in added:
        assert (name in ADDED_OPERATING | ADDED_TESTS or name.startswith('build/optimization43/')
                or name == 'Part1/audit/remediation/73-timing-array-optimization43/changes.json'
                or name == '수정본43_계측_유효성최적화_완료보고서.md'), name
    for name in MODIFIED | ADDED_OPERATING:
        compile((ROOT / name).read_text(encoding='utf-8-sig'), name, 'exec')
    result = {'input_archive': args.input_zip.name, 'input_sha256': sha(args.input_zip.read_bytes()),
              'modified_existing_files': modified, 'missing_files': missing, 'added_files': added,
              'excluded_from_copy': ['previous verification folders', 'Python bytecode and pytest caches'],
              'source_compilation_ok': True,
              'source_hashes': {name: {'input42': before.get(name), 'revision43': after[name]}
                                for name in sorted(MODIFIED | ADDED_OPERATING)},
              'fact42_unchanged': before['Part1/program/event_engine/facts.py'] == after['Part1/program/event_engine/facts.py']}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'scope.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
