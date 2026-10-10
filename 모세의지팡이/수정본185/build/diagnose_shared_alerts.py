"""Classify archived solo-vs-ALL alerts without replay or strategy changes."""
from __future__ import annotations

import collections
import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT.parent.parent / '개피곤_warehouse' / 'runs'
OUT = ROOT / '검증결과'
GROUPS = {
    '2025-06': ('parallel26_positive_june_all', 'parallel26_positive_june_special{}'),
    '2025-09': ('parallel26_a_month_all', 'parallel26_month_pairs_selected_special{}'),
}
STRATEGIES = (1, 2, 6)


def rows(run):
    with (WAREHOUSE / run / 'alerts.csv').open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def key(row):
    return tuple(row[field] for field in ('strategy', 'time_ms', 'symbol', 'tf', 'direction', 'trigger', 'message', 'recipient'))


def market_key(row):
    return tuple(row[field] for field in ('time_ms', 'symbol', 'tf', 'direction'))


def main():
    report = {}
    cases = []
    for period, (all_run, solo_pattern) in GROUPS.items():
        all_rows = rows(all_run)
        all_by_market = collections.defaultdict(list)
        all_by_market_no_tf = collections.defaultdict(list)
        for item in all_rows:
            all_by_market[market_key(item)].append(item)
            all_by_market_no_tf[(item['time_ms'], item['symbol'], item['direction'])].append(item)
        period_result = {'all_total': len(all_rows), 'strategies': {}}
        for number in STRATEGIES:
            name = f'SPECIAL{number}'
            solo = rows(solo_pattern.format(number))
            own = [r for r in all_rows if r['strategy'] == name]
            own_key = set(map(key, own))
            counts = collections.Counter()
            for item in solo:
                if key(item) in own_key:
                    same = [r for r in own if key(r) == key(item)]
                    category = 'identical' if any(r['signal_id'] == item['signal_id'] for r in same) else 'same_output_id_changed'
                    matches = same
                else:
                    matches = all_by_market[market_key(item)]
                    if any(r['strategy'] != name for r in matches):
                        category = 'other_strategy_same_market_key'
                    elif matches:
                        category = 'same_strategy_changed_output'
                    elif all_by_market_no_tf[(item['time_ms'], item['symbol'], item['direction'])]:
                        category = 'same_time_direction_other_tf'
                        matches = all_by_market_no_tf[(item['time_ms'], item['symbol'], item['direction'])]
                    else:
                        category = 'no_same_market_key_in_all'
                counts[category] += 1
                if category not in ('identical', 'same_output_id_changed'):
                    cases.append({'period': period, 'solo_strategy': name, 'classification': category,
                                  'time_ms': item['time_ms'], 'tf': item['tf'], 'direction': item['direction'],
                                  'solo_signal_id': item['signal_id'], 'solo_message': item['message'],
                                  'all_matches': [{'strategy': r['strategy'], 'signal_id': r['signal_id'],
                                                   'tf': r['tf'], 'message': r['message']} for r in matches]})
            period_result['strategies'][name] = {'solo_count': len(solo), 'all_count': len(own),
                                                  'solo_classification': dict(counts)}
        report[period] = period_result
    (OUT / 'shared_alert_classification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'shared_alert_cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
