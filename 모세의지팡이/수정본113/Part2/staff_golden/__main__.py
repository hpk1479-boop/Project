from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import compare


def main():
    parser = argparse.ArgumentParser(description='Offline STAFF S0 baseline tools')
    commands = parser.add_subparsers(dest='command', required=True)
    rec = commands.add_parser('record')
    rec.add_argument('--part1', type=Path, required=True)
    rec.add_argument('--out', type=Path, required=True)
    rec.add_argument('--quick', action='store_true')
    rec.add_argument('--actual-capture', type=Path)
    cmp = commands.add_parser('compare')
    cmp.add_argument('left', type=Path)
    cmp.add_argument('right', type=Path)
    cmp.add_argument('--out', type=Path)
    parity = commands.add_parser('parity')
    parity.add_argument('--before', type=Path, required=True)
    parity.add_argument('--after', type=Path, required=True)
    parity.add_argument('--out', type=Path, required=True)
    parity.add_argument('--specials', default='SPECIAL7')
    parity.add_argument('--window', type=int, default=240)
    bench = commands.add_parser('benchmark')
    bench.add_argument('--part1', type=Path, required=True)
    bench.add_argument('--out', type=Path, required=True)
    actual = commands.add_parser('actual')
    actual.add_argument('--part1', type=Path, required=True)
    actual.add_argument('--capture', type=Path, required=True)
    actual.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'record':
        from .record import record
        result = record(args.part1, args.out, quick=args.quick, actual_capture=args.actual_capture)
        print(json.dumps({k: result[k] for k in ('cases', 'unique_frames', 'sha256')}, indent=2))
        return 0
    if args.command == 'compare':
        result = compare(args.left, args.right)
        if args.out:
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(not result['equal'])
    if args.command == 'benchmark':
        from .benchmark import benchmark
        result = benchmark(args.part1, args.out)
        print(json.dumps(result['baseline'], indent=2))
        return 0
    if args.command == 'actual':
        from .actual import record_actual
        result = record_actual(args.part1, args.capture, args.out)
        print(json.dumps({**{k: result[k] for k in ('cases', 'publications', 'sha256')},
                          'unavailable_responses': len(result['errors']),
                          'first_unavailable': result['errors'][:1]}, ensure_ascii=False, indent=2))
        return int(bool(result['errors']))
    from .parity import three_way
    result = three_way(args.before, args.after, args.out,
                       specials=args.specials.split(','), window=args.window)
    print(json.dumps({k: result[k] for k in ('equal', 'fields', 'nonempty')}, ensure_ascii=False, indent=2))
    return int(not result['equal'] or not all(result['nonempty'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
