"""EA build identity is SHA256 over length-framed filenames and source bytes.
Generated schema header is excluded to avoid a circular build hash.
"""
import hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
program=ROOT/'Part1/program';folder=program/'MT5'
spec=importlib.util.spec_from_file_location('schema_generator',program/'staff_schema.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
h=hashlib.sha256();files=[]
for path in sorted(folder.iterdir()):
    if path.suffix not in ('.mq5','.mqh') or path.name=='STAFF_Wire_Schema.mqh':continue
    name=path.name.encode();raw=path.read_bytes()
    h.update(len(name).to_bytes(4,'little')+name+len(raw).to_bytes(8,'little')+raw)
    files.append({'name':path.name,'sha256':hashlib.sha256(raw).hexdigest()})
build=h.hexdigest()
(folder/'STAFF_Wire_Schema.mqh').write_text(module.generated_mqh(build),encoding='utf-8')
(ROOT/'검증결과/staff_s8/ea_build_identity.json').write_text(json.dumps({'build_sha256':build,
    'schema_id':module.WIRE_SCHEMA_ID,'schema_columns':module.PIPE_VALUE_COLUMNS,'inputs':files},indent=2),encoding='utf-8')
print('schema',hex(module.WIRE_SCHEMA_ID),'build',build)
