"""Record source boundaries only; behavior is checked by verify_current.py."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT.parent / '수정본71'


def inventory(root, folder):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / folder).rglob('*') if p.is_file()
            and not any(part in ('__pycache__', '.pytest_cache') for part in p.relative_to(root).parts)
            and p.suffix not in ('.pyc', '.pyo')}


def main():
    report = {'source': '수정본71', 'target': '수정본72', 'boundaries': {}}
    for folder in ('Part1', 'Part2'):
        before, after = inventory(PREVIOUS, folder), inventory(ROOT, folder)
        report['boundaries'][folder] = {'files': len(after),
            'changed': sorted(p for p in before.keys() & after.keys() if before[p] != after[p]),
            'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys())}
    contracts = ['Part3/lab/ai/agent.py', 'Part3/lab/ai/schema.py', 'Part3/lab/ai/intent.py',
                 'Part3/lab/catalog.py', 'Part3/lab/ai_compiler.py', 'Part3/lab/compiler.py',
                 'Part3/lab/intent_runtime.py', 'Part3/lab/intent_port.py', 'Part3/lab/ai/provider.py']
    report['contracts_unchanged'] = {name: (ROOT / name).read_bytes() == (PREVIOUS / name).read_bytes()
                                     for name in contracts}
    report['ok'] = all(not data['changed'] and not data['added'] and not data['removed']
                       for data in report['boundaries'].values()) and all(report['contracts_unchanged'].values())
    path = ROOT / '검증결과/수정본72_작업범위.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return int(not report['ok'])


if __name__ == '__main__':
    raise SystemExit(main())
