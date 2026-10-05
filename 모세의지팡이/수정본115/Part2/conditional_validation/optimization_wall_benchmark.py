"""Full coordinator wall-time confirmation without concurrent RSS sampling.

Uses the existing project benchmark and its exact configuration. Only optional
benchmark telemetry is disabled; strategy workers and production sources are
unchanged. Memory must be reported from a separate sampled run, not this run.
"""
import argparse
import json
from pathlib import Path
import runpy
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--plugin',default='WATCH_UI_V1')
    p.add_argument('--rows',type=int,default=50000)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.root.resolve();out=args.output.resolve()
    previous=sys.modules.get('psutil','ABSENT');argv=sys.argv
    # The existing benchmark treats ImportError as no optional sampler. Child
    # interpreters have their own module tables and are completely unaffected.
    sys.modules['psutil']=None
    sys.argv=['benchmark.py','--root',str(root),'--archive',str(args.archive.resolve()),
              '--plugin',args.plugin,'--rows',str(args.rows),'--output',str(out)]
    try:runpy.run_path(str(root/'cadence_input_validation/benchmark.py'),run_name='__main__')
    finally:
        sys.argv=argv
        if previous=='ABSENT':sys.modules.pop('psutil',None)
        else:sys.modules['psutil']=previous
    result=json.loads(out.read_text())
    result['wall_confirmation_protocol']='FULL_COORDINATOR_NO_CONCURRENT_RSS_SAMPLER; memory measured separately'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
