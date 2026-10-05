"""S6 wire contracts. Frozen v1 goldens and client formulas are not rewritten."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import struct
import sys
import zlib

import numpy as np
import pytest

from test_staff_s2 import staff, cache_for, raw
from part1_host.capture import pack_wire

ROOT=Path(__file__).resolve().parents[2]

def payload():
    return (np.arange(8,dtype='<i8')+1790035200,
            np.arange(8,dtype='<i8'),np.arange(360,dtype='<f8').reshape(8,45)+1)

def full(staff,seq=1,tf='1m',data=None,**kwargs):
    return staff.wire.pack_v2('BTCUSD',tf,*(payload() if data is None else data),seq=seq,**kwargs)

def equal(a,b):
    for field in ('time','volume','values'):
        assert getattr(a,field).tobytes()==getattr(b,field).tobytes()
    assert a.indicator_validity==b.indicator_validity

def test_v1_bytes_frozen_before_module():
    path=ROOT/'검증결과/staff_s0/baseline_input/Part2/part1_host/capture.py'
    if not path.exists(): path=ROOT.parent/'수정본6/Part2/part1_host/capture.py'
    spec=importlib.util.spec_from_file_location('s6_frozen_capture',path)
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod
    try:
        spec.loader.exec_module(mod)
        for seq in (1,42,2**40):
            assert pack_wire('BTCUSD','1m',*payload(),snapshot=seq)==mod.pack_wire('BTCUSD','1m',*payload(),snapshot=seq)
    finally:sys.modules.pop(spec.name,None)

def test_schema_registry_and_generated_constants(staff):
    wire=staff.wire
    assert len(wire.PIPE_VALUE_COLUMNS)==45
    assert wire.WIRE_SCHEMA_ID==zlib.crc32('\n'.join(wire.PIPE_VALUE_COLUMNS).encode())
    assert wire.WIRE_SCHEMAS[wire.WIRE_SCHEMA_ID]==tuple(staff.PIPE_VALUE_COLUMNS)
    assert f'0x{wire.WIRE_SCHEMA_ID:08X}' in wire.generated_mqh('a'*64)

def test_full_row_heartbeat_exact_and_immutable(staff):
    wire=staff.wire
    cache=cache_for(staff,monotonic=lambda:4)
    reference=cache_for(staff,monotonic=lambda:4)
    data=payload();cache.publish_frame(full(staff,data=data))
    data[1][-1]=900;data[2][-1,:]+=13
    row=wire.pack_v2('BTCUSD','1m',*(a[-1:] for a in data),seq=2,kind=wire.WIRE_ROW)
    cache.publish_frame(row);reference.publish_frame(full(staff,2,data=data))
    equal(cache.snapshot('BTCUSD','1m'),reference.snapshot('BTCUSD','1m'))
    epoch=cache.snapshot('BTCUSD','1m').source_epoch
    cache.publish_frame(wire.pack_v2('BTCUSD','1m',seq=3,kind=wire.WIRE_HEARTBEAT))
    snap=cache.snapshot('BTCUSD','1m')
    assert snap.seq==3 and snap.source_epoch==epoch
    for a in (snap.time,snap.volume,snap.values):
        with pytest.raises(ValueError):a.setflags(write=True)

def test_bundle_matches_individual_feeds_and_all_frames_consumed(staff):
    w=staff.wire;a=cache_for(staff);b=cache_for(staff)
    frames=[full(staff,seq=i,tf=tf) for i in range(1,4) for tf in ('1m','5m','1h')]
    a.publish_frame(w.pack_bundle('BTCUSD',frames))
    for f in frames:b.publish_frame(f)
    for tf in ('1m','5m','1h'):
        equal(a.snapshot('BTCUSD',tf),b.snapshot('BTCUSD',tf))
        assert a.wire_diagnostics()['feeds'][('BTCUSD',tf)]['accepted']==3

@pytest.mark.parametrize('case',['crc','truncated','bad_header','trailing','bad_second_child'])
def test_malformed_frame_never_partially_publishes(staff,case):
    cache=cache_for(staff);cache.publish_frame(full(staff));before=cache.snapshot('BTCUSD','1m')
    raw=full(staff,2)
    if case=='crc':raw=raw[:-1]+bytes([raw[-1]^1])
    if case=='truncated':raw=raw[:-7]
    if case=='bad_header':raw=raw[:4]+struct.pack('<I',7)+raw[8:]
    if case=='trailing':raw+=b'x'
    if case=='bad_second_child':
        data=payload();data[2][-1,0]=np.nan
        raw=staff.wire.pack_bundle('BTCUSD',[full(staff,2),full(staff,3,data=data)])
    with pytest.raises((ValueError,RuntimeError,EOFError)):cache.publish_frame(raw)
    assert cache.snapshot('BTCUSD','1m') is before

def test_sequences_reconnect_hello_unknown_schema_and_journal(staff,tmp_path):
    w=staff.wire;cache=cache_for(staff,gap_journal=tmp_path/'gaps.jsonl')
    ack=cache.publish_frame(w.pack_hello('a'*64));assert w.decode_v2(ack).kind==w.WIRE_ACK
    cache.publish_frame(full(staff,2));before=cache.snapshot('BTCUSD','1m')
    for seq in (1,2):cache.publish_frame(full(staff,seq))
    assert cache.snapshot('BTCUSD','1m') is before
    cache.publish_frame(full(staff,5))
    gap=json.loads((tmp_path/'gaps.jsonl').read_text())
    assert (gap['first_missing_seq'],gap['last_missing_seq'])==(3,4)
    assert cache.wire_diagnostics()['feeds'][('BTCUSD','1m')]['missing_sequences']==2
    with pytest.raises(w.UnknownWireSchema):cache.publish_frame(full(staff,6,schema_id=123))
    assert cache.health('BTCUSD',['1m'])['1m']['status']=='UNAVAILABLE'
    assert cache.snapshot_with_age('BTCUSD','1m')==(None,None)
    cache.reconnect()
    with pytest.raises(w.WireError):cache.publish_frame(w.pack_v2('BTCUSD','1m',seq=1,kind=w.WIRE_HEARTBEAT))
    cache.publish_frame(full(staff,1))
    snap=cache.snapshot('BTCUSD','1m')
    assert snap.seq==1 and snap.source_epoch!=before.source_epoch
    assert cache.health('BTCUSD',['1m'])['1m']['status']=='FRESH'
    assert cache.wire_diagnostics()['reconnects']==1
    assert cache.wire_diagnostics()['feeds'][('BTCUSD','1m')]['reconnects']==1


def test_heartbeat_stale_and_row_epoch_contract(staff):
    now=[0.];cache=cache_for(staff,monotonic=lambda:now[0]);w=staff.wire
    cache.publish_frame(full(staff));epoch=cache.snapshot('BTCUSD','1m').source_epoch
    now[0]=30.1;assert cache.health('BTCUSD',['1m'])['1m']['status']=='STALE'
    cache.publish_frame(w.pack_v2('BTCUSD','1m',seq=2,kind=w.WIRE_HEARTBEAT))
    assert cache.health('BTCUSD',['1m'])['1m']['status']=='FRESH'
    assert cache.snapshot('BTCUSD','1m').source_epoch==epoch
    data=payload();data[2][-1,24]=np.nan
    with pytest.raises(w.WireError):
        cache.publish_frame(w.pack_v2('BTCUSD','1m',*(x[-1:] for x in data),seq=3,kind=w.WIRE_ROW))
    cache.publish_frame(full(staff,3,data=data))
    assert cache.snapshot('BTCUSD','1m').source_epoch!=epoch

def test_concurrent_requests_observe_complete_bundles(staff):
    w=staff.wire;cache=cache_for(staff)
    cache.publish_frame(w.pack_bundle('BTCUSD',[full(staff,1,tf) for tf in ('1m','5m')]))
    def writer():
        for seq in range(2,102):
            cache.publish_frame(w.pack_bundle('BTCUSD',[full(staff,seq,tf) for tf in ('1m','5m')]))
    def reader():
        for _ in range(150):
            state=cache.export_state()
            assert state['snapshots'][('BTCUSD','1m')]['seq']==state['snapshots'][('BTCUSD','5m')]['seq']
    with ThreadPoolExecutor(5) as pool:
        futures=[pool.submit(writer)]+[pool.submit(reader) for _ in range(4)]
        for future in futures:future.result()
    assert all(v['accepted']==101 for v in cache.wire_diagnostics()['feeds'].values())


@pytest.mark.parametrize('version',[1,2])
def test_capture_secondfeed_replays_all_observations(staff,tmp_path,version):
    from part1_host.capture import CaptureWriter,FeedReplay,parse_capture_manifest
    from part1_host.engine import SecondFeed
    writer=CaptureWriter(tmp_path,'BTCUSD',['1m','5m'],wire_version=version)
    data=payload()
    for second in range(5):
        if second==2:data[2][-1]+=4
        for i in range(2):writer.write(i,1790035200+second,*data)
    writer.close();info=parse_capture_manifest(tmp_path)
    assert info['wire_version']==version
    replay=SecondFeed(tmp_path);cache=cache_for(staff)
    replay.advance_to(1790035204)
    for _,raw in replay.wires():cache.publish_frame(raw)
    for tf in ('1m','5m'):
        assert cache.snapshot('BTCUSD',tf).values.tobytes()==data[2].tobytes()
        if version==2:assert cache.wire_diagnostics()['feeds'][('BTCUSD',tf)]['accepted']==5
    if version==2:
        for _,raw in replay.wires():cache.publish_frame(raw)
        assert all(v['accepted']==6 for v in cache.wire_diagnostics()['feeds'].values())
