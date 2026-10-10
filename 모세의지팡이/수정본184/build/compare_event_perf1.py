"""LIVE/replay equality gate; E2 notification differences are diagnostics."""
import json
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/event_perf1'
def read(p):return json.loads(p.read_text('utf-8-sig'))
def notices(result):return [x for x in result['signals'] if x['content'].get('type')=='NOTIFICATION']
def canonical(item):return json.dumps(item,ensure_ascii=False,sort_keys=True)
result={}
for case in ('synthetic240','actualXAU'):
    replay=read(OUT/'final_ready'/f'{case}_replay.json')
    live=read(OUT/'final_ready'/f'{case}_live.json')
    old=read(ROOT.parent/'수정본17/검증결과/event_e2/final_ready'/f'{case}_replay.json')
    assert replay['signals']==live['signals'],case+' LIVE/replay mismatch'
    assert not replay['errors'] and not live['errors']
    before=Counter(map(canonical,notices(old)));after=Counter(map(canonical,notices(replay)))
    removed=[json.loads(x) for x,count in (before-after).items() for _ in range(count)]
    added=[json.loads(x) for x,count in (after-before).items() for _ in range(count)]
    result[case]={'live_replay_all_signals_equal':True,'signals':len(replay['signals']),
        'notifications_before':len(notices(old)),'notifications_after':len(notices(replay)),
        'recipient_deliveries_before':len(old['telegram']),'recipient_deliveries_after':len(replay['telegram']),
        'removed':removed,'added':added,
        'assessment':'No notification difference' if not removed and not added else 'Review listed notifications against PERF1 input-window semantics; historical identity is diagnostic only'}
(OUT/'external_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print({k:{x:v for x,v in value.items() if x not in ('removed','added')} for k,value in result.items()})
