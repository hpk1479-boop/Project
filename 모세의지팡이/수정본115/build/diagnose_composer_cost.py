"""Historical one-day SPECIAL2 cProfile on identical reconstructed bundles.

Run in separate Python processes with argument 25 or 26. The only output is
under this revision's validation directory; legacy source trees are read-only.
"""
from __future__ import annotations

import cProfile
import json
import os
import pathlib
import pstats
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REVISION = int(sys.argv[1])
PROFILED = '--plain' not in sys.argv
if REVISION not in (25, 26):
    raise ValueError('25 or 26')
sys.dont_write_bytecode = True
HOST = ROOT.parent / f'수정본{REVISION}'
sys.path[:0] = [str(HOST / 'Part2'), str(HOST / 'Part1' / 'program')]

from event_backtest.settings import scenario
from event_backtest.runner import run_chunk, runtime_config

OUT = ROOT / '검증결과' / (f'composer_rev{REVISION}' + ('_plain' if not PROFILED else ''))
OUT.mkdir(parents=True, exist_ok=True)
warehouse = ROOT.parent.parent / '개피곤_warehouse'
reference = ROOT.parent / '수정본26' / '검증결과' / 'parallel_oz'
old = json.loads((reference / 'old_captures.json').read_text(encoding='utf-8'))
capture = next(c for c in old if c['start'] == '2025-09-01')
capture = dict(capture)
if REVISION == 25:
    capture['path'] = str(ROOT / '검증결과' / 'composer_msd1_sample')
else:
    source = list((warehouse / 'captures' / 'XAUUSD+' / 'BAR' / '2025' / '09').glob('*/capture.delta2'))
    if len(source) != 1:
        raise ValueError('expected one September MSD2 capture')
    capture['path'] = str(source[0].parent)
s = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-02',
             strategies='SPECIAL2', overlap_trading_days=0)
task = {'scenario': s, 'config': runtime_config(s), 'out': str(OUT),
        'run_id': f'composer_probe_{REVISION}', 'start': s['start'],
        'end': s['end'], 'warm_start': s['start'],
        'captures': [capture], 'warehouse': '', 'transport': 'replay'}
profile = cProfile.Profile() if PROFILED else None
if profile:
    profile.enable()
result = run_chunk(task)
if profile:
    profile.disable()
    profile.dump_stats(str(OUT / 'profile.pstats'))
    with (OUT / 'profile.txt').open('w', encoding='utf-8') as file:
        pstats.Stats(profile, stream=file).sort_stats('cumulative').print_stats(65)
        file.write('\nCOMPOSER-RELATED\n')
        pstats.Stats(profile, stream=file).sort_stats('cumulative').print_stats('event_composer')
(OUT / 'summary.json').write_text(json.dumps({
    'revision': REVISION,
    'bundles': result['bundles'],
    'engine_ms_per_bundle': result['mean_ms'],
    'composer': result['processor_timings'].get('COMPOSER'),
    'oz_state': result['processor_timings'].get('OZ_STATE'),
    'notifications': result['notifications'],
}, indent=2), encoding='utf-8')
print((OUT / 'summary.json').read_text(encoding='utf-8'))
