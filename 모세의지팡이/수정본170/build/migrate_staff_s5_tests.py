from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(rel,fn):
 p=ROOT/rel;p.write_text(fn(p.read_text('utf-8-sig')),encoding='utf-8')
edit('Part1/audit/harness.py',lambda t:t.replace('        self.manager = None','''        self.compat_module = self.modules['staff_compat']
        self.snapshots = self.modules['staff_snapshot'].SnapshotClient(transport=self.server.dispatch_multipart)
        self.compat = self.compat_module.StaffCompat(self.snapshots)
        self.manager = None''').replace('    def staff_handle(self, request):','''    def raw_frame(self, symbol, tf, cache=None):
        cache = cache or self.server.cache
        return self.modules['staff_schema'].legacy_frame(symbol, tf, cache.snapshot(symbol, tf), cache.max_bars)

    def data_request(self, request):
        # Preserve exception assertions at the new public snapshot boundary.
        if str(request.get('kind', '')).upper() in ('PING','SOURCE_HEALTH','SET_WONBI_SIGMA'):
            return self.server.handle(request)
        self.server.snapshot_reply(request)
        result = self.compat.request(request)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result

    def staff_handle(self, request):'''))
for file in ('test_baseline.py','test_ack_pressure.py','test_slow_feed.py','test_oz_trigger_profiles.py'):
 def migrate(t):
  t=t.replace('w.server.handle(', 'w.data_request(').replace('w.server.cache.get(', 'w.raw_frame(')
  t=t.replace("len(cache.get('BTCUSD', '1m'))", "len(w.raw_frame('BTCUSD', '1m', cache))")
  t=t.replace('w.staff.apply_requested_features','w.compat_module.apply_requested_features')
  t=t.replace("patch.object(w.staff,'apply_requested_features'", "patch.object(w.compat_module,'apply_requested_features'")
  t=t.replace("patch.object(w.server.cache,'get',", "patch.object(w.snapshots,'frame',")
  return t
 edit('Part1/audit/'+file,migrate)
edit('Part1/watch_ma_validation/test_live_integration.py',lambda t:t.replace('w.server.handle(', 'w.data_request(').replace('assert w.server._watch_ma_features is None',"assert not hasattr(w.server, '_watch_ma_features')").replace('w.server._watch_ma_features','w.compat._watch_ma_features'))
edit('Part2/validation_suite/test_staff_s1.py',lambda t:t.replace("nodes(PROGRAM / 'THE STAFF OF MOSES.py')[name]", "nodes(PROGRAM / ('THE STAFF OF MOSES.py' if name == 'WonbiState' else 'staff_compat.py'))[name]").replace("staff = rt.modules['staff'] if 'staff' in rt.modules else rt.modules['the_staff_of_moses']", "staff = rt.modules['staff_compat']\n        assert not hasattr(rt.modules['the_staff_of_moses'], 'apply_requested_features')"))
# A client view keeps raw normalization/copy assertions away from server storage.
helper='''
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

'''
edit('Part2/validation_suite/test_staff_s2.py',lambda t:t.replace('def raw(',helper+'def raw('))
def s2(t):
 t=t.replace("original, calls = cache._legacy_frame, []", "reader = view(cache)\n    import staff_snapshot as lib\n    original, calls = lib.legacy_frame, []")
 t=t.replace('calls.append(args[-1].seq)','calls.append(args[2].seq)')
 t=t.replace("monkeypatch.setattr(cache, '_legacy_frame', build)","monkeypatch.setattr(lib, 'legacy_frame', build)")
 t=t.replace('original = cache._legacy_frame',"reader = view(cache)\n    import staff_snapshot as lib\n    original = lib.legacy_frame")
 t=t.replace('def build(symbol, tf, snapshot):','def build(symbol, tf, snapshot, max_bars):').replace('return original(symbol, tf, snapshot)','return original(symbol, tf, snapshot, max_bars)')
 t=t.replace("rt.staff_server.cache.get('BTCUSD','1m')", "(rt.staff_server.cache.get('BTCUSD','1m') if part1 != ROOT/'Part1' else view(rt.staff_server.cache).get('BTCUSD','1m'))")
 t=t.replace('restored.get(', 'view(restored).get(')
 # Default isolated client for each raw read, except the memoization test.
 t=t.replace('cache.get(', 'view(cache).get(').replace('rt.staff_server.view(cache).get(', 'rt.staff_server.cache.get(')
 start=t.index('def test_one_lazy_frame');end=t.index('@pytest.mark.parametrize',start)
 t=t[:start]+t[start:end].replace('view(cache).get(', 'reader.get(')+t[end:]
 t=t.replace("pool.submit(cache.get,", "pool.submit(reader.get,")
 t=t.replace("    start = threading.Barrier(5)", "    from staff_snapshot import SnapshotClient\n    from staff_compat import StaffCompat\n    compat = StaffCompat(SnapshotClient(transport=server.dispatch_multipart))\n    start = threading.Barrier(5)")
 t=t.replace("frame = server.handle(","frame = compat.request(")
 return t
edit('Part2/validation_suite/test_staff_s2.py',s2)
print('S1/S2/audit tests migrated, assertions retained')
