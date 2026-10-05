"""Record change boundaries; strategy correctness is verified independently."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT.parent / '수정본72'


def inventory(root, folder):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / folder).rglob('*') if p.is_file()
            and not any(part in ('__pycache__', '.pytest_cache') for part in p.relative_to(root).parts)
            and p.suffix not in ('.pyc', '.pyo')}


def methods(root, file, name):
    tree = ast.parse((root / file).read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return {n.name: ast.dump(n, include_attributes=False) for n in cls.body
            if isinstance(n, ast.FunctionDef)}


def main():
    report = {'source': '수정본72', 'target': '수정본73', 'boundaries': {}}
    for folder in ('Part1', 'Part2'):
        before, after = inventory(PREVIOUS, folder), inventory(ROOT, folder)
        report['boundaries'][folder] = {'files': len(after),
            'changed': sorted(p for p in before.keys() & after.keys() if before[p] != after[p]),
            'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys())}
    contracts = ['Part3/lab/ai/agent.py', 'Part3/lab/ai/schema.py', 'Part3/lab/ai/intent.py',
                 'Part3/lab/ai/tools.py', 'Part3/lab/catalog.py', 'Part3/lab/ai_compiler.py',
                 'Part3/lab/compiler.py', 'Part3/lab/intent_runtime.py', 'Part3/lab/intent_port.py',
                 'Part3/lab/ai/local_lora.py', 'Part3/lab/ai/lora_worker.py']
    report['contracts_unchanged'] = {name: (ROOT / name).read_bytes() == (PREVIOUS / name).read_bytes()
                                     for name in contracts}
    for file, cls in [('Part3/lab/ai/provider.py', 'OpenAICompatible'),
                      ('Part3/lab/ai/model_runtime.py', 'ModelRuntime')]:
        before, after = methods(PREVIOUS, file, cls), methods(ROOT, file, cls)
        changes = sorted(name for name in before if before[name] != after.get(name))
        report[cls] = {'existing_methods_changed': changes,
                       'methods_added': sorted(after.keys() - before.keys())}
    report['cleanup_guard'] = '실패한 worker 종료의 재시도를 위해 _turn의 finally에서 요청 잠금 해제를 보장'
    report['existing_ai_settings_unchanged'] = ((ROOT / 'Part3/projects/ai_settings.json').read_bytes() ==
                                               (PREVIOUS / 'Part3/projects/ai_settings.json').read_bytes())
    report['ok'] = (all(not d['changed'] and not d['added'] and not d['removed']
                        for d in report['boundaries'].values()) and
                    all(report['contracts_unchanged'].values()) and
                    not report['OpenAICompatible']['existing_methods_changed'] and
                    report['ModelRuntime']['existing_methods_changed'] == ['_turn'] and
                    report['ModelRuntime']['methods_added'] == ['gguf_chat'] and
                    report['existing_ai_settings_unchanged'])
    path = ROOT / '검증결과/수정본73_작업범위.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return int(not report['ok'])


if __name__ == '__main__':
    raise SystemExit(main())
