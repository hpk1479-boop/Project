"""Finalize separate reports only after every requested month has completed."""
from pathlib import Path
from collections import Counter,defaultdict
import csv,gzip,json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/shared_oz_composer_input'
def read(path):return json.loads((OUT/path).read_text('utf-8'))
def rows(label):
    with (OUT/label/'alerts.csv').open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def key(row):return tuple((k,v) for k,v in row.items() if k!='run_id')
def trace(label):
    with gzip.open(OUT/label/'oz_signals.jsonl.gz','rt',encoding='utf-8') as f:return [json.loads(line) for line in f]
required=['policy_ALL','optimized_ALL','final_ALL','final_SPECIAL1','final_SPECIAL2','final_SPECIAL6']
final_only='--final-only' in sys.argv
if final_only:required=required[2:]
results={name:read(name+'/result.json') for name in required}
digests={name:read(name+'/signals_digest.json') for name in required}
if not final_only:
    assert digests['policy_ALL']==digests['optimized_ALL'],'optimization changed signals'
    assert Counter(map(key,rows('policy_ALL')))==Counter(map(key,rows('optimized_ALL')))
all_rows=rows('final_ALL');all_trace=trace('final_ALL')
all_oz={r['content']['event'].get('market_event_id'):r['content']['event'] for r in all_trace if r['content'].get('family')=='OZ'}
all_notifications=defaultdict(list)
for r in all_trace:
    if r['content'].get('type')=='NOTIFICATION':all_notifications[r['content'].get('market_event_id')].append(r)
summary=[];differences=[]
for name in ('SPECIAL1','SPECIAL2','SPECIAL6'):
    solo=rows('final_'+name);combined=[r for r in all_rows if r['strategy']==name]
    left,right=Counter(map(key,solo)),Counter(map(key,combined));same=sum((left&right).values())
    solo_trace=trace('final_'+name)
    lookup={r['signal_id']:r for r in solo_trace if r['content'].get('type')=='NOTIFICATION'}
    for side,counter in [('solo_only',left-right),('ALL_only',right-left)]:
        for pairs,count in counter.items():
            row=dict(pairs);record=lookup.get(row['signal_id'])
            if side=='ALL_only':record=next((r for r in all_trace if r['signal_id']==row['signal_id']),None)
            content=record['content'] if record else {};market=content.get('market_event_id')
            event=all_oz.get(market)
            opposite=combined if side=='solo_only' else solo
            match=[r for r in opposite if (r['time_ms'],r['tf'],r['direction'],r['message'],r['recipient'])==
                   (row['time_ms'],row['tf'],row['direction'],row['message'],row['recipient'])]
            if match:reason='same_output_different_signal_id'
            elif side=='solo_only' and event:reason='same_market_event_different_active_watches'
            else:reason='different_OZ_event_under_selected_watch_environment'
            differences.append({'strategy':name,'side':side,'count':count,'row':row,'market_event_id':market,
                                'source_spec_id':content.get('source_spec_id'),'reason':reason,
                                'ALL_watch_ids':event.get('watch_ids',[]) if event else [],
                                'ALL_source_spec_ids':event.get('source_spec_ids',[]) if event else [],
                                'ALL_same_market_outputs':[(r['strategy'],r['content'].get('source_spec_id')) for r in all_notifications.get(market,[])]})
    summary.append({'strategy':name,'solo':len(solo),'ALL':len(combined),'exact':same,
                    'solo_only':sum((left-right).values()),'ALL_only':sum((right-left).values())})
(OUT/'month_signal_comparison.json').write_text(json.dumps({'strategies':summary,'differences':differences,
    'optimization_signals_identical':None if final_only else True,'optimization_digest':digests.get('policy_ALL')},ensure_ascii=False,indent=2),encoding='utf-8')
timing=[]
for name,result in results.items():
    timing.append({'run':name,'bundles':result['bundles'],'engine_ms':result['mean_ms'],
                  'input_and_overhead_ms':result['elapsed_seconds']*1000/result['bundles']-result['mean_ms'],
                  'elapsed_seconds':result['elapsed_seconds'],'cpu_seconds':result['cpu_seconds'],
                  'composer_ms':result['processor_timings'].get('COMPOSER',{}).get('ms_per_bundle',0)})
(OUT/'month_timings.json').write_text(json.dumps(timing,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'strategies':summary,'difference_cases':len(differences),'timings':timing},ensure_ascii=False,indent=2))
