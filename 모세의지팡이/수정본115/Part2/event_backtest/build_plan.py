"""Read-only recording proposal. The executor requires its approval token."""
from pathlib import Path
import json
from .settings import PROGRAM,digest,file_hash,fragments,overlap_start,milliseconds
from .build_compat import compatible_hashes
from .catalog_plan import coverage,missing_pieces
from .calendar import warm_start

class ConfirmationRequired(RuntimeError):
    def __init__(self,plan):self.plan=plan;super().__init__('녹화 계획 확인 필요: GUI 확인 또는 CLI --yes')

def current_build(warehouse):
    from .recording import source_hash
    folder=Path(warehouse)/'builds'/source_hash();ready=folder/'ready.json'
    if ready.is_file():
        data=json.loads(ready.read_text('utf-8'))
        if all((folder/n).is_file() and file_hash(folder/n)==h for n,h in data['files'].items()):
            return file_hash(folder/'THE_STAFF_OF_MOSES.ex5')
    return file_hash(PROGRAM/'MT5/THE_STAFF_OF_MOSES.ex5')

def schema_id():
    import sys
    sys.path.insert(0,str(PROGRAM));from staff_schema import WIRE_SCHEMA_ID
    return WIRE_SCHEMA_ID

def make_plan(scenario,catalog,*,rebuild=False,today=None):
    import datetime as dt
    from .periods import limit_end,adjustment,available_plan
    today=today or dt.datetime.now(dt.timezone.utc).date()
    requested=scenario
    end=limit_end(scenario,today)
    scenario={**scenario,'end':end}
    ea=current_build(catalog.root);schema=schema_id();mode='BAR' if scenario['mode']=='BAR' else 'TIMER'
    approved_builds=compatible_hashes(ea)
    start=overlap_start(scenario['start'],scenario['overlap_trading_days'])
    prior=[c for c in catalog.available(scenario['symbol'],mode)
           if c['ea_build_hash'] in approved_builds and c['schema_id']==schema
           and int(c.get('timer_ms',1000))==int(scenario['timer_ms']) and not c.get('history_missing')]
    prior.sort(key=lambda c:c.get('recorded_at',''),reverse=True)
    prior.sort(key=lambda c:c['ea_build_hash']!=ea)
    if scenario.get('available_only'):
        if rebuild or scenario.get('build_only'):raise ValueError('보유 데이터만 사용하는 백테스트와 데이터 구축·재구축을 함께 선택할 수 없습니다.')
        selected,periods,excluded=available_plan(scenario,catalog,prior,end)
        plan={'symbol':scenario['symbol'],'mode':mode,'ea_build_hash':ea,'schema_id':schema,
              'rebuild':False,'record':[],'reuse':selected,'convert':[],
              'available_periods':periods,'excluded_periods':excluded,
              'period_adjustment':adjustment(requested,periods[0]['start'],periods[-1]['end'],'AVAILABLE_ONLY'),
              'estimate':{'recording_seconds':0,'temporary_msp3_bytes':0,'delta_storage_bytes':0,'recommended_free_bytes':0}}
        if end<requested['end']:plan['excluded_periods'].append({'start':end,'end':requested['end'],'reason':'FUTURE','message':'아직 완료되지 않은 날짜입니다.'})
        plan['approval_token']=digest({k:v for k,v in plan.items() if k!='reuse'})
        return plan
    checked={};warmup=None
    while True:
        pieces=list(fragments(start,scenario['end'],today=today))
        first,last=pieces[0]['start'],pieces[-1]['end']
        candidates={}
        for row in prior:
            if row['end']<=first or row['start']>=last:continue
            key=row['capture_id']
            if not rebuild and key not in checked:checked[key]=catalog.find_capture(key)
            # Rebuild never reuses old bytes. Its calendar metadata is only a
            # planning hint; prepare checks the newly recorded observed days
            # and asks for a new approval if they still require an earlier span.
            if rebuild or checked[key]:candidates.setdefault((row['start'],row['end']),row)
        selected=[];missing=[]
        if rebuild:
            missing=pieces
        else:
            for piece in pieces:
                chosen,absent=missing_pieces(piece,list(candidates.values()))
                selected.extend(chosen);missing.extend(absent)
        selected=list({c['capture_id']:c for c in selected}.values())
        days=int(scenario['overlap_trading_days'])
        if not days:break
        _,unknown=coverage(list(candidates.values()),first,scenario['start'])
        if unknown:break  # Newly requested recordings must establish their calendar first.
        calendar_captures=list(candidates.values()) if rebuild else selected
        try:
            warm_start(scenario['start'],days,calendar_captures)
            break
        except ValueError:
            observed={day for c in calendar_captures for day in c.get('observed_days',[])
                      if c['start']<=day<c['end'] and day<scenario['start']}
            previous=overlap_start(first,max(1,days-len(observed)))
            if previous>=first:raise ValueError('준비 거래일 추가 계획의 범위가 확장되지 않았습니다.')
            warmup={'required_days':days,'observed_days':len(observed),
                    'previous_start':first,'additional_start':previous}
            start=previous
    reused=[c for c in selected if c.get('storage') in ('MSD1','MSD2') and c.get('reconstruction_verified')]
    conversions=[c for c in selected if c not in reused]
    days=sum((milliseconds(p['end'])-milliseconds(p['start']))/86400000 for p in missing)
    # Measured XAU September 2025 old48 BAR: 203.50s, 27.711GB; new50
    # day TIMER: 182.25s, 16.850GB. Estimates explicitly separate conversion.
    seconds=days*(203.4958869/30*1.2 if mode=='BAR' else 182.25184)
    raw=days*(27710813786/30*50/48 if mode=='BAR' else 16850164449)
    final=days*(130994446/30*50/48) if mode=='BAR' else None
    plan={'symbol':scenario['symbol'],'mode':mode,'ea_build_hash':ea,'schema_id':schema,
        'rebuild':bool(rebuild),'record':missing,'reuse':reused,'convert':conversions,
        'estimate':{'recording_seconds':seconds,'temporary_msp3_bytes':int(raw),
            'delta_storage_bytes':int(final) if final is not None else None,
            'recommended_free_bytes':int(raw*2),'basis':'실측 XAU 2025-09 BAR / 2026-09-25 TIMER; 변환·검증 별도, NAS/BTC 추정 오차 가능'}}
    if warmup:plan['warmup_extension']=warmup
    if end!=requested['end']:
        plan['period_adjustment']=adjustment(requested,requested['start'],end,'COMPLETED_DATES')
        plan['excluded_periods']=[{'start':end,'end':requested['end'],'reason':'FUTURE','message':'아직 완료되지 않은 날짜입니다.'}]
    plan['approval_token']=digest({k:v for k,v in plan.items() if k!='reuse'})
    return plan

def require_approval(plan,approved_token):
    if plan['record'] and approved_token!=plan['approval_token']:raise ConfirmationRequired(plan)
