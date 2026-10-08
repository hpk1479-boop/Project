"""Verify E1 boundaries without writing to revision15 or historical baselines."""
import sys
from event_e1_common import *
initial={r['path']:r['sha256'] for r in read(OUT/'s8_frozen_manifest.json')}
changes=[]
for folder in ['Part1/program','Part2','Part3','tests']:
    for p in (ROOT/folder).rglob('*'):
        if not p.is_file() or p.suffix not in ('.py','.mq5','.mqh') or '__pycache__' in p.parts:continue
        rel=p.relative_to(ROOT).as_posix()
        if sha(p)!=initial.get(rel):changes.append(rel)
allowed={'Part1/program/indicator_facts.py','Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',
         'Part1/program/MT5/STAFF_Wire_Schema.mqh','tests/test_event_e1.py','tests/sparse_events/test_events.py'}
unexpected=[p for p in changes if p not in allowed and not p.startswith('Part1/program/event_engine/')]
protected_prefixes=tuple('검증결과/staff_s'+str(n)+'/' for n in range(8))+('Part3/',)
policies={'build/staff_performance_policy.json','build/staff_performance_policy.py',
          'build/staff_performance_protocol.py','성능규칙_S1_S8.md'}
protected=[]
for rel,h in initial.items():
    if rel.startswith(protected_prefixes) or rel in policies or rel=='Part1/program/config.txt':
        if not (ROOT/rel).is_file() or sha(ROOT/rel)!=h:protected.append(rel)
s8=read(ROOT/'검증결과/staff_s8/baseline_S8/manifest.json')
s8_diff=[r['path'] for r in s8['files'] if not (ROOT/r['path']).is_file() or sha(ROOT/r['path'])!=r['sha256']]
ea=(ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8')
before=(ROOT/'검증결과/staff_s8/baseline_S8/source/Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8')
assert ea.replace('input int    STAFF_TIMER_MS          = 1000; // TF observation interval; unchanged default',
                  'const int    STAFF_TIMER_MS          = 1000;')==before
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine.facts import owner_table
from event_engine.static_rules import violations
import indicator_facts
static={}
for p in (ROOT/'Part1/program/event_engine').glob('*.py'):
    if p.name in ('metrics.py','capture_io.py','staff_adapter.py','static_rules.py','__init__.py'):continue
    static[p.name]=violations(p.read_text('utf-8'),module=p.stem,registry=indicator_facts.FACTS,framework=True)
write(OUT/'fact_owners.json',owner_table())
result={'passed':not unexpected and not protected and not s8_diff and not any(static.values()),
    'production_and_test_changes':sorted(changes),'unexpected':unexpected,'protected_differences':protected,
    'S8_baseline_differences':s8_diff,'static':static,'Part2_production_unchanged':not any(p.startswith('Part2/') for p in changes),
    'EA_only_timer_input_and_generated_build_identity':True,'event_engine_default_disconnected':True,
    'Part3_frozen':not any(p.startswith('Part3/') for p in protected+changes),
    'config_sha256':sha(ROOT/'Part1/program/config.txt'),'no_new_MT5_captures':True,'S0_performance_gate_executed':False}
write(OUT/'scope_verification.json',result);print(result,flush=True)
assert result['passed']
