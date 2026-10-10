"""Lossless storage of the uploaded backtester's real-tick archive contract.

M1 rows are never converted into artificial ticks. Duplicate tick timestamps and
all binary64 payload bits are retained. No import from another application.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import numpy as np
from .legacy import identity
from .store import dumps, sha

TICK_DTYPE = np.dtype([('time','<i8'),('bid','<f8'),('ask','<f8'),('last','<f8'),
                      ('volume','<u8'),('time_msc','<i8'),('flags','<u4'),('volume_real','<f8')])


def decode_chunk(payload: bytes, descriptor: dict, previous_ms: int = -1):
    """Verify the original .npy file, exact dtype, raw bytes, order and ownership."""
    if sha(payload) != descriptor['file_sha256']:
        raise ValueError('TICK_FILE_HASH_MISMATCH')
    rows = np.load(io.BytesIO(payload), allow_pickle=False)
    if rows.ndim != 1 or rows.dtype != TICK_DTYPE:
        raise ValueError('TICK_DTYPE_MISMATCH')
    if len(rows) != descriptor['count'] or sha(rows.tobytes()) != descriptor['raw_sha256']:
        raise ValueError('TICK_RAW_HASH_OR_COUNT_MISMATCH')
    ms = rows['time_msc']
    if len(rows):
        if int(ms[0]) < previous_ms or np.any(ms[1:] < ms[:-1]):
            raise ValueError('TICK_ORDER_MISMATCH')
        # Python integer multiplication: do not overflow int64 nanoseconds.
        if not (int(ms[0])*1_000_000 >= descriptor['coverage_start_ns'] and
                int(ms[-1])*1_000_000 < descriptor['coverage_end_ns']):
            raise ValueError('TICK_RANGE_MISMATCH')
        previous_ms = int(ms[-1])
    return rows, previous_ms


def validate_manifest(manifest: dict):
    if manifest.get('kind') != 'GENERIC_RAW_ARCHIVE_V1' or manifest.get('status') != 'READY':
        raise ValueError('TICK_ARCHIVE_NOT_READY')
    if manifest.get('archive_identity') != identity({k:v for k,v in manifest.items() if k!='archive_identity'}):
        raise ValueError('TICK_MANIFEST_HASH_MISMATCH')
    if manifest.get('input_kind') != 'BROKER_REAL_TICKS':
        raise ValueError('REAL_TICKS_REQUIRED')
    if manifest.get('count', 0) <= 0:
        raise ValueError('EMPTY_TICK_ARCHIVE')
    expected_stream = identity(('BROKER_STREAM', manifest['instrument']['server_fingerprint'],
                                manifest['instrument']['broker_symbol']))
    if manifest.get('stream_namespace') != expected_stream:
        raise ValueError('TICK_SOURCE_IDENTITY_MISMATCH')
    cursor = manifest['coverage_start_ns']
    for d in manifest['chunks']:
        if (d['coverage_start_ns'] != cursor or d['coverage_end_ns'] <= cursor or
                d['stream_namespace'] != expected_stream):
            raise ValueError('TICK_CHUNK_COVERAGE_OR_SOURCE_MISMATCH')
        cursor = d['coverage_end_ns']
    if cursor != manifest['coverage_end_ns']:
        raise ValueError('TICK_ARCHIVE_PARTIAL')
    return manifest


def import_archive(store, root, source_id: str):
    """One atomic import; preflight and round-trip check before publishing PASS."""
    root = Path(root).resolve()
    manifest = validate_manifest(json.loads((root/'manifest.json').read_text('utf8')))
    source = store.db.execute('SELECT broker, server, symbol, instrument_json FROM sources WHERE source_id=?',
                              [source_id]).fetchone()
    if not source or source[2] != manifest['instrument']['broker_symbol']:
        raise ValueError('SOURCE_SYMBOL_MISMATCH')
    if identity(source[1]) != manifest['instrument']['server_fingerprint']:
        raise ValueError('SOURCE_SERVER_MISMATCH')
    if json.loads(source[3]) != manifest['instrument']:
        raise ValueError('SOURCE_INSTRUMENT_MISMATCH')
    aid = manifest['archive_identity']
    total = 0; last = -1; raw_hash = hashlib.sha256()
    # Transaction retains the preceding usable archive on any read/validation error.
    with store.transaction():
        store.db.execute('DELETE FROM tick_archive_chunks WHERE archive_id=?', [aid])
        for i, desc in enumerate(manifest['chunks']):
            path = (root/desc['file']).resolve()
            if not path.is_relative_to(root/'chunks') or path.is_symlink():
                raise ValueError('ARCHIVE_PATH_OUTSIDE_CHUNKS')
            payload = path.read_bytes()
            rows, last = decode_chunk(payload, desc, last)
            total += len(rows); raw_hash.update(rows.tobytes())
            store.db.execute('INSERT INTO tick_archive_chunks VALUES (?, ?, ?, ?)',
                             [aid, i, payload, desc['raw_sha256']])
            stored = bytes(store.db.execute('SELECT payload FROM tick_archive_chunks WHERE archive_id=? AND chunk_number=?',
                                            [aid, i]).fetchone()[0])
            if stored != payload:
                raise ValueError('TICK_DB_ROUNDTRIP_MISMATCH')
        if total != manifest['count'] or raw_hash.hexdigest() != manifest['raw_sha256']:
            raise ValueError('TICK_ARCHIVE_HASH_OR_COUNT_MISMATCH')
        store.db.execute('INSERT OR REPLACE INTO tick_archives VALUES (?, ?, ?, ?, ?, ?, ?)',
                         [aid, source_id, source[2], manifest['coverage_start_ns'],
                          manifest['coverage_end_ns'], dumps(manifest), 'PASS'])
    return {'archive_id': aid, 'status': 'PASS', 'count': total,
            'raw_sha256': raw_hash.hexdigest(), 'broker_history_completeness': 'UNVERIFIED'}


def archive_catalog(store):
    result = []
    for row in store.db.execute('SELECT archive_id, source_id, symbol, coverage_start_ns, coverage_end_ns, manifest_json, status FROM tick_archives ORDER BY symbol, coverage_start_ns').fetchall():
        result.append(dict(zip(('archive_id','source_id','symbol','start_ns','end_ns','manifest','status'),
                               (*row[:5],json.loads(row[5]),row[6]))))
    return result


class TickArchiveSnapshot:
    """A verified read transaction. Only current rows are exposed by the iterator.

    Whole archive verification completes BEFORE use. The held read transaction
    prevents a successful preflight from referring to subsequently revised data.
    It creates no raw .npy or generic_cache disk files.
    """
    def __init__(self, path, *, instrument: dict, start_ns: int, end_ns: int,
                 archive_id: str | None = None, source_id: str | None = None, cancel=lambda:None):
        from .store import Store
        self.store = Store(path, read_only=True)
        self.cancel = cancel
        self.closed = False
        try:
            self.store.db.execute('BEGIN TRANSACTION')
            candidates = [x for x in archive_catalog(self.store) if x['status']=='PASS'
                          and x['start_ns']<=start_ns and x['end_ns']>=end_ns
                          and x['manifest']['instrument']==instrument
                          and (archive_id is None or x['archive_id']==archive_id)
                          and (source_id is None or x['source_id']==source_id)]
            if not candidates:
                raise ValueError('REAL_TICK_ARCHIVE_FULL_RANGE_OR_WARMUP_MISS')
            # Never merge archives. Prefer the shortest qualifying *whole* input.
            selected = min(candidates, key=lambda x:(x['end_ns']-x['start_ns'],x['archive_id']))
            self.manifest = validate_manifest(selected['manifest'])
            self.archive_id = selected['archive_id']
            source = self.store.db.execute('SELECT server, symbol, instrument_json FROM sources WHERE source_id=?',
                                           [selected['source_id']]).fetchone()
            if (not source or source[1]!=instrument['broker_symbol'] or
                    identity(source[0])!=instrument['server_fingerprint'] or json.loads(source[2])!=instrument):
                raise ValueError('TICK_SOURCE_IDENTITY_MISMATCH')
            count_db = self.store.db.execute('SELECT COUNT(*) FROM tick_archive_chunks WHERE archive_id=?',
                                            [self.archive_id]).fetchone()[0]
            if count_db != len(self.manifest['chunks']):
                raise ValueError('TICK_CHUNK_COUNT_MISMATCH')
            last=-1; count=0; digest=hashlib.sha256()
            for i, desc in enumerate(self.manifest['chunks']):
                self.cancel()
                rows, last = decode_chunk(self._payload(i), desc, last)
                count += len(rows); digest.update(rows.tobytes())
            if count!=self.manifest['count'] or digest.hexdigest()!=self.manifest['raw_sha256']:
                raise ValueError('TICK_ARCHIVE_HASH_OR_COUNT_MISMATCH')
        except BaseException:
            self.close(); raise

    def _payload(self, number):
        result = self.store.db.execute('SELECT payload FROM tick_archive_chunks WHERE archive_id=? AND chunk_number=?',
                                       [self.archive_id,number]).fetchone()
        if not result: raise ValueError('TICK_CHUNK_MISSING')
        return bytes(result[0])

    def __iter__(self):
        from pit.models import RAW, TickRecord
        ordinal=0; last=-1
        for i, desc in enumerate(self.manifest['chunks']):
            self.cancel()
            rows, last = decode_chunk(self._payload(i), desc, last)
            for fields in RAW.iter_unpack(memoryview(rows)):
                ordinal+=1
                yield TickRecord(self.manifest['stream_namespace'], ordinal, *fields)

    def close(self):
        if not getattr(self, 'closed', True):
            self.closed=True
            try: self.store.db.execute('ROLLBACK')
            except Exception: pass
            self.store.close()

    def __del__(self):
        self.close()
