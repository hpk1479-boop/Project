"""Verify every original Wire bundle with CRC enabled, without a strategy engine."""
from pathlib import Path
import argparse,hashlib,json,os,sys,time


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);p.add_argument('capture',type=Path)
    p.add_argument('output',type=Path);p.add_argument('--cpu',type=int);a=p.parse_args()
    root=a.project.resolve();a.output.mkdir(parents=True,exist_ok=True);sys.dont_write_bytecode=True
    if a.cpu is not None and hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{a.cpu})
    sys.path[:0]=[str(root/'Part2'),str(root/'Part1/program')]
    from event_backtest.keyframes import read_indexed
    from event_backtest.delta import update_hash
    expected=json.loads((a.capture/'storage.json').read_text())
    digest=hashlib.sha256();rows=[];retained=[];began=time.perf_counter()
    for stamp,raw in read_indexed(a.capture/'capture.delta2',verify_crc=True):
        assert type(raw) is bytes
        update_hash(digest,stamp,raw)
        rows.append({'source_time':stamp,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        if len(retained)<5:retained.append(raw)
    assert all(hashlib.sha256(raw).hexdigest()==rows[i]['sha256'] for i,raw in enumerate(retained))
    result={'bundles':len(rows),'bundle_sha256':digest.hexdigest(),'verify_crc':True,
            'retained_outputs_unchanged':True,'elapsed_seconds':time.perf_counter()-began,
            'first_time':rows[0]['source_time'],'last_time':rows[-1]['source_time']}
    assert result['bundles']==expected['bundles'] and result['bundle_sha256']==expected['bundle_sha256']
    (a.output/'bundles.json').write_text(json.dumps(rows,indent=2))
    (a.output/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':main()
