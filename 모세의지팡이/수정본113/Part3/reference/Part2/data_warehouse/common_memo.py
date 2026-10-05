"""Whole-run, validated pure common-feature memo. No strategy decisions.

Private SQLite files are only a bounded local writer spool. Published identity
uses content/contracts, never a drive path. Both applications own this module.
"""
from __future__ import annotations
import gzip,hashlib,json,sqlite3
from pathlib import Path
from .store import Store,dumps,sha
from .legacy import identity,plain
from .replay import ReplayMismatch
from calculations.identity import calculation_hash,environment_identity
VERSION='LIVE_COMMON_FEATURE_JOB_V1'
DDL='''
CREATE TABLE IF NOT EXISTS common_feature_jobs (
 job_key VARCHAR PRIMARY KEY, descriptor_json VARCHAR NOT NULL, scope_json VARCHAR NOT NULL,
 calc_hash VARCHAR NOT NULL, environment_json VARCHAR NOT NULL, status VARCHAR NOT NULL,
 row_count BIGINT NOT NULL, payload_hash VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS common_feature_values (
 job_key VARCHAR NOT NULL, entry_key VARCHAR NOT NULL, checksum VARCHAR NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(job_key,entry_key));
'''


def job_identity(scope,descriptor):return identity((VERSION,scope,descriptor))

def _add_chain(chain,key,checksum):chain.update((key+':'+checksum+'\n').encode('ascii'))


def import_common_memo(store,path,scope,descriptor):
    """Original runtime numerical exports -> exact byte read-back -> PASS."""
    key=job_identity(scope,descriptor);path=Path(path).resolve()
    if not path.is_file():raise ValueError('COMMON_REQUEST_SPOOL_MISSING')
    source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    store.db.execute(DDL)
    try:
        count=0;chain=hashlib.sha256()
        with store.transaction():
            store.db.execute('DELETE FROM common_feature_values WHERE job_key=?',[key])
            for entry,checksum,data in source.execute('SELECT k,checksum,data FROM numeric_inputs ORDER BY k'):
                if len(entry)!=64 or len(checksum)!=64 or sha(data)!=checksum:raise ValueError('COMMON_REQUEST_HASH_MISMATCH')
                json.loads(gzip.decompress(data))
                store.db.execute('INSERT INTO common_feature_values VALUES (?,?,?,?)',[key,entry,checksum,data])
                back=store.db.execute('SELECT payload FROM common_feature_values WHERE job_key=? AND entry_key=?',[key,entry]).fetchone()
                if not back or bytes(back[0])!=data:raise ValueError('COMMON_WRITE_ROUNDTRIP')
                count+=1;_add_chain(chain,entry,checksum)
            store.db.execute('INSERT OR REPLACE INTO common_feature_jobs VALUES (?,?,?,?,?,?,?,?)',
                [key,dumps(plain(descriptor)),dumps(plain(scope)),calculation_hash('COMMON_FEATURES'),
                 dumps(environment_identity()),'PASS',count,chain.hexdigest()])
        return {'job_key':key,'status':'PASS','rows':count,'validation':'CURRENT_RUNTIME_NUMERICAL_EXPORT_EXACT_BYTE_ROUNDTRIP'}
    finally:source.close()


class CommonMemoSnapshot:
    def __init__(self,path,scope,descriptor):
        self.scope=plain(scope);self.descriptor=plain(descriptor);self.key=job_identity(self.scope,self.descriptor)
        self.store=Store(path,read_only=True);self.closed=False;self.used=set()
        try:
            self.store.db.execute('BEGIN TRANSACTION')
            row=self.store.db.execute('SELECT descriptor_json,scope_json,calc_hash,environment_json,status,row_count,payload_hash FROM common_feature_jobs WHERE job_key=?',[self.key]).fetchone()
            if not row:raise ValueError('COMMON_FULL_REQUEST_MISS')
            if json.loads(row[0])!=self.descriptor or json.loads(row[1])!=self.scope:raise ValueError('COMMON_REQUEST_IDENTITY')
            if row[2]!=calculation_hash('COMMON_FEATURES') or json.loads(row[3])!=environment_identity():raise ValueError('CALC_HASH_OR_ENVIRONMENT_MISMATCH')
            if row[4]!='PASS':raise ValueError('VALIDATION_NOT_PASS:'+row[4])
            self.expected_count=row[5];count=0;chain=hashlib.sha256()
            # Verify the entire requested memo before returning any values.
            cursor=self.store.db.execute('SELECT entry_key,checksum,payload FROM common_feature_values WHERE job_key=? ORDER BY entry_key',[self.key])
            while True:
                values=cursor.fetchmany(256)
                if not values:break
                for entry,checksum,payload in values:
                    if sha(bytes(payload))!=checksum:raise ValueError('COMMON_BLOCK_HASH_MISMATCH')
                    json.loads(gzip.decompress(bytes(payload)));_add_chain(chain,entry,checksum);count+=1
            if count!=self.expected_count or chain.hexdigest()!=row[6]:raise ValueError('COMMON_FULL_REQUEST_COUNT_OR_HASH')
        except BaseException:self.close();raise
    def get(self,key):
        try:return self._get(key)
        except ReplayMismatch:raise
        except Exception as exc:raise ReplayMismatch('COMMON_FEATURE_READ_FAILED: '+str(exc)) from exc
    def _get(self,key):
        entry=identity((self.scope,key));row=self.store.db.execute('SELECT checksum,payload FROM common_feature_values WHERE job_key=? AND entry_key=?',[self.key,entry]).fetchone()
        if row is None:raise ReplayMismatch('COMMON_FEATURE_REQUEST_PREFIX_MISMATCH')
        if sha(bytes(row[1]))!=row[0]:raise ReplayMismatch('COMMON_FEATURE_READ_HASH_MISMATCH')
        self.used.add(entry);return json.loads(gzip.decompress(bytes(row[1])))
    def finish(self):
        try:
            if len(self.used)!=self.expected_count:raise ReplayMismatch('COMMON_FEATURE_REQUEST_INCOMPLETE')
        finally:self.close()
    def close(self):
        if not getattr(self,'closed',True):
            self.closed=True
            try:self.store.db.execute('ROLLBACK')
            except Exception:pass
            self.store.close()
    def __del__(self):self.close()
