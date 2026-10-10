"""178: run_many verifies the one following recording before replay starts.

The catalog, capture files, selection and SHA verification are real. Gate tests stop
at replay setup; the successful route also reaches the real virtual calculator.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import build_plan, runner, warehouse as wh
from event_backtest.settings import file_hash, milliseconds, scenario
import staff_schema as wire

END = milliseconds('2026-09-02')
OPENED = END - 60_000
CLOCK = {'utc_offset': 0, 'dst': 'NONE'}


def write_capture(folder, *, following=False, high=25):
    from event_host import load_staff
    from event_backtest.keyframes import write_indexed, verify_indexed
    history = [(OPENED // 1000 - 120, 10, 10, 10, 10),
               (OPENED // 1000 - 60, 10, 10, 10, 10)]
    bars = history + [(OPENED // 1000, 10, high if following else 10, 9 if following else 10, 11 if following else 10)]
    stamp = OPENED + 30
    if following:
        stamp = END + 3_606_000
        bars.append(((END + 3_600_000) // 1000, 10, 1e6, -1e6, 10))
    values = np.ones((len(bars), len(wire.PIPE_VALUE_COLUMNS)))
    for i, bar in enumerate(bars):
        for name, value in zip(('open', 'high', 'low', 'close'), bar[1:]):
            values[i, wire.PIPE_VALUE_COLUMNS.index(name)] = value
    child = wire.pack_v2('XAUUSD+', '1m', [bar[0] for bar in bars], np.ones(len(bars)), values,
                         seq=1, kind=wire.WIRE_FULL)
    raw = wire.pack_bundle('XAUUSD+', [child], seq=1, sent_at_ms=stamp)
    folder.mkdir(parents=True, exist_ok=True)
    staff = load_staff().StaffPipeCache('', health_session='INTEGRITY178', monotonic=lambda: 0.,
                                      gap_journal=folder.parent / (folder.name + '_gaps.jsonl'))
    index = write_indexed([(stamp, raw)], folder / 'capture.delta2', staff_cache=staff)
    verify_indexed(folder / 'capture.delta2', index)
    (folder / 'storage.json').write_text(json.dumps(index), encoding='utf-8')
    (folder / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')


def register(root, key, start, end, *, following=False):
    folder = root / 'captures' / key
    write_capture(folder, following=following)
    row = dict(capture_id=key, symbol='XAUUSD+', mode='BAR', start=start, end=end, unit='DAY',
               ea_build_hash='integrity178-ea', schema_id=wire.WIRE_SCHEMA_ID, timer_ms=1000,
               storage='MSD2', reconstruction_verified=True, path='captures/' + key,
               observed_days=[], server_time=CLOCK, files={p.name: file_hash(p) for p in folder.iterdir()},
               tick_evidence={'actual': 'REAL_TICKS'}, stored_bytes=sum(p.stat().st_size for p in folder.iterdir()),
               recorded_at='2026-10-01T00:00:00+00:00')
    catalog = wh.Warehouse(root)
    try:
        catalog.register(row)
    finally:
        catalog.close()
    return row


class ReplayReady(Exception):
    """The real runner has accepted its source recordings and reached replay setup."""


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / 'warehouse'
    current = register(root, 'current', '2026-09-01', '2026-09-02')
    following = register(root, 'following', '2026-09-02', '2026-09-03', following=True)
    register(root, 'later', '2026-09-03', '2026-09-04', following=True)
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'integrity178-ea')
    hashes = []
    original_hash = wh._capture_file_hash
    def counted(path):
        hashes.append(Path(path).relative_to(root).as_posix())
        return original_hash(path)
    monkeypatch.setattr(wh, '_capture_file_hash', counted)
    original_run = runner._Run
    def ready(*args, **kwargs):
        raise ReplayReady
    monkeypatch.setattr(runner, '_Run', ready)
    request = scenario(symbol='XAUUSD+', start='2026-09-01', end='2026-09-02', mode='BAR',
                       strategies=['SPECIAL1'], overlap_trading_days=0, result_mode='VIRTUAL_ENTRY',
                       virtual_entry={'schema': 2, 'mode': 'IMMEDIATE', 'conditions': [], 'stop': {'kind': 'OZ_B0'}})
    return root, current, following, hashes, request, original_run


def expected_hashes(*rows):
    return Counter(row['path'] + '/' + name for row in rows for name in row['files'])


@pytest.mark.parametrize('verified', [False, True])
def test_only_selected_following_is_verified_once_even_when_current_was_verified(store, verified):
    root, current, following, hashes, request, _ = store
    with pytest.raises(ReplayReady):
        runner.run_many([request], root, captures=[current], sequential=True, verified=verified)
    assert Counter(hashes) == expected_hashes(*([following] if verified else [current, following]))


@pytest.mark.parametrize('mutation', ['price', 'deleted', 'marker', 'manifest'])
def test_changed_following_never_reaches_replay_setup(store, mutation):
    root, current, following, hashes, request, _ = store
    folder = root / following['path']
    if mutation == 'price':
        # Valid MSD2 data and internally matching storage/index hashes, but not the catalog's bytes.
        write_capture(folder, following=True, high=35)
    elif mutation == 'deleted':
        (folder / 'capture.delta2').unlink()
    elif mutation == 'marker':
        (folder / 'complete.txt').unlink()
    else:
        storage = json.loads((folder / 'storage.json').read_text(encoding='utf-8'))
        storage['bundle_sha256'] = '0' * 64
        (folder / 'storage.json').write_text(json.dumps(storage), encoding='utf-8')
    with pytest.raises(ValueError, match='녹화 무결성 오류: following'):
        runner.run_many([request], root, captures=[current], sequential=True, verified=True)
    assert all(path.startswith('captures/following/') for path in hashes)


def test_current_recording_extending_past_end_is_not_checked_twice(store):
    root, current, _, hashes, request, _ = store
    request = {**request, 'end': '2026-09-01T12:00:00+00:00'}
    with pytest.raises(ReplayReady):
        runner.run_many([request], root, captures=[current], sequential=True)
    assert Counter(hashes) == expected_hashes(current)


def test_alert_only_never_reads_or_hashes_following(store, monkeypatch):
    root, current, following, hashes, request, _ = store
    (root / following['path'] / 'capture.delta2').unlink()
    monkeypatch.setattr(runner, 'following_capture', lambda *args: pytest.fail('alert-only searched following'))
    with pytest.raises(ReplayReady):
        runner.run_many([{**request, 'result_mode': 'ALERT_ONLY'}], root, captures=[current], sequential=True)
    assert Counter(hashes) == expected_hashes(current)


def test_following_on_a_different_clock_is_not_read_or_hashed(store):
    root, current, following, hashes, request, _ = store
    catalog = wh.Warehouse(root)
    try:
        catalog.register({**following, 'server_time': {'utc_offset': 3, 'dst': 'NONE'}})
    finally:
        catalog.close()
    (root / following['path'] / 'capture.delta2').unlink()
    with pytest.raises(ReplayReady):
        runner.run_many([request], root, captures=[current], sequential=True)
    assert Counter(hashes) == expected_hashes(current)


def test_valid_following_reaches_real_virtual_calculation_through_runner(store, monkeypatch):
    root, current, following, hashes, request, original_run = store
    monkeypatch.setattr(runner, '_Run', original_run)
    monkeypatch.setattr(runner, 'runtime_config', lambda s: {'POINT_XAUUSD+': '0.01'})
    monkeypatch.setattr(runner, 'code_hash', lambda: 'integrity178-code')
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', ThreadPoolExecutor)
    def chunk(task):
        out = Path(task['out'])
        out.mkdir(parents=True, exist_ok=True)
        with (out / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=wh.FIELDS)
            writer.writeheader()
            writer.writerow(dict(run_id=task['run_id'], time_ms=OPENED, strategy='SPECIAL1', symbol='XAUUSD+', tf='1m',
                                 signal_id='last', recipient='A', direction='LONG', message='last candle',
                                 signal_source='OZ', signal_price=10, b0_price=8))
        return dict(pid=os.getpid(), bundles=1, processor_timings={}, max_memory_bytes=1, approximate=False,
                    alerts_csv=(out / 'alerts.csv').relative_to(root).as_posix(), task_start=task['start'],
                    task_end=task['end'], warm_start=task['warm_start'], warmup_bundles=0, elapsed_seconds=.01,
                    cancelled=False, processed_start_ms=OPENED, processed_end_ms=OPENED, alert_months={'2026-09': 1})
    monkeypatch.setattr(runner, 'run_chunk', chunk)
    (done,) = runner.run_many([request], root, sequential=True)
    assert done['status'] == 'COMPLETE'
    with (root / done['virtual_entry']['trades_csv']).open(encoding='utf-8-sig', newline='') as handle:
        trades = list(csv.DictReader(handle))
    assert len(trades) == 9 and {row['result'] for row in trades} == {'WIN'}
    assert {int(row['exit_time']) for row in trades} == {OPENED}
    assert Counter(hashes) == expected_hashes(current, following)
