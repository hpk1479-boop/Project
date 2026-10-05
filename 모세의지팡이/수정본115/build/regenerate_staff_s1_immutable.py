"""Regenerate S1 source inventory only; S0 expected results remain frozen."""
import json
from staff_s1_evidence import ROOT, OUT, sha

if __name__ == '__main__':
    path = ROOT / 'build/part1_immutable_sha256.json'
    before = sha(path)
    inventory = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'Part1').rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
                 and p.name != 'special_settings.json' and p.suffix != '.ex5'}
    path.write_text(json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
    (OUT / 'immutable_regeneration.json').write_text(json.dumps({'file': str(path), 'files': len(inventory),
        'before_sha256': before, 'after_sha256': sha(path), 'scope': 'S1 source inventory, not golden/behavior expectations'},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('S1 immutable source inventory regenerated:', len(inventory))
