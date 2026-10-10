"""S4b: switch explicit generic/WATCH requests, preserving the MA owner verbatim."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p = root/'Part1/program/monitor_OZ.py'
s = p.read_text('utf-8')
a, b = s.index('class StaffClient:'), s.index('\nclass OZFactMemo:')
block = s[a:b]
block = block.replace('timeout_ms: int = 3000):', 'timeout_ms: int = 3000, *, transport=None):', 1)
block = block.replace('        self._connect()\n', '''        self._connect()
        from staff_snapshot import SnapshotClient
        from staff_compat import StaffCompat
        self.snapshots = SnapshotClient(transport=transport or self._snapshot_exchange)
        self.compat = StaffCompat(self.snapshots)
''', 1)
block = block.replace('    def request(', '''    def _snapshot_exchange(self, parts):
        self.socket.send_multipart(parts)
        return self.socket.recv_multipart()

    def request(''', 1)
block = block.replace('            self.socket.send_pyobj(req)\n            data = self.socket.recv_pyobj()', '''            if indicators is not None:
                data = self.compat.request(req)
            else:
                self.socket.send_pyobj(req)
                data = self.socket.recv_pyobj()''', 1)
p.write_text(s[:a]+block+s[b:], 'utf-8')
p = root/'Part2/live_replay/runtime.py'
s = p.read_text('utf-8').replace('self.symbol=symbol;self.controller=runtime.generic;self.client=runtime.staff',
    "self.symbol=symbol;self.controller=runtime.generic;self.client=O.StaffClient('', transport=runtime.server.dispatch_multipart)")
p.write_text(s, 'utf-8')

# Transport traces now hold raw multipart. Inspect the actual consumer response
# for the same MA arithmetic assertion, preserving every original assertion.
p = root/'Part1/watch_ma_validation/test_live_integration.py'
s = p.read_text('utf-8')
s = s.replace('    monitor.run(OneCycle())\n    delivered =', '''    received = []
    original_request = monitor.client.compat.request
    def capture_response(request):
        result = original_request(request)
        received.append((request, result))
        return result
    monitor.client.compat.request = capture_response
    monitor.run(OneCycle())
    delivered =''', 1)
s = s.replace("    frame = slope_request['reply']['1m']", "    frame = next(reply['1m'] for req, reply in received if req['indicators'] == ['SMA17'])\n    assert slope_request['protocol'] == 'SNAPSHOT'\n    assert w.server._watch_ma_features is None")
p.write_text(s, 'utf-8')
