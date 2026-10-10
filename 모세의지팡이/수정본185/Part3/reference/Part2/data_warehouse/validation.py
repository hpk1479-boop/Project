"""Pure exact validators shared as build-time copies, never runtime cross-imports."""
from __future__ import annotations
import hashlib
import json
import math
import struct
import numpy as np
from calculations.timeframe import M1_DTYPE

M1_COLUMNS = M1_DTYPE.names


def normalize_m1(rows) -> np.ndarray:
    rows = np.asarray(rows)
    if rows.ndim != 1 or rows.dtype.names is None or set(M1_COLUMNS) - set(rows.dtype.names):
        raise ValueError('M1_COLUMNS_OR_SHAPE')
    out = np.empty(len(rows), dtype=M1_DTYPE)
    for name in M1_COLUMNS:
        values = rows[name]
        if name in ('time','tick_volume','spread','real_volume'):
            if not np.issubdtype(values.dtype,np.integer):raise ValueError('M1_INTEGER_DTYPE:'+name)
            if len(values) and np.any(values < 0):raise ValueError('M1_NEGATIVE:'+name)
        elif values.dtype.kind != 'f' or values.dtype.itemsize != 8:
            raise ValueError('M1_FLOAT64_REQUIRED:'+name)
        out[name] = values
        # Reject lossy narrowing, including uint64 volume and spread overflow.
        if not np.array_equal(out[name], values, equal_nan=True):raise ValueError('M1_CAST_LOSS:'+name)
    return out


def inspect_m1(rows, *, completed_before: int | None = None) -> dict:
    rows = normalize_m1(rows)
    detail = {'row_count':len(rows), 'duplicates':0, 'sorted':True,
              'first_timestamp':int(rows['time'][0]) if len(rows) else None,
              'last_timestamp':int(rows['time'][-1]) if len(rows) else None,
              'dtype':rows.dtype.descr, 'status':'PASS', 'mismatch':None}
    if len(rows):
        times=rows['time']; detail['duplicates']=int(len(times)-len(np.unique(times)))
        detail['sorted']=bool(np.all(times[1:]>times[:-1]))
        conditions=[('DUPLICATE_OR_SORT', np.r_[False,times[1:]<=times[:-1]]),
                    ('M1_ALIGNMENT',times%60!=0)]
        if completed_before is not None:conditions.append(('FORMING_BAR',times+60>completed_before))
        for name in ('open','high','low','close'):
            conditions.append(('NONFINITE_'+name,~np.isfinite(rows[name])))
        conditions.append(('OHLC_ORDER', (rows['low']>np.minimum(rows['open'],rows['close'])) |
            (np.maximum(rows['open'],rows['close'])>rows['high']) | (rows['low']>rows['high'])))
        bad=[(int(np.flatnonzero(mask)[0]),code) for code,mask in conditions if np.any(mask)]
        if bad:
            index,code=min(bad);detail.update(status='FAIL',mismatch={'timestamp':int(times[index]),'column':code})
    detail['sha256']=hashlib.sha256(rows.tobytes()).hexdigest()
    return detail


def compare_m1(expected, actual) -> dict:
    a=normalize_m1(expected);b=normalize_m1(actual)
    report={'status':'PASS','row_count':len(a),'dtype_mt5':np.asarray(expected).dtype.descr,'dtype_db':np.asarray(actual).dtype.descr,'canonical_dtype':a.dtype.descr,
            'first_timestamp':int(a['time'][0]) if len(a) else None,
            'last_timestamp':int(a['time'][-1]) if len(a) else None,
            'expected_sha256':hashlib.sha256(a.tobytes()).hexdigest(),
            'actual_sha256':hashlib.sha256(b.tobytes()).hexdigest(),'mismatch':None}
    n=min(len(a),len(b));bad=[]
    for column in M1_COLUMNS:
        x,y=a[column][:n],b[column][:n]
        equal=(x.view('<u8')==y.view('<u8')) if x.dtype.kind=='f' else (x==y)
        if not np.all(equal):bad.append((int(np.flatnonzero(~equal)[0]),M1_COLUMNS.index(column),column))
    if bad:
        i,_,column=min(bad)
        report.update(status='FAIL',mismatch={'timestamp':int(a['time'][i]),'column':column,
            'MT5':a[column][i].item(),'DuckDB':b[column][i].item()})
    elif len(a)!=len(b):
        report.update(status='FAIL',mismatch={'timestamp':int(a['time'][n]) if n<len(a) else int(b['time'][n]),
            'column':'row_count','MT5':len(a),'DuckDB':len(b)})
    if report['status']=='PASS':
        # Even equal invalid arrays do not receive PASS. Never sort/deduplicate.
        for label,rows in [('MT5',a),('DuckDB',b)]:
            structural=inspect_m1(rows)
            if structural['status']!='PASS':
                report.update(status='FAIL',side=label,structure=structural,mismatch=structural['mismatch'])
                break
    return report


def range_coverage(rows, start: int, end: int) -> dict:
    """Conservative observed envelope, not a claim about broker history completeness."""
    rows=normalize_m1(rows)
    first=int(rows['time'][0]) if len(rows) else None
    last_end=int(rows['time'][-1])+60 if len(rows) else None
    status='PASS' if len(rows) and first<=start and last_end>=end else 'PARTIAL'
    gaps=[{'start':int(a)+60,'end':int(b),'status':'SESSION_OR_MISSING_UNVERIFIED'}
          for a,b in zip(rows['time'][:-1],rows['time'][1:]) if b>a+60]
    return {'status':status,'requested':[start,end],'available':[first,last_end],
            'gaps':gaps,'broker_history_completeness':'UNVERIFIED','range_semantics':'UTC_HALF_OPEN'}
