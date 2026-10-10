"""Inspect only the failed checkpoint-byte assertion; keep the original test intact."""
from collections import deque
import sys,tempfile,zlib
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'tests/sparse_events'))
import test_events as test
saved=deque(maxlen=2);original=test.E.EventCollector.checkpoint
def capture(self):
    blob=original(self);saved.append(blob);return blob
test.E.EventCollector.checkpoint=capture
failure=None
try:
    with tempfile.TemporaryDirectory(prefix='s6-checkpoint-',dir=OUT) as folder:
        test.test_midnight_checkpoint_has_unfinished_candidate_then_one_event(Path(folder))
except AssertionError as exc:
    failure=repr(exc)
finally:test.E.EventCollector.checkpoint=original
assert len(saved)==2
items=[json.loads(zlib.decompress(blob)) for blob in saved]
def canonical(value):
    if isinstance(value,list):return [canonical(x) for x in value]
    if isinstance(value,dict):
        result={k:canonical(v) for k,v in value.items()}
        if result.get('$') in ('set','frozenset'):
            result['value']=sorted(result['value'],key=lambda x:json.dumps(x,sort_keys=True,ensure_ascii=False))
        return result
    return value
normal=[canonical(x) for x in items]
for label,blob in zip(('restored','full'),saved):(OUT/f'checkpoint_{label}.bin').write_bytes(blob)
report={'original_assertion_failed':failure is not None,'byte_equal':saved[0]==saved[1],
    'typed_set_order_normalized_equal':normal[0]==normal[1],
    'preceding_external_assertions':'Original test confirmed unfinished candidate before midnight, zero premature events, then exactly the same single event after restore.',
    'original_test_modified':False,'normalization':'Only $=set/frozenset item order; values, lists, tuples, mappings, epochs and event fields not removed.',
    'restored_sha256':sha(OUT/'checkpoint_restored.bin'),'full_sha256':sha(OUT/'checkpoint_full.bin')}
write(OUT/'checkpoint_diagnosis.json',report)
if normal[0]!=normal[1]:
    write(OUT/'checkpoint_restored_typed.json',items[0]);write(OUT/'checkpoint_full_typed.json',items[1])
print(report)
assert normal[0]==normal[1]
