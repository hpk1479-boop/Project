"""Keep exact notification differences; no text/time normalization for the gate."""
from collections import defaultdict,deque,Counter
from event_e2_common import *
rows=[]
for case in ('synthetic240','xau1200','btc1200','actualXAU','actualBTC'):
    lp=OUT/'final_ready'/(case+'_live.json');rp=OUT/'final_ready'/(case+'_replay.json')
    pp=OUT/('development/polling240.json' if case=='synthetic240' else 'comparison/'+case+'_polling.json')
    if not all(p.exists() for p in (lp,rp,pp)):continue
    live,replay,poll=read(lp),read(rp),read(pp)
    notices=lambda data:[s for s in data['signals'] if s['content'].get('type')=='NOTIFICATION']
    ln,rn=notices(live),notices(replay)
    exact=ln==rn
    old=defaultdict(deque)
    for index,(stamp,recipient,message) in enumerate(poll['telegram']):old[(recipient,message)].append((index,stamp))
    differences=[];same=0;matches=[]
    def family(message):
        if message.startswith('[') and '. ' in message:return 'SPECIAL'+message[1:].split('.')[0]
        if 'FVG' in message:return 'FVG/Watch'
        if '원비' in message:return 'WONBI/Watch'
        if '올존' in message:return 'OZ/Watch'
        if '기울기' in message or '추세' in message:return 'INDICATOR/Watch'
        return 'Watch/Composer'
    for index,(stamp,recipient,message) in enumerate(live['telegram']):
        key=(recipient,message)
        if old[key]:
            before_index,before=old[key].popleft();matches.append((before_index,index))
            if before==stamp:same+=1;continue
            registration=message.startswith('✅') and ('감시' in message or '등록' in message) and stamp==live['start']-1 and before==live['start']
            differences.append({'strategy':family(message),'classification':'판단 시점 변화(정당) — 사용자 승인 대상' if registration else '시점 차이 — 원인 분석 필요',
                'reason':'폴링 JSONL worker의 첫 시장 주기 대기에서 Ingress COMMAND 즉시 처리로 변경' if registration else None,
                'polling_time':before,'event_time':stamp,'recipient':recipient,'message':message})
        else:differences.append({'strategy':family(message),'classification':'이벤트판 추가 — 원인 분석 필요','event_time':stamp,'recipient':recipient,'message':message})
    for (recipient,message),values in old.items():
        for _,stamp in values:differences.append({'strategy':family(message),'classification':'이벤트판 누락 — 원인 분석 필요','polling_time':stamp,'recipient':recipient,'message':message})
    row={'case':case,'symbol':live['symbol'],'seconds':live['seconds'],
         'polling_notifications':len(poll['telegram']),'event_live_notifications':len(ln),
         'event_replay_notifications':len(rn),'live_replay_signals_exact':exact,
         'live_replay_telegram_exact':live['telegram']==replay['telegram'],
         'unchanged_time_recipient_message':same,'differences':differences,
         'same_time_registration_order_changed':matches!=sorted(matches),
         'errors':live['errors']+replay['errors'],'measurement':live.get('measurement'),
         'checkpoint_equal':live.get('checkpoint_equal'),
         'final_source_hashes_equal':live['source_hashes']==replay['source_hashes']}
    # Report registration acknowledgements separately so non-empty validation
    # cannot be mistaken for coverage of market-triggered notifications.
    registration_count=sum(stamp==live['start']-1 and message.startswith('✅')
                           for stamp,_,message in live['telegram'])
    row['event_registration_notifications']=registration_count
    row['event_market_notifications']=len(live['telegram'])-registration_count
    row['event_market_notification_titles']=[message.splitlines()[0]
        for stamp,_,message in live['telegram'] if stamp!=live['start']-1]
    rows.append(row)
result={'scenarios':rows,'complete':len(rows)==5,
        'completed_scenarios_passed':bool(rows) and all(r['live_replay_signals_exact'] and r['live_replay_telegram_exact'] and not r['errors'] and r['final_source_hashes_equal'] for r in rows),
        'live_replay_passed':len(rows)==5 and all(r['live_replay_signals_exact'] and r['live_replay_telegram_exact'] and not r['errors'] and r['final_source_hashes_equal'] for r in rows),
        'unclassified_difference_count':sum('원인 분석 필요' in d['classification'] for r in rows for d in r['differences']),
        'approval_pending_difference_count':sum('사용자 승인 대상' in d['classification'] for r in rows for d in r['differences'])}
write(OUT/'external_comparison.json',result)
print({k:v for k,v in result.items() if k!='scenarios'},flush=True)
for row in rows:print(row['case'],row['polling_notifications'],row['event_live_notifications'],'exact',row['live_replay_signals_exact'],Counter(d['classification'] for d in row['differences']),flush=True)
