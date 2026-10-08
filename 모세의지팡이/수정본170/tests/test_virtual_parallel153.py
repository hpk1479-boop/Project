"""153: the virtual entry on worker processes, and its reader stepping over unread feeds.

The reference for the reader is the lossless DeltaCodec state; for the worker split it is the
same calculation on one stream (workers=1). Synthetic recordings only.
"""
from pathlib import Path
import csv
import json
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
import staff_schema as wire
from event_backtest.delta import DeltaCodec
from event_backtest.keyframes import DAY_MS
from event_backtest.virtual_msd import Projection, INDICES
from event_backtest import virtual_entry as ve

SYMBOL = 'XAUUSD+'
COLS = len(wire.PIPE_VALUE_COLUMNS)
STEP = {'1m': 60, '5m': 300, '1h': 3600}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)


def same_bits(left, right):
    return left.shape == right.shape and np.array_equal(np.asarray(left).view('<u8'), np.asarray(right).view('<u8'))


def market_values(rng, rows):
    values = rng.normal(2400., 5., (rows, COLS))
    # Bit patterns the reader keeps (NaN payload, -0) or turns into NaN (inf, beyond 1e300).
    for pattern in (np.nan, -0., np.inf, 1e301, -np.inf):
        values[rng.integers(0, rows), INDICES[rng.integers(0, len(INDICES))]] = pattern
    values.view('<u8')[rng.integers(0, rows), INDICES[0]] = 0x7ff8000000001234
    return values


def random_bundles(seed, steps=160):
    """Bundles of 1m, 5m and 1h feeds, each a FULL, ROW, APPEND or HEARTBEAT like the EA sends."""
    rng = np.random.default_rng(seed)
    windows = {}
    for seq in range(1, steps + 1):
        children = []
        for tf in ('1m', '5m', '1h'):
            old = windows.get(tf)
            kind = wire.WIRE_FULL if old is None else rng.choice(
                [wire.WIRE_FULL, wire.WIRE_ROW, wire.WIRE_APPEND, wire.WIRE_HEARTBEAT], p=[.1, .35, .4, .15])
            if kind == wire.WIRE_FULL:
                rows = int(rng.choice([3, 4, 40, 649, 650]))
                last = (int(old[-1]) + STEP[tf] * int(rng.integers(-2, 3))) if old is not None else 1_790_000_000
                times = last - STEP[tf] * np.arange(rows - 1, -1, -1, dtype=np.int64)
                windows[tf] = times
                children.append(wire.pack_v2(SYMBOL, tf, times, rng.integers(1, 9, rows), market_values(rng, rows),
                                             seq=seq, kind=kind))
            elif kind == wire.WIRE_ROW:
                children.append(wire.pack_v2(SYMBOL, tf, old[-1:], [3], market_values(rng, 1), seq=seq, kind=kind))
            elif kind == wire.WIRE_APPEND:
                times = np.array([old[-1], old[-1] + STEP[tf]], dtype=np.int64)
                windows[tf] = np.r_[old[int(len(old) >= wire.WIRE_MAX_BARS):], times[1:]]
                children.append(wire.pack_v2(SYMBOL, tf, times, [4, 1], market_values(rng, 2), seq=seq, kind=kind))
            else:
                children.append(wire.pack_v2(SYMBOL, tf, seq=seq, kind=kind))
        yield wire.pack_bundle(SYMBOL, children, seq=seq, sent_at_ms=seq * 1000)


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('frames', [('1m',), ('1m', '1h'), ('5m',)])
def test_reader_windows_equal_the_lossless_state_and_skip_unread_feeds(seed, frames):
    codec = DeltaCodec()
    projection = Projection({(SYMBOL, tf) for tf in frames})
    for raw in random_bundles(seed):
        views = projection.decode(*codec.encode(raw))
        assert set(projection.states) == set(views) == {(SYMBOL, tf) for tf in frames}
        for tf in frames:
            times, stored = codec.feeds[(SYMBOL + tf).encode()]
            bits = stored[:, INDICES + 1]
            state_times, state_bits = projection.states[SYMBOL, tf]
            assert np.array_equal(state_times, times) and np.array_equal(state_bits, bits)
            expected = bits.view('<f8').copy()
            expected[~np.isfinite(expected) | (np.abs(expected) > 1.e300)] = np.nan
            assert np.array_equal(views[SYMBOL, tf].time, times) and same_bits(views[SYMBOL, tf].values, expected)


