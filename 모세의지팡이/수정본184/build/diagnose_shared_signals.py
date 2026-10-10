"""Link first-week solo-only alerts with preserved ALL internal OZ events."""
from __future__ import annotations
import collections,gzip,json,pathlib

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과'
cases=json.loads((OUT/'shared_alert_cases.json').read_text(encoding='utf-8'))
end=1757289600000  # 2025-09-08 UTC
targets={}
for c in cases:
    if c['period']=='2025-09' and c['classification']=='no_same_market_key_in_all' and int(c['time_ms'])<end:
        targets.setdefault((int(c['time_ms']),c['tf'],c['direction']),[]).append(c)
trace=ROOT.parent.parent/'개피곤_warehouse'/'runs'/'parallel26_before_week_all_trace'/'signals.jsonl.gz'
evidence=[]
with gzip.open(trace,'rt',encoding='utf-8') as file:
    for line in file:
        record=json.loads(line)
        if record.get('strategy')!='OZ':continue
        content=record.get('content') or {}
        event=content.get('event') or {}
        if event.get('kind')!='FINAL_ALERT':continue
        key=(int(record['time']),event.get('source_tf'),event.get('direction'))
        for case in targets.get(key,()):
            evidence.append({'solo_strategy':case['solo_strategy'],'time_ms':case['time_ms'],
                'tf':case['tf'],'direction':case['direction'],'solo_signal_id':case['solo_signal_id'],
                'all_internal_oz':{'event_id':event.get('event_id'),
                    'source_spec_id':event.get('source_spec_id'),
                    'source_spec_ids':event.get('source_spec_ids'),
                    'watch_ids':event.get('watch_ids'),
                    'request_chat_id':event.get('request_chat_id'),
                    'kind':event.get('kind')}})
report={'target_count':sum(map(len,targets.values())),'matched_internal_oz_count':len(evidence),
        'source_specs':dict(collections.Counter(e['all_internal_oz']['source_spec_id'] for e in evidence)),
        'cases':evidence}
(OUT/'shared_signal_links.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'target_count':report['target_count'],'matched_internal_oz_count':len(evidence),
    'source_specs':report['source_specs']},ensure_ascii=False))
