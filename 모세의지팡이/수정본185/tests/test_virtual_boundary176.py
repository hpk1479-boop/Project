"""176: the exclusive end closes the last in-period candle without admitting later entries.

Synthetic, verified MSD2 recordings run through the production reader and calculate entry point.
Sequential and process-worker runs must judge the same final candle, including available-period ends.

177: the first observation after the end closes that candle however late it comes, also from the next
recording. A period ends at the market's daily break, so real recordings have no observation at the end.
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
from event_backtest.settings import milliseconds
from event_backtest.virtual_contract import normalize_virtual_entry
from event_backtest.virtual_entry import calculate

MINUTE = 60_000
DAY = 86_400_000
START = milliseconds('2026-09-01')
SYMBOL = 'TEST'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)


def policy(mode='IMMEDIATE'):
    return normalize_virtual_entry({'schema': 2, 'mode': mode,
                                    'conditions': [{'kind': 'CANDLE_CLOSE'}] if mode == 'CONFIRM' else [],
                                    'stop': {'kind': 'OZ_B0'}})


def make_recording(warehouse, *, direction='LONG', outcome='WIN', boundary='exact'):
    """Two alerts on separate days; the final bar closes at each available period's end."""
    from event_host import load_staff
    from event_backtest.keyframes import write_indexed, verify_indexed
    rows = []
    alerts = []
    for day in (0, 2):
        opened = START + (day + 1) * DAY - MINUTE
        end = opened + MINUTE
        alerts.append(dict(signal_id=f'last-{day}', strategy='CUSTOM', symbol=SYMBOL, tf='1m',
                           direction=direction, time_ms=opened, signal_source='OZ', signal_price=10,
                           b0_price=8 if direction == 'LONG' else 12))
        if outcome == 'NONE':
            high, low = 11, 9
        elif outcome == 'BOTH':
            high, low = 25, -5
        elif (outcome == 'WIN') == (direction == 'LONG'):
            high, low = 25, 9
        else:
            high, low = 11, -5
        # A short's stop must be touched for LOSS as well.
        if outcome == 'LOSS' and direction == 'SHORT':
            high, low = 13, 9
        closed = 11 if direction == 'LONG' else 9
        history = [(opened // 1000 - 120, 10, 10, 10, 10),
                   (opened // 1000 - 60, 10, 10, 10, 10)]
        snapshots = [(opened, history + [(opened // 1000, 10, 10, 10, 10)])]
        if boundary == 'unfinished':
            # A touched target in a candle that has not closed is not an exit.
            snapshots.append((end - 1, history + [(opened // 1000, 10, high, low, closed)]))
        else:
            stamp = end if boundary == 'exact' else end + 1
            snapshots.append((stamp, history + [(opened // 1000, 10, high, low, closed),
                                               (end // 1000, 10, 1e6, -1e6, 10)]))
        for stamp, bars in snapshots:
            values = np.ones((len(bars), len(wire.PIPE_VALUE_COLUMNS)))
            for index, bar in enumerate(bars):
                for name, value in zip(('open', 'high', 'low', 'close'), bar[1:]):
                    values[index, wire.PIPE_VALUE_COLUMNS.index(name)] = value
            seq = len(rows) + 1
            child = wire.pack_v2(SYMBOL, '1m', [bar[0] for bar in bars], np.ones(len(bars)), values,
                                 seq=seq, kind=wire.WIRE_FULL)
            rows.append((stamp, wire.pack_bundle(SYMBOL, [child], seq=seq, sent_at_ms=stamp)))
    capture = warehouse / 'capture'
    capture.mkdir(parents=True)
    cache = load_staff().StaffPipeCache('', health_session='BOUNDARY176', monotonic=lambda: 0.,
                                        gap_journal=warehouse / 'gaps.jsonl')
    index = write_indexed(rows, capture / 'capture.delta2', staff_cache=cache)
    verify_indexed(capture / 'capture.delta2', index)
    (capture / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
    (capture / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
    return alerts


def run(warehouse, alerts, *, workers=1, available=False, mode='IMMEDIATE', name='run'):
    path = warehouse / (name + '.csv')
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(alerts[0]))
        writer.writeheader()
        writer.writerows(alerts)
    out = warehouse / name
    out.mkdir()
    scenario = dict(start='2026-09-01', end='2026-09-04', symbol=SYMBOL,
                    strategies=['CUSTOM'], virtual_entry=policy(mode))
    if available:
        scenario['_available_periods'] = [{'start': '2026-09-01', 'end': '2026-09-02'},
                                          {'start': '2026-09-03', 'end': '2026-09-04'}]
    result = calculate(path, [dict(start='2026-09-01', end='2026-09-05', path='capture',
                                  server_time={'utc_offset': 0, 'dst': 'NONE'})],
                       warehouse, scenario, {}, out, workers=workers)
    with (out / 'virtual_trades.csv').open(encoding='utf-8-sig', newline='') as handle:
        trades = [{k: v for k, v in row.items() if k != 'run_id'} for row in csv.DictReader(handle)]
    return result, trades


@pytest.mark.parametrize('available', [False, True])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
@pytest.mark.parametrize('outcome', ['WIN', 'LOSS', 'BOTH'])
def test_last_confirmed_candle_is_judged_at_end(tmp_path, available, direction, outcome):
    alerts = make_recording(tmp_path, direction=direction, outcome=outcome)
    one, trades = run(tmp_path, alerts, available=available, name='one')
    expected = 'LOSS' if outcome == 'BOTH' else outcome
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {expected}
    assert {int(row['exit_time']) for row in trades} == {a['time_ms'] for a in alerts}
    assert one['alert_groups'] == 1
    assert [p['last_observation_ms'] for p in one['processed_periods']] == [a['time_ms'] + MINUTE for a in alerts]


# Two inputs cover both period kinds, both directions and a same-candle stop/target for the worker run.
@pytest.mark.parametrize('available,direction,outcome', [(False, 'LONG', 'WIN'), (True, 'SHORT', 'BOTH')])
def test_parallel_judges_the_same_last_candle(tmp_path, available, direction, outcome):
    alerts = make_recording(tmp_path, direction=direction, outcome=outcome)
    one, trades = run(tmp_path, alerts, available=available, name='one')
    many, parallel = run(tmp_path, alerts, workers=2, available=available, name='many')
    assert parallel == trades
    assert one['alert_groups'] == 1 and many['alert_groups'] == 2
    assert one['summary'] == many['summary']
    assert one['processed_periods'] == many['processed_periods']


@pytest.mark.parametrize('workers', [1, 2])
def test_confirmation_at_exclusive_end_never_opens_a_trade(tmp_path, workers):
    alerts = make_recording(tmp_path)
    _, trades = run(tmp_path, alerts, workers=workers, available=True, mode='CONFIRM')
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {'WAITING'}
    assert all(row['entry_time'] == '' and row['exit_time'] == '' for row in trades)


@pytest.mark.parametrize('workers', [1, 2])
def test_new_candle_at_end_is_not_used_for_exits(tmp_path, workers):
    alerts = make_recording(tmp_path, outcome='NONE')
    _, trades = run(tmp_path, alerts, workers=workers, available=True)
    # The new candle has enormous high/low values, but it is outside the period and unfinished.
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {'UNCLOSED'}
    assert all(row['exit_time'] == '' for row in trades)


@pytest.mark.parametrize('workers', [1, 2])
def test_unfinished_last_candle_does_not_close_a_trade(tmp_path, workers):
    alerts = make_recording(tmp_path, boundary='unfinished')
    _, trades = run(tmp_path, alerts, workers=workers, available=True)
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {'UNCLOSED'}
    assert all(row['exit_time'] == '' for row in trades)


@pytest.mark.parametrize('available', [False, True])
@pytest.mark.parametrize('workers', [1, 2])
def test_late_observation_closes_the_last_candle_without_its_new_candle(tmp_path, available, workers):
    # 수정본177: the user's decision. The candle opened inside the period, so its exits count; the new
    # candle in the same observation (high 1e6, low -1e6) is outside the period and never used.
    alerts = make_recording(tmp_path, boundary='late')
    _, trades = run(tmp_path, alerts, workers=workers, available=available)
    assert len(trades) == 18
    assert {row['result'] for row in trades} == {'WIN'}
    assert {int(row['exit_time']) for row in trades} == {a['time_ms'] for a in alerts}


def write_capture(folder, snapshots):
    """A verified MSD2 recording of 1m FULL snapshots [(stamp, [(open time s, o, h, l, c), ...]), ...]."""
    from event_host import load_staff
    from event_backtest.keyframes import write_indexed, verify_indexed
    rows = []
    for stamp, bars in snapshots:
        values = np.ones((len(bars), len(wire.PIPE_VALUE_COLUMNS)))
        for index, bar in enumerate(bars):
            for name, value in zip(('open', 'high', 'low', 'close'), bar[1:]):
                values[index, wire.PIPE_VALUE_COLUMNS.index(name)] = value
        seq = len(rows) + 1
        child = wire.pack_v2(SYMBOL, '1m', [bar[0] for bar in bars], np.ones(len(bars)), values,
                             seq=seq, kind=wire.WIRE_FULL)
        rows.append((stamp, wire.pack_bundle(SYMBOL, [child], seq=seq, sent_at_ms=stamp)))
    folder.mkdir(parents=True)
    cache = load_staff().StaffPipeCache('', health_session='BOUNDARY177', monotonic=lambda: 0.,
                                        gap_journal=folder.parent / (folder.name + '_gaps.jsonl'))
    index = write_indexed(rows, folder / 'capture.delta2', staff_cache=cache)
    verify_indexed(folder / 'capture.delta2', index)
    (folder / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
    (folder / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')


def test_next_recording_closes_the_period_after_the_market_break(tmp_path):
    """The period's recording ends with the last candle's opening; the next one starts after the break."""
    end = START + DAY
    opened = end - MINUTE
    history = [(opened // 1000 - 120, 10, 10, 10, 10), (opened // 1000 - 60, 10, 10, 10, 10)]
    write_capture(tmp_path / 'september01', [(opened + 30, history + [(opened // 1000, 10, 10, 10, 10)])])
    reopened = end + 3600_000
    write_capture(tmp_path / 'september02', [(reopened + 6_000, history + [(opened // 1000, 10, 25, 9, 11),
                                                                          (reopened // 1000, 10, 1e6, -1e6, 10)])])
    # Entered at the candle's open (an entry inside a candle is UNCERTAIN there), observed 30 ms later.
    alert = dict(signal_id='last', strategy='CUSTOM', symbol=SYMBOL, tf='1m', direction='LONG', time_ms=opened,
                 signal_source='OZ', signal_price=10, b0_price=8)
    path = tmp_path / 'alerts.csv'
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(alert))
        writer.writeheader()
        writer.writerow(alert)
    clock = {'utc_offset': 0, 'dst': 'NONE'}
    period = [dict(start='2026-09-01', end='2026-09-02', path='september01', server_time=clock)]
    after = dict(start='2026-09-02', end='2026-09-03', path='september02', server_time=clock)

    def results(name, following):
        out = tmp_path / name
        out.mkdir()
        calculate(path, period, tmp_path, dict(start='2026-09-01', end='2026-09-02', symbol=SYMBOL,
                  strategies=['CUSTOM'], virtual_entry=policy()), {}, out, following=following)
        with (out / 'virtual_trades.csv').open(encoding='utf-8-sig', newline='') as handle:
            return [row for row in csv.DictReader(handle)]
    judged = results('with_next', [after])
    assert {row['result'] for row in judged} == {'WIN'} and {int(row['exit_time']) for row in judged} == {opened}
    # Without the next recording, or with one on another broker clock, the candle's close is unknown.
    assert {row['result'] for row in results('alone', [])} == {'UNCLOSED'}
    other = {**after, 'server_time': {'utc_offset': 3, 'dst': 'NONE'}}
    assert {row['result'] for row in results('other_clock', [other])} == {'UNCLOSED'}


def test_following_capture_is_the_first_recording_after_the_end(monkeypatch):
    from event_backtest import runner
    rows = [dict(start='2026-08-01', end='2026-09-01'), dict(start='2026-09-01', end='2026-10-01'),
            dict(start='2026-10-05', end='2026-11-01')]
    monkeypatch.setattr(runner, 'usable_captures', lambda catalog, s: list(reversed(rows)))
    assert runner.following_capture(None, {'end': '2026-09-01'}, [rows[0]]) == [rows[1]]
    # A gap after the end: the next recording, however late.
    assert runner.following_capture(None, {'end': '2026-10-01'}, [rows[1]]) == [rows[2]]
    # The run's own recording reaches past the end: the reader goes on in it.
    assert runner.following_capture(None, {'end': '2026-08-15'}, [rows[0]]) == []
    assert runner.following_capture(None, {'end': '2026-11-01'}, [rows[2]]) == []
