"""Explain Part2's actual collection domain with immutable node-ID inventories."""
import json
from collections import Counter
from staff_s1_evidence import OUT


def nodes(path):
    return [line.strip().replace('\\', '/') for line in path.read_text('utf-8-sig').splitlines()
            if '.py::' in line and not line.startswith(('ERROR', 'FAILED', ' '))]


if __name__ == '__main__':
    old = nodes(OUT / 'part2_baseline_all_collection.log')
    new = nodes(OUT / 'part2_all_collection.log')
    added, missing = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    report = {'baseline_revision6_collected': len(old), 's1_collected': len(new),
              'baseline_by_directory': dict(Counter(n.split('/')[0] for n in old)),
              's1_by_directory': dict(Counter(n.split('/')[0] for n in new)),
              'added': added, 'missing': missing,
              'prior_scope': 'validation_suite only; conditional_validation, cadence_input_validation, watch_ma_validation were omitted',
              'historical_660': 'The old document states 660 PASSED, not 660 collected. Raw output for that historical count is unavailable; do not equate it with collection size.',
              's0_golden_regenerated': False}
    (OUT / 'part2_collection_scope.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('added',)}, ensure_ascii=False, indent=2))
    assert not missing
    assert len(added) == 30  # S0 recorder tests 11 + S1 ownership tests 16 + set comparer tests 3.
