from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part2/validation_suite/test_staff_s3.py'
t=p.read_text('utf-8')
start=t.index('@pytest.fixture');end=t.index('\ndef publish',start)
t=t[:start]+'''@pytest.fixture
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

'''+t[end:]
start=t.index('def legacy(');end=t.index('\ndef equal(',start)
t=t[:start]+'''def legacy(server, req):
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

'''+t[end:]
t=t.replace('        equal(expected,actual)\n    finally:', '''        assert expected['error_code'] == 'SNAPSHOT_API_REQUIRED'
        assert 'SNAPSHOT API 사용' in expected['error']
        equal(legacy(server, req),actual)
    finally:''')
p.write_text(t,encoding='utf-8')
p=ROOT/'Part2/validation_suite/test_staff_s4.py';t=p.read_text('utf-8')
t=t.replace('from test_staff_s3 import env, publish, equal','from test_staff_s3 import env, publish, equal, legacy\nfrom test_staff_s2 import view')
t=t.replace("side_effect=AssertionError('server calculation'))", "side_effect=AssertionError('server calculation'), create=True)")
t=t.replace('cache.get(', 'view(cache).get(').replace('server.handle(req)', 'legacy(server, req)')
t=t.replace('is not server._watch_ma_features.frames','is not server._test_reference._watch_ma_features.frames')
t=t.replace('is not server._watch_ma_features','is not server._test_reference._watch_ma_features')
t=t.replace('server._watch_ma_features is None',"not hasattr(server, '_watch_ma_features')")
t=t.replace("'THE STAFF OF MOSES.py',*[f'SPECIAL", "'staff_compat.py',*[f'SPECIAL")
# Preserve the old STAFF whole-file assertion as a complete AST scope guard:
t=t.replace('    # Shared caching/TTL', '''    # S5 removes only the explicitly authorized server calculation/storage nodes.
    from test_staff_s5 import assert_staff_scope
    assert_staff_scope()
    # Shared caching/TTL''')
p.write_text(t,encoding='utf-8')
print('S3/S4 comparisons use whole frozen S4 program oracle')
