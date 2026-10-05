"""Feeder-only large-data measurement; never reported as full-backtest speed."""
import argparse
from pathlib import Path
import json
import sys
import time
try:
    import resource
except ImportError:
    resource=None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--reader',choices=('legacy','current'),required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from generic_backtest.history.cache import GenericArchiveReader
    from cadence_input_validation.reference_reader import LegacyArchiveReader
    cls=LegacyArchiveReader if args.reader=='legacy' else GenericArchiveReader
    started=time.perf_counter();reader=cls(args.archive);load=time.perf_counter()-started
    count=0;last=None;loop=time.perf_counter()
    for tick in reader:
        count+=1;last=tick
    elapsed=time.perf_counter()-loop;wall=time.perf_counter()-started
    assert count==reader.manifest['count'] and last.source_ordinal==count
    result={'scope':'FEEDER_ONLY_NOT_FULL_BACKTEST','reader':args.reader,
        'observations':count,'preflight_seconds':load,'iteration_seconds':elapsed,
        'total_seconds':wall,'observations_per_second':count/wall,
        'peak_parent_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 if resource else None,
        'archive_identity':reader.manifest['archive_identity'],'last_source_ordinal':last.source_ordinal,
        'last_tick_hex':last.to_bytes().hex()}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
