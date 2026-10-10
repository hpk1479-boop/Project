"""Read-only replay timing probe; its temporary MSD1 sample stays in this revision."""
from __future__ import annotations

import cProfile
import hashlib
import io
import itertools
import json
import pathlib
import pstats
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1' / 'program')]

from event_backtest.delta import read_delta, update_hash, write_delta
from event_backtest.keyframes import read_index, read_indexed
from event_backtest.settings import settings

COUNT = 800
OUT = ROOT / '검증결과'


def measure(factory, *, profile=False):
    profiler = cProfile.Profile() if profile else None
    if profiler:
        profiler.enable()
    wall = time.perf_counter()
    cpu = time.process_time()
    digest = hashlib.sha256()
    count = 0
    total_raw = 0
    for stamp, raw in itertools.islice(factory(), COUNT):
        update_hash(digest, stamp, raw)
        total_raw += len(raw)
        count += 1
    cpu = time.process_time() - cpu
    wall = time.perf_counter() - wall
    if profiler:
        profiler.disable()
    return ({'bundles': count, 'cpu_ms_per_bundle': cpu * 1000 / count,
             'wall_ms_per_bundle': wall * 1000 / count,
             'raw_bytes': total_raw, 'sha256': digest.hexdigest()}, profiler)


def main():
    warehouse = pathlib.Path(settings()['warehouse'])
    captures = list((warehouse / 'captures' / 'XAUUSD+' / 'BAR' / '2025' / '09').glob('*/capture.delta2'))
    if len(captures) != 1:
        raise ValueError(f'expected one 2025-09 XAU BAR capture, found {len(captures)}')
    path = captures[0]
    index = read_index(path)
    sample = OUT / '_msd1_same_frames_sample.gz'
    try:
        write_delta(itertools.islice(read_indexed(path), COUNT), sample)
        factories = {'MSD2': lambda: read_indexed(path),
                     'MSD1_same_frames': lambda: read_delta(sample)}
        timings = {key: [] for key in factories}
        for key in ('MSD2', 'MSD1_same_frames', 'MSD1_same_frames', 'MSD2'):
            result, _ = measure(factories[key])
            timings[key].append(result)
        hashes = {r['sha256'] for rows in timings.values() for r in rows}
        if len(hashes) != 1:
            raise AssertionError('restored bytes differ')
        profiled, profile = measure(factories['MSD2'], profile=True)
        report = io.StringIO()
        pstats.Stats(profile, stream=report).sort_stats('cumulative').print_stats(30)
        (OUT / 'msd2_read_profile.txt').write_text(report.getvalue(), encoding='utf-8')
        summary = {key: {'cpu_ms_per_bundle_median': statistics.median(r['cpu_ms_per_bundle'] for r in rows),
                         'wall_ms_per_bundle_median': statistics.median(r['wall_ms_per_bundle'] for r in rows)}
                   for key, rows in timings.items()}
        result = {'sample_bundles': COUNT, 'sample_raw_bytes': profiled['raw_bytes'],
                  'sample_msd1_bytes': sample.stat().st_size,
                  'month_msd2_bytes': path.stat().st_size,
                  'month_msd2_days': len(index['days']),
                  'first_day_msd2_bytes': index['days'][0]['size'],
                  'reconstruction_sha256_equal': True,
                  'timings': timings, 'summary': summary,
                  'profiled_ms_per_bundle': profiled['cpu_ms_per_bundle']}
        (OUT / 'msd2_read_cost.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(summary, indent=2))
    finally:
        if sample.exists():
            sample.unlink()


if __name__ == '__main__':
    main()
