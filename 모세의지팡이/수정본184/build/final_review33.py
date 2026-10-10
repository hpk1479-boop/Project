"""Read-only source review plus relative-path evidence for revision 33."""
from pathlib import Path
import ast
import difflib
import hashlib
import importlib
import json
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT.parent / '수정본32'
OUT = ROOT / '검증결과/stop_virtual_parallel'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
changed = []
diffs = []
for directory in ('Part1/program', 'Part2/event_backtest', 'tests'):
    for path in sorted((ROOT / directory).rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(ROOT)
        old = BEFORE / rel
        if old.exists() and sha(old) == sha(path):
            continue
        ast.parse(path.read_bytes(), filename=rel.as_posix())
        changed.append(rel.as_posix())
        diffs.extend(difflib.unified_diff(
            old.read_text('utf-8-sig').splitlines(True) if old.exists() else [],
            path.read_text('utf-8-sig').splitlines(True),
            fromfile='before/' + rel.as_posix(), tofile=rel.as_posix()))
(OUT / 'changed_files.json').write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'source_review.diff').write_text(''.join(diffs), encoding='utf-8')

protected = json.loads((OUT / 'protected.json').read_text('utf-8'))
assert all(sha(ROOT / r['file']) == r['sha256'] == sha(BEFORE / r['file']) for r in protected)
configs = []
for directory in ('Part1/program', 'Part2'):
    for p in (ROOT / directory).glob('*'):
        if p.is_file() and (p.name in ('config.txt', 'backtest_ui.json', 'backtest_ui_scenario.json', 'event_backtest_settings.json') or 'settings' in p.stem and p.suffix == '.json'):
            rel = p.relative_to(ROOT)
            same = (BEFORE / rel).exists() and sha(p) == sha(BEFORE / rel)
            configs.append({'file': rel.as_posix(), 'unchanged': same})
assert all(r['unchanged'] for r in configs), configs

sys.path[:0] = [str(ROOT/'Part2'), str(ROOT/'Part1/program')]
from event_backtest.system import deny_network
deny_network()
imports = []
for rel in changed:
    if rel.startswith('Part2/event_backtest/'):
        name = 'event_backtest.' + Path(rel).stem
    elif rel.startswith('Part1/program/'):
        name = Path(rel).stem
    else:
        continue
    importlib.import_module(name)
    imports.append(name)

# Preserve every original run, including failures. Latest targeted results
# supersede only the same test identity, never a different test or skip.
results = {}
for p in sorted(OUT.glob('*.xml'), key=lambda p: p.stat().st_mtime_ns):
    for case in ET.parse(p).getroot().iter('testcase'):
        key = case.attrib.get('classname', '') + '::' + case.attrib['name']
        state = 'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        results[key] = {'status': state, 'evidence': p.name}
summary = {'method': 'latest result per identical test identity; original XML files retained',
           'counts': {s: sum(r['status'] == s for r in results.values()) for s in ('passed','failed','skipped')},
           'tests': results}
(OUT / 'test_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
review = {'syntax_passed': changed, 'imports_passed': imports, 'protected_unchanged': len(protected), 'config_checks': configs}
(OUT / 'final_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'tests': summary['counts'], 'syntax_files': len(changed), 'imports': len(imports), 'protected_unchanged': len(protected)},ensure_ascii=False))
