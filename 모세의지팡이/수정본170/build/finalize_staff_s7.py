"""Seal successful S7 behavior as the next baseline; never modify S0–S6."""
import datetime as dt,sys
from staff_s7_evidence import *
from finalize_staff_s1 import cases
from run_staff_s7_behavior import external_non_wonbi,evidence_compare
names=['parity_240','weekday_1200','weekend_1200','actual_actual','actual_btc']
scenarios=[]
for name in names:
    r=read(OUT/name/'summary.json');assert r['passed'],name
    previous=read(OUT/name/'before_live.json')['evidence']
    current=read(OUT/name/'after_live.json')['evidence']
    projection=evidence_compare(external_non_wonbi(previous),external_non_wonbi(current))
    assert projection['equal'],(name,projection)
    write(OUT/name/'non_wonbi_policy_comparison.json',projection)
    r['non_wonbi_S6']=projection
    scenarios.append(dict(name=name,**r))
assert scenarios[0]['counts']['after_live']['final_alerts']>0
assert sum(x['counts']['after_live']['condition_deliveries'] for x in scenarios if x['input']=='actual_MT5')>0
behavior={'passed':True,'scenarios':scenarios,
    'final_alerts':sum(r['counts']['after_live']['final_alerts'] for r in scenarios),
    'condition_deliveries':sum(r['counts']['after_live']['condition_deliveries'] for r in scenarios),
    'changed_notifications_removed':sum(r['changed_notifications_removed'] for r in scenarios),
    'changed_notifications_added':sum(r['changed_notifications_added'] for r in scenarios),
    'counting':'Each scenario S7 LIVE counted once; SPECIAL is a subset of finals. Finals and Telegram condition deliveries can overlap.',
    'coverage_limit':'Bounded intervals on several dates, including actual BTC weekend; not continuous multi-day playback.'}
write(OUT/'external_behavior_summary.json',behavior)

sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
chain=verify_sources();registered=read(OUT/'integrity_registration.json')
write(OUT/'final_integrity_verify.json',{'matches_registered_diagnostics':chain['integrity_errors']==registered['remaining_errors'],
    'integrity_errors':chain['integrity_errors'],'units':chain['units']})
inventory=read(ROOT/'build/part1_immutable_sha256.json')
actual={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(OUT/'immutable_verify.json',{'unchanged':actual==inventory,'files':len(actual),
    'differences':sorted(k for k in actual.keys()|inventory.keys() if actual.get(k)!=inventory.get(k))})
build=read(OUT/'ea_build_identity.json');compiled=read(sorted(OUT.glob('compile_attempt_*/result.json'))[-1])
for r in compiled:assert r['source_sha256']==sha(ROOT/'Part1/program/MT5'/r['source'])
live=read(OUT/'live_BTC/result.json');inputs=read(OUT/'mt5_wonbi_verification.json')
for row in live['wonbi_samples'].values():assert row[1]>=row[0]>=row[2] and row[3]==3.
tests=read(OUT/'baseline_signature_compare.json');scope=read(OUT/'scope_verification.json')
gates={'external_behavior_all_scenarios':behavior['passed'],
    'MT5_wonbi_sigma3_and_2_5':inputs['passed'],
    'G1_no_new_failures':tests['pass'],
    'S7_new_tests':tests['s7_counts']=={'PASSED':20},
    'actual_compile_zero_errors_warnings':all(x['success'] for x in compiled),
    'actual_BTC_live_wonbi':live['actual_live_received'] and len(live['wonbi_samples'])==19,
    'scope_and_user_config':scope['passed'] and scope['user_config_sha256']==sha(ROOT/'Part1/program/config.txt'),
    'hash_chain':registered['chain_valid'] and not registered['added_diagnostics'] and chain['integrity_errors']==registered['remaining_errors'],
    'immutable_inventory':actual==inventory,
    'original_S0_S6_expected_policy_unchanged':read(OUT/'frozen_guard.json')['unchanged'],
    'Part3_frozen':read(OUT/'part3_legacy_references.json')['Part3_all_files_hash_identical'],
    'performance_reference_only':read(OUT/'performance_reference.json')['runs_per_variant']==1 and not read(OUT/'performance_reference.json')['adjudicated']}
assert all(gates.values()),gates

baseline=OUT/'baseline_S7';baseline.mkdir(exist_ok=False)
files={}
for name in names:
    for label in ('after_live','after_backtest'):
        src=OUT/name/(label+'.json');dest=baseline/(name+'_'+label+'.json');dest.write_bytes(src.read_bytes())
        files[dest.relative_to(ROOT).as_posix()]=sha(dest)
for folder in OUT.glob('*_sigma*_v2'):
    for p in folder.glob('capture_*/*'):
        if p.is_file():files[p.relative_to(ROOT).as_posix()]=sha(p)
for name in ('parity_240','weekday_1200','weekend_1200'):
    for p in (OUT/name/'MSP3').iterdir():
        if p.is_file():files[p.relative_to(ROOT).as_posix()]=sha(p)
write(baseline/'manifest.json',{'schema':'staff-s7-frozen-baseline/1','files':files,
    'basis':'MT5 Wonbi; old Python Wonbi is not an oracle',
    'notification_change_authorization':'원비 원본 MT5 전환에 따른 승인된 기준 변경',
    'changed_removed':behavior['changed_notifications_removed'],'changed_added':behavior['changed_notifications_added'],
    'S0_preserved_as_reference':True})
write(OUT/'status.json',{'stage':'S7','revision':'수정본14','s7_complete':True,'gates':gates,
    'baseline':str(baseline.relative_to(ROOT)),'baseline_manifest_sha256':sha(baseline/'manifest.json'),
    'schema_id':build['schema_id'],'ea_build_hash':build['build_sha256'],'s8_started':False,
    'performance_status':'REFERENCE_ONLY','Part2_old_general_suites_executed':False,
    'S0_goldens_expected_modified':False,'original_revision_modified':False,'Part3_modified':False})
provenance();print('S7 complete; baseline frozen:',len(files),'files')
