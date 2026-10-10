import sys
from event_e1_common import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
before=verify_sources()['final_sha256'];rows=[]
changed=[ROOT/'Part1/program/indicator_facts.py',ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',
         ROOT/'Part1/program/MT5/STAFF_Wire_Schema.mqh',*sorted((ROOT/'Part1/program/event_engine').glob('*.py'))]
empty=hashlib.sha256(b'').hexdigest()
for path in changed:
    name=path.relative_to(ROOT/'Part1').as_posix()
    rows.append({'file':name,'before_sha256':before.get(name,empty),'after_sha256':sha(path)})
unit=ROOT/'Part1/audit/remediation/32-event-e1-skeleton';unit.mkdir(exist_ok=False)
write(unit/'changes.json',rows)
write(unit/'review.json',{'stage':'E1','authorization':'User: opt-in event skeleton only, unchanged polling, no E2',
    'no_production_strategy_migration':True,'no_Part2_Part3_dependency':True,'tests_network_blocked':True})
result=verify_sources();old=read(ROOT/'검증결과/staff_s8/final_integrity_verify.json')['integrity_errors']
record={'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
        'remaining_errors':result['integrity_errors'],'added_diagnostics':sorted(set(result['integrity_errors'])-set(old))}
write(OUT/'integrity_registration.json',record);assert record['chain_valid'] and not record['added_diagnostics'],record
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
print('E1 integrity registered',len(rows),'files; existing diagnostics',len(record['remaining_errors']),flush=True)
