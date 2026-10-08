"""S4d: Composer/SPECIAL transport boundary; no strategy or policy edits."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'Part1/program/manager_KIM.py';s=p.read_text('utf-8')
a,b=s.index('class StaffClientV2:'),s.index('\nclass SpecialPluginAPI:')
block=s[a:b].replace('timeout_ms: int = 5000):','timeout_ms: int = 5000, *, transport=None):',1)
block=block.replace('        self._connect()\n', '''        self._connect()
        from staff_snapshot import SnapshotClient
        from staff_compat import StaffCompat
        self.snapshots = SnapshotClient(transport=transport or self._snapshot_exchange)
        self.compat = StaffCompat(self.snapshots)
''',1)
block=block.replace('    def request(', '''    def _snapshot_exchange(self, parts):
        self.socket.send_multipart(parts)
        return self.socket.recv_multipart()

    def request(''',1)
block=block.replace('self.socket.send_pyobj({\n                "symbol": symbol,',
                    'reply = self.compat.request({\n                "symbol": symbol,',1)
block=block.replace('            reply = self.socket.recv_pyobj()\n','',1)
p.write_text(s[:a]+block+s[b:],'utf-8')

p=root/'Part2/live_replay/runtime.py';s=p.read_text('utf-8')
s=s.replace("        self.oz_client=O.StaffClient('', transport=self.exchange_snapshot)",
    "        self.oz_client=O.StaffClient('', transport=self.exchange_snapshot)\n        self.compat_client=C.StaffClientV2('', transport=self.exchange_snapshot)",1)
s=s.replace("            return self.runtime.server.handle({'symbol':symbol,'timeframes':list(timeframes),'indicators':inds,**extra})",
    "            if extra:\n                return self.oz_client.request(symbol, timeframes, inds, **extra)\n            return self.compat_client.request(symbol, timeframes, inds)",1)
p.write_text(s,'utf-8')
