"""178: a boundary waits for M1 final data, within the first post-end recorded server day."""
from pathlib import Path
import csv
import json
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

import staff_schema as wire
from event_backtest.settings import milliseconds
from event_backtest.virtual_entry import calculate

DAY = 86_400_000
MINUTE = 60_000
START = milliseconds('2026-09-01')
SYMBOL = 'TEST'


def frame(tf, bars, seq, kind=wire.WIRE_FULL):
    if kind == wire.WIRE_HEARTBEAT:
        return wire.pack_v2(SYMBOL, tf, seq=seq, kind=kind)
    values = np.ones((len(bars), len(wire.PIPE_VALUE_COLUMNS)))
    for i, bar in enumerate(bars):
        for name, value in zip(('open', 'high', 'low', 'close'), bar[1:]):
            values[i, wire.PIPE_VALUE_COLUMNS.index(name)] = value
    return wire.pack_v2(SYMBOL, tf, [bar[0] for bar in bars], np.ones(len(bars)), values,
                        seq=seq, kind=kind)


def capture(folder, observations, *, indexed=True):
    """Write a new, verified synthetic recording; each observation can update just one TF."""
    from event_backtest.keyframes import write_indexed, verify_indexed
    from event_backtest.delta import write_delta
    from event_host import load_staff
    rows = [(stamp, wire.pack_bundle(SYMBOL, children, seq=i + 1, sent_at_ms=stamp))
            for i, (stamp, children) in enumerate(observations)]
    folder.mkdir()
    if not indexed:
        write_delta(rows, folder / 'capture.delta.gz')
        return
    cache = load_staff().StaffPipeCache('', health_session='FINAL178', monotonic=lambda: 0.,
                                        gap_journal=folder.parent / (folder.name + '_gaps.jsonl'))
    index = write_indexed(rows, folder / 'capture.delta2', staff_cache=cache)
    verify_indexed(folder / 'capture.delta2', index)
    (folder / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
    (folder / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')


def rows_for_boundary(end, kind, final=True, *, offset=3 * DAY + 3_600_000):
    opened = end - MINUTE
    history = [(opened // 1000 - 120, 10, 10, 10, 10), (opened // 1000 - 60, 10, 10, 10, 10)]
    initial = history + [(opened // 1000, 10, 10, 10, 10)]
    after = end + offset
    five = [(opened // 1000 // 300 * 300 - offset, 10, 10, 10, 10) for offset in (600, 300, 0)]
    observations = [(opened + 20, [frame('1m', initial, 1), frame('5m', five, 1)])]
    children = [frame('5m', [], 2, wire.WIRE_HEARTBEAT)]
    if kind == 'heartbeat':
        children.append(frame('1m', [], 2, wire.WIRE_HEARTBEAT))
    elif kind == 'row':
        # Even a touched target is not final until a successor M1 arrives.
        children.append(frame('1m', [(opened // 1000, 10, 25, 9, 11)], 2, wire.WIRE_ROW))
    observations.append((after + 6_000, children))
    if final:
        observations.append((after + 7_000, [frame('1m', history + [(opened // 1000, 10, 25, 9, 11),
            (after // 1000, 10, 1e6, -1e6, 10)], 2 if kind == 'absent' else 3)]))
    return opened, after, observations


def run(tmp_path, observations, ends, *, workers=1, available=True, indexed=True, mode='IMMEDIATE',
        server_time=None):
    capture(tmp_path / 'capture', observations, indexed=indexed)
    alerts = [dict(signal_id=f'last-{i}', strategy='CUSTOM', symbol=SYMBOL, tf='1m', direction='LONG',
                   time_ms=end - MINUTE - (server_time or {}).get('utc_offset', 0) * 3_600_000,
                   signal_source='OZ', signal_price=10, b0_price=8)
              for i, end in enumerate(ends)]
    path = tmp_path / 'alerts.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(alerts[0])); writer.writeheader(); writer.writerows(alerts)
    out = tmp_path / 'run'; out.mkdir()
    from datetime import datetime, timezone
    date = lambda stamp: datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()
    scenario = dict(start=date(ends[0] - DAY), end=date(ends[-1]), symbol=SYMBOL, strategies=['CUSTOM'],
                    virtual_entry={'schema': 2, 'mode': mode, 'stop': {'kind': 'OZ_B0'},
                                   'conditions': [{'kind': 'CANDLE_CLOSE'}] if mode == 'CONFIRM' else []})
    if available:
        scenario['_available_periods'] = [{'start': date(end - DAY), 'end': date(end)} for end in ends]
    clock = server_time or {'utc_offset': 0, 'dst': 'NONE'}
    result = calculate(path, [dict(start=date(ends[0] - DAY), end=date(ends[-1] + 20 * DAY),
                       path='capture', server_time=clock)], tmp_path, scenario, {}, out, workers=workers)
    with (out / 'virtual_trades.csv').open(encoding='utf-8-sig', newline='') as f:
        trades = list(csv.DictReader(f))
    return result, trades


@pytest.mark.parametrize('kind', ['absent', 'heartbeat', 'row'])
@pytest.mark.parametrize('indexed', [True, False])
@pytest.mark.parametrize('workers', [1, 2])
def test_wait_for_m1_finalization_across_each_available_end(tmp_path, kind, indexed, workers):
    ends = [START + DAY, START + 8 * DAY]
    rows = []
    for end in ends:
        _, _, observations = rows_for_boundary(end, kind)
        # Every block is a full independent feed restart, as a new recording would be.
        if rows:
            # Keep feed sequence numbers contiguous inside this one recording.
            offset = 3 if kind != 'absent' else 2
            fixed = []
            for stamp, children in observations:
                fixed.append((stamp, [wire.pack_v2(p.symbol, p.timeframe, p.times, p.volumes, p.values,
                    seq=p.seq + (offset if p.timeframe == '1m' else 2), kind=p.kind)
                    for p in map(wire.decode_v2, children)]))
            observations = fixed
        rows.extend(observations)
    result, trades = run(tmp_path, rows, ends, workers=workers, indexed=indexed)
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {'WIN'}
    assert {int(row['exit_time']) for row in trades} == {end - MINUTE for end in ends}
    assert result['read_bundles'] >= 6


@pytest.mark.parametrize('kind', ['absent', 'heartbeat', 'row'])
def test_unconfirmed_m1_is_not_inferred_from_late_observation(tmp_path, kind):
    end = START + DAY
    _, _, observations = rows_for_boundary(end, kind, final=False)
    _, trades = run(tmp_path, observations, [end], available=False)
    assert {row['result'] for row in trades} == {'UNCLOSED'}


def test_stop_at_first_post_end_server_day_without_scanning_next_day(tmp_path):
    end = START + DAY
    opened, after, observations = rows_for_boundary(end, 'absent', final=False)
    next_day = after - after % DAY + DAY
    observations.append((next_day + 1_000, [frame('1m', [(opened // 1000 - 60, 10, 10, 10, 10), (opened // 1000, 10, 25, 9, 11),
        (next_day // 1000, 10, 1e6, -1e6, 10)], 2)]))
    result, trades = run(tmp_path, observations, [end], available=False)
    assert {row['result'] for row in trades} == {'UNCLOSED'}
    assert result['read_bundles'] == 2


def test_post_end_confirmation_never_enters(tmp_path):
    end = START + DAY
    _, _, observations = rows_for_boundary(end, 'absent')
    _, trades = run(tmp_path, observations, [end], available=False, mode='CONFIRM')
    assert {row['result'] for row in trades} == {'WAITING'}
    assert all(row['entry_time'] == '' for row in trades)


def test_late_history_cannot_create_an_entry_even_before_period_end(tmp_path):
    end = START + DAY
    _, _, observations = rows_for_boundary(end, 'absent')
    # Move the existing alert one minute earlier. A historical confirmation candle now falls
    # inside the period, but is first delivered in the post-period finalization tail.
    observations[0] = (end - 2 * MINUTE + 20, [frame('1m', [
        (end // 1000 - n, 10, 10, 10, 10) for n in (240, 180, 120)], 1),
        frame('5m', [(end // 1000 - n, 10, 10, 10, 10) for n in (900, 600, 300)], 1)])
    late=observations[-1][0]
    observations=[observations[0], (late, [frame('1m', [
        (end//1000-180,10,10,10,10), (end//1000-120,10,11,9,11),
        (end//1000-60,10,25,9,11), ((late-7_000)//1000,10,1e6,-1e6,10)], 2)])]
    capture(tmp_path / 'capture', observations)
    path = tmp_path / 'alerts.csv'
    alert = dict(signal_id='waiting', strategy='CUSTOM', symbol=SYMBOL, tf='1m', direction='LONG',
                 time_ms=end - 2 * MINUTE, signal_source='OZ', signal_price=10, b0_price=8)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(alert));writer.writeheader();writer.writerow(alert)
    out=tmp_path / 'run';out.mkdir()
    calculate(path, [dict(start='2026-09-01', end='2026-09-10', path='capture',
              server_time={'utc_offset': 0, 'dst': 'NONE'})], tmp_path,
              dict(start='2026-09-01', end='2026-09-02', symbol=SYMBOL, strategies=['CUSTOM'],
                   virtual_entry={'schema': 2, 'mode': 'CONFIRM', 'stop': {'kind': 'OZ_B0'},
                                  'conditions': [{'kind': 'CANDLE_CLOSE'}]}), {}, out)
    with (out / 'virtual_trades.csv').open(encoding='utf-8-sig', newline='') as f:
        trades=list(csv.DictReader(f))
    assert {row['result'] for row in trades} == {'WAITING'}
    assert all(row['entry_time'] == '' for row in trades)


@pytest.mark.parametrize('workers', [1, 2])
def test_each_boundary_uses_server_day_for_its_tail_limit(tmp_path, workers):
    ends = [START + DAY, START + 8 * DAY]
    opened, after, first = rows_for_boundary(ends[0], 'absent', final=False,
                                            offset=3 * DAY + DAY - 10_000)
    next_day = after - after % DAY + DAY
    # Next server day, but still the same UTC day at UTC+2: this data is outside the tail limit.
    first.append((next_day + 1_000, [frame('1m', [(opened // 1000 - 60, 10, 10, 10, 10),
        (opened // 1000, 10, 25, 9, 11), (next_day // 1000, 10, 1e6, -1e6, 10)], 2)]))
    _, _, later = rows_for_boundary(ends[1], 'absent')
    for stamp, children in later:
        first.append((stamp, [wire.pack_v2(p.symbol, p.timeframe, p.times, p.volumes, p.values,
            seq=p.seq+2, kind=p.kind) for p in map(wire.decode_v2, children)]))
    _, trades = run(tmp_path, first, ends, workers=workers, server_time={'utc_offset': 2, 'dst': 'NONE'})
    assert {row['result'] for row in trades if row['signal_id']=='last-0'} == {'UNCLOSED'}
    assert {row['result'] for row in trades if row['signal_id']=='last-1'} == {'WIN'}


@pytest.mark.parametrize('indexed', [True, False])
def test_new_capture_can_start_with_another_timeframe(tmp_path, indexed):
    end=START+DAY
    opened, after, observations=rows_for_boundary(end, 'absent')
    capture(tmp_path / 'before', observations[:1], indexed=indexed)
    observations[1]=(observations[1][0], [frame('5m', [
        (after//1000-n, 10, 10, 10, 10) for n in (600, 300, 0)], 1)])
    capture(tmp_path / 'after', observations[1:], indexed=indexed)
    alert=dict(signal_id='capture-seam', strategy='CUSTOM', symbol=SYMBOL, tf='1m', direction='LONG',
               time_ms=opened, signal_source='OZ', signal_price=10, b0_price=8)
    path=tmp_path / 'alerts.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(alert));writer.writeheader();writer.writerow(alert)
    clock={'utc_offset': 0, 'dst': 'NONE'}
    out=tmp_path / 'run';out.mkdir()
    result=calculate(path, [dict(start='2026-09-01', end='2026-09-02', path='before', server_time=clock)],
        tmp_path, dict(start='2026-09-01', end='2026-09-02', symbol=SYMBOL, strategies=['CUSTOM'],
            virtual_entry={'schema': 2, 'mode': 'IMMEDIATE', 'stop': {'kind': 'OZ_B0'}}), {}, out,
        following=[dict(start='2026-09-02', end='2026-09-10', path='after', server_time=clock)])
    with (out/'virtual_trades.csv').open(encoding='utf-8-sig', newline='') as f:
        trades=list(csv.DictReader(f))
    assert {row['result'] for row in trades} == {'WIN'}
    assert result['read_bundles']==3
