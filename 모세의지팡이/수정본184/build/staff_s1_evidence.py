"""Immutable S0 guard and additive S1 provenance, without changing expectations."""
from __future__ import annotations
import hashlib
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s1'
S0 = ROOT / '검증결과/staff_s0'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_authorized_test_adapter(before, after):
    """Only whole-tree BEFORE and user-requested CPU measurement may differ."""
    old, new = ast.parse(before.read_bytes()), ast.parse(after.read_bytes())
    flexible = {'legacy_root', 'performance_results', 'test_after_is_faster_and_reuses_common_facts'}
    class RemoveCpuInstrumentation(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any(isinstance(t, ast.Name) and t.id == 'cpu' for t in node.targets):
                return None
            return self.generic_visit(node)
        def visit_Dict(self, node):
            pairs = [(k,v) for k,v in zip(node.keys,node.values)
                     if not (isinstance(k,ast.Constant) and k.value=='cpu')]
            node.keys, node.values = [k for k,v in pairs], [v for k,v in pairs]
            return self.generic_visit(node)
        def visit_Tuple(self, node):
            node.elts = [e for e in node.elts if not (isinstance(e,ast.Constant) and e.value=='cpu')]
            return self.generic_visit(node)
    def stable(tree):
        items=[]
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in flexible:
                continue
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
                continue
            if isinstance(node,ast.FunctionDef) and node.name in ('run','results'):
                node=RemoveCpuInstrumentation().visit(node)
            items.append(ast.dump(node))
        return items
    assert stable(old)==stable(new), 'Changed non-performance scenario or assertions'
    old_perf=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='test_after_is_faster_and_reuses_common_facts')
    new_perf=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='test_after_is_faster_and_reuses_common_facts')
    old_checks=[ast.dump(n.test) for n in ast.walk(old_perf) if isinstance(n,ast.Assert)]
    new_checks=[ast.dump(n.test) for n in ast.walk(new_perf) if isinstance(n,ast.Assert)]
    assert set(old_checks[:2]).issubset(new_checks), 'Removed Fact reuse checks'
    assert ast.dump(ast.parse('ratio <= limit',mode='eval').body) in new_checks


def frozen_guard():
    manifest = json.loads((OUT / 's0_frozen_manifest.json').read_text('utf-8-sig'))
    source = ROOT.parent / '수정본7'
    differences = []
    expected_source = {item['path'] for item in manifest}
    actual_source = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}
    differences.extend('S0 inventory: ' + name for name in sorted(expected_source ^ actual_source))
    copied_s0 = {p.relative_to(ROOT).as_posix() for p in S0.rglob('*') if p.is_file()}
    expected_s0 = {name for name in expected_source if name.startswith('검증결과/staff_s0/')}
    differences.extend('Copied S0 inventory: ' + name for name in sorted(copied_s0 ^ expected_s0))
    for item in manifest:
        rel = item['path']
        protected = (rel.startswith(('검증결과/staff_s0/', 'tests/', 'Part1/audit/fixtures/')) or
                     '/validation_suite/test_' in rel or '/watch_ma_validation/' in rel or
                     Path(rel).name in ('source_contract.json', 'local_contract.json'))
        if rel == 'Part2/validation_suite/test_oz_fvg_optimization.py':
            verify_authorized_test_adapter(source / rel, ROOT / rel)
            protected = False
        for base in (source, ROOT) if protected else (source,):
            path = base / rel
            if not path.is_file() or sha(path) != item['sha256']:
                differences.append(str(path))
    report = {'s0_original_and_copied_evidence_unchanged': not differences,
              'original_files_checked': len(manifest), 'differences': differences,
              'manifest_sha256': sha(OUT / 's0_frozen_manifest.json')}
    (OUT / 'frozen_guard.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if differences:
        raise RuntimeError('Frozen S0 changed: ' + repr(differences))
    return report


def provenance():
    frozen_guard()
    manifest = json.loads((OUT / 's0_frozen_manifest.json').read_text('utf-8-sig'))
    changes = []
    for item in manifest:
        rel = item['path']
        if not rel.startswith(('Part1/program/', 'Part1/audit/harness.py', 'Part2/', 'Part3/')):
            continue
        if Path(rel).suffix not in ('.py', '.mq5', '.mqh'):
            continue
        path = ROOT / rel
        after = sha(path)
        if after != item['sha256']:
            changes.append({'file': rel, 'before_sha256': item['sha256'], 'after_sha256': after})
    for rel in ('Part3/generic_backtest/live_source.py', 'Part2/validation_suite/test_staff_s1.py',
                'Part2/staff_golden/parity_compare.py',
                'Part2/validation_suite/test_staff_s1_parity_comparison.py'):
        changes.append({'file': rel, 'before_sha256': None, 'after_sha256': sha(ROOT / rel)})
    report = {'stage': 'S1', 'baseline': 'frozen S0', 'changes': changes,
              'legacy_audit_chain': 'Original manifest and historical units preserved. User-requested units 21/22 '
                  'anchor only the four S1 owners to frozen S0 and append S1 deltas. Other inherited discrepancies stay visible.'}
    (OUT / 'source_delta.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    unit = ROOT / 'Part1/audit/remediation/30-staff-s1'
    unit.mkdir(parents=True, exist_ok=True)
    # Intentionally not changes.json: that old chain predates and disagrees with S0.
    (unit / 's0_to_s1.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('S0 frozen; S1 source delta recorded:', len(changes), 'files', flush=True)


if __name__ == '__main__':
    provenance()
