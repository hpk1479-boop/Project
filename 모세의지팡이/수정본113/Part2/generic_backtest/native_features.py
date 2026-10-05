"""Read MT5 snapshots as-of the consumer; never run a percentile kernel."""
from dataclasses import replace
import math
import numpy as np

from .canonical import plain
from .contracts import GenericError
from .features import OptionalFeatureRegistry
from data_warehouse.native import parse_export, RECORD_DTYPE, LEGACY_RECORD_DTYPE, HEADER


class NativeSnapshotSource:
    def __init__(self, descriptor, symbol):
        parsed = parse_export(descriptor['path'])
        if parsed['symbol'] != symbol or descriptor.get('symbol') != symbol:
            raise GenericError('E_NATIVE_SOURCE', 'MT5 symbol mismatch')
        if parsed['payload_sha256'] != descriptor.get('payload_sha256'):
            raise GenericError('E_NATIVE_SOURCE', 'MT5 payload hash mismatch')
        if parsed['sampling_policy'] not in ('SECOND_SNAPSHOT_V1','TIMER_SNAPSHOT_V2'):
            raise GenericError('E_NATIVE_SOURCE', 'MT5 export requires a timed native snapshot')
        self.observation_ns=10**6 if parsed['format_version']==2 else 10**9
        dtype=RECORD_DTYPE if parsed['format_version']==2 else LEGACY_RECORD_DTYPE
        self.identity = parsed['payload_sha256']
        self.symbol = symbol
        self.feeds = {}
        for feed in parsed['feeds']:
            tf = feed['timeframe']
            if tf in self.feeds or feed['count'] <= 0:
                raise GenericError('E_NATIVE_SOURCE', 'Missing or duplicate MT5 feed: '+tf)
            rows = np.memmap(feed['path'], mode='r', dtype=dtype,
                             offset=HEADER.size, shape=(feed['count'],))
            # Chunk validation avoids allocating large boolean arrays for a long run.
            previous = None
            for i in range(0,len(rows),250000):
                block = rows[i:i+250000]
                ts, bars = block['observed_time'], block['bar_time']
                if (np.any(ts[1:] <= ts[:-1]) or np.any(bars[1:] < bars[:-1])
                        or np.any(bars > ts//(10**9//self.observation_ns)) or (previous is not None and
                            (int(ts[0]) <= previous[0] or int(bars[0]) < previous[1]))):
                    raise GenericError('E_NATIVE_SOURCE', 'Invalid MT5 observation order: '+tf)
                previous = int(ts[-1]), int(bars[-1])
            self.feeds[tf] = rows

    def row(self, tf, now_ns, bar_ns):
        rows = self.feeds[tf]
        # Both time and bar identity must match. In close mode the base bar is
        # just closed while larger TFs are forming; a global latest row is wrong.
        i = min(int(np.searchsorted(rows['observed_time'], now_ns//self.observation_ns, side='right')),
                int(np.searchsorted(rows['bar_time'], bar_ns//10**9, side='right'))) - 1
        if i < 0 or int(rows[i]['bar_time'])*10**9 != bar_ns:
            return None
        return rows[i]

    def previous_bar(self, tf, now_ns, bar_ns):
        rows=self.feeds[tf]
        i=min(int(np.searchsorted(rows['observed_time'],now_ns//self.observation_ns,side='right')),
              int(np.searchsorted(rows['bar_time'],bar_ns//10**9,side='left')))-1
        return None if i<0 else rows[i]


def _number(value):
    value = float(value)
    return value if math.isfinite(value) and abs(value) < 1e300 else None


class NativeFeatureRegistry:
    def __init__(self, requirements, owner, source):
        self.specs = requirements.features
        self.source = source
        self._latest = {}
        # HMA-only requests may still use their existing OPEN calculation.
        # No MOSES_PERCENTILE spec is ever passed to the Python provider.
        self.other = OptionalFeatureRegistry(replace(requirements, features=tuple(
            s for s in self.specs if s['kind'] != 'MOSES_PERCENTILE')), owner)
        for spec in self.specs:
            if spec['kind'] != 'MOSES_PERCENTILE': continue
            if spec.get('params'):
                raise GenericError('E_NATIVE_SOURCE', 'MT5 default export cannot satisfy custom parameters')
            if spec['timeframe'] not in source.feeds:
                raise GenericError('E_NATIVE_SOURCE', 'Missing MT5 timeframe: '+spec['timeframe'])

    def advance(self, view, change, *, materialize=True):
        values = self.other.advance(view, change, materialize=materialize)
        for spec in self.specs:
            if spec['kind'] != 'MOSES_PERCENTILE': continue
            bars = view.bars(spec['timeframe'])
            row = self.source.row(spec['timeframe'], view.token.now_ns, bars[-1].open_ns) if bars else None
            values[spec['name']] = ({'value':None,'status':'UNAVAILABLE'} if row is None
                                   else self._bundle(spec, row, bars[-1], view.token))
        self._latest = values
        return values if materialize else {}

    def _bundle(self, spec, row, bar, token):
        family = spec['family']; prefix = family.lower()
        keys = {'strategy_value':'value', 'lower':'lower', 'upper':'upper',
                'basis':'basis', 'regime_lower':'regime_lower', 'regime_upper':'regime_upper'}
        numbers = {key:_number(row[prefix+'_'+column]) for key,column in keys.items()}
        fields = [(key,{'value':value,'validity':'SOURCE_DEFINED_UNCERTIFIED' if value is not None else 'UNAVAILABLE',
                        'unavailable_reason':None if value is not None else 'SOURCE_EMPTY'})
                  for key,value in numbers.items()]
        # Only simple projections of recorded cells, no band/oscillator formulas.
        from pit.features.percentile.derived import StrategyStateProjector
        previous=self.source.previous_bar(spec['timeframe'],token.now_ns,bar.open_ns)
        prior={} if previous is None else {key:_number(previous[prefix+'_'+column]) for key,column in keys.items()}
        state = plain(StrategyStateProjector.project(family,numbers,prior))
        slope = _number(row[prefix+'_regime_slope'])
        state['regime_slope'] = slope
        state['long_regime']=slope is not None and slope>0 and state['regime_zone'] in ('IN','UPPER_OUT')
        state['short_regime']=slope is not None and slope<0 and state['regime_zone'] in ('IN','LOWER_OUT')
        columns = dict(state['staff_columns'])
        columns[('price' if family=='PRICE' else family)+'_regime_slope'] = slope
        state['staff_columns'] = tuple(columns.items())
        return {'family':family,'symbol':self.source.symbol,'timeframe':spec['timeframe'],
                'bar_time':int(row['bar_time'])*10**9,'bar_id':bar.bar_id,'bar_role':bar.state,
                'source_ordinal':token.source_ordinal,'asof_token':plain(token),
                'fields':fields,'strategy_state':state,'seed_policy':'SOURCE_DEFINED_ONLY',
                'parity_status':'MT5_RECORDED_SECOND_SNAPSHOT',
                'source_hash':self.source.identity,'observed_time_ns':int(row['observed_time'])*self.source.observation_ns,
                'calculation_policy':'MT5_SECOND_SNAPSHOT_NO_PYTHON_RECALC'}

    def snapshot(self):
        return dict(self._latest)

    def export_snapshot(self, snapshot, token):
        return {name:dict(value,asof_token=plain(token),source_ordinal=token.source_ordinal)
                if 'asof_token' in value else value for name,value in snapshot.items()}
