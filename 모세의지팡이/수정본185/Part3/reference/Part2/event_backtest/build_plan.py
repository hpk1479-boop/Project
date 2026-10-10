"""Read-only recording proposal. The executor requires its approval token."""
from pathlib import Path
import json
from .settings import PROGRAM,digest,file_hash,fragments,overlap_start,milliseconds

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
    ea=current_build(catalog.root);schema=schema_id();mode='BAR' if scenario['mode']=='BAR' else 'TIMER'
    start=overlap_start(scenario['start'],scenario['overlap_trading_days'])
    pieces=list(fragments(start,scenario['end'],today=today));prior=catalog.available(scenario['symbol'],mode)
    reused=[];missing=[];conversions=[]
    for piece in pieces:
        matching=[c for c in prior if all(c.get(k)==v for k,v in piece.items()) and c['ea_build_hash']==ea
            and c['schema_id']==schema and int(c.get('timer_ms',1000))==int(scenario['timer_ms'])
            and not c.get('history_missing')]
        chosen=next((c for c in matching if catalog.find_capture(c['capture_id'])),None)
        if chosen and not rebuild:
            if chosen.get('storage') in ('MSD1','MSD2') and chosen.get('reconstruction_verified'):reused.append(chosen)
            else:conversions.append(chosen)
        else:missing.append(piece)
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
    plan['approval_token']=digest({k:v for k,v in plan.items() if k!='reuse'})
    return plan

def require_approval(plan,approved_token):
    if plan['record'] and approved_token!=plan['approval_token']:raise ConfirmationRequired(plan)
