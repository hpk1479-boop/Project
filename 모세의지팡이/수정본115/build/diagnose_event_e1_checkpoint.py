"""Reproduce only the failed legacy checkpoint assertion; preserve raw evidence."""
import sys,socket,importlib.util,zlib,json,os
from collections import deque
from event_e1_common import *
label=sys.argv[1];folder=OUT/('checkpoint_diagnosis_'+label+'_seed'+os.environ.get('PYTHONHASHSEED','random'));folder.mkdir(exist_ok=False)
def deny(*a,**k):raise AssertionError('network forbidden')
socket.socket.connect=deny;socket.socket.connect_ex=deny
sys.path.insert(0,str(ROOT/'Part2'))
if label=='S8':
    from part1_host import loader
    loader._SINGLETON=loader.ReplayPart1(ROOT/'검증결과/staff_s8/baseline_S8/source/Part1')
spec=importlib.util.spec_from_file_location('e1_checkpoint_diagnostic_test',ROOT/'tests/sparse_events/test_events.py')
test=importlib.util.module_from_spec(spec);spec.loader.exec_module(test)
last=deque(maxlen=2);original=test.E.EventCollector.checkpoint
def record(self):
    blob=original(self);last.append(blob);return blob
test.E.EventCollector.checkpoint=record
failed=False
try:test.test_midnight_checkpoint_has_unfinished_candidate_then_one_event(folder,lambda *a:None)
except AssertionError:failed=True
assert len(last)==2
for name,blob in zip(['restored','continuous'],last):
    (folder/(name+'.bin')).write_bytes(blob)
    (folder/(name+'.json')).write_bytes(zlib.decompress(blob))
a,b=[json.loads(zlib.decompress(blob)) for blob in last]
def normalize(value):
    if isinstance(value,list):return [normalize(v) for v in value]
    if not isinstance(value,dict):return value
    result={k:normalize(v) for k,v in value.items()}
    if result.get('$') in ('set','frozenset'):
        result['value']=sorted(result['value'],key=lambda x:json.dumps(x,sort_keys=True,ensure_ascii=False))
    return result
paths=[]
def differences(x,y,path='$'):
    if x==y:return
    if isinstance(x,dict) and isinstance(y,dict):
        if x.get('$') in ('set','frozenset') and normalize(x)==normalize(y):
            paths.append({'path':path,'type':x['$'],'left':x,'right':y});return
        for key in sorted(x.keys()|y.keys()):differences(x.get(key),y.get(key),path+'/'+key)
    elif isinstance(x,list) and isinstance(y,list) and len(x)==len(y):
        for i,(u,v) in enumerate(zip(x,y)):differences(u,v,path+'/'+str(i))
    else:paths.append({'path':path,'type':'semantic_difference','left':str(x)[:120],'right':str(y)[:120]})
differences(a,b)
result={'label':label,'raw_assertion_failed':failed,'raw_bytes_equal':last[0]==last[1],
        'equal_after_only_tagged_set_order':normalize(a)==normalize(b),'differences':paths,
        'external_event_assertions_passed_before_checkpoint':True}
write(folder/'result.json',result);print(result,flush=True)
