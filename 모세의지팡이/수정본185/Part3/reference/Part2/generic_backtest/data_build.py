"""Import a verified Generic Backtest real-tick archive into DuckDB.

This module never talks to MT5. Acquisition stays in the existing history
process; this stage only verifies and publishes the immutable archive into the
warehouse transactionally.
"""
from __future__ import annotations
from pathlib import Path
import json


def import_archive_to_duckdb(archive, database, source_identity):
    from data_warehouse.store import Store
    from data_warehouse.ticks import import_archive, validate_manifest

    archive=Path(archive).expanduser().resolve()
    database=Path(database).expanduser().resolve()
    if not (archive/'manifest.json').is_file():
        raise ValueError('DATA_BUILD_ARCHIVE_MANIFEST_MISSING')
    manifest=validate_manifest(json.loads((archive/'manifest.json').read_text('utf-8')))
    source_identity=dict(source_identity or {})
    broker=str(source_identity.get('broker','')).strip()
    server=str(source_identity.get('server','')).strip()
    if not broker or not server:
        raise ValueError('DATA_BUILD_SOURCE_IDENTITY_MISSING')
    from data_warehouse.legacy import identity
    if identity(server)!=manifest['instrument'].get('server_fingerprint'):
        raise ValueError('DATA_BUILD_SERVER_IDENTITY_MISMATCH')
    symbol=manifest['instrument']['broker_symbol']
    create=not database.is_file()
    with Store(database,create=create) as store:
        source_id=store.add_source(broker,server,symbol,manifest['instrument'])
        result=import_archive(store,archive,source_id)
        store.checkpoint_file()
    # Persistent data lives only in DuckDB. The Generic-owned acquisition
    # archive (generic_cache/raw) is just a transfer artifact: remove it after
    # the verified, checkpointed import. A failed import raises above and keeps
    # it so the next build can retry without re-downloading.
    removed=_remove_transfer_archive(archive,manifest['archive_identity'],result)
    return dict(result,database=str(database),source_id=source_id,symbol=symbol,
                start_ns=manifest['coverage_start_ns'],end_ns=manifest['coverage_end_ns'],
                archive_removed=removed)


def _remove_transfer_archive(archive,archive_identity,result):
    import shutil
    from .contracts import ROOT
    transfer_root=(ROOT/'generic_cache'/'raw').resolve()
    if (result.get('status')!='PASS' or result.get('archive_id')!=archive_identity
            or archive.parent!=transfer_root):
        return False
    shutil.rmtree(archive)
    return True


def build_native_to_duckdb(profile, symbol, start_ns, end_ns, database, source_id, work_dir,
                           *, emit=lambda *a:None, cancel=lambda:None):
    """Run unified STAFF in MT5 Strategy Tester and atomically import native snapshots."""
    from .native_mt5 import run_native_tester
    from data_warehouse.native import import_native_export
    from data_warehouse.store import Store
    launch=run_native_tester(profile,symbol,int(start_ns),int(end_ns),work_dir,emit=emit,cancel=cancel)
    start_s=(int(start_ns)+999_999_999)//1_000_000_000
    end_s=(int(end_ns)+999_999_999)//1_000_000_000
    with Store(database) as store:
        result=import_native_export(store,launch['export'],source_id,requested_start=start_s,requested_end=end_s)
        store.checkpoint_file()
    # Native binary is only a transfer artifact. Remove it after a verified DB commit
    # so a 5-year build never permanently doubles disk usage. Failed imports keep it.
    import shutil
    shutil.rmtree(launch['export'],ignore_errors=False)
    return dict(result,database=str(Path(database).expanduser().resolve()),source_id=source_id,
                symbol=symbol,session=launch['session'],export_removed=True,feeds=launch['feeds'],
                elapsed_seconds=launch['elapsed_seconds'])
