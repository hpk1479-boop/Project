"""Backtest one-time caches live in RAM only; SSD persistence is data-build only.

Regression tests for the RAM-only cache change:
- numerical replay tape: no files, no cross-run reuse, released at run end
- common-feature memo: in-memory SQLite, shared inside one run only
- DuckDB backtest miss: no %TEMP% request spools, no DuckDB writeback
- MT5 backtest raw ticks / tester export: run scratch folder deleted after the run
- numerical fingerprint unchanged so data built by the data-build mode stays valid
"""
import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generic_backtest.contracts import ROOT
from generic_backtest.fast import disk_cache
from generic_backtest.fast.disk_cache import NumericalReplayCache, calculation_version
from generic_backtest.fast.feature_memo import CommonFeatureMemo
from generic_backtest.fast import memo_client


def _files(root):
    root = Path(root)
    return sorted(p for p in root.rglob('*')) if root.exists() else []


def _moses_spools():
    return sorted(p.name for p in Path(tempfile.gettempdir()).glob('moses-*'))


def test_tape_writes_nothing_and_never_hits_a_previous_run(tmp_path):
    descriptor = {'case': 'ram-only', 'values': [1, 2, 3]}
    name = ('PRICE', {'tf': '1m', 'window': 50})
    rows = [({'i': i}, {'v': i * 0.125}) for i in range(300)]
    before = _files(ROOT / 'generic_cache')
    first = NumericalReplayCache(descriptor, root=tmp_path)
    stream = first.stream(name)
    for expected, value in rows:
        assert stream.read(expected) is None
        stream.append(expected, value)
    assert first.stats['misses'] == 300 and first.stats['storage'] == 'RAM_RUN_SCOPED'
    assert first.path is None and first.destination is None
    first.finish()
    assert first.stats['status'] == 'RAM_RELEASED' and first.streams == {}
    assert _files(tmp_path) == [] and _files(ROOT / 'generic_cache') == before
    # A second run with the identical descriptor recomputes everything.
    second = NumericalReplayCache(descriptor, root=tmp_path)
    assert second.hit is False and second.stream(name).read(rows[0][0]) is None
    second.abort()
    assert second.stats['status'] == 'RAM_RELEASED_ABORT'


def test_tape_does_not_accumulate_sealed_blocks():
    cache = NumericalReplayCache({'case': 'bounded'})
    stream = cache.stream(('STO', '1m'))
    for i in range(disk_cache.BLOCK_ROWS * 5 + 3):
        stream.append({'i': i}, i)
    assert len(stream.buffer) == 3 and stream.blocks == [disk_cache.BLOCK_ROWS] * 5
    cache.finish()


def test_disabled_tape_is_inert():
    cache = NumericalReplayCache({'case': 'off'}, enabled=False)
    assert cache.stream(('DI', '1m')) is None and cache.stats['status'] == 'DISABLED'
    cache.finish();cache.abort()


def test_storage_only_change_keeps_numerical_fingerprint():
    pinned = disk_cache._STORAGE_ONLY_COMPAT_SHA256
    assert set(pinned) == {'generic_backtest/fast/disk_cache.py', 'generic_backtest/fast/feature_memo.py'}
    # Formula sources are still hashed from disk; only storage files are pinned.
    assert pinned['generic_backtest/fast/disk_cache.py'] != hashlib.sha256(
        (ROOT / 'generic_backtest/fast/disk_cache.py').read_bytes()).hexdigest()


def test_common_memo_is_in_memory_and_run_scoped(tmp_path):
    before = _files(ROOT / 'generic_cache')
    key = memo_client.make_key('OPEN_HMA', '1m', {'period': 6}, 'a' * 64)
    memo = CommonFeatureMemo({'fixture': 'ram'}, root=tmp_path)
    assert memo.handle({'op': 'get', 'key': key}) is None
    memo.handle({'op': 'put', 'key': key, 'value': {'value': 1.5}})
    assert memo.handle({'op': 'get', 'key': key}) == {'value': 1.5}
    assert memo.stats['hits'] == 1 and memo.stats['storage'] == 'RAM_RUN_SCOPED'
    memo.close()
    assert memo.connection is None
    assert _files(tmp_path) == [] and _files(ROOT / 'generic_cache') == before
    fresh = CommonFeatureMemo({'fixture': 'ram'}, root=tmp_path)
    try:assert fresh.handle({'op': 'get', 'key': key}) is None
    finally:fresh.close()


