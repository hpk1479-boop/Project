"""Read-only MSD2/STAFF ingress and keyframe timing diagnostic."""
from __future__ import annotations

import cProfile
import io
import itertools
import json
import pathlib
import pstats
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1' / 'program')]

from event_backtest.bridge import CaptureInputs
from event_backtest.keyframes import read_index, read_indexed
from event_backtest.settings import settings
from event_engine.model import Kind
from event_host import load_staff

COUNT = 800
OUT = ROOT / '검증결과'


def bridge(path, *, start_ms=0, count=COUNT, profile=False):
    clock = [0.0]
    staff = load_staff()
    cache = staff.StaffPipeCache('', health_session='DIAGNOSTIC',
                                monotonic=lambda: clock[0],
                                gap_journal=OUT / 'input_seq_gaps.jsonl')
    inputs = CaptureInputs(staff, cache, [path], transport='replay',
                           clock=clock, start_ms=start_ms,
                           capture_start='keyframe')
    profiler = cProfile.Profile() if profile else None
    if profiler:
        profiler.enable()
    wall = time.perf_counter()
    cpu = time.process_time()
    market = 0
    other = 0
    first = None
    for item in inputs:
        if item.kind == Kind.MARKET_BUNDLE:
            market += 1
            if first is None:
                first = time.perf_counter() - wall
            if market >= count:
                break
        else:
            other += 1
    cpu = time.process_time() - cpu
    wall = time.perf_counter() - wall
    if profiler:
        profiler.disable()
    return ({'bundles': market, 'other_events': other,
             'cpu_ms_per_bundle': 1000 * cpu / market,
             'wall_ms_per_bundle': 1000 * wall / market,
             'first_event_seconds': first,
             'keyframe_seek': inputs.seek_info}, profiler)


def main():
    warehouse = pathlib.Path(settings()['warehouse'])
    matches = list((warehouse / 'captures' / 'XAUUSD+' / 'BAR' / '2025' / '09').glob('*/capture.delta2'))
    if len(matches) != 1:
        raise ValueError('expected one September XAU capture')
    path = matches[0]
    index = read_index(path)
    day = index['days'][1]['day'] * 86400000
    began = time.perf_counter()
    first = next(read_indexed(path, start_ms=day))
    seek_first_seconds = time.perf_counter() - began
    regular, profiler = bridge(path.parent, profile=True)
    sought, _ = bridge(path.parent, start_ms=day, count=100)
    report = io.StringIO()
    pstats.Stats(profiler, stream=report).sort_stats('cumulative').print_stats(35)
    (OUT / 'input_bridge_profile.txt').write_text(report.getvalue(), encoding='utf-8')
    result = {'regular': regular, 'seek_next_day': sought,
              'seek_first_storage_seconds': seek_first_seconds,
              'seek_first_observation_ms': first[0]}
    (OUT / 'input_bridge_cost.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
