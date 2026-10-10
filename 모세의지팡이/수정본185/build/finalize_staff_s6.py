"""Final S6 record: external results govern; retain all original failures."""
import datetime as dt,sys
from staff_s6_evidence import *
from finalize_staff_s1 import cases

case_names=('parity_240_S6','weekday_XAU_1200','weekend_BTC_1200_configured',
            'actual_behavior_240','actual_BTC_weekend_600_configured')
behavior=[]
for name in case_names:
    summary=read(OUT/name/'summary.json');rows={}
    for side in ('before_live','after_live','after_backtest'):
        ev=read(OUT/name/(side+'.json'))['evidence']
        registrations=[];alerts=[]
        for at,chat,text in ev['telegram']:
            record={'time':at,'recipient':chat,'text':text}
            (registrations if '감시' in text or '개인전략 등록' in text else alerts).append(record)
        assert len(registrations)==11,(name,side,registrations)
        assert not any('전략을 이해하지 못했습니다' in x['text'] for x in alerts)
        assert 'error' not in ev['source_health']
        rows[side]={'final_alerts':len(ev['finals']),'special_alerts':len(ev['special']),
            'registration_deliveries':len(registrations),'condition_alert_deliveries':len(alerts),
            'condition_alerts':alerts,'total_telegram_deliveries':len(ev['telegram'])}
    behavior.append({'name':name,'equal':summary['equal'],'symbol':summary['symbol'],
        'start_utc':dt.datetime.fromtimestamp(summary['start'],dt.timezone.utc).isoformat(),
        'seconds':summary['seconds'],'input':summary['input'],'counts':rows,
        'comparison_fields':summary['mandatory_fields']})
assert behavior[0]['counts']['before_live']['final_alerts']>0
assert sum(b['counts']['before_live']['condition_alert_deliveries'] for b in behavior if b['input']=='ACTUAL_MT5_STRATEGY_TESTER')>0
write(OUT/'external_behavior_summary.json',{'all_equal':all(x['equal'] for x in behavior),
    'scenarios':behavior,'unique_scenario_final_alerts':sum(x['counts']['before_live']['final_alerts'] for x in behavior),
    'unique_scenario_condition_deliveries':sum(x['counts']['before_live']['condition_alert_deliveries'] for x in behavior),
    'counting':'Per scenario baseline once, not three copies; SPECIAL is a subset of final alerts, and final delivery can overlap Telegram count.',
    'coverage_limit':'Five bounded intervals on multiple dates, not a continuous multi-day backtest. Actual XAU final alerts are zero; two Watch deliveries and state transitions are compared.',
    'invalid_zero_runs_excluded':['weekend_BTC_1200','actual_BTC_weekend_600']})

sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
chain=verify_sources();registered=read(OUT/'integrity_registration.json')
write(OUT/'final_integrity_verify.json',{'matches_registered_diagnostics':chain['integrity_errors']==registered['remaining_errors'],
    'integrity_errors':chain['integrity_errors'],'units':chain['units']})
inventory=read(ROOT/'build/part1_immutable_sha256.json')
current={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(OUT/'immutable_verify.json',{'unchanged':inventory==current,'files':len(current),
    'differences':sorted(k for k in current.keys()|inventory.keys() if current.get(k)!=inventory.get(k))})
delta=provenance();write(OUT/'existing_test_changes.json',[r for r in delta if Path(r['file']).name.startswith('test_')])
tests=read(OUT/'baseline_signature_compare.json');inputs=read(OUT/'decision_input_samples.json')
wire=cases(OUT/'targeted_final.xml');wire={k:v for k,v in wire.items() if k.startswith('test_staff_s6::')}
compile_reports=sorted(OUT.glob('compile_attempt_*/result.json'))
compiled=read(compile_reports[-1]);build=read(OUT/'ea_build_identity.json')
for record in compiled:
    assert record['source_sha256']==sha(ROOT/'Part1/program/MT5'/record['source'])
live=read(OUT/'live_BTC/result.json');perf=read(OUT/'performance_reference.json')
config=read(OUT/'user_symbol_validation.json');scope=read(OUT/'scope_verification.json')
gates={'G1_no_new_external_regression':tests['pass'],
    'G1_Part2_824_preserved':not read(OUT/'part2_collection_scope.json')['missing'],
    'G2_representative_decision_inputs':inputs['equal'],
    'G3_240_and_S0':behavior[0]['equal'],
    'expanded_actual_and_weekend_external_behavior':all(x['equal'] for x in behavior),
    'G4_wire':len(wire)==14 and all(v['status']=='PASSED' for v in wire.values()),
    'actual_MT5_compile':all(x['success'] for x in compiled),
    'actual_BTC_live_v2':live['actual_live_received'],
    'user_symbols':config['passed'] and config['source_sha256']==sha(ROOT/'Part1/program/config.txt'),
    'integrity_chain':registered['chain_valid'] and not registered['added_diagnostics'] and chain['integrity_errors']==registered['remaining_errors'],
    'immutable_inventory':inventory==current,
    'original_and_goldens_frozen':read(OUT/'frozen_guard.json')['unchanged'],
    'Part3_frozen':read(OUT/'part3_legacy_references.json')['Part3_all_files_hash_identical'],
    'wonbi_clients_and_calculations_unchanged':scope['passed'],
    'performance_reference_recorded_once':perf['runs_per_variant']==1 and not perf['adjudicated']}
status={'stage':'S6','revision':'수정본13','s6_complete':all(gates.values()),'gates':gates,
    'verification_policy':'검증정책_S6이후.md','internal_representation_failures':'diagnostic; original records retained',
    'performance_status':'REFERENCE_ONLY','S5_user_approved_exceptions_preserved':True,
    's7_started':False,'golden_expected_updated':False,'wonbi_changed':False,'Part3_modified':False,
    'user_config_preserved':True,'schema_id':build['schema_id'],'ea_build_hash':build['build_sha256']}
write(OUT/'status.json',status)
print(status)
assert status['s6_complete'],status
