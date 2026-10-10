"""S6-authorized transport scope; preserve the assertions behind old S1/S5 guards."""
import ast,sys
from staff_s6_evidence import *
allowed={'THE STAFF OF MOSES.py','staff_schema.py','MT5/THE_STAFF_OF_MOSES.mq5',
         'MT5/STAFF_Wire_V2.mqh','MT5/STAFF_Wire_Schema.mqh'}
program=ROOT/'Part1/program';old=SOURCE/'Part1/program';checked=[]
for p in old.rglob('*'):
    if p.is_file() and p.suffix in {'.py','.mq5','.mqh'}:
        rel=p.relative_to(old).as_posix()
        if rel not in allowed:
            assert sha(p)==sha(program/rel),rel
            checked.append(rel)
for p in program.rglob('*'):
    if p.is_file() and p.suffix in {'.py','.mq5','.mqh'}:
        rel=p.relative_to(program).as_posix()
        assert (old/rel).exists() or rel in allowed,rel
def node(path,name):
    return next(n for n in ast.parse(path.read_bytes()).body if getattr(n,'name',None)==name)
for name in ('WonbiState',):
    assert ast.dump(node(old/'THE STAFF OF MOSES.py',name))==ast.dump(node(program/'THE STAFF OF MOSES.py',name))
assert ast.dump(node(old/'staff_schema.py','legacy_frame'))==ast.dump(node(program/'staff_schema.py','legacy_frame'))
for part in ('Part2','Part3'):
    name='calculations/common.py'
    assert ast.dump(node(S0/'baseline_input'/part/name,'add_wonbi_features'))==ast.dump(node(ROOT/part/name,'add_wonbi_features'))
tree=ast.parse((program/'THE STAFF OF MOSES.py').read_bytes())
forbidden={'pandas','indicator_facts','monitor_OZ','strategy_FVG','watch_ma','watch_ma_features','staff_compat'}
for n in ast.walk(tree):
    imports=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else []
    assert not forbidden.intersection(x.split('.')[0] for x in imports)
removed={'get','_legacy_frame','add_wonbi_features','apply_requested_features','validate_mt5_snapshot'}
assert not removed.intersection(n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef))
assert sha(SOURCE/'Part2/validation_suite/test_oz_fvg_optimization.py')==sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py')
write(OUT/'scope_verification.json',{'passed':True,'unchanged_program_files':checked,
    'allowed_transport_files':sorted(allowed),'wonbi_class_and_formulas_unchanged':True,
    'legacy_frame_normalizer_unchanged':True,'server_no_pandas_or_derived_imports':True,
    'removed_server_calculations_still_absent':True,'existing_14_test_source_unchanged':True,
    'old_guard_failures':'S1/S4/S5 exact EA/server-AST freeze assertions are diagnostic under user S6 policy. No old assertions were deleted.'})
print('S6 scope PASS:',len(checked),'client/strategy/indicator source files unchanged')
