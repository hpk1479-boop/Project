from staff_s8_evidence import *
before={r['path']:r['sha256'] for r in read(OUT/'s7_frozen_manifest.json')}
allowed={'Part1/program/THE STAFF OF MOSES.py','Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',
         'Part1/program/MT5/STAFF_Identity_Status.mqh','Part1/program/MT5/STAFF_Wire_Schema.mqh'}
changed=[];illegal=[]
for rel,h in before.items():
    if rel.startswith(('Part1/program/','Part2/','Part3/')) and Path(rel).suffix in ('.py','.mq5','.mqh','.txt') and '__pycache__' not in rel:
        if sha(ROOT/rel)!=h:
            changed.append(rel)
            if rel not in allowed:illegal.append(rel)
config='Part1/program/config.txt'
header=(ROOT/'Part1/program/MT5/STAFF_Wire_Schema.mqh').read_text('utf-8')
old=(SOURCE/'Part1/program/MT5/STAFF_Wire_Schema.mqh').read_text('utf-8')
strip=lambda t:'\n'.join(l for l in t.splitlines() if 'STAFF_EA_BUILD_HASH' not in l)
assert strip(header)==strip(old)
result={'passed':not illegal,'production_changes':changed,'unexpected_changes':illegal,
    'config_unchanged':sha(ROOT/config)==before[config],'user_config_sha256':sha(ROOT/config),
    'Wire_schema_structure_and_columns_unchanged':True,'Part3_all_files_hash_identical':all(sha(ROOT/p)==h for p,h in before.items() if p.startswith('Part3/')),
    'event_engine_started':False}
write(OUT/'scope_verification.json',result);assert result['passed'] and result['config_unchanged'] and result['Part3_all_files_hash_identical']
provenance();print('S8 scope: only four authorized production files changed')
