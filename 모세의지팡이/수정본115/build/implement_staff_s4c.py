"""S4c: native OZ facts and the general ATR at the consuming client."""
from pathlib import Path

root=Path(__file__).resolve().parents[1]
p=root/'Part1/program/monitor_OZ.py'
s=p.read_text('utf-8')
a,b=s.index('class StaffClient:'),s.index('\nclass OZFactMemo:')
block=s[a:b].replace('*, transport=None):','*, transport=None, facts=None):',1)
block=block.replace('        self.compat = StaffCompat(self.snapshots)',
    '        self.compat = StaffCompat(self.snapshots)\n        self.oz_features = OZSnapshotFeatures(self.snapshots, facts=facts)')
block=block.replace('                self.socket.send_pyobj(req)\n                data = self.socket.recv_pyobj()',
    '                data = self.oz_features.request(req)')
block=block.replace('# ATR14/원비를 포함한 공통 파생값은 Staff가 계산한 값을 그대로 소비합니다.',
    '# Snapshot의 원시 열에서 클라이언트 소유 Fact를 계산합니다.')
s=s[:a]+block+s[b:]
pos=s.index('\nclass SharedOZStaffClient:')
new='''
class OZSnapshotFeatures:
    """OZ owns its band/regime facts; same publication reuses the shared memo."""
    def __init__(self, client, *, facts=None):
        self.client = client
        self.facts = facts if facts is not None else OZFactMemo()
        self._raw = {}
        self._lock = threading.RLock()

    @staticmethod
    def compose(raw, tf, sigma):
        from indicator_facts import standalone_frame, add_mt5_basis_slopes
        from staff_compat import add_wonbi_features
        out = raw.copy(deep=True)
        # These S1 owners and their order are the exact original STAFF sequence.
        for owner in (add_price_band_state_features, add_rsi_band_state_features,
                      add_sto_band_state_features, add_di_band_state_features):
            out = owner(out)
        out = add_mt5_basis_slopes(out)
        out[EXTERNAL_ATR_COLUMN] = standalone_frame(raw, tf).get('ATR14_GENERAL')
        # Temporary Python wonbi arithmetic is unchanged until S7.
        return add_wonbi_features(out, sigma=sigma)

    def request(self, req):
        with self._lock:
            batch = self.client.request(req)
            if batch.error is not None:
                return dict(batch.error)
            if batch.closed:
                return {}
            result = {}
            for tf, snapshot in batch.feeds.items():
                key = (snapshot.symbol, tf)
                cached = self._raw.get(key)
                if cached is None or cached[0] != snapshot.identity:
                    cached = (snapshot.identity, self.client.frame(snapshot))
                    self._raw[key] = cached
                raw = cached[1]
                enriched = self.facts.get(raw, ('snapshot_features', batch.sigma),
                    lambda: self.compose(raw, tf, batch.sigma))
                result[tf] = enriched.copy(deep=True)
            return result

'''
s=s[:pos]+new+s[pos:]
s=s.replace('cache_ttl: float = LOOP_SLEEP_SEC):\n        self.client = StaffClient(endpoint, timeout_ms)\n        self.facts = OZFactMemo()',
    'cache_ttl: float = LOOP_SLEEP_SEC, *, transport=None):\n        self.facts = OZFactMemo()\n        self.client = StaffClient(endpoint, timeout_ms, transport=transport, facts=self.facts)',1)
p.write_text(s,'utf-8')

p=root/'Part2/live_replay/runtime.py';s=p.read_text('utf-8')
s=s.replace('    def __init__(self, runtime): self.runtime=runtime\n    def request(self, symbol, timeframes, indicators=None, **extra):', '''    def __init__(self, runtime):
        self.runtime=runtime
        self.oz_client=O.StaffClient('', transport=self.exchange_snapshot)
    def exchange_snapshot(self, parts):
        reply=self.runtime.server.dispatch_multipart(parts)
        error=json.loads(reply[0]).get('error')
        if error:
            req=json.loads(parts[1])
            message=error['error']
            if message.startswith('request processing failed: '):
                message=message.split(': ',2)[-1]
            self.runtime.audit.append({'kind':'STAFF_UNAVAILABLE','timestamp_ns':clock.now_ns(),
                'symbol':req['symbol'],'timeframes':req['timeframes'],'error':message})
        return reply
    def request(self, symbol, timeframes, indicators=None, **extra):''',1)
s=s.replace('        inds=list(O.REQUIRED_INDS if indicators is None else indicators)',
    "        if indicators is None:\n            return self.oz_client.request(symbol, timeframes, **extra)\n        inds=list(indicators)",1)
a=s.index('            shared=bare(O.SharedOZStaffClient)');b=s.index('            runtime.oz_staff[symbol]=shared',a)
s=s[:a]+'''            shared=O.SharedOZStaffClient('',
                cache_ttl=float(runtime.config.get('LOOP_SLEEP_SEC','0.5')),
                transport=runtime.staff.exchange_snapshot)
'''+s[b:]
s=s.replace('        self.client=runtime.oz_staff[symbol]\n        self.checkpoint=None',
    '        self.client=runtime.oz_staff[symbol]\n        self._oz_facts=self.client.facts\n        self.checkpoint=None',1)
s=s.replace('transport=runtime.server.dispatch_multipart','transport=runtime.staff.exchange_snapshot')
s=s.replace('transport=self.server.dispatch_multipart','transport=self.staff.exchange_snapshot')
p.write_text(s,'utf-8')
