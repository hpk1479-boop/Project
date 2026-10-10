"""One compile only. No terminal, tester, new captures, or S0 measurements."""
import hashlib,shutil,subprocess,sys
from event_e1_common import *
sys.path.insert(0,str(ROOT/'Part1/program'))
import staff_schema as schema
source=ROOT/'Part1/program/MT5'
h=hashlib.sha256()
for path in sorted(source.iterdir()):
    if path.suffix not in ('.mq5','.mqh') or path.name=='STAFF_Wire_Schema.mqh':continue
    name=path.name.encode();raw=path.read_bytes()
    h.update(len(name).to_bytes(4,'little')+name+len(raw).to_bytes(8,'little')+raw)
build=h.hexdigest()
(source/'STAFF_Wire_Schema.mqh').write_text(schema.generated_mqh(build),encoding='utf-8')
folder=OUT/'compile';folder.mkdir(exist_ok=False)
compiler=ROOT/'검증결과/staff_s8/mt5_terminal/metaeditor64.exe'
shutil.copy2(compiler,folder/compiler.name)
for path in source.iterdir():
    if path.suffix in ('.mq5','.mqh'):shutil.copy2(path,folder/path.name)
ea=folder/'THE_STAFF_OF_MOSES.mq5'
hidden=subprocess.STARTUPINFO();hidden.dwFlags|=subprocess.STARTF_USESHOWWINDOW;hidden.wShowWindow=0
run=subprocess.run([str(folder/compiler.name),'/portable','/compile:'+str(ea),'/inc:'+str(folder),'/log'],
    cwd=folder,startupinfo=hidden,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
raw=ea.with_suffix('.log').read_bytes();log=raw.decode('utf-16') if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else raw.decode('utf-8',errors='replace')
(folder/'compiler_log.txt').write_text(log,encoding='utf-8')
result={'passed':'0 errors, 0 warnings' in log and ea.with_suffix('.ex5').exists(),
    'source_sha256':sha(ea),'compiler_sha256':sha(compiler),'EA_build_hash':build,'schema_id':hex(schema.WIRE_SCHEMA_ID),
    'timer_default_ms':1000,'timer_is_input':True,'tester_executed':False,'new_capture_created':False,
    'dispatch_unchanged':True,'exit_code':run.returncode}
write(OUT/'compile_result.json',result);print(result,flush=True)
assert result['passed'],log
