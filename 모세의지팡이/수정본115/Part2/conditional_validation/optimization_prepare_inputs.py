"""Prepare unchanged consecutive raw prefixes for optimization validation.

Does not fabricate OHLC/warmup or alter any original archive. Empty prehistory
chunks may be consolidated. The last duplicate-millisecond group is retained
whole, so actual rows can exceed the requested limit. Outputs are validation
raw inputs, not a feature cache. Existing output directories are never reused.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from generic_backtest.history.cache import RawChunkWriter,verify_archive
from generic_backtest.canonical import identity,write_json
from pit.archive.reader import TICK_DTYPE


def prepare(source,target,n):
    if n<=0:raise ValueError('rows must be positive')
    if target.exists():raise FileExistsError(f'Refusing to overwrite {target}')
    m=verify_archive(source);chunks=[d for d in m['chunks'] if d['count']]
    if not chunks:raise ValueError('No raw ticks in the supplied archive')
    writer=RawChunkWriter(target,m['stream_namespace']);out=[];remaining=n
    digest=hashlib.sha256();first=chunks[0]['coverage_start_ns']
    if m['coverage_start_ns']<first:
        out.append(writer.write(0,np.empty(0,dtype=TICK_DTYPE),m['coverage_start_ns'],first))
    previous=first;sources=[]
    for d in chunks:
        if remaining<=0:break
        rows=np.load(source/d['file'],allow_pickle=False,mmap_mode='r');k=min(remaining,len(rows))
        while k<len(rows) and rows['time_msc'][k]==rows['time_msc'][k-1]:k+=1
        rows=rows[:k];end=(int(rows['time_msc'][-1])+1)*1_000_000
        if k==d['count']:end=d['coverage_end_ns']
        if d['coverage_start_ns']>previous:
            out.append(writer.write(len(out),np.empty(0,dtype=TICK_DTYPE),previous,d['coverage_start_ns']))
        out.append(writer.write(len(out),rows,d['coverage_start_ns'],end));previous=end;remaining-=k
        digest.update(rows.tobytes());sources.append({'file':d['file'],'file_sha256':d['file_sha256'],'rows':k})
    sub={**m,'chunks':out,'coverage_end_ns':previous,'count':sum(d['count'] for d in out),
         'raw_sha256':digest.hexdigest(),'validation_subset':{
             'kind':'CONSECUTIVE_UNALTERED_BROKER_RAW_PREFIX','source_archive_identity':m['archive_identity'],
             'source_chunks':sources,'requested_rows':n,
             'note':'Empty prehistory chunks consolidated; raw rows unchanged; no fabricated warmup'}}
    sub.pop('archive_identity',None)
    sub['gaps']=[g for g in m.get('gaps',()) if g['start_ns']<previous]
    sub['archive_identity']=identity(sub);write_json(target/'manifest.json',sub);verify_archive(target)
    return {'requested_rows':n,'actual_rows':sub['count'],'gaps':len(sub['gaps']),
            'archive_identity':sub['archive_identity'],'path':str(target)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Original raw archive directory containing manifest.json')
    p.add_argument('--output',type=Path,required=True,help='New parent directory for real_N prefixes')
    p.add_argument('--rows',type=int,nargs='+',default=[5000,50000,200000])
    args=p.parse_args()
    for n in args.rows:print(json.dumps(prepare(args.source.resolve(),args.output.resolve()/f'real_{n}',n)),flush=True)

if __name__=='__main__':main()
