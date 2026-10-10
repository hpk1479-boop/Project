"""Record actual gate outcomes; never turn failed gates into implicit approval."""
import sys
from staff_s8_evidence import *
if (OUT / 'baseline_S8/manifest.json').exists():
    raise SystemExit('S8 is approved and frozen; historical finalizer must not overwrite it')
names=['parity_240','weekday_1200','weekend_1200','actual_actual','actual_btc']
scenarios=[dict(name=n,**read(OUT/n/'summary.json')) for n in names]
behavior={'passed':all(s['passed'] for s in scenarios),'scenarios':scenarios,
    'final_alerts':sum(s['counts']['after_live']['final_alerts'] for s in scenarios),
    'condition_deliveries':sum(s['counts']['after_live']['condition_deliveries'] for s in scenarios)}
write(OUT/'external_behavior_summary.json',behavior)
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
chain=verify_sources();registered=read(OUT/'integrity_registration.json')
write(OUT/'final_integrity_verify.json',{'matches_registered_diagnostics':chain['integrity_errors']==registered['remaining_errors'],
    'integrity_errors':chain['integrity_errors'],'units':chain['units']})
actual={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
inventory=read(ROOT/'build/part1_immutable_sha256.json')
write(OUT/'immutable_verify.json',{'unchanged':actual==inventory,'files':len(actual)})
compiled=read(sorted(OUT.glob('compile_attempt_*/result.json'))[-1])
assert all(r['source_sha256']==sha(ROOT/'Part1/program/MT5'/r['source']) for r in compiled)
live=read(OUT/'live_BTC/result.json');tests=read(OUT/'baseline_signature_compare.json')
perf=read(OUT/'performance/comparison.json');scope=read(OUT/'scope_verification.json')
ea=read(OUT/'ea_output_equality.json');diag=read(OUT/'ea_diagnosis.json')
gates={'external_behavior_5_cases':behavior['passed'],'EA_separate_capture_values_identical':ea['passed'],
    'EA_same_observation_full_row_probe':diag['live_fast_vs_full_same_observation']['differences']==0,
    'G1_no_new_failures':tests['pass'],'S8_and_S7_tests':tests['new_tests_counts']=={'PASSED':32},
    'compile_zero_errors_warnings':all(r['success'] for r in compiled),
    'BTC_live_v2':live['actual_live_received'] and len(live['wonbi_samples'])==19,
    'frozen_performance_gate':perf['pass'],'scope_and_config':scope['passed'] and scope['config_unchanged'],
    'hash_chain':registered['chain_valid'] and not registered['added_diagnostics'] and chain['integrity_errors']==registered['remaining_errors'],
    'immutable_list':actual==inventory,'original_and_frozen_inputs':read(OUT/'frozen_guard.json')['unchanged'],
    'Part3_frozen':scope['Part3_all_files_hash_identical']}
status={'stage':'S8','revision':'수정본15','implementation_complete':True,'verification_complete':True,
    's8_complete':all(gates.values()),'gates':gates,'failed_gates':[k for k,v in gates.items() if not v],
    'approved_exceptions':[],'event_engine_started':False,'Part2_general_suites_executed':False,
    'S0_S7_baselines_expected_modified':False,'performance_policy_modified':False,
    'baseline_in_force':'검증결과/staff_s7/baseline_S7/manifest.json',
    'new_baseline_frozen':False,'reason':'S8 has no authority to waive failed gates or replace S7 baseline'}
write(OUT/'status.json',status);provenance();print(json.dumps(status,ensure_ascii=False,indent=2))
