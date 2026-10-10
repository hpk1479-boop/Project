"""Record every old search hit and classify the remaining production transport."""
import ast
from staff_s5_evidence import *

before=read(OUT/'legacy_callers_before.json')
decisions={
 'Part2/pit/adapters/legacy_staff.py':'Unused prototype depended on absent replay package; removed legacy server loader/call, explicit E_SNAPSHOT_API_REQUIRED at construction and request; no current callers',
 'Part2/live_replay/event_catalog.py':'STAFF feature aliases -> unchanged owner functions; raw cache.get -> client legacy_frame(snapshot)',
 'Part2/part1_host/runtime.py':'strict legacy transport handler preserved; verification data requests use explicit staff_data_request SnapshotClient + StaffCompat',
 'Part2/staff_golden/record.py':'all data requests -> staff_data_request; SOURCE_HEALTH remains control',
 'Part2/staff_golden/actual.py':'all data requests -> staff_data_request; SOURCE_HEALTH remains control',
 'Part2/staff_golden/benchmark.py':'data requests -> complete client request boundary',
 'Part2/staff_golden/parity.py':'only SOURCE_HEALTH data-independent control and timing spy remain',
 'Part1/program/THE STAFF OF MOSES.py':'server calculation/cache removed; pickle replies only controls or explicit legacy error',
 'Part1/program/manager_KIM.py':'SET_WONBI_SIGMA, SOURCE_HEALTH and alert replies only; no legacy data request',
 'Part1/program/monitor_OZ.py':'strategy event emission to manager only',
 'Part1/program/strategy_FVG.py':'strategy event emission to manager only',
 'Part1/program/strategy_SWEEP.py':'strategy event emission to manager only',
 'Part1/program/strategy_INDICATOR.py':'strategy event emission to manager only',
 'Part1/audit/harness.py':'test transport preserves strict legacy controls; new explicit data_request/raw_frame clients',
}
for row in before:
    row['resolution']=('Frozen fixture, not an executable current client; unchanged' if row['category']=='frozen fixture' else
        'Existing assertions retained at Snapshot/client boundary; complete S4 program oracle for S3/S4 comparisons' if row['category']=='test' else
        decisions.get(row['file'],'Inspected source/helper; see text and current ownership in source delta'))
remaining=[]
for base in ('Part1/program','Part2'):
    for p in (ROOT/base).rglob('*.py'):
        if p.name.startswith('test_') or any(x.startswith(('.venv','tmp_')) for x in p.parts):continue
        tree=ast.parse(p.read_bytes())
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {'send_pyobj','_staff_handle'}:
                remaining.append({'file':p.relative_to(ROOT).as_posix(),'line':n.lineno,'call':ast.unparse(n),
                                  'resolution':decisions.get(p.relative_to(ROOT).as_posix(),'golden serialization/control helper')})
write(OUT/'legacy_callers_review.json',{'scope':['Part1','Part2'],'Part3':'frozen/excluded',
    'before_hits':before,'remaining_transport_calls':remaining,'legacy_production_data_callers':[],
    'verification':'test_staff_s5 static AST scan plus full Part1/Part2 text inventory; excludes immutable fixtures and tests as callers'})
print(len(before),'old hits;',len(remaining),'remaining transport/control calls')
