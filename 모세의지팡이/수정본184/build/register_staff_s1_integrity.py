"""Append reviewed S0 anchors and S1 deltas; preserve the original audit manifest."""
from __future__ import annotations
import hashlib
import json
import sys
from staff_s1_evidence import ROOT, OUT, sha


def main():
    part1 = ROOT / 'Part1'
    root = part1 / 'audit/remediation'
    first, second = root / '21-s0-fact-source-anchor', root / '22-s1-pure-facts'
    if first.exists() or second.exists():
        raise FileExistsError('Integrity units already registered')
    original = json.loads((part1 / 'audit/fixtures/source_manifest.json').read_text('utf-8'))
    expected = {k: v['sha256'] for k, v in original.items()}
    for unit in sorted(root.glob('*/changes.json')):
        for change in json.loads(unit.read_text('utf-8')):
            if change['file'] in expected or change['file'].startswith('program/'):
                expected[change['file']] = change['after_sha256']
    frozen = {r['path']: r['sha256'] for r in json.loads((OUT / 's0_frozen_manifest.json').read_text('utf-8-sig'))}
    empty = hashlib.sha256(b'').hexdigest()
    files = ['program/THE STAFF OF MOSES.py', 'program/monitor_OZ.py',
             'program/indicator_facts.py', 'program/strategy_FVG.py']
    anchors, deltas = [], []
    for name in files:
        before = frozen['Part1/' + name]
        anchors.append({'file': name, 'before_sha256': expected.get(name, empty), 'after_sha256': before})
        deltas.append({'file': name, 'before_sha256': before, 'after_sha256': sha(part1 / name)})
    for folder, changes, purpose in (
        (first, anchors, 'The old chain predates S0. Explicitly anchor ONLY the four S1 owners to the hash-frozen S0 source; other inherited discrepancies stay visible.'),
        (second, deltas, 'S1 pure Fact extraction from the reviewed S0 anchor; function ASTs and golden responses are unchanged.')):
        folder.mkdir()
        (folder / 'changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'review.json').write_text(json.dumps({'purpose': purpose,
            'authorization': 'User explicitly requested hash-chain registration during S1',
            's0_manifest_sha256': sha(OUT / 's0_frozen_manifest.json'),
            'original_manifest_modified': False}, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.path.insert(0, str(part1 / 'audit'))
    from source_integrity import verify_sources
    result = verify_sources()
    old_errors = json.loads((ROOT / '검증결과/staff_s0/integrity_baseline_comparison.json').read_text('utf-8'))['baseline_errors']
    removed = sorted(set(old_errors) - set(result['integrity_errors']))
    added = sorted(set(result['integrity_errors']) - set(old_errors))
    report = {'units': [first.name, second.name], 'anchored_files': files,
              'removed_diagnostics': removed, 'added_diagnostics': added,
              'remaining_errors': result['integrity_errors'], 'chain_valid': not any('broken hash chain' in e for e in result['integrity_errors'])}
    (OUT / 'integrity_registration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if added or not report['chain_valid']:
        raise RuntimeError('Unexpected integrity difference')


if __name__ == '__main__':
    main()
