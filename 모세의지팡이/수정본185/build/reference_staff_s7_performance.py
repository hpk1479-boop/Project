"""Single reference pass, same recorded observations in S6/45 and S7/48 Wire v2."""
import gc,importlib.util,struct,sys,time
from staff_s7_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.capture import wire_schema
from part1_host.runtime import Part1Runtime
before=OUT/'baseline_input/Part1'
spec=importlib.util.spec_from_file_location('s7_perf_frozen_schema',before/'program/staff_schema.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
w=wire_schema();frames={name:[] for name in ('S6','S7')}
with (OUT/'live_BTC/live_frames.bin').open('rb') as f:
    while head:=f.read(12):
        observed,size=struct.unpack('<qI',head);raw=f.read(size);p=w.decode_v2(raw)
        frames['S7'].append(raw)
        if p.kind==w.WIRE_HELLO:converted=old.pack_hello(p.build_hash)
        else:
            assert p.kind==w.WIRE_BUNDLE
            children=[]
            for c in p.children:
                args=() if c.kind==w.WIRE_HEARTBEAT else (c.times,c.volumes,c.values[:,:45])
                children.append(old.pack_v2(c.symbol,c.timeframe,*args,seq=c.seq,kind=c.kind))
            converted=old.pack_bundle(p.symbol,children,sent_at_ms=p.sent_at_ms,seq=p.seq)
        frames['S6'].append(converted)
result={}
for name,root in [('S6',before),('S7',ROOT/'Part1')]:
    with Part1Runtime(part1_root=root,symbols=['BTCUSD'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
        cache=rt.modules['the_staff_of_moses'].StaffPipeCache('',health_session='PERF',monotonic=lambda:0.)
        gc.collect();start=time.process_time();wall=time.perf_counter()
        for raw in frames[name]:cache.publish_frame(raw)
        result[name]={'receive_cpu_s':time.process_time()-start,'wall_s':time.perf_counter()-wall,
                      'bytes':sum(map(len,frames[name])),'frames':len(frames[name]),'feeds':len(cache.keys())}
write(OUT/'performance_reference.json',{'stage':'S7','adjudicated':False,'runs_per_variant':1,
    'measurement_boundary':'Same actual BTC observations and frame kinds; parse/CRC/validation/snapshot storage. Encoding and host startup excluded.',
    'measurements':result,'byte_ratio':result['S7']['bytes']/result['S6']['bytes'],
    'cpu_ratio':result['S7']['receive_cpu_s']/result['S6']['receive_cpu_s'] if result['S6']['receive_cpu_s'] else None,
    'policy_changed':False,'limits_applied':False})
print(result)
