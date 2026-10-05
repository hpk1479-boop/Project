"""Read-only structural diff of frozen and candidate parity evidence."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part2'))
from staff_golden.contracts import json_bytes


def diff(a, b, path=''):
    if json_bytes(a) == json_bytes(b):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for k in sorted(a.keys() | b.keys()):
            if k not in a or k not in b:
                result.append({'path': path + '/' + k, 's0': a.get(k), 's1': b.get(k)})
            else:
                result.extend(diff(a[k], b[k], path + '/' + k))
        return result
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [item for i, (aa, bb) in enumerate(zip(a, b)) for item in diff(aa, bb, path + '/' + str(i))]
    return [{'path': path, 's0': a, 's1': b}]


if __name__ == '__main__':
    out = ROOT / '검증결과/staff_s1'
    old = json.loads((ROOT / '검증결과/staff_s0/parity_240/before_live.json').read_text('utf-8'))['evidence']
    result = {}
    for name in ('before_live', 'after_live', 'after_backtest'):
        path = out / 'parity_240' / (name + '.json')
        if path.exists():
            result[name] = diff(old, json.loads(path.read_text('utf-8'))['evidence'])
    (out / 'parity_diagnosis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: {'differences': len(v), 'first': v[:8]} for k, v in result.items()}, ensure_ascii=False, indent=2))
