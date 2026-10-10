"""Transactional warehouse. Importing this module never imports DuckDB/MT5."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import uuid
import numpy as np
from .legacy import identity, encode
from .validation import normalize_m1, inspect_m1, compare_m1, range_coverage
from calculations.timeframe import M1_DTYPE

SCHEMA_VERSION='MOSES_WAREHOUSE_V1'


def dumps(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def sha(payload):return hashlib.sha256(payload).hexdigest()


def source_identity(broker: str, server: str, symbol: str) -> str:
    if not all(isinstance(v,str) and v.strip() for v in (broker,server,symbol)):
        raise ValueError('BROKER_SERVER_SYMBOL_REQUIRED')
    return sha(dumps({'broker':broker,'server':server,'symbol':symbol}).encode())


class Store:
    def __init__(self,path,*,read_only=False,create=False):
        self.path=Path(path).expanduser().resolve();self.read_only=bool(read_only)
        if not create and not self.path.is_file():raise FileNotFoundError(self.path)
        if read_only and create:raise ValueError('read_only and create are exclusive')
        # Optional dependency: even Part2 startup must work without it.
        import duckdb
        if create:self.path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=duckdb.connect(str(self.path),read_only=read_only,config={'threads':'1'})
        try:
            if create:
                known=self.connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='warehouse_meta'").fetchone()[0]
                if known:
                    version=self.connection.execute("SELECT value FROM warehouse_meta WHERE key='schema_version'").fetchone()
                    if version!=(SCHEMA_VERSION,):raise ValueError('SCHEMA_VERSION_MISMATCH')
                self.connection.execute((Path(__file__).with_name('schema.sql')).read_text('utf8'))
                self.connection.execute('INSERT INTO warehouse_meta VALUES (?,?) ON CONFLICT DO NOTHING', ['schema_version',SCHEMA_VERSION])
            row=self.connection.execute("SELECT value FROM warehouse_meta WHERE key='schema_version'").fetchone()
            if row!=(SCHEMA_VERSION,):raise ValueError('SCHEMA_VERSION_MISMATCH')
        except BaseException:
            self.connection.close();raise

    @property
    def db(self):return self.connection

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        if self.connection is not None:self.connection.close();self.connection=None
    @contextmanager
    def transaction(self):
        if self.read_only:raise ValueError('READ_ONLY_WAREHOUSE')
        self.connection.execute('BEGIN TRANSACTION')
        try:
            yield
            self.connection.execute('COMMIT')
        except BaseException:
            self.connection.execute('ROLLBACK');raise

    def add_source(self,broker,server,symbol,instrument):
        sid=source_identity(broker,server,symbol)
        if instrument.get('broker_symbol')!=symbol:raise ValueError('INSTRUMENT_SYMBOL_MISMATCH')
        with self.transaction():
            old=self.connection.execute('SELECT instrument_json FROM sources WHERE source_id=?',[sid]).fetchone()
            if old and json.loads(old[0])!=instrument:
                self.connection.execute("UPDATE feature_sets SET status='STALE' WHERE source_id=?",[sid])
                self.connection.execute("UPDATE tick_archives SET status='STALE' WHERE source_id=?",[sid])
                self.connection.execute('DELETE FROM calculation_checkpoints WHERE source_id=?',[sid])
            self.connection.execute('INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET instrument_json=excluded.instrument_json',
                                    [sid,broker,server,symbol,dumps(instrument)])
        return sid

    def sources(self):
        return [dict(source_id=r[0],broker=r[1],server=r[2],symbol=r[3],instrument=json.loads(r[4]))
                for r in self.connection.execute('SELECT * FROM sources ORDER BY broker,server,symbol').fetchall()]

    def read_m1(self,source_id,start,end):
        columns=','.join('timestamp' if n=='time' else n for n in M1_DTYPE.names)
        data=self.connection.execute(f'SELECT {columns} FROM raw_m1 WHERE source_id=? AND timestamp>=? AND timestamp<? ORDER BY timestamp',
                                     [source_id,int(start),int(end)]).fetchnumpy()
        out=np.empty(len(data['timestamp']),dtype=M1_DTYPE)
        for name in M1_DTYPE.names:out[name]=data['timestamp' if name=='time' else name]
        return out

    def read_timeframe(self,source_id,timeframe,start,end,calc_hash):
        dtype=np.dtype([('time','<i8'),('open','<f8'),('high','<f8'),('low','<f8'),('close','<f8'),('tick_volume','<u8'),('complete','?')])
        data=self.connection.execute('SELECT timestamp,open,high,low,close,tick_volume,complete FROM timeframe_bars WHERE source_id=? AND timeframe=? AND timestamp>=? AND timestamp<? AND calc_hash=? ORDER BY timestamp',
            [source_id,timeframe,int(start),int(end),calc_hash]).fetchnumpy()
        out=np.empty(len(data['timestamp']),dtype=dtype)
        for name in dtype.names:out[name]=data['timestamp' if name=='time' else name]
        return out

    def upsert_m1(self,source_id,rows,*,completed_before):
        rows=normalize_m1(rows);report=inspect_m1(rows,completed_before=completed_before)
        if report['status']!='PASS':raise ValueError('INVALID_M1:'+dumps(report))
        if not len(rows):return report
        if not self.connection.execute('SELECT 1 FROM sources WHERE source_id=?',[source_id]).fetchone():raise ValueError('UNKNOWN_SOURCE')
        previous_max=self.connection.execute('SELECT max(timestamp) FROM raw_m1 WHERE source_id=?',[source_id]).fetchone()[0]
        before=self.read_m1(source_id,int(rows['time'][0]),int(rows['time'][-1])+60)
        old={int(row['time']):row.tobytes() for row in before}
        changed=[int(row['time']) for row in rows if old.get(int(row['time']))!=row.tobytes()]
        import pandas as pd
        frame=pd.DataFrame.from_records(rows).rename(columns={'time':'timestamp'})
        frame.insert(0,'source_id',source_id)
        self.connection.register('_incoming_m1',frame)
        try:
            with self.transaction():
                self.connection.execute('INSERT OR REPLACE INTO raw_m1 SELECT * FROM _incoming_m1')
                if changed:
                    first=min(changed)
                    self.connection.execute("UPDATE feature_sets SET status='STALE' WHERE source_id=? AND end_ts>?",[source_id,first])
                    # A correction inside the latest M3/M5 bar can be later than
                    # its open timestamp. Invalidate the entire recursive checkpoint.
                    if previous_max is not None and first<=int(previous_max):
                        self.connection.execute('DELETE FROM calculation_checkpoints WHERE source_id=?',[source_id])
                    tfs=self.connection.execute('SELECT DISTINCT timeframe FROM timeframe_bars WHERE source_id=?',[source_id]).fetchall()
                    for (tf,) in tfs:
                        width=int(tf[1:])*60
                        self.connection.execute('DELETE FROM timeframe_bars WHERE source_id=? AND timeframe=? AND timestamp>=?', [source_id,tf,first//width*width])
                # Verify BEFORE COMMIT: a failed exact round trip rolls back this chunk.
                actual=self.read_m1(source_id,int(rows['time'][0]),int(rows['time'][-1])+60)
                indices=np.isin(actual['time'],rows['time'])
                exact=compare_m1(rows,actual[indices])
                if exact['status']!='PASS':raise ValueError('WRITE_ROUNDTRIP_FAILED:'+dumps(exact))
        finally:self.connection.unregister('_incoming_m1')
        return dict(report,changed_rows=len(changed),first_changed=min(changed) if changed else None)

    def record_validation(self,source_id,start,end,expected,actual,*,error=None):
        exact=compare_m1(expected,actual);coverage=range_coverage(expected,start,end)
        if error:exact.update(status='FAIL',error=str(error))
        report={'exact':exact,'coverage':coverage}
        now=datetime.now(timezone.utc).isoformat()
        self.connection.execute('INSERT INTO raw_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            [uuid.uuid4().hex,source_id,int(start),int(end),coverage['available'][0],coverage['available'][1],len(expected),
             exact['status'],coverage['status'],sha(normalize_m1(expected).tobytes()),dumps(report),now])
        if exact['status']!='PASS':
            self.connection.execute("UPDATE feature_sets SET status='STALE' WHERE source_id=? AND start_ts<? AND end_ts>?",[source_id,end,start])
        return report

    def put_timeframe(self,source_id,timeframe,rows,calc_hash,start,end):
        import pandas as pd
        frame=pd.DataFrame.from_records(rows).rename(columns={'time':'timestamp'})
        frame.insert(0,'timeframe',timeframe);frame.insert(0,'source_id',source_id);frame['calc_hash']=calc_hash
        self.connection.register('_incoming_tf',frame)
        try:
            with self.transaction():
                self.connection.execute('DELETE FROM timeframe_bars WHERE source_id=? AND timeframe=? AND timestamp>=? AND timestamp<?', [source_id,timeframe,start,end])
                self.connection.execute('INSERT INTO timeframe_bars SELECT * FROM _incoming_tf')
                checked=self.connection.execute('SELECT timestamp,open,high,low,close,tick_volume,complete FROM timeframe_bars WHERE source_id=? AND timeframe=? AND timestamp>=? AND timestamp<? ORDER BY timestamp',[source_id,timeframe,start,end]).fetchnumpy()
                observed=np.empty(len(checked['timestamp']),dtype=rows.dtype)
                for n in observed.dtype.names:observed[n]=checked['timestamp' if n=='time' else n]
                if observed.dtype!=rows.dtype or observed.tobytes()!=rows.tobytes():raise ValueError('TIMEFRAME_EXACT_ROUNDTRIP_FAIL')
        finally:self.connection.unregister('_incoming_tf')

    def put_feature(self,spec,values,*,status='PASS',metadata=None,checkpoint=None,append_from=None):
        required={'source_id','symbol','timeframe','feature','parameters','calc_hash','environment','source_hash',
                  'evaluation_policy','start_ts','end_ts','warmup_start'}
        if set(spec)!=required:raise ValueError('FEATURE_IDENTITY_FIELDS')
        values=list(values)
        if any(a[0]>=b[0] for a,b in zip(values,values[1:])):raise ValueError('FEATURE_ORDER')
        if any(not spec['start_ts']<=t<spec['end_ts'] for t,_ in values):raise ValueError('FEATURE_RANGE')
        key=sha(dumps({k:v for k,v in spec.items() if k not in ('source_hash','end_ts')}).encode()); packed=[]; total=hashlib.sha256()
        for timestamp,value in values:
            payload=gzip.compress(dumps(value).encode(),mtime=0);checksum=sha(payload)
            packed.append((key,int(timestamp),payload,checksum));total.update(str(timestamp).encode()+bytes.fromhex(checksum))
        with self.transaction():
            if append_from is not None:
                prior=self.connection.execute('SELECT end_ts,status FROM feature_sets WHERE feature_key=?',[key]).fetchone()
                if not prior or prior[0]!=append_from or prior[1] not in ('PASS','PARTIAL'):
                    raise ValueError('INCREMENTAL_FEATURE_PREFIX_MISMATCH')
                self.connection.execute('DELETE FROM feature_values WHERE feature_key=? AND timestamp>=?',[key,append_from])
                total=hashlib.sha256()
                for t,checksum in self.connection.execute('SELECT timestamp,payload_hash FROM feature_values WHERE feature_key=? ORDER BY timestamp',[key]).fetchall():
                    total.update(str(t).encode()+bytes.fromhex(checksum))
                for _,t,payload,checksum in packed:total.update(str(t).encode()+bytes.fromhex(checksum))
            else:self.connection.execute('DELETE FROM feature_values WHERE feature_key=?',[key])
            if packed:self.connection.executemany('INSERT INTO feature_values VALUES (?,?,?,?)',packed)
            for _,t,payload,checksum in packed:
                check=self.connection.execute('SELECT payload,payload_hash FROM feature_values WHERE feature_key=? AND timestamp=?',[key,t]).fetchone()
                if not check or bytes(check[0])!=payload or check[1]!=checksum:raise ValueError('FEATURE_WRITE_EXACT_ROUNDTRIP_FAIL')
            self.connection.execute('INSERT OR REPLACE INTO feature_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                [key,spec['source_id'],spec['symbol'],spec['timeframe'],spec['feature'],dumps(spec['parameters']),spec['calc_hash'],
                 dumps(spec['environment']),spec['source_hash'],spec['evaluation_policy'],spec['start_ts'],spec['end_ts'],spec['warmup_start'],
                 status,total.hexdigest(),dumps(metadata or {})])
            if checkpoint:
                payload=gzip.compress(dumps(checkpoint['state']).encode(),mtime=0)
                self.connection.execute('INSERT OR REPLACE INTO calculation_checkpoints VALUES (?,?,?,?,?,?,?,?)',
                    [checkpoint['key'],spec['source_id'],spec['feature'],checkpoint['last_timestamp'],spec['calc_hash'],
                     checkpoint['source_prefix_hash'],payload,sha(payload)])
        return key

    def checkpoint(self,key,calc_hash):
        row=self.connection.execute('SELECT calc_hash,source_prefix_hash,payload,payload_hash,last_timestamp FROM calculation_checkpoints WHERE checkpoint_key=?',[key]).fetchone()
        if row is None:return None
        h,prefix,payload,checksum,timestamp=row
        if h!=calc_hash or sha(payload)!=checksum:return None
        return {'source_prefix_hash':prefix,'last_timestamp':timestamp,'state':json.loads(gzip.decompress(payload))}

    def status(self):
        result=[]
        for source in self.sources():
            sid=source['source_id']
            bounds=self.connection.execute('SELECT min(timestamp),max(timestamp),count(*) FROM raw_m1 WHERE source_id=?',[sid]).fetchone()
            proof=self.connection.execute('SELECT validation,coverage FROM raw_validations WHERE source_id=? ORDER BY checked_at DESC LIMIT 1',[sid]).fetchone()
            features=self.connection.execute('SELECT timeframe,feature,status,calc_hash FROM feature_sets WHERE source_id=? ORDER BY end_ts DESC',[sid]).fetchall()
            from calculations.identity import calculation_hash
            seen={}
            for tf,name,status,hash_ in features:
                seen.setdefault((tf,name),status if hash_==calculation_hash(name) else 'STALE')
            result.append(dict(source,first=bounds[0],last=bounds[1],rows=bounds[2],raw='NOT BUILT' if not proof else
                               ('FAIL' if proof[0]!='PASS' else proof[1]),features=seen))
        return result

    def checkpoint_file(self):
        if self.read_only:raise ValueError('READ_ONLY_CHECKPOINT')
        self.connection.execute('CHECKPOINT')
