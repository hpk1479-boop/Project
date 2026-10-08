"""Lockstep equality over every supplied record, without storing all Python ticks."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from generic_backtest.history.cache import GenericArchiveReader
    from cadence_input_validation.reference_reader import LegacyArchiveReader
    start=time.perf_counter()
    old=LegacyArchiveReader(args.archive);new=GenericArchiveReader(args.archive)
    from itertools import zip_longest
    count=same_ms=0;last_ms=None;first_ms=None
    old_hash=hashlib.sha256();new_hash=hashlib.sha256()
    for a,b in zip_longest(old,new):
        assert a is not None and b is not None, 'record count changed'
        assert type(a) is type(b) and a==b, ('record fields changed',count+1)
        aa=a.to_bytes();bb=b.to_bytes()
        assert aa==bb, ('record bits changed',count+1)
        old_hash.update(aa);new_hash.update(bb)
        count+=1
        assert b.source_ordinal==count
        if first_ms is None:first_ms=b.time_msc
        if b.time_msc==last_ms:same_ms+=1
        if last_ms is not None:assert b.time_msc>=last_ms
        last_ms=b.time_msc
    assert count==old.manifest['count']==new.manifest['count']
    assert old_hash.hexdigest()==new_hash.hexdigest()==old.manifest['raw_sha256']
    result={'status':'EXACT_EQUAL','records':count,'consecutive_same_ms_observations':same_ms,
            'first_ms':first_ms,'last_ms':last_ms,'last_source_ordinal':count,
            'old_raw_sha256':old_hash.hexdigest(),'new_raw_sha256':new_hash.hexdigest(),
            'archive_identity':old.manifest['archive_identity'],
            'verification_wall_seconds_NOT_performance_benchmark':time.perf_counter()-start}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
