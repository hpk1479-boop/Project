"""Capture fragment continuity before canonical STAFF ingress (no strategy logic)."""
import io
import threading
import struct
import zlib
import numpy as np
import staff_schema as wire
from .storage import bundles as capture_bundles
from event_engine.staff_adapter import StaffIngressAdapter
from event_engine.model import Input
from event_pipe_host import PipeReceiver

def continuous(old,frame):
    if old is None:return True
    if frame.kind!=wire.WIRE_FULL or frame.times[-1]<old.time[-1]:return False
    common,ai,bi=np.intersect1d(old.time[:-1],frame.times[:-1],return_indices=True)
    if not len(common):return False
    # Prior unfinished bar is intentionally excluded: its legitimate close may change.
    # Compare market history, not indicator warm-up slots at the leading edge of
    # a rolling FULL. All new indicator values still reach STAFF and the Board.
    return np.array_equal(old.volume[ai],frame.volumes[bi]) and np.array_equal(old.values[ai,:4],frame.values[bi,:4],equal_nan=True)

class Collector:
    def __init__(self):self.pending=[]
    def post(self,kind,*,source,source_seq,source_time,payload,engine_time=None):
        self.pending.append(Input(source,source_seq,source_time,kind,payload,0))

def remap_bundle(raw, packet, sequences, bundle_seq, observed):
    """Change only continuity headers after the canonical decoder validated raw.

    Child payloads and their CRCs are untouched. The outer CRC is refreshed
    because it covers the child headers. STAFF validates the resulting bytes.
    """
    if packet.kind!=wire.WIRE_BUNDLE:raise ValueError('capture must contain bundles')
    if packet.seq==bundle_seq and packet.sent_at_ms==observed and all(
            f.seq==seq for f,seq in zip(packet.children,sequences)):
        return raw
    result=bytearray(raw)
    struct.pack_into('<q',result,8,bundle_seq)
    offset=wire.WIRE_HEADER.size+len(packet.symbol.encode('utf-8'))
    struct.pack_into('<q',result,offset,observed)
    offset+=12
    for seq in sequences:
        length=struct.unpack_from('<I',result,offset)[0];offset+=4
        struct.pack_into('<q',result,offset+8,seq)
        offset+=length
    struct.pack_into('<I',result,len(result)-4,zlib.crc32(memoryview(result)[wire.WIRE_HEADER.size:-4]) & 0xffffffff)
    return bytes(result)

class CaptureInputs:
    """Recorded bundles through the LIVE STAFF boundary.

    server_times (수정본172): each path's broker clock (recording_time.clocks_of). The engine receives
    real time, as LIVE does: the bundle's observation time and its bar times (StaffIngressAdapter). The
    selection of the period (start_ms/end_ms) and the capture order stay on the recorded server clock.
    None passes recorded times unchanged."""
    def __init__(self,staff,cache,paths,*,transport='replay',clock=None,start_ms=0,end_ms=2**63-1,seam_sink=None,capture_start='keyframe',
                 server_times=None):
        self.cache=cache;self.paths=paths;self.clock=clock or [0.0];self.start=start_ms;self.end=end_ms
        self.collector=Collector();self.adapter=StaffIngressAdapter(cache,self.collector)
        self.receiver=PipeReceiver(staff,cache,self.adapter,(),threading.Event())
        self.transport=transport;self.seams=[];self.seam_sink=seam_sink
        if capture_start not in ('keyframe','beginning'):raise ValueError('capture_start: keyframe / beginning')
        self.capture_start=capture_start;self.seek_info=None
        if server_times is not None and len(server_times)!=len(paths):raise ValueError('녹화마다 브로커 서버 시간이 필요합니다.')
        self.server_times=server_times
    def __iter__(self):
        global_seq={};bundle_seq=0;last_time=None
        for number,path in enumerate(self.paths):
            offsets={};first=True
            broker=self.server_times[number] if self.server_times is not None else None
            self.adapter.server_time=broker
            real=(lambda stamp:stamp) if broker is None else broker.to_utc_ms
            def bootstrap(raw,metadata):
                nonlocal offsets,first,global_seq,bundle_seq,last_time
                packet=self.cache.inspect_publication(raw);saved=metadata['staff']
                seqs=[saved['sequences'][f.symbol,f.timeframe] for f in packet.children]
                raw=remap_bundle(raw,packet,seqs,metadata['prefix_bundles'],real(metadata['last_stamp']))
                self.clock[0]=metadata['clock']
                # FULL always enters via the normal LIVE/replay STAFF boundary.
                if self.transport=='live':self.receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=real(metadata['last_stamp']))
                else:self.adapter.publish(raw,source_time=real(metadata['last_stamp']))
                # Restore only continuation metadata on those validated arrays.
                # No stored snapshot is inserted around STAFF validation.
                self.cache.restore_metadata(saved)
                self.collector.pending=[]
                offsets=dict(metadata['offsets']);global_seq=dict(saved['sequences'])
                bundle_seq=metadata['prefix_bundles'];last_time=metadata['last_stamp'];first=False
                self.seek_info={'prefix_bundles_skipped':bundle_seq,'bootstrap_feeds':len(seqs),'previous_observation_ms':last_time}
            seek=self.start if number==0 and self.capture_start=='keyframe' else None
            for observed,raw in capture_bundles(path,start_ms=seek,bootstrap=bootstrap,staff_validation=True):
                if observed>=self.end:return
                packet=self.cache.inspect_publication(raw)
                if first:
                    first=False
                    if last_time is not None and observed<=last_time:raise ValueError('overlapping/out-of-order capture fragments')
                    olds=self.cache.snapshots_with_age(packet.symbol,[f.timeframe for f in packet.children])
                    joined=all(continuous(olds[f.timeframe][0],f) for f in packet.children)
                    if number:
                        seam={'path':str(path),'time_ms':real(observed),'continuous':joined}
                        self.seams.append(seam)
                        if self.seam_sink:self.seam_sink(seam)
                        if not joined:
                            self.adapter.reconnect(symbol=packet.symbol,source_time=real(observed));global_seq={}
                sequences=[]
                for f in packet.children:
                    key=(f.symbol,f.timeframe)
                    if key not in offsets:
                        if f.kind!=wire.WIRE_FULL:raise ValueError('capture piece must start each feed with FULL')
                        offsets[key]=global_seq.get(key,0)+1-f.seq
                    seq=f.seq+offsets[key];global_seq[key]=seq
                    sequences.append(seq)
                bundle_seq+=1
                raw=remap_bundle(raw,packet,sequences,bundle_seq,real(observed))
                # Sampling omissions are not disconnects. Use virtual receive time;
                # explicit seam/health events carry actual discontinuities.
                self.clock[0]+=0.001
                if self.transport=='live':self.receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=real(observed))
                else:self.adapter.publish(raw,source_time=real(observed))
                last_time=observed
                pending=self.collector.pending;self.collector.pending=[]
                if observed>=self.start:yield from pending
