"""S3 transport plus 600 seeded request combinations against legacy responses."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pickle
import random
import sys
import threading

import numpy as np
import pandas as pd
import pytest
import zmq as REAL_ZMQ

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Part2'))
from part1_host.runtime import Part1Runtime
from part1_host.capture import pack_wire


@pytest.fixture
def env():
    # Whole S4 program, copied by the host; never mix an old STAFF with new owners.
    with Part1Runtime(part1_root=ROOT.parent/'수정본11/Part1', symbols=['BTCUSD'],
                      specials=['SPECIAL7'], start_epoch=1790035200) as before:
        with Part1Runtime(symbols=['BTCUSD'], specials=['SPECIAL7'], start_epoch=1790035200) as rt:
            staff = rt.modules['the_staff_of_moses']
            cache = staff.StaffPipeCache('', health_session='S3', monotonic=lambda: rt.clock.mono)
            server = staff.DataServer({}, staff.WonbiState(3), cache=cache)
            old = before.modules['the_staff_of_moses']
            old_cache = old.StaffPipeCache('', health_session='S3', monotonic=lambda: rt.clock.mono)
            server._test_reference = old.DataServer({}, old.WonbiState(3), cache=old_cache)
            server._test_reference_modules = before.modules
            lib = rt.modules['staff_snapshot']
            client = lib.SnapshotClient(transport=server.dispatch_multipart)
            compat = rt.modules['staff_compat'].StaffCompat(client)
            yield rt, staff, cache, server, lib, client, compat


def publish(cache, seq=1, tf='1m', rows=80, invalid=False, shift=0, dirty=False):
    rng = np.random.default_rng(42 + seq)
    times = np.arange(rows, dtype='<i8') * 60 + 1790035200 + shift
    values = rng.uniform(10, 20, (rows, 45))
    if invalid:
        values[:, 24:28] = np.nan
    if dirty:
        times[0] = times[1]
        times[[2, 3]] = times[[3, 2]]
        values[4, 8] = np.inf
    cache.publish_frame(pack_wire('BTCUSD', tf, times, np.arange(rows, dtype='<i8'), values, snapshot=seq))


def legacy(server, req):
    old = server._test_reference
    old.cache.restore_state(server.cache.export_state())
    old.cache.max_bars = server.cache.max_bars
    old.allowed_symbols, old.allowed_tfs = server.allowed_symbols, server.allowed_tfs
    old._is_weekend_market_closed = server._is_weekend_market_closed
    old.wonbi_state.set_sigma(server.wonbi_state.get_sigma())
    saved = {k:sys.modules.get(k) for k in server._test_reference_modules}
    try:
        sys.modules.update(server._test_reference_modules)
        return pickle.loads(old.dispatch_multipart([pickle.dumps(req)])[0])
    finally:
        for key, value in saved.items():
            if value is None: sys.modules.pop(key, None)
            else: sys.modules[key] = value


def equal(before, after):
    assert before.keys() == after.keys()
    for key, value in before.items():
        if isinstance(value, pd.DataFrame):
            pd.testing.assert_frame_equal(value, after[key], check_exact=True)
            assert value.attrs == after[key].attrs
        else:
            assert value == after[key]


def test_600_random_legacy_combinations_exact(env):
    rt, staff, cache, server, lib, client, compat = env
    for tf in ('1m', '5m', '1h'):
        publish(cache, tf=tf, rows=80, dirty=True)
    rng = random.Random(32026)
    choices = ['EMA', 'PRICE', 'RSI', 'STO', 'DI', 'HMA', 'FVG', 'SUPERTREND',
               'WONBI', 'SMA4', 'EMA9', 'HMA6', 'WMA17', 'UNKNOWN']
    successful = 0
    for i in range(600):
        if i and i % 100 == 0:
            for tf in ('1m', '5m', '1h'):
                publish(cache, seq=1+i//100, tf=tf, shift=i*60, dirty=True)
        if i % 75 == 0:
            server.wonbi_state.set_sigma(rng.choice([2.5, 3.0, 4.0]))
        req = {'symbol':'BTCUSD', 'timeframes':rng.sample(['1m','5m','1h'], rng.randint(1,3)),
               'indicators':rng.sample(choices, rng.randint(0,8)),
               'watch_ma_history_rows':rng.choice([0,50,100,650])}
        before, after = legacy(server, req), compat.request(req)
        equal(before, after)
        successful += 'error' not in before
    assert successful == 600


@pytest.mark.parametrize('case', ['missing','stale','exact30','invalid_indicator','invalid_symbol',
    'invalid_tf','empty_tf','bad_history','bad_tfs','bad_indicators','blank_symbol','weekend','bad_ma'])
def test_errors_and_readiness_match(env, case):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache, invalid=case=='invalid_indicator')
    req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['RSI']}
    if case=='missing': req['timeframes']=['5m']
    if case=='stale': rt.clock.mono += 30.1
    if case=='exact30': rt.clock.mono += 30
    if case=='invalid_symbol': server.allowed_symbols={'XAUUSD+'}
    if case=='invalid_tf': server.allowed_tfs={'5m'}
    if case=='empty_tf': req['timeframes']=[]
    if case=='bad_history': req.update(indicators=['SMA4'],watch_ma_history_rows=-1)
    if case=='bad_ma': req['indicators']=['SMMA14']
    if case=='bad_tfs': req['timeframes']='1m'
    if case=='bad_indicators': req['indicators']='RSI'
    if case=='blank_symbol': req['symbol']=''
    if case=='weekend': server._is_weekend_market_closed=lambda symbol: True
    equal(legacy(server, req), compat.request(req))


def test_seq_epoch_and_sigma_cache_invalidation(env, monkeypatch):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':[]}
    first=client.request(req).feeds['1m']
    calls=[]; original=lib.legacy_frame
    monkeypatch.setattr(lib,'legacy_frame',lambda *a: (calls.append(1), original(*a))[1])
    a=client.frame(first); a.iloc[0,1]=-99; a.attrs['indicator_validity']['EMA']=False
    assert client.request(req).feeds['1m'] is first
    assert client.frame(first).iloc[0,1] != -99
    assert client.frame(first).attrs['indicator_validity']['EMA']
    assert len(calls)==1
    for array in (first.time, first.volume, first.values):
        with pytest.raises(ValueError): array.setflags(write=True)
    with pytest.raises(TypeError): first.indicator_validity['EMA']=False
    cache.reconnect(); publish(cache)
    second=client.request(req).feeds['1m']
    assert second is not first and second.seq==first.seq and second.source_epoch!=first.source_epoch
    client.frame(second); assert len(calls)==2
    old=compat.request(req)['1m']
    server.wonbi_state.set_sigma(2.5)
    new=compat.request(req)['1m']
    assert new['wonbi_sigma'].iloc[-1]==2.5 and old['wonbi_sigma'].iloc[-1]==3
    equal(legacy(server,req),compat.request(req))


def test_new_transport_never_uses_pickle_or_server_dataframe(env, monkeypatch):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    with monkeypatch.context() as p:
        p.setattr(pickle,'loads',lambda *a,**k: pytest.fail('pickle on SNAPSHOT path'))
        p.setattr(pd,'DataFrame',lambda *a,**k: pytest.fail('server dataframe'))
        batch=client.request({'symbol':'BTCUSD','timeframes':['1m']})
    assert batch.feeds['1m'].values.shape==(80,45)


@pytest.mark.parametrize('damage',['missing','extra','truncated','schema','rows','seq','validity'])
def test_malformed_reply_does_not_poison_cache(env, damage):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    req={'symbol':'BTCUSD','timeframes':['1m']}
    first=client.request(req).feeds['1m']
    parts=server.snapshot_reply(req)
    if damage=='missing': parts.pop()
    elif damage=='extra': parts.append(b'')
    elif damage=='truncated': parts[-1]=parts[-1][:-1]
    else:
        meta=json.loads(parts[0])
        if damage=='schema': meta['schema_id']='unknown'
        if damage=='rows': meta['feeds'][0]['bars']=100000000
        if damage=='seq': meta['feeds'][0]['seq']='1'
        if damage=='validity': meta['feeds'][0]['indicator_validity']={'EMA':'yes'}
        parts[0]=json.dumps(meta).encode()
    with pytest.raises(ValueError): client.decode(parts)
    assert client.request(req).feeds['1m'] is first


def test_concurrent_clients_and_receive(env):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['EMA','SMA4']}
    def writer():
        for seq in range(2,30): publish(cache,seq)
    def reader():
        for _ in range(15):
            result=compat.request(req)
            assert len(result['1m'])==80
            assert result['1m'].attrs['source_epoch'].startswith('S3:')
    with ThreadPoolExecutor(5) as pool:
        futures=[pool.submit(writer),*[pool.submit(reader) for _ in range(4)]]
        for future in futures: future.result()


def test_real_zmq_json_and_legacy_share_endpoint(env, monkeypatch):
    zmq = REAL_ZMQ
    monkeypatch.setitem(sys.modules, 'zmq', zmq)
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    context=zmq.Context(); ready=threading.Event(); endpoint=[]; failures=[]
    def serve():
        try:
            with context.socket(zmq.REP) as sock:
                port=sock.bind_to_random_port('tcp://127.0.0.1')
                sock.setsockopt(zmq.RCVTIMEO,5000)
                endpoint.append(f'tcp://127.0.0.1:{port}'); ready.set()
                for _ in range(3): sock.send_multipart(server.dispatch_multipart(sock.recv_multipart()))
        except Exception as exc: failures.append(exc); ready.set()
    worker=threading.Thread(target=serve); worker.start(); assert ready.wait(5)
    try:
        req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['PRICE','SMA4']}
        for _ in range(2):
            with context.socket(zmq.REQ) as sock:
                sock.setsockopt(zmq.RCVTIMEO,5000); sock.setsockopt(zmq.LINGER,0)
                sock.connect(endpoint[0]); sock.send_pyobj(req); expected=sock.recv_pyobj()
        actual=rt.modules['staff_compat'].StaffCompat(lib.SnapshotClient(endpoint[0])).request(req)
        assert expected['error_code'] == 'SNAPSHOT_API_REQUIRED'
        assert 'SNAPSHOT API 사용' in expected['error']
        equal(legacy(server, req),actual)
    finally:
        worker.join(6); context.destroy(linger=0)
    assert not worker.is_alive() and not failures


def test_multifeed_error_order_preserved(env):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache, invalid=True)
    req={'symbol':'BTCUSD','timeframes':['1m','5m'],'indicators':['RSI']}
    equal(legacy(server,req),compat.request(req))


@pytest.mark.parametrize('max_bars', [1000, -1, 0])
def test_existing_max_bars_configuration_preserved(env, max_bars):
    rt, staff, cache, server, lib, client, compat = env
    cache.max_bars = max_bars
    if max_bars:
        publish(cache)
    req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['RSI']}
    equal(legacy(server,req),compat.request(req))


def test_copied_arithmetic_and_existing_strategy_files_unchanged():
    before=ROOT.parent/'수정본9/Part1/program'
    after=ROOT/'Part1/program'
    old=ast.parse((before/'THE STAFF OF MOSES.py').read_bytes())
    new=ast.parse((after/'staff_compat.py').read_bytes())
    for name in ('add_wonbi_features','apply_requested_features','validate_mt5_snapshot'):
        a=next(n for n in old.body if getattr(n,'name',None)==name)
        b=next(n for n in new.body if getattr(n,'name',None)==name)
        assert ast.dump(a)==ast.dump(b)
    for path in before.rglob('*.py'):
        if path.name=='THE STAFF OF MOSES.py': continue
        target = after/path.relative_to(before)
        allowed = {'strategy_SWEEP.py': {'StaffClient'}, 'strategy_FVG.py': {'StaffClient'},
                   'strategy_INDICATOR.py': {'StaffClient'}, 'monitor_OZ.py': {'StaffClient', 'OZSnapshotFeatures', 'SharedOZStaffClient'},
                   'manager_KIM.py': {'StaffClientV2'}}
        if path.name in allowed:
            trees = [ast.parse(p.read_bytes()) for p in (path, target)]
            for tree in trees:
                tree.body = [n for n in tree.body if getattr(n, 'name', None) not in allowed[path.name]]
            assert ast.dump(trees[0]) == ast.dump(trees[1])
        else:
            assert path.read_bytes()==target.read_bytes()
