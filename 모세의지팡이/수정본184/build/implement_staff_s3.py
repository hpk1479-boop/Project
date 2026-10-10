"""Mechanical S3 extraction; numerical bodies and legacy path remain unchanged."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
program = ROOT / 'Part1/program'
source = (program/'THE STAFF OF MOSES.py').read_text('utf-8-sig')
tree = ast.parse(source)
def node(name):
    return next(n for n in tree.body if getattr(n, 'name', None) == name)
def segment(n):
    return ast.get_source_segment(source, n)
def assignment(name):
    return segment(next(n for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))
schema = '''"""Snapshot API v1 schema. This does not change the EA wire protocol."""
import numpy as np
import pandas as pd

PROTOCOL = 'staff-snapshot/1'
REQUEST_TAG = b'STAFF_SNAPSHOT_V1'
SCHEMA_ID = 'staff-wire-v1-45'
'''
schema += assignment('PIPE_VALUE_COLUMNS')+'\n'+assignment('BASE_COLUMNS')+'\n'
method = next(n for n in node('StaffPipeCache').body if getattr(n,'name',None)=='_legacy_frame')
body = segment(method).replace('def _legacy_frame(self, symbol, timeframe, snapshot)',
                              'def legacy_frame(symbol, timeframe, snapshot, max_bars=650)')
body = body.replace('self.max_bars','max_bars')
# ast source segment keeps method indentation for body; remove one class level.
lines=body.splitlines(); body=lines[0]+'\n'+'\n'.join(l[4:] for l in lines[1:])
(program/'staff_schema.py').write_text(schema+'\n'+body+'\n',encoding='utf-8')

compat = '''"""Opt-in client-side legacy adapter. S3 leaves existing clients unchanged.

The temporary Python wonbi body is byte-for-byte S2 arithmetic, guarded by AST
tests. MT5 wonbi mapping is deliberately deferred to S7.
"""
import threading
from typing import Iterable
import numpy as np
import pandas as pd
from staff_schema import BASE_COLUMNS
from indicator_facts import add_ema_derived, add_atr14_feature, add_supertrend, add_mt5_basis_slopes
from monitor_OZ import add_price_band_state_features, add_rsi_band_state_features, add_sto_band_state_features, add_di_band_state_features
from strategy_FVG import add_fvg_features
WONBI_LENGTH = 4
WONBI_DEFAULT_SIGMA = 3.0
'''
for name in ('add_wonbi_features','apply_requested_features'):
    compat += '\n'+segment(node(name))+'\n'
compat += '\n'+assignment('MT5_REQUIRED_BY_INDICATOR')+'\n'
compat += '\n'+segment(node('validate_mt5_snapshot'))+'\n'
handler=next(n for n in node('DataServer').body if getattr(n,'name',None)=='_handle_request')
block=segment(handler)
block=block[block.index('        # Canonical MA requests'):]
block=block.replace('self.wonbi_state.get_sigma()','sigma')
compat += '''

class StaffCompat:
    """Own WATCH history per client; copy every response and serialize requests."""
    def __init__(self, client):
        self.client = client
        self._watch_ma_features = None
        self._lock = threading.RLock()

    def request(self, req):
        with self._lock:
            try:
                batch = self.client.request(req)
                if batch.error is not None:
                    return dict(batch.error)
                if batch.closed:
                    return {}
                symbol = str(req.get('symbol', '')).strip()
                indicators = [str(x).strip().upper() for x in req.get('indicators', []) if str(x).strip()]
                prepared = {}
                for tf, snapshot in batch.feeds.items():
                    df = self.client.frame(snapshot)
                    validate_mt5_snapshot(df, symbol, tf, indicators)
                    prepared[tf] = df
                return self._compose(req, symbol, indicators, prepared, batch.sigma)
            except (ValueError, RuntimeError) as exc:
                return {'error': f'request processing failed: {type(exc).__name__}: {exc}'}

    def _compose(self, req, symbol, indicators, prepared, sigma):
        response = {}
'''+block+'\n'
(program/'staff_compat.py').write_text(compat,encoding='utf-8')
print('created schema and compatibility adapter from exact S2 source')