def test_a_cut_or_padded_record_is_refused_as_a_value_error():
    codec = DeltaCodec()
    raw = next(random_bundles(3, steps=1))
    structure, bits = codec.encode(raw)
    for selected in ({(SYMBOL, '1m')}, {(SYMBOL, '1h')}, set()):
        Projection(selected).decode(structure, bits)
        for cut in (1, 3, 60, len(structure) // 2, len(structure) - 3, len(structure) - 1):
            with pytest.raises(ValueError):
                Projection(selected).decode(structure[:cut], bits)
        with pytest.raises(ValueError, match='trailing'):
            Projection(selected).decode(structure + b'\0', bits)
        with pytest.raises(ValueError, match='truncated delta values|trailing'):
            Projection(selected).decode(structure, bits[:-1])
        with pytest.raises(ValueError, match='trailing'):
            Projection(selected).decode(structure, np.r_[bits, bits[:1]])


def times_at(days):
    return [d * DAY_MS + m * 60_000 for d, m in days]


@pytest.mark.parametrize('workers', [1, 2, 3, 8])
def test_groups_are_whole_days_of_consecutive_alerts(workers):
    inputs = [{'time_ms': t} for t in times_at([(0, 1), (0, 5), (0, 5), (1, 2), (3, 0), (3, 9), (4, 1), (9, 0)])]
    groups = ve.alert_groups(inputs, workers)
    assert groups[0][0] == 0 and groups[-1][1] == len(inputs)
    assert all(a[1] == b[0] and a[0] < a[1] for a, b in zip(groups, groups[1:]))
    assert len(groups) == (1 if workers == 1 else min(5, 2 * workers))
    for first, last in groups:
        if last < len(inputs):    # a day never spans two groups
            assert inputs[last - 1]['time_ms'] // DAY_MS != inputs[last]['time_ms'] // DAY_MS
    assert ve.alert_groups(inputs[:3], 8) == [(0, 3)]          # one day: one stream, no processes


def bars(start, minutes):
    """Coherent 1m candles (open, high, low, close; HMA17/50, ATR inputs) for the given minutes."""
    rng = np.random.default_rng(int(start) % 1000)
    price = 2400. + np.cumsum(rng.normal(0., .8, len(minutes)))
    values = np.ones((len(minutes), COLS))
    column = wire.PIPE_VALUE_COLUMNS.index
    opened = np.r_[price[0], price[:-1]]
    values[:, column('open')] = opened
    values[:, column('close')] = price
    values[:, column('high')] = np.maximum(opened, price) + np.abs(rng.normal(0., .4, len(minutes)))
    values[:, column('low')] = np.minimum(opened, price) - np.abs(rng.normal(0., .4, len(minutes)))
    for name, span in (('hma_6', 3), ('hma_17', 8), ('hma_50', 25), ('hma_168', 60)):
        smooth = np.convolve(np.r_[np.full(span - 1, price[0]), price], np.ones(span) / span, 'valid')
        values[:, column(name)] = smooth
    return values


def three_day_recording(warehouse):
    """Three UTC days of a published MSD2 capture: 1m APPEND + ROW per minute, an unread 5m feed."""
    from event_backtest.keyframes import write_indexed, verify_indexed
    from event_host import load_staff
    day0 = 20_300 * DAY_MS // 1000                      # seconds, a UTC midnight
    minutes = np.r_[np.arange(day0 - 650 * 60, day0, 60),
                    *(day0 + d * 86400 + np.arange(0, 120 * 60, 60) for d in range(3))].astype(np.int64)
    values = bars(day0, minutes)
    rows, seq = [], 0
    first = 650
    for k in range(first - 1, len(minutes)):
        seq += 1
        stamp = int(minutes[k]) * 1000 + 500
        if k == first - 1:
            children = [wire.pack_v2(SYMBOL, '1m', minutes[:k + 1], np.ones(k + 1, dtype=np.int64), values[:k + 1],
                                     seq=seq, kind=wire.WIRE_FULL)]
        else:
            children = [wire.pack_v2(SYMBOL, '1m', minutes[k - 1:k + 1], [5, 1], values[k - 1:k + 1],
                                     seq=seq, kind=wire.WIRE_APPEND)]
        five = minutes[(minutes % 300 == 0) & (minutes <= minutes[k])][-650:]
        children.append(wire.pack_v2(SYMBOL, '5m', five, np.ones(len(five), dtype=np.int64), bars(day0, five),
                                     seq=seq, kind=wire.WIRE_FULL))
        rows.append((stamp, wire.pack_bundle(SYMBOL, children, seq=seq, sent_at_ms=stamp)))
        if k % 3 == 0 and k + 1 < len(minutes):          # a forming-bar update before the next minute
            seq += 1
            moved = values[k:k + 1].copy(); moved[0, wire.PIPE_VALUE_COLUMNS.index('high')] += .05
            row = wire.pack_v2(SYMBOL, '1m', minutes[k:k + 1], [3], moved, seq=seq, kind=wire.WIRE_ROW)
            beat = wire.pack_v2(SYMBOL, '5m', seq=seq, kind=wire.WIRE_HEARTBEAT)
            rows.append((stamp + 20_000, wire.pack_bundle(SYMBOL, [row, beat], seq=seq, sent_at_ms=stamp + 20_000)))
    root = warehouse / 'capture'
    root.mkdir(parents=True)
    staff = load_staff()
    cache = staff.StaffPipeCache('', health_session='PARALLEL153', monotonic=lambda: 0.,
                                 gap_journal=warehouse / 'build_gaps.jsonl')
    index = write_indexed(rows, root / 'capture.delta2', staff_cache=cache)
    verify_indexed(root / 'capture.delta2', index)
    (root / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
    (root / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
    assert len(index['days']) == 4                         # the history's last minute, then three days
    return day0, rows


def write_alerts(path, alerts):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(alerts[0]))
        writer.writeheader()
        writer.writerows(alerts)
    return path


def alerts_over(day0, tf='1m'):
    """Alerts on all three days, two at the same minute, some late in a day (their trades run into the next)."""
    result = []
    for d in range(3):
        for minute in (3, 3, 17, 40, 77, 112, 118, 119):
            stamp = (day0 + d * 86400 + minute * 60) * 1000
            result.append(dict(signal_id=f'{d}-{minute}-{len(result)}', strategy='SPECIAL8', symbol=SYMBOL, tf=tf,
                               direction='LONG' if len(result) % 3 else 'SHORT', time_ms=stamp,
                               signal_source='SIGNAL', signal_price=2400 + len(result) % 7))
    return result


def scenario(day0, policy):
    import datetime as dt
    start = dt.datetime.fromtimestamp(day0, dt.timezone.utc).date()
    return dict(start=start.isoformat(), end=(start + dt.timedelta(days=4)).isoformat(), symbol=SYMBOL,
                strategies=['SPECIAL8'], spread_points={}, virtual_entry=policy)


POLICIES = {
    # The SPECIAL8 recipe's own entry (conditional, environment limit, ATR stop) and an immediate one.
    'recipe': lambda: __import__('event_backtest.virtual_defaults', fromlist=['x']).target_policy(['SPECIAL8']),
    'immediate': lambda: ve.normalize_virtual_entry({'schema': 2, 'mode': 'IMMEDIATE',
                                                     'stop': {'kind': 'ATR', 'tf': 'SIGNAL', 'period': 14,
                                                              'multiplier': 3.}}),
}


def run(warehouse, inputs, s, name, **options):
    out = warehouse / name / 'same-run'
    out.mkdir(parents=True)
    events = []
    result = ve.calculate(write_alerts(warehouse / f'{name}.csv', inputs),
                          [dict(start=s['start'], end=s['end'], path='capture', point=.01)],
                          warehouse, s, {}, out, emit=lambda kind, data: events.append((kind, data)), **options)
    files = {n: (out / n).read_bytes() for n in ('virtual_trades.csv', 'virtual_summary.csv')}
    return result, files, events


@pytest.fixture(scope='module')
def recording(tmp_path_factory):
    warehouse = tmp_path_factory.mktemp('parallel153')
    day0, _ = three_day_recording(warehouse)
    return warehouse, day0


@pytest.mark.parametrize('policy', sorted(POLICIES))
def test_worker_groups_give_the_one_stream_fills(recording, policy):
    warehouse, day0 = recording
    s = scenario(day0, POLICIES[policy]())
    inputs = alerts_over(day0)
    one, one_files, _ = run(warehouse, inputs, s, f'{policy}-one')
    many, many_files, events = run(warehouse, inputs, s, f'{policy}-many', workers=2)
    assert one['alert_groups'] == 1 and many['alert_groups'] == 3
    assert many_files == one_files
    trades = list(csv.DictReader(one_files['virtual_trades.csv'].decode('utf-8-sig').splitlines()))
    entered = {t['signal_id'] for t in trades if t['entry_time']}
    closed = {t['result'] for t in trades}
    assert entered and {'WIN', 'LOSS'} <= closed                  # the fills did something to compare
    # Some trade entered on one day and was still open when the next day's group began.
    overnight = [t for t in trades if t['entry_time'] and t['exit_time']
                 and int(t['exit_time']) // DAY_MS > int(t['alert_time']) // DAY_MS]
    assert overnight
    for key in ('processed_signals', 'observed_signals', 'processed_periods', 'summary', 'last_closed_m1_ms',
                'eligible_signals', 'cancelled'):
        assert many[key] == one[key], key
    kinds = [kind for kind, _ in events]
    assert kinds[0] == 'VIRTUAL_ENTRY_START' and kinds[-1] == 'VIRTUAL_ENTRY_COMPLETE'
    progress = [data for kind, data in events if kind == 'VIRTUAL_ENTRY_PROGRESS']
    assert progress and all(0 <= p['percent'] <= 100 and p['total_alerts'] == len(inputs) for p in progress)
    # Trades still open when the recording ends count once every alert has been observed.
    assert events[-1][1]['processed_alerts'] == len(inputs) and not events[-1][1]['cancelled']


def test_a_stop_ends_every_group_with_partial_results(recording):
    warehouse, day0 = recording
    s = scenario(day0, POLICIES['recipe']())
    result, files, _ = run(warehouse, alerts_over(day0), s, 'stopped', workers=2, cancel=lambda: True)
    assert result['cancelled'] and result['status_label'] == '중단됨(부분 결과)'
    assert result['observed_signals'] < len(alerts_over(day0))
    assert (warehouse / 'stopped' / 'same-run' / 'stop.request').exists()
    rows = list(csv.DictReader(files['virtual_trades.csv'].decode('utf-8-sig').splitlines()))
    assert len(rows) == 9 * result['observed_signals']


def test_a_group_error_is_the_one_stream_error(recording):
    warehouse, day0 = recording
    s = scenario(day0, POLICIES['recipe']())
    inputs = alerts_over(day0, tf='15m')               # the recording has no 15m feed
    for options in ({}, {'workers': 2}):
        with pytest.raises(ValueError, match='필요한 타임프레임이 없습니다: 15m'):
            run(warehouse, inputs, s, f'missing-{len(options)}', **options)


def test_one_worker_or_one_day_never_starts_processes(recording, monkeypatch):
    warehouse, day0 = recording
    monkeypatch.setattr(ve, '_parallel', lambda *a, **k: pytest.fail('worker processes started'))
    s = scenario(day0, POLICIES['recipe']())
    inputs = alerts_over(day0)
    assert run(warehouse, inputs, s, 'single', workers=1)[0]['alert_groups'] == 1
    same_day = [a for a in inputs if a['time_ms'] // DAY_MS == inputs[0]['time_ms'] // DAY_MS]
    assert run(warehouse, same_day, s, 'one-day', workers=8)[0]['alert_groups'] == 1
