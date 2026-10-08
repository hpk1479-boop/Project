"""Run a later stage with the already-frozen S1 rule; never recalibrate it."""
import argparse
from pathlib import Path
from staff_performance_policy import compare

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',required=True,choices=['S5','S8'])
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    raise SystemExit(compare(args.out.resolve(),args.stage))
