"""Synthetic fixtures and comparison loader; no production module is replaced."""
from pathlib import Path
from types import SimpleNamespace
import csv
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
import numpy as np
from staff_schema import PIPE_VALUE_COLUMNS

from event_backtest.virtual_source import NAMES as PROJECTED
from event_backtest.virtual_contract import normalize_virtual_entry


def oz_policy():
    """An OZ recipe's entry: the candle closed bullish above HMA6 (bearish below), B0 stop."""
    return normalize_virtual_entry({'mode': 'CONFIRM', 'conditions': [
        {'kind': 'CANDLE_CLOSE'}, {'kind': 'MA_POSITION', 'family': 'HMA', 'period': 6, 'tf': 'SIGNAL'}],
        'stop': {'kind': 'OZ_B0'}})


def alert(signal_id='a', *, direction='LONG', stop=8., symbol='XAUUSD+',
          strategy='SPECIAL1', tf='1m', time_ms=0):
    return dict(signal_id=signal_id, strategy=strategy, symbol=symbol, tf=tf,
                direction=direction, time_ms=time_ms, b0_price=stop, b0_time=-60,
                signal_source='OZ',signal_price=10)


def frame(rows, *, projected=False):
    """Rows are (seconds, open, high, low, hma6, hma17)."""
    columns = PROJECTED if projected else PIPE_VALUE_COLUMNS
    values = np.ones((len(rows), len(columns)), dtype=np.float64)
    for index, (_, op, high, low, fast, slow) in enumerate(rows):
        for name, value in dict(open=op, high=high, low=low, hma_6=fast, hma_17=slow).items():
            values[index, columns.index(name)] = value
        if 'close' in columns:
            values[index, columns.index('close')] = op+(.5 if fast>slow else -.5)
    times = np.array([row[0] for row in rows], dtype='<i8')
    return SimpleNamespace(time=times, values=values, columns=columns)


def write_alerts(path, alerts):
    fields = list(alerts[0] if alerts else alert())
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(alerts)
    return Path(path)


def config():
    # Every fixture alert is an OZ signal of SPECIAL1, an OZ strategy.
    return dict(start='1970-01-01', end='1970-01-02', symbol='XAUUSD+',
                strategies=['SPECIAL1'], spread_points={}, virtual_entry=oz_policy())


def load_comparison(project):
    """Load supplied input solely for diagnostics/measurements, never as live logic."""
    path = Path(project) / 'Part2/event_backtest/virtual_entry.py'
    spec = importlib.util.spec_from_file_location('event_backtest._comparison44', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_result(result):
    """Exclude only elapsed time and the run-folder path from diagnostic comparison."""
    return {key: value for key, value in result.items()
            if key not in ('elapsed_seconds', 'summary_csv', 'trades_csv')}


def recorded_fixture(warehouse, *, format='MSD2', published=True):
    """Seven synthetic FULL/ROW/HEARTBEAT observations, two independent trades."""
    import json
    import staff_schema as wire
    from event_backtest.delta import write_delta
    from event_backtest.keyframes import write_indexed, verify_indexed
    from event_host import load_staff

    warehouse = Path(warehouse)
    warehouse.mkdir(parents=True, exist_ok=True)
    root = warehouse / 'capture'
    root.mkdir()
    start = 1_756_684_800
    history = [(start - (649 - i) * 60, 10., 11., 9., 9., 8.) for i in range(650)]
    packets = []
    kinds = (wire.WIRE_FULL, wire.WIRE_FULL, wire.WIRE_ROW, wire.WIRE_HEARTBEAT,
             wire.WIRE_FULL, wire.WIRE_FULL, wire.WIRE_FULL)
    for index, (offset, kind) in enumerate(zip((0, 60, 90, 100, 120, 180, 240), kinds)):
        if offset in (60, 120, 180, 240):
            low = -1. if offset == 180 else 9.
            history.append((start + offset, 10., 13. if offset == 60 else 11., low, 9., 8.))
            history = history[-650:]
        if kind == wire.WIRE_ROW:
            history[-1] = (start + 60, 10., 30., 7., 9., 8.)
        data = frame(history)
        if kind == wire.WIRE_HEARTBEAT:
            child = wire.pack_v2('XAUUSD+', '1m', seq=index + 1, kind=kind)
        else:
            n = 1 if kind == wire.WIRE_ROW else len(data.time)
            child = wire.pack_v2('XAUUSD+', '1m', data.time[-n:], np.ones(n, dtype='<i8'),
                                 data.values[-n:], seq=index + 1, kind=kind)
        stamp = (start + offset) * 1000
        packets.append((stamp, wire.pack_bundle('XAUUSD+', [child], seq=index + 1, sent_at_ms=stamp)))
    if format == 'MSD1':
        index = write_delta(packets, root / 'capture.delta.gz')
    elif format == 'MSD2':
        clock = [0.]
        staff = load_staff()
        cache = staff.StaffPipeCache('', health_session='SYNTHETIC44', monotonic=lambda: clock[0],
                                     gap_journal=warehouse / 'build_gaps.jsonl')
        index = write_indexed(packets, root / 'capture.delta2', staff_cache=cache, receive_clock=clock)
        verify_indexed(root / 'capture.delta2', index)
        (root / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
        if published:
            (root / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
    else:
        raise ValueError('Unsupported synthetic format.')
    inputs = [alert('first', stop=0., time_ms=start * 1000), alert('second', time_ms=start * 1000)]
    path = write_alerts(warehouse / 'alerts.csv', inputs)
    scenario = dict(config(), start='2025-09-01', end='2025-09-02')
    # The alerts above are written on the recording's own clock: a recording already in real time (수정본172).
    captures = [dict(start='2025-09-01', end='2025-09-02', path='capture', point=.01,
                     server_time={'utc_offset': 0, 'dst': 'NONE'})]
    return path, captures, scenario, packets
