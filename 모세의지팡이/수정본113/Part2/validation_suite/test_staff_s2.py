"""S2 transport/storage contracts; no golden expectations are regenerated."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
import uuid

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Part2'))
from part1_host.runtime import Part1Runtime
from part1_host.capture import pack_wire, PIPE_HEADER
from staff_golden import baseline_host


@pytest.fixture
def staff():
    with Part1Runtime(symbols=['BTCUSD'], specials=['SPECIAL7'], start_epoch=1790035200) as rt:
        yield rt.modules['the_staff_of_moses']



class ClientView:
    def __init__(self, cache):
        import staff_snapshot as lib
        self.cache = cache
        self.client = lib.SnapshotClient(transport=self.exchange)
    def exchange(self, parts):
        import staff_snapshot as lib
        req = json.loads(parts[1])
        return lib.encode_reply(((req['symbol'],tf,self.cache.snapshot(req['symbol'],tf)) for tf in req['timeframes']),max_bars=self.cache.max_bars)
    def get(self, symbol, tf):
        return self.client.raw_frames({'symbol':symbol,'timeframes':[tf]})[tf]

def view(cache):
    # Test-owned cache, never attached to STAFF.
    return ClientView(cache)

def raw(seq=1, rows=8, times=None, values=None):
    times = np.arange(rows, dtype='int64') + 1790035200 if times is None else np.array(times, dtype='int64')
    values = np.full((len(times), 45), float(seq)) if values is None else values
    return pack_wire('BTCUSD', '1m', times, np.arange(len(times), dtype='int64'), values, snapshot=seq)


def cache_for(staff, **kwargs):
    return staff.StaffPipeCache('', health_session='S2', **kwargs)


def test_receiver_numpy_only_and_snapshot_strictly_immutable(staff, monkeypatch):
    cache = cache_for(staff)
    with monkeypatch.context() as patch:
        patch.setattr(pd, 'DataFrame', lambda *a, **k: pytest.fail('pandas on receive path'))
        cache.publish_frame(raw())
        assert cache.health('BTCUSD', ['1m'])['1m']['status'] == 'FRESH'
    snap = cache.snapshot('BTCUSD', '1M')
    for array in (snap.time, snap.volume, snap.values):
        with pytest.raises(ValueError): array.flat[0] = 2
        with pytest.raises(ValueError): array.setflags(write=True)
    with pytest.raises(TypeError): snap.indicator_validity['EMA'] = False
    with pytest.raises(AttributeError): snap.seq = 2
    assert snap.seq == 1 and snap.source_epoch == 'S2:1'


def test_one_lazy_frame_per_publication_and_independent_copies(staff, monkeypatch):
    cache = cache_for(staff)
    cache.publish_frame(raw())
    reader = view(cache)
    import staff_snapshot as lib
    original, calls = lib.legacy_frame, []
    def build(*args):
        calls.append(args[2].seq)
        time.sleep(.01)
        return original(*args)
    monkeypatch.setattr(lib, 'legacy_frame', build)
    with ThreadPoolExecutor(8) as pool:
        frames = list(pool.map(lambda _: reader.get('BTCUSD', '1m'), range(24)))
    assert calls == [1]
    frames[0].iloc[0, 1] = -1
    frames[0].attrs['indicator_validity']['EMA'] = False
    assert frames[1].iloc[0, 1] == 1
    assert frames[1].attrs['indicator_validity']['EMA']
    cache.publish_frame(raw())
    reader.get('BTCUSD', '1m')
    assert calls == [1]
    cache.publish_frame(raw(2))
    reader.get('BTCUSD', '1m')
    assert calls == [1, 2]


@pytest.mark.parametrize('length', [0, 1, 31, 32, 35, 40, 100, 3047])
def test_truncated_frame_is_atomic(staff, length):
    cache = cache_for(staff)
    cache.publish_frame(raw())
    before = cache.snapshot('BTCUSD', '1m')
    with pytest.raises(staff.StaffFrameTruncatedError): cache.publish_frame(raw(2)[:length])
    assert cache.snapshot('BTCUSD', '1m') is before


@pytest.mark.parametrize('field,value', [(0,0),(1,2),(3,0),(3,129),(4,0),(4,17),(5,2),(5,651),(6,44)])
def test_corrupt_header_is_atomic(staff, field, value):
    cache = cache_for(staff)
    cache.publish_frame(raw())
    before = cache.snapshot('BTCUSD', '1m')
    wire = raw(2)
    head = list(PIPE_HEADER.unpack_from(wire)); head[field] = value
    with pytest.raises(RuntimeError): cache.publish_frame(PIPE_HEADER.pack(*head) + wire[PIPE_HEADER.size:])
    assert cache.snapshot('BTCUSD', '1m') is before


def test_trailing_bytes_are_rejected_before_commit(staff):
    cache = cache_for(staff)
    cache.publish_frame(raw())
    with pytest.raises(staff.StaffFrameTrailingError): cache.publish_frame(raw(2) + b'x')
    assert cache.snapshot('BTCUSD', '1m').seq == 1


def test_seq_stale_validity_and_reconnect_rules(staff):
    clock = [0.0]
    cache = cache_for(staff, monotonic=lambda: clock[0])
    cache.publish_frame(raw(5))
    first = cache.snapshot('BTCUSD', '1m')
    clock[0] = 30
    for seq in (5, 4, 1): cache.publish_frame(raw(seq))
    assert cache.snapshot('BTCUSD', '1m') is first
    assert cache.health('BTCUSD', ['1m'])['1m']['status'] == 'FRESH'
    clock[0] = 30.001
    assert cache.health('BTCUSD', ['1m'])['1m']['status'] == 'STALE'
    cache.publish_frame(raw(6))
    assert cache.snapshot('BTCUSD', '1m').source_epoch == 'S2:2'
    values = np.ones((8,45)); values[-1,5] = np.inf
    cache.publish_frame(raw(7, values=values))
    assert cache.snapshot('BTCUSD', '1m').source_epoch == 'S2:3'
    assert not cache.snapshot('BTCUSD', '1m').indicator_validity['EMA']
    before = view(cache).get('BTCUSD', '1m')
    cache.reconnect()
    pd.testing.assert_frame_equal(before, view(cache).get('BTCUSD', '1m'), check_exact=True)
    cache.publish_frame(raw(1))
    assert cache.snapshot('BTCUSD', '1m').seq == 1
    assert cache.snapshot('BTCUSD', '1m').source_epoch == 'S2:4'


@pytest.mark.parametrize('max_bars', [650, 5, -2])
def test_legacy_normalization_exact_against_entire_s1_program(max_bars):
    times = [1790035204, 1790035201, -(2**63), 1790035202, 1790035204,
             1790035203, 1790035205, 1790035206, 1790035207, 1790035208]
    values = np.arange(450, dtype=float).reshape(10,45)
    values[0,0] = np.inf  # dropped duplicate must not invalidate OHLC
    values[2,0] = np.nan  # dropped NaT must not invalidate OHLC
    values[1,8] = np.inf; values[3,9] = 1.7e308
    wire = raw(times=times, values=values)
    outputs = []
    for part1 in (ROOT.parent/'수정본8/Part1', ROOT/'Part1'):
        with baseline_host.Part1Runtime(part1_root=part1, symbols=['BTCUSD'],
                specials=['SPECIAL7'], start_epoch=1790035200) as rt:
            rt.staff_server.cache.max_bars = max_bars
            rt.publish(wire)
            frame = (rt.staff_server.cache.get('BTCUSD','1m') if part1 != ROOT/'Part1' else view(rt.staff_server.cache).get('BTCUSD','1m'))
            frame.attrs['source_epoch'] = frame.attrs['source_epoch'].rsplit(':',1)[1]
            outputs.append(frame)
    pd.testing.assert_frame_equal(*outputs, check_exact=True)
    assert outputs[0].attrs == outputs[1].attrs


def test_continuation_public_api_preserves_epoch_seq_and_readonly_arrays(staff):
    now = [9.0]
    cache = cache_for(staff, monotonic=lambda: now[0]); cache.publish_frame(raw(9))
    cache.reconnect()
    restored = cache_for(staff, monotonic=lambda: now[0])
    state = cache.export_state()
    metadata = cache.export_state(metadata_only=True)
    assert metadata == {k:v for k,v in state.items() if k!='snapshots'}
    restored.restore_state(state)
    pd.testing.assert_frame_equal(view(cache).get('BTCUSD','1m'), view(restored).get('BTCUSD','1m'), check_exact=True)
    assert restored.health('BTCUSD',['1m']) == cache.health('BTCUSD',['1m'])
    assert not restored.snapshot('BTCUSD','1m').values.flags.writeable
    restored.publish_frame(raw(1))
    assert restored.snapshot('BTCUSD','1m').source_epoch == 'S2:2'


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, 'receiver did not make progress'
        time.sleep(.01)


def test_actual_pipe_thread_is_not_blocked_by_slow_dataframe_consumer(staff, monkeypatch):
    cache = staff.StaffPipeCache(r'\\.\pipe\MosesS2_' + uuid.uuid4().hex)
    stop, entered, release = threading.Event(), threading.Event(), threading.Event()
    reader = view(cache)
    import staff_snapshot as lib
    original = lib.legacy_frame
    def build(symbol, tf, snapshot, max_bars):
        if snapshot.seq == 1:
            entered.set()
            assert release.wait(5)
        return original(symbol, tf, snapshot, max_bars)
    monkeypatch.setattr(lib, 'legacy_frame', build)
    client = None
    cache.start(stop)
    pool = ThreadPoolExecutor(1)
    try:
        deadline = time.monotonic() + 5
        while client is None:
            try: client = open(cache.pipe_name,'wb',buffering=0)
            except FileNotFoundError:
                assert time.monotonic() < deadline
                time.sleep(.01)
        client.write(raw())
        wait_for(lambda: cache.has('BTCUSD','1m'))
        pending = pool.submit(reader.get,'BTCUSD','1m')
        assert entered.wait(5)
        client.write(raw(2))
        wait_for(lambda: cache.snapshot('BTCUSD','1m').seq == 2)
        assert view(cache).get('BTCUSD','1m').open.iloc[-1] == 2
        assert not pending.done()
        release.set()
        assert pending.result(5).open.iloc[-1] == 1
    finally:
        release.set(); stop.set()
        if client is not None: client.close()
        pool.shutdown(wait=True)
        cache._thread.join(5)
    assert not cache._thread.is_alive()


def test_concurrent_receive_and_legacy_requests_are_consistent(staff):
    cache = cache_for(staff)
    cache.publish_frame(raw())
    server = staff.DataServer({}, staff.WonbiState(3), cache=cache)
    from staff_snapshot import SnapshotClient
    from staff_compat import StaffCompat
    compat = StaffCompat(SnapshotClient(transport=server.dispatch_multipart))
    start = threading.Barrier(5)
    def publish():
        start.wait()
        for seq in range(2,102):
            cache.publish_frame(raw(seq)); time.sleep(.0001)
    def request():
        start.wait()
        for _ in range(30):
            frame = compat.request({'symbol':'BTCUSD','timeframes':['1m'],'indicators':['SMA3']})['1m']
            assert frame.open.nunique() == 1
            assert np.array_equal(frame.open, frame.close)
            frame.iloc[0,1] = -1000
    with ThreadPoolExecutor(5) as pool:
        futures = [pool.submit(publish)] + [pool.submit(request) for _ in range(4)]
        for future in futures: future.result(15)
    assert cache.snapshot('BTCUSD','1m').seq == 101
    assert view(cache).get('BTCUSD','1m').open.min() == 101


def test_actual_pipe_partial_disconnect_then_seq_one_restart(staff):
    cache = staff.StaffPipeCache(r'\\.\pipe\MosesS2Reconnect_' + uuid.uuid4().hex,
                                health_session='RECONNECT')
    stop = threading.Event()
    client = None
    def connect():
        deadline = time.monotonic() + 5
        while True:
            try: return open(cache.pipe_name,'wb',buffering=0)
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(.01)
    cache.start(stop)
    try:
        client = connect()
        client.write(raw(9))
        wait_for(lambda: cache.has('BTCUSD','1m'))
        before = cache.snapshot('BTCUSD','1m')
        client.write(raw(10)[:100])
        client.close(); client = None
        client = connect()
        assert cache.snapshot('BTCUSD','1m') is before
        client.write(raw(1))
        wait_for(lambda: cache.snapshot('BTCUSD','1m').seq == 1)
        assert cache.snapshot('BTCUSD','1m').source_epoch == 'RECONNECT:2'
        assert view(cache).get('BTCUSD','1m').open.iloc[-1] == 1
    finally:
        stop.set()
        if client is not None: client.close()
        cache._thread.join(5)
    assert not cache._thread.is_alive()


def test_part2_staff_transport_uses_public_boundary_only():
    forbidden = {'_read_exact','_consume_one','_entries','_snapshot','_updated','_health_epochs','_health_session'}
    for path in ('Part2/part1_host/runtime.py','Part2/live_replay/runtime.py','Part2/live_replay/checkpoint.py',
                 'Part2/live_replay/allzone_events.py','Part2/live_replay/event_catalog.py'):
        tree = ast.parse((ROOT/path).read_bytes())
        assert not forbidden.intersection(n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute))
        assert not any(isinstance(n,ast.Name) and n.id=='CACHE_FIELDS' for n in ast.walk(tree))
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('getattr','setattr')
                       and n.args and isinstance(n.args[0],ast.Attribute) and n.args[0].attr=='cache'
                       for n in ast.walk(tree))
    tree = ast.parse((ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py').read_bytes())
    tests = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
    assert len(tests) == 4  # 6 before/after + 6 LIVE/backtest + scenario + performance
