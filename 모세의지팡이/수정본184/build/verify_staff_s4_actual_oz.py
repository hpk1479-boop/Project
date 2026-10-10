"""Targeted coverage supplement: actual golden uses superset, not OZ-only requests.

Replay all immutable actual golden raw frames through the public Snapshot wire
decoder and OZ client. Compare a fixed projection of the S0 superset response;
never generate a new golden or recalculate the expected values.
"""
import json
import sqlite3
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
from staff_s4_evidence import ROOT,OUT,S0,sha,write

sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.runtime import Part1Runtime
from staff_golden.contracts import read_frame,frame_digest

path=S0/'actual_run1/golden.sqlite'
source_hash=sha(path)
assert source_hash=='8309f7d91f6a742f39516af969099acf145ac6dcf8e1af61f7b24c4e6e97e9d0'
compared=0
with Part1Runtime(symbols=['XAUUSD+'], specials=['SPECIAL7'],start_epoch=1790035200) as rt, \
        sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
    schema=rt.modules['staff_schema'];lib=rt.modules['staff_snapshot'];oz=rt.modules['monitor_OZ']
    raw_columns=schema.BASE_COLUMNS+schema.PIPE_VALUE_COLUMNS[4:]
    columns=raw_columns+[f'{p}_{name}' for p in ('price','RSI','STO','DI')
        for name in ('percentile_zone','percentile_in','regime_zone','regime_in','regime_slope')]
    columns+=['RSI_slope','STO_slope','DI_slope','atr_14',
              'wonbi_mid','wonbi_std','wonbi_upper','wonbi_lower','wonbi_sigma']
    tf_by_hash={}
    for (response,) in db.execute('SELECT response FROM cases'):
        for tf,item in json.loads(response).items():
            if isinstance(item,dict) and 'frame_sha256' in item:
                tf_by_hash.setdefault(item['frame_sha256'],tf)
    wire=[]
    client=oz.StaffClient('',transport=lambda parts:wire)
    for seq,(digest,blob) in enumerate(db.execute('SELECT sha,data FROM frames ORDER BY sha'),1):
        expected_all=read_frame(db,blob)
        tf=tf_by_hash[digest]
        attrs={k:expected_all.attrs[k] for k in ('source_epoch','indicator_validity')}
        snapshot=SimpleNamespace(
            time=pd.DatetimeIndex(expected_all['time']).as_unit('s').asi8,
            volume=expected_all['volume'].to_numpy(dtype='<i8'),
            values=expected_all[schema.PIPE_VALUE_COLUMNS].to_numpy(dtype='<f8'),
            seq=seq,source_epoch=attrs['source_epoch'],indicator_validity=attrs['indicator_validity'],
            received_at=float(seq))
        wire=lib.encode_reply([('XAUUSD+',tf,snapshot)],sigma=float(expected_all['wonbi_sigma'].iloc[-1]))
        actual=client.request('XAUUSD+',[tf])[tf]
        assert list(actual.columns)==columns
        expected=expected_all[columns].copy(deep=True)
        expected.attrs=attrs
        pd.testing.assert_frame_equal(expected,actual,check_exact=True)
        assert expected.attrs==actual.attrs and frame_digest(expected)==frame_digest(actual)
        compared+=1
        if compared%1000==0: print('actual OZ frames:',compared,flush=True)
assert compared==4302 and sha(path)==source_hash
write(OUT/'actual_oz_supplement.json',{'equal':True,'frames':compared,'source_sha256':source_hash,
    'reason':'Actual golden requests include EMA/FVG/MA/SUPERTREND, so the OZ-only branch had zero requests in the full G2 run.',
    'path':'S0 actual raw projection -> Snapshot JSON/arrays -> actual OZ StaffClient -> OZSnapshotFeatures',
    'expected_projection_columns':columns,'attrs':['source_epoch','indicator_validity'],
    'excluded_attrs':'Only WATCH MA request metadata, not used by default OZ requests',
    'exact_value_dtype_column_order_index_attrs_and_frame_digest':True,'golden_regenerated':False})
print('actual OZ exact:',compared)
