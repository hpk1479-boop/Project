"""Completed-M1 aggregation extracted from pit/candles.py:completed_seed.

The integer UTC-grid boundary is the current Generic CalendarRegistry formula.
This does NOT replace GenericMarketCore.apply_tick, its completed-at-order
boundary, or its forming higher-TF bars. No synthetic empty bars are created.
"""
from __future__ import annotations
import numpy as np

M1_DTYPE = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),
                    ('close','<f8'),('tick_volume','<u8'),('spread','<i4'),('real_volume','<u8')])


def aggregate_completed_m1(rows, minutes: int, cutoff_seconds: int):
    if type(minutes) is not int or minutes < 1:
        raise ValueError('positive integer timeframe required')
    rows = np.asarray(rows)
    groups = []
    previous = -1
    width = minutes * 60
    for r in rows:
        start = int(r['time'])
        if start <= previous or start + 60 > cutoff_seconds:
            raise ValueError('unordered, duplicate or forming M1 source')
        previous = start
        a = start // width * width
        b = a + width
        if b > cutoff_seconds:
            continue
        vals = [float(r[k]) for k in ('open','high','low','close')]
        if not groups or groups[-1]['start'] != a:
            groups.append({'start': a, 'end': b, 'open': vals[0], 'high': vals[1],
                           'low': vals[2], 'close': vals[3], 'volume': int(r['tick_volume']),
                           'minutes': [start]})
        else:
            x = groups[-1]
            x['high'] = max(x['high'], vals[1]); x['low'] = min(x['low'], vals[2])
            x['close'] = vals[3]; x['volume'] += int(r['tick_volume']); x['minutes'].append(start)
    dtype = np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),
                      ('close','<f8'),('tick_volume','<u8'),('complete','?')])
    out = np.empty(len(groups), dtype=dtype)
    for i, x in enumerate(groups):
        full = len(x['minutes']) == minutes and x['minutes'][0] == x['start']
        out[i] = (x['start'], x['open'], x['high'], x['low'], x['close'], x['volume'], full)
    return out
