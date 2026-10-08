"""Classify completed alert comparisons without hiding signal-id differences."""
from pathlib import Path
from collections import Counter
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/engine_optimization'


def without_id(row):
    return json.dumps({k: v for k, v in row.items() if k not in ('count', 'signal_id')},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def main():
    controlled = json.loads((OUT / 'controlled_seed_comparison.json').read_text('utf-8'))
    comparisons = json.loads((OUT / 'alert_comparisons.json').read_text('utf-8'))
    results = {}
    for name, result in comparisons.items():
        removed, added = result['removed'], result['added']
        left, right = Counter(), Counter()
        for row in removed:
            left[without_id(row)] += row['count']
        for row in added:
            right[without_id(row)] += row['count']
        same_content = left == right
        pairs = []
        available = [dict(row) for row in added]
        for old in removed:
            remaining = old['count']
            for new in available:
                if remaining and new['count'] and without_id(old) == without_id(new):
                    count = min(remaining, new['count'])
                    remaining -= count
                    new['count'] -= count
                    pairs.append({'count': count, 'alert': json.loads(without_id(old)),
                                  'before_signal_id': old['signal_id'],
                                  'after_signal_id': new['signal_id']})
        if not removed and not added:
            classification = 'all_fields_equal' if result['exact_order_equal'] else 'order_only_requires_analysis'
        elif same_content:
            classification = 'signal_id_only_existing_unordered_touch_sequence'
        else:
            classification = 'requires_cause_analysis'
        results[name] = {'classification': classification, 'content_equal': same_content,
                         'exact_order_equal': result['exact_order_equal'],
                         'id_only_pairs': pairs,
                         'unpaired_removed': sum((left - right).values()),
                         'unpaired_added': sum((right - left).values())}
    report = {
        'controlled_all_seed_0': controlled,
        'cause': ('The unchanged wonbi tf_set iteration assigns global touch sequence numbers. '
                  'Different process hash seeds can alter touch IDs and downstream signal IDs. '
                  'The same week with seed 0 is exactly equal before/after. '
                  'wonbi_order_probe.json reproduces the same seed-dependent sequence in both revisions.'),
        'scope': ('Controlled full-week equality proves the observed ALL difference. '
                  'Standalone ID-only pairs are reported with their exact values; '
                  'content-changing differences require separate investigation.'),
        'comparisons': results,
    }
    (OUT / 'alert_difference_analysis.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: {'classification': v['classification'],
                          'id_only_count': sum(p['count'] for p in v['id_only_pairs'])}
                      for k, v in results.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
