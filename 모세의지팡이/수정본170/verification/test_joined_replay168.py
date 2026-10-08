"""168: blocks joined where their states meet give the alerts of one continuous replay.

A synthetic recording (1m feed every 10 minutes, 12 trading days) makes SPECIAL8 alert every hour:
HMA17 crosses above HMA50 on the bar of minute 29, the bars of minutes 35-44 touch HMA50, and HMA17
falls back under HMA50 on the bar of minute 59 (a closed-bar cross compares the two last closed bars
of one publication). The same scenario runs with three blocks in real worker processes and as one
sequential replay; the alerts must be the same and every join exact.
"""
from __future__ import annotations

import csv
import datetime as dt
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import build_plan, runner
from event_backtest.settings import file_hash, milliseconds, scenario
from event_backtest.warehouse import Warehouse
import staff_schema as wire

DAYS = [d.isoformat() for d in (dt.date(2026, 8, 24) + dt.timedelta(days=i) for i in range(20)) if d.weekday() < 5][:12]
WINDOW = 25
SEMANTIC = ('time_ms', 'strategy', 'profile', 'grade', 'symbol', 'tf', 'direction', 'trigger', 'message', 'recipient',
            'b0_price', 'b0_time', 'signal_source', 'signal_tf', 'signal_price', 'env_tf', 'neckline_price', 'neckline_time_ms')


def bars(times):
    columns = wire.PIPE_VALUE_COLUMNS
    values = np.full((len(times), len(columns)), 100., dtype='<f8')
    for column in columns:
        if column.endswith(('_lower_out', '_upper_out')):
            values[:, columns.index(column)] = np.nan
    values[:, columns.index('wonbi_upper')] = 103.
    values[:, columns.index('wonbi_lower')] = 97.
    minute = (times // 60) % 60
    values[:, columns.index('open')] = values[:, columns.index('close')] = 100.75
    values[:, columns.index('high')] = 101.5
    values[:, columns.index('low')] = np.where((minute >= 35) & (minute < 45), 99.5, 100.5)
    values[:, columns.index('hma_50')] = 100.
    values[:, columns.index('hma_17')] = np.where((minute >= 29) & (minute < 59), 101., 99.)
    return values


def recording(root):
    directory = root / 'captures/joined'
    directory.mkdir(parents=True)
    stamps = [milliseconds(day) + (hour * 60 + minute) * 60000 for day in DAYS for hour in range(1, 13) for minute in range(0, 60, 10)]
    with (directory / 'pipe_000.bin').open('wb') as stream:
        stream.write(struct.pack('<IIII', 0x4D535033, 2, len(wire.PIPE_VALUE_COLUMNS), 650))
        for seq, stamp in enumerate(stamps, 1):
            times = np.arange(WINDOW, dtype='<i8') * 60 + stamp // 1000 - (WINDOW - 1) * 60
            payload = wire.pack_v2('XAUUSD+', '1m', times, np.ones(WINDOW, dtype='<i8'), bars(times), seq=seq)
            stream.write(struct.pack('<qiI', stamp, 31, len(payload)))
            stream.write(payload)
    (directory / 'manifest.tsv').write_text('MSP3\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\npipe_observation_unit\t'
                                            'milliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t' + str(len(stamps)) + '\n',
                                            encoding='ascii')
    (directory / 'complete.txt').write_text('complete', encoding='ascii')
    row = {'capture_id': 'joined', 'symbol': 'XAUUSD+', 'mode': 'TIMER', 'start': DAYS[0],
           'end': (dt.date.fromisoformat(DAYS[-1]) + dt.timedelta(days=1)).isoformat(), 'unit': 'MONTH',
           'ea_build_hash': 'test-ea', 'schema_id': wire.WIRE_SCHEMA_ID, 'timer_ms': 1000, 'storage': 'MSP3',
           'reconstruction_verified': True, 'path': 'captures/joined', 'observed_days': DAYS,
           'files': {p.name: file_hash(p) for p in directory.iterdir()}, 'tick_evidence': {'actual': 'REAL_TICKS'},
           'stored_bytes': sum(p.stat().st_size for p in directory.iterdir()), 'recorded_at': '2026-10-03T00:00:00+00:00'}
    catalog = Warehouse(root)
    catalog.register(row)
    catalog.close()
    return row


def rows(root, result):
    with (root / result['alerts_csv']).open(encoding='utf-8', newline='') as handle:
        return sorted(tuple(row.get(name, '') for name in SEMANTIC) for row in csv.DictReader(handle))


@pytest.fixture(scope='module')
def replays(tmp_path_factory):
    root = tmp_path_factory.mktemp('joined') / 'w'
    capture = recording(root)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    try:
        request = scenario(symbol='XAUUSD+', start=DAYS[3], end=(dt.date.fromisoformat(DAYS[-1]) + dt.timedelta(days=1)).isoformat(),
                           mode='TICK', strategies=['SPECIAL8'], overlap_trading_days=3, commands=[])
        joined = runner.run(dict(request), root, captures=[capture], cores=3)
        single = runner.run(dict(request), root, captures=[capture], sequential=True)
    finally:
        monkey.undo()
    return root, joined, single, capture


def test_three_blocks_give_the_alerts_of_one_continuous_replay(replays):
    root, joined, single, _ = replays
    assert joined['status'] == single['status'] == 'COMPLETE'
    assert joined['partition'] == 'JOINED_BLOCKS' and len(joined['chunks']) == 3 and joined['chains'] == [[0, 1, 2]]
    assert single['partition'] == 'SEQUENTIAL' and len(single['chunks']) == 1
    expected = rows(root, single)
    assert len(expected) >= 9 * 12 - 2                     # about one alert an hour
    assert rows(root, joined) == expected
    assert joined['alert_statistics']['total'] == single['alert_statistics']['total'] == len(expected)


def test_every_join_is_exact_and_soon_after_the_block_end(replays):
    _, joined, _, _ = replays
    assert joined['joins_exact'] is True and [join['status'] for join in joined['joins']] == ['CONVERGED', 'CONVERGED']
    for chunk, join in zip(joined['chunks'], joined['joins']):
        assert join['at_ms'] >= milliseconds(chunk['task_end'])
        assert join['at_ms'] - milliseconds(chunk['task_end']) <= 2 * 3600 * 1000
        assert chunk['join']['overrun_bundles'] <= 2 * 6 + 6
    assert joined['chunks'][-1]['join']['status'] == 'END'
    assert not any('근사' in warning for warning in joined['warnings'])


def test_a_later_request_with_other_blocks_uses_the_continuous_replay(replays, tmp_path):
    root, joined, _, capture = replays
    monkey = pytest.MonkeyPatch()
    monkey.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    try:
        request = scenario(symbol='XAUUSD+', start=DAYS[3], end=(dt.date.fromisoformat(DAYS[-1]) + dt.timedelta(days=1)).isoformat(),
                           mode='TICK', strategies=['SPECIAL8'], overlap_trading_days=3, commands=[])
        again = runner.run(dict(request), root, captures=[capture], cores=2)
    finally:
        monkey.undo()
    assert again['replay_reused_from'] == joined['run_id'] and rows(root, again) == rows(root, joined)
