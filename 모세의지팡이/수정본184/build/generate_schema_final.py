"""Generate the final registry header; retain every earlier stage's evidence."""
import hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program';folder=P/'MT5'
spec=importlib.util.spec_from_file_location('schema_generator',P/'staff_schema.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
h=hashlib.sha256();files=[]
for path in sorted(folder.iterdir()):
    if path.suffix not in ('.mq5','.mqh') or path.name=='STAFF_Wire_Schema.mqh':continue
    name=path.name.encode();raw=path.read_bytes()
    h.update(len(name).to_bytes(4,'little')+name+len(raw).to_bytes(8,'little')+raw)
    files.append({'name':path.name,'sha256':hashlib.sha256(raw).hexdigest()})
build=h.hexdigest()
(folder/'STAFF_Wire_Schema.mqh').write_text(module.generated_mqh(build),encoding='utf-8')
(ROOT/'검증결과/schema_cleanup/ea_build_identity.json').write_text(json.dumps({'build_sha256':build,
    'schema_id':module.WIRE_SCHEMA_ID,'schema_columns':module.PIPE_VALUE_COLUMNS,'inputs':files},indent=2),encoding='utf-8')
print('schema',hex(module.WIRE_SCHEMA_ID),'build',build)
