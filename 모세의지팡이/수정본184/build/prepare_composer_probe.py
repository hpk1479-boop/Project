"""Generate a temporary MSD1 one-day sample from the preserved MSD2 bytes."""
from __future__ import annotations
import datetime as dt
import itertools
import json
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1'/'program')]
from event_backtest.delta import write_delta,read_delta,update_hash
from event_backtest.keyframes import read_indexed
from event_backtest.settings import settings
import hashlib

warehouse=pathlib.Path(settings()['warehouse'])
paths=list((warehouse/'captures'/'XAUUSD+'/'BAR'/'2025'/'09').glob('*/capture.delta2'))
if len(paths)!=1:raise ValueError('expected one September source')
end=int(dt.datetime(2025,9,2,tzinfo=dt.timezone.utc).timestamp()*1000)
out=ROOT/'검증결과'/'composer_msd1_sample'
out.mkdir(parents=True,exist_ok=True)
expected=write_delta(itertools.takewhile(lambda row:row[0]<end,read_indexed(paths[0])),out/'capture.delta.gz')
actual=hashlib.sha256();count=0
for stamp,raw in read_delta(out/'capture.delta.gz'):
    update_hash(actual,stamp,raw);count+=1
if count!=expected['bundles'] or actual.hexdigest()!=expected['bundle_sha256']:
    raise AssertionError('MSD1 sample round trip')
(out/'sample.json').write_text(json.dumps(expected,indent=2),encoding='utf-8')
print(json.dumps(expected))
