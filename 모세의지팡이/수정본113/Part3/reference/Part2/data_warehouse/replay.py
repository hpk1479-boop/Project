"""DuckDB copy of complete, current-runtime numerical replay tapes.

Stored data are numbers/NativeCell exports, not Alert/Entry/Exit decisions.
M1 research materializations are NOT accepted by this replay contract.
"""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
from .legacy import identity,plain
from .store import Store,dumps,sha
from calculations.identity import calculation_hash

CACHE_VERSION='BACKTEST_COMMON_TAPE_V1'


class ReplayMismatch(RuntimeError):
    """A selected complete tape diverged: restart the whole run without it."""


def verify_replay_manifest(manifest,descriptor,calc_hash):
    if manifest.get('identity')!=identity({k:v for k,v in manifest.items() if k!='identity'}):
        raise ValueError('REPLAY_MANIFEST_HASH_MISMATCH')
    if manifest.get('status')!='COMPLETE' or manifest.get('version')!=CACHE_VERSION:
        raise ValueError('REPLAY_NOT_COMPLETE')
    if manifest.get('descriptor')!=plain(descriptor):raise ValueError('REPLAY_DESCRIPTOR_MISMATCH')
    if manifest.get('canonical_calculation_hash')!=calc_hash:raise ValueError('CALC_HASH_MISMATCH')
    key=identity((CACHE_VERSION,plain(descriptor)))
    if manifest.get('cache_key')!=key:raise ValueError('REPLAY_KEY_MISMATCH')
    for sk,entry in manifest['streams'].items():
        if sk!=identity(entry['name']):raise ValueError('REPLAY_STREAM_IDENTITY_MISMATCH')
        if entry['count']!=sum(x['rows'] for x in entry['blocks']):raise ValueError('REPLAY_STREAM_COUNT_MISMATCH')
    return key


def import_replay(store,root,*,defer_percentile_validation=False):
    """Import only newly stamped canonical tapes; never relabel an old formula."""
    root=Path(root).resolve();m=json.loads((root/'manifest.json').read_text('utf8'))
    h=calculation_hash();key=verify_replay_manifest(m,m['descriptor'],h)
    has_percentile=any(entry['name'][0]=='PERCENTILE' for entry in m['streams'].values())
    status='PENDING' if has_percentile else 'PASS'
    with store.transaction():
        store.db.execute('DELETE FROM numerical_replay_blocks WHERE replay_key=?',[key])
        for stream_key,entry in m['streams'].items():
            for number,block in enumerate(entry['blocks']):
                relative=Path(block['file']);path=(root/relative).resolve()
                if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(root):
                    raise ValueError('REPLAY_BLOCK_PATH')
                payload=path.read_bytes()
                if sha(payload)!=block['sha256']:raise ValueError('REPLAY_BLOCK_HASH_MISMATCH')
                decoded=json.loads(gzip.decompress(payload))
                if len(decoded)!=block['rows'] or any(not isinstance(x,list) or len(x)!=2 for x in decoded):
                    raise ValueError('REPLAY_BLOCK_LAYOUT_MISMATCH')
                store.db.execute('INSERT INTO numerical_replay_blocks VALUES (?,?,?,?,?)',
                                 [key,stream_key,number,payload,sha(payload)])
                actual=bytes(store.db.execute('SELECT payload FROM numerical_replay_blocks WHERE replay_key=? AND stream_key=? AND block_number=?',
                                              [key,stream_key,number]).fetchone()[0])
                if actual!=payload:raise ValueError('REPLAY_DB_ROUNDTRIP_MISMATCH')
        store.db.execute('INSERT OR REPLACE INTO numerical_replays VALUES (?,?,?,?,?)',
                         [key,dumps(m['descriptor']),h,dumps(m),status])
    if has_percentile and not defer_percentile_validation:
        validate_percentile_replay(store,key)
        status='PASS'
    return {'status':status,'replay_key':key,'streams':len(m['streams']),
            'validation':'RECORDED_CURRENT_RUNTIME_TO_DB_EXACT_BYTES'}


class ReplaySnapshot:
    def __init__(self,path,descriptor):
        self.store=Store(path,read_only=True);self.closed=False
        self.descriptor=plain(descriptor);self.key=identity((CACHE_VERSION,self.descriptor))
        try:
            self.store.db.execute('BEGIN TRANSACTION')
            row=self.store.db.execute('SELECT descriptor_json,calc_hash,manifest_json,status FROM numerical_replays WHERE replay_key=?',
                                      [self.key]).fetchone()
            if not row:raise ValueError('REPLAY_FULL_REQUEST_MISS')
            if row[3]!='PASS':raise ValueError('REPLAY_VALIDATION_NOT_PASS')
            if json.loads(row[0])!=self.descriptor:raise ValueError('REPLAY_DESCRIPTOR_MISMATCH')
            if row[1]!=calculation_hash():raise ValueError('CALC_HASH_MISMATCH')
            self.manifest=json.loads(row[2]);verify_replay_manifest(self.manifest,self.descriptor,row[1])
            expected_count=sum(len(e['blocks']) for e in self.manifest['streams'].values())
            count=self.store.db.execute('SELECT count(*) FROM numerical_replay_blocks WHERE replay_key=?',[self.key]).fetchone()[0]
            if count!=expected_count:raise ValueError('REPLAY_BLOCK_COUNT_MISMATCH')
            for sk,entry in self.manifest['streams'].items():
                for i,block in enumerate(entry['blocks']):self.block(sk,i,block)
        except BaseException:
            self.close();raise

    def block(self,stream_key,number,descriptor):
        row=self.store.db.execute('SELECT payload,payload_hash FROM numerical_replay_blocks WHERE replay_key=? AND stream_key=? AND block_number=?',
                                  [self.key,stream_key,number]).fetchone()
        if not row:raise ReplayMismatch('REPLAY_BLOCK_MISSING')
        payload=bytes(row[0])
        if sha(payload)!=row[1] or row[1]!=descriptor['sha256']:raise ReplayMismatch('REPLAY_BLOCK_HASH_MISMATCH')
        values=json.loads(gzip.decompress(payload))
        if len(values)!=descriptor['rows'] or any(not isinstance(x,list) or len(x)!=2 for x in values):
            raise ReplayMismatch('REPLAY_BLOCK_LAYOUT_MISMATCH')
        return values

    def close(self):
        if not getattr(self,'closed',True):
            self.closed=True
            try:self.store.db.execute('ROLLBACK')
            except Exception:pass
            self.store.close()
    def __del__(self):self.close()


