"""Retain exact coordinates of pre-existing MT5 uninitialized history values."""
import sys,numpy as np
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture
paths=[next((OUT/n).glob('capture_*')) for n in ('actual_mt5_v1','actual_mt5_v2','final_mt5_v2')]
paths.append(next((S0/'actual_mt5').glob('capture_*')))
samples=[]
for path in paths:
    info=capture.parse_capture_manifest(path);feed=info['feeds'][-1]
    samples.append(next(iter(capture.FeedReplay(info['symbol'],feed))))
rows=[];a=samples[0]
for name,b in zip(('initial_v2','final_v2','frozen_S0'),samples[1:]):
    equal=(a[3]==b[3]) | (np.isnan(a[3])&np.isnan(b[3]))
    positions=np.argwhere(~equal)
    rows.append({'comparison':name,'differences':len(positions),
        'coordinates':[{'row':int(i),'column':capture.wire_schema().PIPE_VALUE_COLUMNS[j],
                        'v1':float(a[3][i,j]),'other':float(b[3][i,j])} for i,j in positions],
        'time_identical':np.array_equal(a[1],b[1]),'volume_identical':np.array_equal(a[2],b[2]),
        'ohlc_identical':np.array_equal(a[3][:,:4],b[3][:,:4]),
        'last_20_rows_identical':np.array_equal(a[3][-20:],b[3][-20:],equal_nan=True)})
write(OUT/'existing_MT5_history_diagnostic.json',{'feed':'XAUUSD+/1d','first_observed':a[0],
    'diagnosis':'Subnormal, process-dependent indicator values in oldest warm-up rows; found in both unchanged v1 and frozen S0 captures. No transport conversion or indicator formula change applied.',
    'comparisons':rows})
print([(r['comparison'],r['differences'],r['last_20_rows_identical']) for r in rows])
