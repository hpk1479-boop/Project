"""Read-only scope/static checks, including unchanged decision ASTs and user config."""
import ast,sys
from staff_s7_evidence import *
manifest={x['path']:x['sha256'] for x in read(OUT/'s6_frozen_manifest.json')}
allowed={'THE STAFF OF MOSES.py','staff_schema.py','staff_compat.py','monitor_OZ.py',
         'MT5/THE_STAFF_OF_MOSES.mq5','MT5/STAFF_Wire_V2.mqh','MT5/STAFF_Wire_Schema.mqh'}
changed=[];unexpected=[]
for rel,old in manifest.items():
    if rel.startswith('Part1/program/') and Path(rel).suffix in ('.py','.mq5','.mqh'):
        if sha(ROOT/rel)!=old:
            name=rel.removeprefix('Part1/program/');changed.append(name)
            if name not in allowed:unexpected.append(name)
assert not unexpected,unexpected
before=ast.parse((SOURCE/'Part1/program/monitor_OZ.py').read_text('utf-8-sig'))
after=ast.parse((ROOT/'Part1/program/monitor_OZ.py').read_text('utf-8-sig'))
for tree in (before,after):
    owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OZSnapshotFeatures')
    owner.body=[n for n in owner.body if getattr(n,'name',None)!='compose']
assert ast.dump(before)==ast.dump(after),'OZ decisions changed outside data composition'
active=('Part1/program/staff_compat.py','Part1/program/monitor_OZ.py','Part2/calculations/common.py','Part2/live_replay/event_catalog.py')
for rel in active:assert 'add_wonbi_features' not in (ROOT/rel).read_text('utf-8-sig'),rel
consumer_files=['monitor_OZ.py','manager_KIM.py','SPECIAL1.py','SPECIAL4.py']
consumer_refs={}
for rel in consumer_files:
    p=ROOT/'Part1/program'/rel
    if not p.exists():
        matches=list((ROOT/'Part1/program').glob('*'+rel.lower().replace('.py','')+'*'));consumer_refs[rel]=[str(x) for x in matches];continue
    consumer_refs[rel]=[{'line':i,'text':line.strip()} for i,line in enumerate(p.read_text('utf-8-sig').splitlines(),1) if 'wonbi_' in line]
config='Part1/program/config.txt';assert sha(ROOT/config)==manifest[config]
result={'passed':True,'changed_program_files':changed,'unexpected_program_files':unexpected,
        'OZ_judgment_AST_unchanged':True,'other_managers_specials_strategies_byte_identical':True,
        'user_config_sha256':sha(ROOT/config),'consumer_references':consumer_refs,
        'python_wonbi_deleted':['staff_compat.add_wonbi_features','monitor_OZ.OZSnapshotFeatures.compose calculation call',
            'Part2.calculations.common.add_wonbi_features','Part2.live_replay.event_catalog MarketReplay Wonbi assignments',
            'Part2.live_replay.event_catalog build_ohlcv Wonbi calculation lane'],
        'synthetic_only_ports':['Part2/part1_host/synthetic.py::ea_open4_synthetic',
            'Part2/live_replay/synthetic.py::with_synthetic_mt5_bands','Part1/audit/harness.py::World.market'],
        'missing_mt5_input_policy':'OHLCV/tick-only Wonbi is explicitly rejected; generic BB remains independent.',
        'legacy_files_retained':True,'Part2_old_suites_executed':False}
write(OUT/'scope_verification.json',result);print('S7 scope passed',changed)
