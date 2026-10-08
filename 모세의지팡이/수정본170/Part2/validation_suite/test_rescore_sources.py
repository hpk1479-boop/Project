"""RR rescore keeps working after one-time raw ticks are deleted.

Also: data build keeps permanent data only in DuckDB (transfer archive removed
after a verified import; a failed import keeps it for retry).
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generic_backtest import runner, rescore as rescore_mod, rescore_job, run_scratch
from generic_backtest.contracts import GenericError, GenericRunConfig
from generic_backtest.canonical import file_hash
from generic_backtest.history.cache import GenericArchiveReader
from generic_backtest.results import read_lines, verify_result
from cadence_input_validation.test_input import archive_from_parts, native_rows, CAL, INSTRUMENT

PLUGIN = 'RESCORE_SOURCE_TEST_ONLY'
SOURCE = '''from generic_backtest.contracts import StrategyRequirements, AlertIntent, GenericEntryIntent
BACKTEST_PLUGIN = {
    'api_version': 'GENERIC_BACKTEST_PLUGIN_V1', 'plugin_id': 'RESCORE_SOURCE_TEST_ONLY',
    'display_name': 'Test fixture only', 'plugin_version': '1.0.0',
    'supported_modes': ['ALERT_ONLY', 'TRADE'], 'parameters_schema': {}, 'risk_anchors': {}}
def requirements(params, selection):
    req=StrategyRequirements(('1m',), {'1m':0})
    return {'signal':req, 'entry':req}
class Signal:
    def on_observation(self,ctx):
        n=ctx.token.source_ordinal
        if n%3:return ()
        return (AlertIntent(str(n),'LONG' if n%2 else 'SHORT',ctx.symbol,signal_tf='1m'),)
class Entry:
    def on_observation(self,ctx,alerts):
        return tuple(GenericEntryIntent(str(a['event_id']),ctx.symbol,a['direction'],
            ctx.quote['bid'],ctx.token.now_ns,ctx.token,'OBSERVED_QUOTE',{'field':'bid'},
            (a['event_id'],),'1m',{}) for a in alerts)
def create_alert_strategy(params):return Signal()
def create_entry_strategy(params):return Entry()
'''


@pytest.fixture(scope='module')
def trade_run():
    plugin = ROOT/'BACKTEST_SPECIAL'/(PLUGIN+'.py')
    assert not plugin.exists()
    plugin.write_text(SOURCE, encoding='utf-8')
    parent = ROOT/'generic_runs'; parent.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='rescore-source-test-', dir=parent) as directory:
            root = Path(directory)
            rows = native_rows(sorted(range(0, 660_000, 10_000)))
            rows['bid'] = 100.+np.sin(np.arange(len(rows))*.8)*2
            rows['ask'] = rows['bid']+.1; rows['last'] = rows['bid']
            raw, manifest = archive_from_parts(root/'raw', [rows], [0, 660*10**9])
            config = GenericRunConfig(mode='TRADE', plugin_id=PLUGIN, plugin_sha256=file_hash(plugin), parameters={},
                instrument=INSTRUMENT, start_ns=0, end_ns=660*10**9, calendar=CAL, archive=str(raw),
                evaluation_mode='TICK', session_filter={'enabled': False},
                stop_variants=({'id': 'distance', 'kind': 'PRICE_DISTANCE', 'value': .5},),
                target_variants=({'id': 'rr', 'kind': 'RR_MULTIPLE', 'value': 1.5},),
                outcome_price_basis='CHART_PRICE',
                resources={'max_history_bars': 100000, 'max_occurrences': 100000,
                           'worker_timeout_seconds': 120, 'max_ipc_bytes': 32*1024*1024})
            result = runner.GenericRunCoordinator(config).run(root/'run')
            assert result['status'] == 'SUCCEEDED'
            assert read_lines(root/'run'/'entries.jsonl')
            # Reference rescore while the original archive still exists.
            rescore_mod.rescore(root/'run', root/'reference', ['2'])
            yield root, raw, rows, manifest
    finally:
        plugin.unlink(missing_ok=True)


def _outcomes(path):
    return (Path(path)/'outcomes.jsonl').read_bytes()


def test_rescore_without_any_source_fails_with_clear_error(trade_run):
    root, raw, rows, _ = trade_run
    hidden = root/'raw-hidden'
    raw.rename(hidden)
    try:
        with pytest.raises(GenericError, match='E_RESCORE_SOURCE'):
            rescore_mod.rescore(root/'run', root/'no-source', ['2'])
    finally:
        hidden.rename(raw)


def test_rescore_from_explicit_reacquired_archive_is_exact(trade_run):
    root, raw, rows, _ = trade_run
    hidden = root/'raw-hidden-2'
    copy = root/'reacquired'
    shutil.copytree(raw, copy); raw.rename(hidden)
    try:
        rescore_mod.rescore(root/'run', root/'explicit', ['2'], archive=copy)
    finally:
        hidden.rename(raw)
    assert _outcomes(root/'explicit') == _outcomes(root/'reference')
    assert verify_result(root/'explicit')['metadata']['rescore_tick_source'] == 'ARCHIVE'


def test_rescore_rejects_different_ticks(trade_run):
    root, raw, rows, _ = trade_run
    changed = rows.copy(); changed['bid'][5] += 1.0
    other, _ = archive_from_parts(root/'different', [changed], [0, 660*10**9])
    with pytest.raises(GenericError, match='E_CHUNK_CORRUPT'):
        rescore_mod.rescore(root/'run', root/'different-out', ['2'], archive=other)


def test_rescore_reads_same_identity_from_data_build_duckdb(trade_run, monkeypatch):
    root, raw, rows, manifest = trade_run
    db = root/'built.duckdb'; db.touch()
    hidden = root/'raw-hidden-3'; copy = root/'db-copy'
    shutil.copytree(raw, copy); raw.rename(hidden)
    seen = {}
    import data_warehouse.ticks as ticks
    class FakeSnapshot(GenericArchiveReader):
        def __init__(self, path, *, instrument, start_ns, end_ns, archive_id=None, source_id=None, cancel=None):
            seen.update(path=path, archive_id=archive_id, instrument=instrument)
            super().__init__(copy); self.closed = False
        def close(self): self.closed = True; seen['closed'] = True
    monkeypatch.setattr(ticks, 'TickArchiveSnapshot', FakeSnapshot)
    try:
        rescore_mod.rescore(root/'run', root/'from-db', ['2'], database=str(db))
    finally:
        hidden.rename(raw)
    assert seen['archive_id'] == manifest['archive_identity'] and seen['closed']
    assert _outcomes(root/'from-db') == _outcomes(root/'reference')
    assert verify_result(root/'from-db')['metadata']['rescore_tick_source'] == 'DUCKDB'


def test_rescore_job_reacquires_into_scratch_and_deletes_it(trade_run):
    root, raw, rows, _ = trade_run
    hidden = root/'raw-hidden-4'; raw.rename(hidden)
    calls = []
    def fake_fetch(profile, symbol, start_ns, end_ns, scratch):
        calls.append((profile, symbol, start_ns, end_ns, Path(scratch)))
        archive, _ = archive_from_parts(Path(scratch)/'raw'/'key', [rows], [0, 660*10**9])
        return str(archive)
    try:
        rescore_job.rescore_with_source(root/'run', root/'job-out', ['2'], profile='profile.json', fetch=fake_fetch)
    finally:
        hidden.rename(raw)
    (profile, symbol, start_ns, end_ns, scratch), = calls
    assert profile == 'profile.json' and symbol == INSTRUMENT['broker_symbol']
    assert (start_ns, end_ns) == (0, 660*10**9)
    assert scratch.parent == run_scratch.SCRATCH_ROOT and not scratch.exists()
    assert _outcomes(root/'job-out') == _outcomes(root/'reference')


def test_rescore_job_uses_existing_source_without_mt5(trade_run):
    root, raw, rows, _ = trade_run
    rescore_job.rescore_with_source(root/'run', root/'job-direct', ['2'], profile='p.json',
                                    fetch=lambda *a: pytest.fail('MT5 must not be contacted'))
    assert _outcomes(root/'job-direct') == _outcomes(root/'reference')


def _fake_store(monkeypatch, calls, *, fail=False):
    import data_warehouse.store as store_mod
    import data_warehouse.ticks as tick_mod
    class FakeStore:
        def __init__(self, path, *, create=False): calls.append('open')
        def __enter__(self): return self
        def __exit__(self, *args): calls.append('close')
        def add_source(self, *a): return 'SID'
        def checkpoint_file(self): calls.append('checkpoint')
    def fake_import(store, root, sid):
        if fail: raise ValueError('TICK_DB_ROUNDTRIP_MISMATCH')
        m = json.loads((Path(root)/'manifest.json').read_text('utf-8'))
        calls.append('import')
        return {'archive_id': m['archive_identity'], 'status': 'PASS', 'count': m['count'], 'raw_sha256': m['raw_sha256']}
    monkeypatch.setattr(store_mod, 'Store', FakeStore)
    monkeypatch.setattr(tick_mod, 'import_archive', fake_import)


def _transfer_archive(name):
    from validation_suite.test_execution_modes import make_archive
    path = ROOT/'generic_cache'/'raw'/name
    shutil.rmtree(path, ignore_errors=True)
    make_archive(path, server='TEST-SERVER')
    return path


def test_data_build_removes_transfer_archive_after_verified_import(monkeypatch, tmp_path):
    from generic_backtest.data_build import import_archive_to_duckdb
    archive = _transfer_archive('build_transfer_test_' + tmp_path.name)
    calls = []
    _fake_store(monkeypatch, calls)
    try:
        result = import_archive_to_duckdb(archive, tmp_path/'x.duckdb', {'broker': 'B', 'server': 'TEST-SERVER'})
        assert calls == ['open', 'import', 'checkpoint', 'close']
        assert result['archive_removed'] is True and not archive.exists()
    finally:
        shutil.rmtree(archive, ignore_errors=True)


def test_data_build_keeps_transfer_archive_when_import_fails(monkeypatch, tmp_path):
    from generic_backtest.data_build import import_archive_to_duckdb
    archive = _transfer_archive('build_transfer_fail_' + tmp_path.name)
    _fake_store(monkeypatch, [], fail=True)
    try:
        with pytest.raises(ValueError, match='TICK_DB_ROUNDTRIP_MISMATCH'):
            import_archive_to_duckdb(archive, tmp_path/'x.duckdb', {'broker': 'B', 'server': 'TEST-SERVER'})
        assert (archive/'manifest.json').is_file()
    finally:
        shutil.rmtree(archive, ignore_errors=True)


def test_data_build_never_removes_archives_outside_generic_cache_raw(monkeypatch, tmp_path):
    from generic_backtest.data_build import import_archive_to_duckdb
    from validation_suite.test_execution_modes import make_archive
    make_archive(tmp_path/'user_archive', server='TEST-SERVER')
    _fake_store(monkeypatch, [])
    result = import_archive_to_duckdb(tmp_path/'user_archive', tmp_path/'x.duckdb', {'broker': 'B', 'server': 'TEST-SERVER'})
    assert result['archive_removed'] is False and (tmp_path/'user_archive'/'manifest.json').is_file()
