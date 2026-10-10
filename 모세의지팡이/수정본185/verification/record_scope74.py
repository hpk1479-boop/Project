"""Record changes against the untouched source folder; not a behavior oracle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / '수정본73'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(root, directory):
    return {p.relative_to(root).as_posix(): digest(p) for p in (root / directory).rglob('*')
        if p.is_file() and p.suffix.lower() in ('.py', '.pyw') and '__pycache__' not in p.parts}


def assets(root, directory):
    return {p.relative_to(root).as_posix(): digest(p) for p in (root / directory).rglob('*')
        if p.is_file() and p.suffix.lower() not in ('.pyc', '.pyo') and
        not any(part in ('__pycache__', '.pytest_cache', '검증결과') for part in p.parts)}


def differences(before, after):
    return {'changed': sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
        'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys())}


def main():
    report = {'source': SOURCE.name, 'target': ROOT.name, 'previous_evidence_copied': False}
    for name in ('Part1', 'Part2', 'Part3'):
        before, after = sources(SOURCE, name), sources(ROOT, name)
        report[name] = {'source_code_files': len(before), 'current_code_files': len(after),
            **differences(before, after)}
    report['Part2_all_files'] = differences(assets(SOURCE, 'Part2'), assets(ROOT, 'Part2'))
    immutable = ['Part1/program/command_interpreter.py',
        'Part3/lab/ai/agent.py', 'Part3/lab/ai/schema.py', 'Part3/lab/ai/intent.py',
        'Part3/lab/catalog.py', 'Part3/lab/ai_compiler.py', 'Part3/lab/compiler.py',
        'Part3/lab/intent_runtime.py', 'Part3/lab/intent_port.py']
    report['unchanged_contract_files'] = {name: digest(SOURCE / name) == digest(ROOT / name)
        for name in immutable if (SOURCE / name).is_file()}
    report['Part1_config_unchanged'] = digest(SOURCE / 'Part1/program/config.txt') == digest(ROOT / 'Part1/program/config.txt')
    report['common_ai_files'] = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'common_ai').glob('*.py'))
    report['common_settings'] = 'settings/ai_settings.json'
    report['only_requested_product_scope'] = (
        report['Part1']['changed'] == ['Part1/program/event_host.py'] and
        not report['Part1']['added'] and not report['Part1']['removed'] and
        not any(report['Part2'][key] for key in ('changed', 'added', 'removed')) and
        not any(report['Part2_all_files'].values()) and
        all(report['unchanged_contract_files'].values()) and report['Part1_config_unchanged'])
    destination = ROOT / '검증결과' / '수정본74_작업범위.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return int(not report['only_requested_product_scope'])


if __name__ == '__main__':
    raise SystemExit(main())
