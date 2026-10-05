"""S8 transport-only gates, independent of excluded Part2 regression suites."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.runtime import Part1Runtime

@pytest.fixture
def host():
    with Part1Runtime(symbols=['BTCUSD'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
        yield rt

def setup(host):
    m=host.modules['the_staff_of_moses'];clock=[5.];sigma=[3.]
    c=m.StaffPipeCache('',health_session='S8',monotonic=lambda:clock[0])
    srv=m.DataServer({},SimpleNamespace(get_sigma=lambda:sigma[0]),cache=c,allowed_symbols=['BTCUSD'])
    t=np.arange(8,dtype='<i8')+1790035200;v=np.arange(8,dtype='<i8');x=np.full((8,48),10.)
    x[:,47]=3.;c.publish_frame(m.wire.pack_v2('BTCUSD','1m',t,v,x,seq=1))
    return m,c,srv,clock,sigma,(t,v,x)

def request(**kw):return dict(kind='SNAPSHOT',symbol='BTCUSD',timeframes=['1m'],**kw)

def test_encode_once_per_publication_and_fresh_return_container(host):
    m,c,srv,clock,sigma,data=setup(host)
    import staff_snapshot
    with patch.object(staff_snapshot,'encode_reply',wraps=staff_snapshot.encode_reply) as enc:
        a=srv.snapshot_reply(request());b=srv.snapshot_reply(request(indicators=['PRICE']))
        assert enc.call_count==1 and a==b and a is not b
        a.clear();assert srv.snapshot_reply(request())==b
        c.publish_frame(m.wire.pack_v2('BTCUSD','1m',seq=2,kind=m.wire.WIRE_HEARTBEAT))
        assert srv.snapshot_reply(request())!=b and enc.call_count==2

def test_cached_reply_never_bypasses_stale_or_expected_sigma(host):
    m,c,srv,clock,sigma,data=setup(host)
    a=srv.snapshot_reply(request());clock[0]=36.
    with pytest.raises(RuntimeError,match='갱신되지'):srv.snapshot_reply(request())
    clock[0]=6.;sigma[0]=2.5
    b=srv.snapshot_reply(request());assert a[0]!=b[0] and a[1:]==b[1:]
    assert srv.handle(dict(kind='SOURCE_HEALTH',symbol='BTCUSD',timeframes=['1m']))['feeds']['1m']['warnings']

def test_reconnect_same_seq_invalidates_encoded_reply(host):
    m,c,srv,clock,sigma,data=setup(host);a=srv.snapshot_reply(request())
    c.reconnect();c.publish_frame(m.wire.pack_v2('BTCUSD','1m',*data,seq=1))
    b=srv.snapshot_reply(request());assert a[0]!=b[0] and a[1:]==b[1:]

def test_missing_and_unready_indicators_not_hidden_by_cached_success(host):
    m,c,srv,clock,sigma,(t,v,x)=setup(host);srv.snapshot_reply(request())
    x[-1,24]=np.nan;c.publish_frame(m.wire.pack_v2('BTCUSD','1m',t,v,x,seq=2))
    srv.snapshot_reply(request())
    with pytest.raises(RuntimeError,match='RSI_val'):srv.snapshot_reply(request(indicators=['RSI']))
    with pytest.raises(m.SnapshotUnavailable):srv.snapshot_reply(dict(kind='SNAPSHOT',symbol='BTCUSD',timeframes=['5m']))

def test_response_encoding_does_not_hold_receiver_lock(host):
    m,c,srv,clock,sigma,data=setup(host)
    import staff_snapshot
    entered=threading.Event();release=threading.Event();received=threading.Event();errors=[]
    original=staff_snapshot.encode_reply
    def slow(*a,**k):entered.set();assert release.wait(5);return original(*a,**k)
    def ask():
        try:srv.snapshot_reply(request())
        except Exception as e:errors.append(e)
    with patch.object(staff_snapshot,'encode_reply',slow):
        thread=threading.Thread(target=ask);thread.start();assert entered.wait(5)
        def publish():
            try:c.publish_frame(m.wire.pack_v2('BTCUSD','1m',*data,seq=2));received.set()
            except Exception as e:errors.append(e)
        rx=threading.Thread(target=publish);rx.start()
        try:assert received.wait(2), 'receiver blocked by response encoding'
        finally:release.set();thread.join(5);rx.join(5)
    assert not errors and c.snapshot('BTCUSD','1m').seq==2

@pytest.mark.parametrize('layout',['ordered','duplicate','reverse','NaT'])
def test_numpy_validation_and_readonly_ownership(host,layout):
    m,c,srv,clock,sigma,(t,v,x)=setup(host)
    if layout=='duplicate':t[-2]=t[-1]
    if layout=='reverse':t=t[::-1].copy()
    if layout=='NaT':t[0]=np.iinfo(np.int64).min
    x[1,25]=np.inf;x[2,25]=1.7e308
    c._publish_arrays('BTCUSD','1m',2,t,v,x)
    saved=c.snapshot('BTCUSD','1m');expected=np.array(x);expected[1:3,25]=np.nan
    np.testing.assert_array_equal(saved.values,expected)
    t[:]=0;v[:]=0;x[:]=0
    assert saved.time[-1]!=0 and saved.volume[-1]!=0 and saved.values[-1,47]==3.
    for a in (saved.time,saved.volume,saved.values):
        with pytest.raises(ValueError):a.setflags(write=True)

def test_bundle_atomicity_seq_gaps_and_no_dropped_frames(host,tmp_path):
    m,c,srv,clock,sigma,data=setup(host);c._gap_journal=tmp_path/'gaps.jsonl';w=m.wire
    for i in range(2,52):c.publish_frame(w.pack_v2('BTCUSD','1m',seq=i,kind=w.WIRE_HEARTBEAT))
    c.publish_frame(w.pack_v2('BTCUSD','1m',seq=55,kind=w.WIRE_HEARTBEAT))
    d=c.wire_diagnostics();assert d['gaps'][-1]['first_missing_seq']==52
    assert d['gaps'][-1]['last_missing_seq']==54
    snap=c.snapshot('BTCUSD','1m')
    for seq in (55,4):c.publish_frame(w.pack_v2('BTCUSD','1m',seq=seq,kind=w.WIRE_HEARTBEAT))
    assert c.snapshot('BTCUSD','1m') is snap
    raw=w.pack_v2('BTCUSD','1m',*data,seq=56)
    for bad in (raw[:-1],raw[:-1]+bytes([raw[-1]^1])):
        with pytest.raises(Exception):c.publish_frame(bad)
    assert c.snapshot('BTCUSD','1m') is snap

def test_cache_is_bounded(host):
    m,c,srv,clock,sigma,data=setup(host)
    for i in range(140):sigma[0]=3.+i/100;srv.snapshot_reply(request())
    assert len(srv._encoded_replies)<=128

def test_wire_schema_and_decision_sources_unchanged():
    import hashlib,json
    prior=json.loads((ROOT/'검증결과/staff_s8/s7_frozen_manifest.json').read_text('utf-8'))
    names=['staff_schema.py','staff_compat.py','staff_snapshot.py','manager_KIM.py','monitor_OZ.py','watch_orchestrator.py']
    for r in prior:
        rel=r['path']
        if rel.startswith('Part1/program/') and (Path(rel).name in names or '/SPECIAL/' in rel or Path(rel).name.startswith('strategy_')):
            assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==r['sha256'],rel
