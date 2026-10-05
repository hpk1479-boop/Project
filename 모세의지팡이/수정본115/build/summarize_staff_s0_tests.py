"""Consolidate raw runs, retaining collection corrections and known failures."""
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s0'


def cases(path):
    result = {}
    for c in ET.parse(path).getroot().iter('testcase'):
        module = c.get('classname', '').split('.')[-1]
        key = module + '::' + c.get('name', '')
        issue = c.find('failure')
        if issue is None:
            issue = c.find('error')
        status = ('FAILED' if c.find('failure') is not None else 'ERROR' if c.find('error') is not None
                  else 'SKIPPED' if c.find('skipped') is not None else 'PASSED')
        result[key] = {'status': status, 'detail': issue.text if issue is not None else ''}
    return result


def main():
    initial = cases(OUT / 'regressions/part2.xml')
    supplement = cases(OUT / 'regressions/part2_supplement.xml')
    baseline = cases(OUT / 'baseline_input/baseline_failures.xml')
    baseline_root = cases(OUT / 'baseline_root.xml')
    # Three collection errors came from the first runner's importlib option.
    # Those files were run in full using normal pytest collection afterward.
    resolved = {k: v for k, v in initial.items() if 'ModuleNotFoundError' in v['detail'] and
                any(x in v['detail'] for x in ('ipc_fixtures', 'oz_fixtures', 'integration_fixtures'))}
    combined = {k: v for k, v in initial.items() if k not in resolved}
    combined.update(supplement)
    failures = {k: {**v, 'reproduced_on_baseline_copy':
                    k in baseline and baseline[k]['status'] == v['status']}
                for k, v in combined.items() if v['status'] in ('FAILED', 'ERROR')}
    report = {'part2': {'counts': dict(Counter(v['status'] for v in combined.values())),
                        'failures': failures, 'collection_errors_resolved_by_supplement': list(resolved)},
              'root': {'counts': dict(Counter(v['status'] for v in cases(OUT / 'regressions/root.xml').values())),
                       'failures': {k: {**v, 'reproduced_on_baseline_copy': k in baseline_root and
                                          baseline_root[k]['status'] == v['status']}
                                    for k, v in cases(OUT / 'regressions/root.xml').items()
                                    if v['status'] in ('FAILED', 'ERROR')}},
              'watch_ma': dict(Counter(v['status'] for v in cases(OUT / 'regressions/watch_ma.xml').values())),
              'staff_s0': dict(Counter(v['status'] for v in cases(OUT / 'unit.xml').values()))}
    (OUT / 'test_summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v.get('counts', v) for k, v in report.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
