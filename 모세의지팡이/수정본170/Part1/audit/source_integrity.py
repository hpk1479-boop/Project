"""Verify the original manifest plus the ordered, reviewed remediation deltas.

The original baseline manifest remains immutable. Each delta must start at the
previous recorded hash; only the final recorded hash is allowed on disk.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_sources():
    manifest = json.loads((ROOT/'audit/fixtures/source_manifest.json').read_text(encoding='utf-8'))
    expected = {name: meta['sha256'] for name, meta in manifest.items()}
    empty = hashlib.sha256(b'').hexdigest()
    errors, units = [], []
    for path in sorted((ROOT/'audit/remediation').glob('*/changes.json')):
        units.append(path.parent.name)
        for change in json.loads(path.read_text(encoding='utf-8')):
            name = change['file']
            if name not in expected and not name.startswith('program/'):
                continue
            if change['before_sha256'] != expected.get(name, empty):
                errors.append(f'{path.parent.name}: broken hash chain: {name}')
            expected[name] = change['after_sha256']
    for name, sha in expected.items():
        path = ROOT/name
        if sha is None:
            if path.exists():errors.append(f'Restored deleted source without authorization: {name}')
            continue
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            errors.append(f'Unrecorded source change: {name}')
    for path in (ROOT/'program').rglob('*'):
        if path.is_file() and path.suffix in {'.py', '.mq5'}:
            name = path.relative_to(ROOT).as_posix()
            if name not in expected:
                errors.append(f'Unrecorded new source: {name}')
    changed = [name for name, meta in manifest.items() if expected[name] != meta['sha256']]
    return {'original_source_files': len(manifest), 'authorized_changed_files': changed,
            'authorized_new_files': sorted(set(expected)-set(manifest)),
            'units': units, 'integrity_errors': errors,
            'final_sha256': expected}