def _duckdb_resources(tmp_path):
    db = tmp_path / 'built.duckdb';db.touch()
    return {'duckdb_enabled': True, 'duckdb_path': str(db), 'disk_cache': True}


def test_duckdb_numerical_miss_uses_ram_tape_without_spool(tmp_path, monkeypatch):
    from generic_backtest import hybrid
    def miss(*a, **k):raise ValueError('REPLAY_NOT_FOUND')
    monkeypatch.setattr(hybrid, 'DatabaseNumericalReplay', miss)
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda *a, **k: pytest.fail('no temporary spool allowed'))
    spools = _moses_spools()
    cache = hybrid.select_replay({'timeframes': ['1m'], 'indicator_parameters': []}, _duckdb_resources(tmp_path))
    assert isinstance(cache, NumericalReplayCache) and cache.enabled and not cache.hit
    assert not getattr(cache, 'capture_inputs', False) and not hasattr(cache, 'temporary_root')
    cache.finish()
    assert _moses_spools() == spools


def test_duckdb_common_miss_uses_ram_memo_without_spool(tmp_path, monkeypatch):
    import types
    from generic_backtest import common_hybrid
    fake = types.ModuleType('data_warehouse.common_memo')
    def miss(*a, **k):raise ValueError('COMMON_NOT_FOUND')
    fake.CommonMemoSnapshot = miss
    monkeypatch.setitem(sys.modules, 'data_warehouse.common_memo', fake)
    monkeypatch.setattr(tempfile, 'mkdtemp', lambda *a, **k: pytest.fail('no temporary spool allowed'))
    events = []
    memo = common_hybrid.select_common_memo({'s': 1}, {'d': 1}, _duckdb_resources(tmp_path),
                                            lambda kind, payload: events.append(payload))
    assert isinstance(memo, CommonFeatureMemo) and memo.enabled and not hasattr(memo, 'temporary_root')
    assert events[-1]['source'] == 'RUNTIME' and 'queued_writeback' not in events[-1]
    assert not hasattr(common_hybrid, 'completed_job')
    memo.close()


def test_duckdb_allzone_miss_records_nothing(tmp_path, monkeypatch):
    import data_warehouse.observations as observations
    from generic_backtest.allzone_hybrid import AllzoneJobChannel
    def miss(*a, **k):raise ValueError('OBSERVATION_JOB_NOT_FOUND')
    monkeypatch.setattr(observations, 'ObservationSnapshot', miss)
    monkeypatch.setattr(observations, 'RequestRecorder', lambda *a, **k: pytest.fail('no request spool allowed'))
    channel = AllzoneJobChannel({'role': 'SIGNAL'}, _duckdb_resources(tmp_path), lambda request: None)
    assert channel.enabled is False and channel.finish() is None
    channel.abort()


def test_backtest_has_no_duckdb_writeback_path():
    assert importlib.util.find_spec('generic_backtest.duckdb_writeback') is None
    source = (ROOT / 'generic_backtest' / 'runner.py').read_text(encoding='utf-8')
    for token in ('publish(', 'duckdb_writeback', 'moses-numerical-request', 'moses-common-request',
                  'moses-allzone-request'):
        assert token not in source


def test_run_scratch_is_deleted_and_stale_folders_are_purged():
    from generic_backtest import run_scratch
    mine = run_scratch.create()
    (mine / 'raw' / 'chunks').mkdir(parents=True)
    (mine / 'raw' / 'chunks' / '00000000.npy').write_bytes(b'x' * 64)
    assert mine.parent == run_scratch.SCRATCH_ROOT and mine.name.startswith(f'{os.getpid()}_')
    stale = run_scratch.SCRATCH_ROOT / '999999999_deadbeef'
    (stale / 'native_mt5').mkdir(parents=True)
    try:
        removed = run_scratch.purge_stale()
        assert stale.name in removed and not stale.exists()
        assert mine.exists()  # the owning (current) process is alive
        assert run_scratch.release(mine) and not mine.exists()
        assert run_scratch.release(ROOT / 'generic_cache' / 'raw') is False  # never outside scratch
    finally:
        run_scratch.release(mine);run_scratch.release(stale)


def test_history_prepare_rejects_foreign_scratch_folder():
    from generic_backtest import run_scratch
    assert not run_scratch._owned(ROOT / 'generic_cache' / 'raw')
    assert not run_scratch._owned(run_scratch.SCRATCH_ROOT / 'no-owner')
    assert run_scratch._owned(run_scratch.SCRATCH_ROOT / '123_abc')
