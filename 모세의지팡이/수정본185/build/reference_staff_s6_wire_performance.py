"""One non-gating reference measurement on the actual captured LIVE observations.
Prebuild equivalent v1 FULL messages outside timing. Both costs include parser,
validation and Snapshot publication; no pandas/features on either S5/S6 server.
"""
import gc,os,struct,sys,time
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture
from part1_host.runtime import Part1Runtime
w=capture.wire_schema();v2=[];v1=[];latest={};kinds={};bundles=0
with (OUT/'live_BTC/live_frames.bin').open('rb') as f:
    while header:=f.read(12):
        observed,size=struct.unpack('<qI',header);raw=f.read(size);assert len(raw)==size
        packet=w.decode_v2(raw);v2.append(raw)
        if packet.kind==w.WIRE_HELLO:continue
        assert packet.kind==w.WIRE_BUNDLE;bundles+=1
        for frame in packet.children:
            key=(frame.symbol,frame.timeframe);kinds[frame.kind]=kinds.get(frame.kind,0)+1
            if frame.kind==w.WIRE_FULL:latest[key]=[x.copy() for x in (frame.times,frame.volumes,frame.values)]
            elif frame.kind==w.WIRE_ROW:
                assert key in latest and latest[key][0][-1]==frame.times[0]
                latest[key][1][-1]=frame.volumes[0];latest[key][2][-1]=frame.values[0]
            else:assert key in latest
            v1.append(capture.pack_wire(*key,*latest[key],snapshot=len(v1)+1))
assert v1 and v2
measurements={}
for name,part1,frames in [('S5_v1',SOURCE/'Part1',v1),('S6_v2',ROOT/'Part1',v2)]:
    with Part1Runtime(part1_root=part1,symbols=['BTCUSD'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
        cache=rt.modules['the_staff_of_moses'].StaffPipeCache('',health_session='PERF_REFERENCE')
        gc.collect();start_cpu=time.process_time();start_wall=time.perf_counter()
        for raw in frames:cache.publish_frame(raw)
        cpu=time.process_time()-start_cpu;wall=time.perf_counter()-start_wall
        measurements[name]={'cpu_s':cpu,'wall_s':wall,'pipe_frames':len(frames),
            'bytes':sum(map(len,frames)),'feeds':len(cache.keys())}
live=read(OUT/'live_BTC/result.json');seconds=live['wall_seconds']
result={'stage':'S6','status':'REFERENCE_ONLY','adjudicated':False,'runs_per_variant':1,
    'input':'actual BTCUSD LIVE archive, same observations reconstructed as v1 FULL',
    'measurements':measurements,'cpu_ratio':measurements['S6_v2']['cpu_s']/measurements['S5_v1']['cpu_s'],
    'byte_ratio':sum(map(len,v2))/sum(map(len,v1)),'v2_kinds':kinds,
    'observations':len(v1),'bundles':bundles,'elapsed_live_s':seconds,
    'v1_equivalent_feed_writes_per_s':len(v1)/seconds,'v2_bundle_writes_per_s':bundles/seconds,
    'measurement_boundary':'one pass each, input preparation/runtime startup excluded; parse+CRC+validate+store included; pipe waiting excluded from CPU',
    'performance_policy_changed':False,'limits_applied':False}
write(OUT/'performance_reference.json',result);print(result,flush=True)
