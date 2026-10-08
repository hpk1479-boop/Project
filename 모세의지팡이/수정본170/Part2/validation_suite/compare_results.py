"""Exact evidence comparer. Never widens numeric tolerance to produce PASS."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--out',required=True);a=p.parse_args();D=Path(a.evidence);results=[]
def load(name):
    path=D/name
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
def record(name,expected,actual,**details):
    results.append({'test':name,'status':'PASS' if expected==actual else 'FAIL','expected':expected,'actual':actual,**details})
def skip(name,why):results.append({'test':name,'status':'SKIP','reason':why})
base=load('cumulative_step1.json')
for stage in ('baseline','step1','step2','step3','step4'):
    data=load(f'cumulative_{stage}.json')
    if not data:skip('cumulative_'+stage,'file missing');continue
    record('cumulative_'+stage+'_case_count',16,len(data['rows']))
    record('cumulative_'+stage+'_successful_cases',16,sum(r.get('status')=='PASS' for r in data['rows']))
    if stage not in ('baseline','step1') and base:
        ref={(r['days'],r['base_case'],r['filter_on']):r for r in base['rows']}
        differences=[];files_compared=0
        for row in data['rows']:
            key=(row['days'],row['base_case'],row['filter_on']);old=ref.get(key)
            if not old or row['status']!='PASS' or old['status']!='PASS':differences.append([key,'status']);continue
            for f in set(old['files'])|set(row['files']):
                files_compared+=1
                if old['files'].get(f)!=row['files'].get(f):differences.append([key,f])
            for f in ('plan_id','input_sha256','alert_population','entry_population'):
                if old[f]!=row[f]:differences.append([key,f])
        record('cumulative_'+stage+'_exact',[],differences,files_compared=files_compared,reference='step1 (new requested close semantics)')
for reference,modes in [('baseline',('TICK',)),('step1',('TICK','ONE_MINUTE_CLOSE'))]:
    old=load(f'trade_{reference}.json');new=load('trade_step4.json')
    if not old or not new:skip('trade_'+reference,'file missing');continue
    differences=[];files_compared=0;cases=0
    rr={(r['evaluation_mode'],r['filter_on']):r for r in old['rows']}
    for r in new['rows']:
        if r['evaluation_mode'] not in modes:continue
        cases+=1;k=(r['evaluation_mode'],r['filter_on']);b=rr[k]
        if b['status']!='PASS' or r['status']!='PASS':differences.append([k,'status']);continue
        for f in set(b['files'])|set(r['files']):
            files_compared+=1
            if b['files'].get(f)!=r['files'].get(f):differences.append([k,f])
        if b['input_sha256']!=r['input_sha256']:differences.append([k,'raw'])
    record('trade_'+reference+'_exact',[],differences,cases=cases,files_compared=files_compared)
old=load('contracts_before.json');new=load('contracts_after.json')
if old and new:
    record('watch_plan_exact',old['plans'],new['plans'],command_count=len(old['plans']))
    for kind in ('ohlc','trend'):
        aa=old[kind]['digests'];bb=new[kind]['digests']
        record(kind+'_observation_count',len(aa),len(bb))
        record(kind+'_exact_mismatch_count',0,sum(x!=y for x,y in zip(aa,bb)),observations=len(aa))
else:skip('plan_ohlc_trend','file missing')
old=load('hma_tick_before.json');new=load('hma_tick_after.json')
if old and new:
    differences=[]
    for b,r in zip(old['rows'],new['rows']):
        for field in ('input_sha256','plan','files','alerts','status'):
            if b[field]!=r[field]:differences.append([r['direction'],r['filter_on'],field])
        if r['status']!='PASS' or not r.get('alerts',0):differences.append([r['direction'],r['filter_on'],'expected nonempty successful result'])
    record('hma_tick_nonempty_exact',[],differences,cases=len(new['rows']),alert_counts=[r.get('alerts') for r in new['rows']])
else:skip('hma_tick_nonempty_exact','file missing')
# Exact native row data include float bits and provenance, not only final bands.
for family in ('PRICE','RSI','STO','DI'):
    d=load(f'stage2_{family}_long.json') or load(f'native_{family}.json')
    if not d:skip('native_'+family,'file missing');continue
    for c in d['cases']:
        bad=c.get('mismatch_count',c.get('mismatches'))
        record('native_'+family+'_'+str(c.get('case'))+'_'+str(c.get('precision')),0,bad)
old=load('stage3_before_long.json') or load('oz_before.json');new=load('stage3_after_long.json') or load('oz_after.json')
if old and new:
    for b,r in zip(old['profiles'],new['profiles']):
        aa=b['observation_digests'];bb=r['observation_digests']
        record('OZ_'+b['validation_mode']+'_'+b['trigger_mode']+'_count',len(aa),len(bb))
        record('OZ_'+b['validation_mode']+'_'+b['trigger_mode']+'_exact',0,sum(x!=y for x,y in zip(aa,bb)),observations=len(aa))
else:skip('oz_exact','file missing')
for n in (1,2,3):
    suffix='' if n==1 else f'_repeat{n}'
    old=load(f'stage4_before{suffix}.json') or load(f'ipc_before_{n}.json');new=load(f'stage4_after{suffix}.json') or load(f'ipc_after_{n}.json')
    if not old or not new:skip(f'ipc_{n}_exact','file missing');continue
    dif=[]
    for b,r in zip(old['rows'],new['rows']):
        for field in ('input_sha256','ordered_output_sha256','finish','observations'):
            if b[field]!=r[field]:dif.append([r['mode'],field])
    record(f'ipc_{n}_exact',[],dif,scope='unavailable native-band IPC fixture, not positive OZ')
# Keep large expected/actual plans in contract files, not in this small ledger.
for r in results:
    if r['test']=='watch_plan_exact':
        same=r['status']=='PASS';r['expected']='Exact equality of 24 command results including rejected input';r['actual']='exact match' if same else 'DIFFERENCE: inspect contracts_before/after.json'
summary={'scope':'exact checks on supplied/synthetic fixtures, NOT Windows/real broker/native terminal certification',
 'counts':{s:sum(r['status']==s for r in results) for s in ('PASS','FAIL','SKIP')},'checks':results}
Path(a.out).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(summary['counts']);sys.exit(1 if summary['counts']['FAIL'] else 0)
