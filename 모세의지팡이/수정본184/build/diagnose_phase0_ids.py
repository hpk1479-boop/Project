"""Trace notification identities through recorded parent events, without rerunning."""
from pathlib import Path
import json
from compare_phase0_trace import records

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/schema_cleanup/phase0'
TARGETS = {
    '21': ['b09892af2cf7a455ca3b46f921e5426ff51f9b7015e99d137a27a867e73c8f2d',
           '851fbe36dc2889349e3f745734434a37ce9f2a7e1ca23fb0f3025e76a93a01aa'],
    '22': ['011c9a09de7cd2858629c6aace3103affa02080c67f3be033c51d471f54fd7ab',
           'ae7476c3e43968dc4186fb1bd6d2ddb6e863ba6b84b7cc049dc073fcfd73dbaa'],
}

def main():
    result = {}
    for revision, targets in TARGETS.items():
        folder = OUT / ('revision' + revision)
        # Restrict retention to the minute of the final SPECIAL1 notifications.
        causes = {r['signal_id']: r for r in records(folder/'causes.jsonl.gz')
                  if r['time'] == 1757116140000}
        signals = {r['payload']['signal_id']: r for r in records(folder/'signals.jsonl.gz')
                   if r['time'] == 1757116140000}
        entries = []
        for target in targets:
            chain = []
            key = target
            for _ in range(12):
                cause = causes.get(key)
                if cause is None:
                    break
                chain.append({'cause': cause, 'signal': signals.get(key)})
                key = cause['parent_signal_id']
            entries.append({'target': target, 'chain': chain})
        result[revision] = entries
    (OUT/'identity_diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for revision, entries in result.items():
        for entry in entries:
            print(revision, entry['target'])
            for row in entry['chain']:
                c = row['cause']; p = c.get('parent_payload') or {}
                print('  ',c['owner'],c['condition_key'],c['parent_kind'],
                      p.get('content',{}).get('family'),p.get('content',{}).get('event',{}))

if __name__ == '__main__': main()
