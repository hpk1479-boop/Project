"""Import/read MT5-native STAFF snapshots produced only in Strategy Tester data-build runs."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import struct
import numpy as np

MAGIC=0x4D4E5331
VERSION=2
REQUEST_MAGIC='MOSES_NATIVE_BUILD_V1'
VALUE_COLUMNS=(
    'open','high','low','close','hma_6','hma_17',
    'price_value','price_lower','price_upper','price_basis','price_regime_upper','price_regime_lower','price_lower_out','price_upper_out','price_regime_slope',
    'rsi_value','rsi_lower','rsi_upper','rsi_basis','rsi_regime_upper','rsi_regime_lower','rsi_lower_out','rsi_upper_out','rsi_regime_slope',
    'sto_value','sto_lower','sto_upper','sto_basis','sto_regime_upper','sto_regime_lower','sto_lower_out','sto_upper_out','sto_regime_slope',
    'di_value','di_lower','di_upper','di_basis','di_regime_upper','di_regime_lower','di_lower_out','di_upper_out','di_regime_slope',
)
HEADER=struct.Struct('<IIII')
LEGACY_VALUE_COLUMNS=VALUE_COLUMNS
LEGACY_RECORD_DTYPE=np.dtype([('observed_time','<i8'),('bar_time','<i8')]+[(name,'<f8') for name in VALUE_COLUMNS])
VALUE_COLUMNS=VALUE_COLUMNS+('wonbi_upper','wonbi_lower','wonbi_sigma')
RECORD_DTYPE=np.dtype([('observed_time','<i8'),('bar_time','<i8')]+[(name,'<f8') for name in VALUE_COLUMNS])
RECORD_BYTES=RECORD_DTYPE.itemsize

NATIVE_SCHEMA='''
CREATE TABLE IF NOT EXISTS native_builds (
 build_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
 requested_start BIGINT NOT NULL, requested_end BIGINT NOT NULL,
 recorded_start BIGINT, recorded_end BIGINT, timeframe_count INTEGER NOT NULL,
 row_count BIGINT NOT NULL, format_version INTEGER NOT NULL,
 manifest_json VARCHAR NOT NULL, payload_sha256 VARCHAR NOT NULL,
 status VARCHAR NOT NULL, imported_at VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS native_snapshots (
 source_id VARCHAR NOT NULL, timeframe VARCHAR NOT NULL,
 observed_time BIGINT NOT NULL, bar_time BIGINT NOT NULL,
 open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL,
 hma_6 DOUBLE NOT NULL, hma_17 DOUBLE NOT NULL,
 price_value DOUBLE NOT NULL, price_lower DOUBLE NOT NULL, price_upper DOUBLE NOT NULL, price_basis DOUBLE NOT NULL,
 price_regime_upper DOUBLE NOT NULL, price_regime_lower DOUBLE NOT NULL, price_lower_out DOUBLE NOT NULL, price_upper_out DOUBLE NOT NULL, price_regime_slope DOUBLE NOT NULL,
 rsi_value DOUBLE NOT NULL, rsi_lower DOUBLE NOT NULL, rsi_upper DOUBLE NOT NULL, rsi_basis DOUBLE NOT NULL,
 rsi_regime_upper DOUBLE NOT NULL, rsi_regime_lower DOUBLE NOT NULL, rsi_lower_out DOUBLE NOT NULL, rsi_upper_out DOUBLE NOT NULL, rsi_regime_slope DOUBLE NOT NULL,
 sto_value DOUBLE NOT NULL, sto_lower DOUBLE NOT NULL, sto_upper DOUBLE NOT NULL, sto_basis DOUBLE NOT NULL,
 sto_regime_upper DOUBLE NOT NULL, sto_regime_lower DOUBLE NOT NULL, sto_lower_out DOUBLE NOT NULL, sto_upper_out DOUBLE NOT NULL, sto_regime_slope DOUBLE NOT NULL,
 di_value DOUBLE NOT NULL, di_lower DOUBLE NOT NULL, di_upper DOUBLE NOT NULL, di_basis DOUBLE NOT NULL,
 di_regime_upper DOUBLE NOT NULL, di_regime_lower DOUBLE NOT NULL, di_lower_out DOUBLE NOT NULL, di_upper_out DOUBLE NOT NULL, di_regime_slope DOUBLE NOT NULL,
 build_id VARCHAR NOT NULL,
 PRIMARY KEY(source_id,timeframe,observed_time));
'''


def ensure_native_schema(store):
    if getattr(store,'read_only',False):
        raise ValueError('READ_ONLY_WAREHOUSE')
    store.db.execute(NATIVE_SCHEMA)
    # Separate ms-keyed native table keeps old second-keyed archives readable.
    columns=', '.join(name+' DOUBLE NOT NULL' for name in VALUE_COLUMNS)
    store.db.execute('CREATE TABLE IF NOT EXISTS native_snapshots_v2 ('
        'source_id VARCHAR NOT NULL,timeframe VARCHAR NOT NULL,observed_time BIGINT NOT NULL,'
        'bar_time BIGINT NOT NULL,'+columns+',build_id VARCHAR NOT NULL,'
        'PRIMARY KEY(source_id,timeframe,observed_time))')


def _sha_file(path: Path, block=1024*1024):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b=f.read(block)
            if not b:break
            h.update(b)
    return h.hexdigest()


def parse_export(root):
    root=Path(root).expanduser().resolve()
    complete=root/'complete.txt'; manifest_path=root/'manifest.tsv'
    if not complete.is_file() or not manifest_path.is_file():
        raise ValueError('NATIVE_EXPORT_INCOMPLETE')
    c=[x.strip() for x in complete.read_text('ascii',errors='strict').splitlines() if x.strip()]
    if len(c)<2 or c[0]!=REQUEST_MAGIC:raise ValueError('NATIVE_COMPLETE_MARKER_INVALID')
    lines=manifest_path.read_text('ascii',errors='strict').splitlines()
    if not lines or lines[0].strip()!=REQUEST_MAGIC:raise ValueError('NATIVE_MANIFEST_MAGIC')
    meta={};feeds=[]
    for line in lines[1:]:
        parts=line.rstrip('\r\n').split('\t')
        if not parts or not parts[0]:continue
        if parts[0]=='feed':
            if len(parts)!=5:raise ValueError('NATIVE_MANIFEST_FEED_FORMAT')
            feeds.append({'index':int(parts[1]),'timeframe':parts[2].lower(),'file':parts[3],'count':int(parts[4])})
        elif parts[0]=='pipe_feed':
            # STAFF_PIPE_V1 (LIVE와 같은 45열 STAFF payload)는 part1_host.capture가 읽습니다.
            if len(parts)!=5:raise ValueError('NATIVE_MANIFEST_FEED_FORMAT')
        elif len(parts)==2:meta[parts[0]]=parts[1]
        else:raise ValueError('NATIVE_MANIFEST_FORMAT')
    if meta.get('session')!=c[1]:raise ValueError('NATIVE_SESSION_MISMATCH')
    version=int(meta.get('format_version','-1'))
    if version not in (1,2):raise ValueError('NATIVE_FORMAT_VERSION')
    dtype=LEGACY_RECORD_DTYPE if version==1 else RECORD_DTYPE
    expected_columns=len(LEGACY_VALUE_COLUMNS) if version==1 else len(VALUE_COLUMNS)
    if int(meta.get('value_columns','-1'))!=expected_columns:raise ValueError('NATIVE_COLUMN_COUNT')
    if meta.get('sampling_policy') not in ('MINUTE_OR_STATE_CHANGE_V1','SECOND_SNAPSHOT_V1','TIMER_SNAPSHOT_V2'):raise ValueError('NATIVE_SAMPLING_POLICY')
    if version==2 and (meta.get('observation_unit')!='milliseconds' or int(meta.get('observation_interval_ms','0'))<=0):raise ValueError('NATIVE_TIME_UNIT')
    if not feeds:raise ValueError('NATIVE_NO_FEEDS')
    if [x['index'] for x in feeds]!=list(range(len(feeds))):raise ValueError('NATIVE_FEED_INDEX')
    hashes=[]
    for feed in feeds:
        path=(root/feed['file']).resolve()
        if path.parent!=root or not path.is_file() or path.is_symlink():raise ValueError('NATIVE_FEED_PATH')
        with path.open('rb') as f:raw=f.read(HEADER.size)
        if len(raw)!=HEADER.size:raise ValueError('NATIVE_HEADER_SHORT')
        magic,file_version,columns,record_bytes=HEADER.unpack(raw)
        if (magic,file_version,columns,record_bytes)!=(MAGIC,version,expected_columns,dtype.itemsize):
            raise ValueError('NATIVE_HEADER_MISMATCH')
        expected=HEADER.size+feed['count']*dtype.itemsize
        if path.stat().st_size!=expected:raise ValueError('NATIVE_FILE_SIZE_MISMATCH')
        feed['path']=path;feed['sha256']=_sha_file(path);hashes.append(feed['sha256'])
        feed['format_version']=version
    payload_sha=hashlib.sha256((''.join(hashes)).encode()).hexdigest()
    return {'root':root,'session':meta['session'],'symbol':meta['symbol'],'feeds':feeds,
            'format_version':version,'sampling_policy':meta['sampling_policy'],'payload_sha256':payload_sha,
            'observation_unit':'milliseconds' if version==2 else 'seconds',
            'observation_interval_ms':int(meta.get('observation_interval_ms','1000'))}


def iter_feed_blocks(feed, *, start_s=None, end_s=None, block_rows=250_000):
    count=feed['count'];path=feed['path']
    version=feed.get('format_version',1)
    dtype=RECORD_DTYPE if version==2 else LEGACY_RECORD_DTYPE
    scale=1000 if version==2 else 1
    rows=np.memmap(path,dtype=dtype,mode='r',offset=HEADER.size,shape=(count,))
    previous=None
    for offset in range(0,count,block_rows):
        block=np.asarray(rows[offset:min(count,offset+block_rows)])
        if len(block):
            ts=block['observed_time']
            if previous is not None and int(ts[0])<=previous:raise ValueError('NATIVE_TIME_ORDER')
            if np.any(ts[1:]<=ts[:-1]):raise ValueError('NATIVE_TIME_ORDER')
            previous=int(ts[-1])
            keep=np.ones(len(block),dtype=bool)
            if start_s is not None:keep &= ts>=int(start_s)*scale
            if end_s is not None:keep &= ts<int(end_s)*scale
            block=block[keep]
        if len(block):yield block


def import_native_export(store, root, source_id, *, requested_start, requested_end):
    import pandas as pd
    export=parse_export(root)
    source=store.db.execute('SELECT symbol FROM sources WHERE source_id=?',[source_id]).fetchone()
    if not source:raise ValueError('NATIVE_SOURCE_NOT_FOUND')
    if source[0]!=export['symbol']:raise ValueError('NATIVE_SOURCE_SYMBOL_MISMATCH')
    ensure_native_schema(store)
    total=0;first=None;last=None;timeframes=[]
    start_s=int(requested_start);end_s=int(requested_end)
    if not 0<=start_s<end_s:raise ValueError('NATIVE_RANGE')
    build_id=hashlib.sha256(json.dumps({'session':export['session'],'source_id':source_id,
        'start':start_s,'end':end_s,'payload':export['payload_sha256']},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    dtype=RECORD_DTYPE if export['format_version']==2 else LEGACY_RECORD_DTYPE
    table='native_snapshots_v2' if export['format_version']==2 else 'native_snapshots'
    scale=1000 if export['format_version']==2 else 1
    columns=['source_id','timeframe',*dtype.names,'build_id']
    with store.transaction():
        for feed in export['feeds']:
            timeframes.append(feed['timeframe'])
            for block in iter_feed_blocks(feed,start_s=start_s,end_s=end_s):
                frame=pd.DataFrame.from_records(block)
                frame.insert(0,'timeframe',feed['timeframe']);frame.insert(0,'source_id',source_id)
                frame['build_id']=build_id
                store.db.register('_native_incoming',frame)
                try:
                    # Existing rows for the same source/tf/second must be byte-for-byte numerically identical.
                    mismatch=' OR '.join(f'x.{c} IS DISTINCT FROM n.{c}' for c in dtype.names)
                    conflict=store.db.execute(f'''SELECT count(*) FROM {table} x JOIN _native_incoming n
                      ON x.source_id=n.source_id AND x.timeframe=n.timeframe AND x.observed_time=n.observed_time
                      WHERE {mismatch}''').fetchone()[0]
                    if conflict:raise ValueError('NATIVE_SNAPSHOT_CONFLICT')
                    store.db.execute(f'''INSERT INTO {table} ({','.join(columns)})
                      SELECT {','.join(columns)} FROM _native_incoming
                      ON CONFLICT(source_id,timeframe,observed_time) DO NOTHING''')
                finally:store.db.unregister('_native_incoming')
                total+=len(block);a=int(block['observed_time'][0])//scale;b=int(block['observed_time'][-1])//scale
                first=a if first is None else min(first,a);last=b if last is None else max(last,b)
        if total<=0:raise ValueError('NATIVE_EXPORT_RANGE_EMPTY')
        manifest={'session':export['session'],'symbol':export['symbol'],'timeframes':timeframes,
                  'payload_sha256':export['payload_sha256'],'format_version':export['format_version'],
                  'observation_unit':export['observation_unit'],
                  'sampling_policy':export['sampling_policy']}
        store.db.execute('INSERT OR REPLACE INTO native_builds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
            build_id,source_id,export['symbol'],start_s,end_s,first,last,len(timeframes),total,export['format_version'],
            json.dumps(manifest,sort_keys=True,separators=(',',':')),export['payload_sha256'],'PASS',
            datetime.now(timezone.utc).isoformat()])
    return {'build_id':build_id,'status':'PASS','rows':total,'timeframes':timeframes,
            'recorded_start':first,'recorded_end':last,'payload_sha256':export['payload_sha256']}


def native_coverage(store,source_id):
    result=[]
    tables={r[0] for r in store.db.execute('SHOW TABLES').fetchall()}
    for version,table,scale in [(1,'native_snapshots',1),(2,'native_snapshots_v2',1000)]:
        if table not in tables:continue
        rows=store.db.execute(f'SELECT timeframe,min(observed_time),max(observed_time),count(*) FROM {table} WHERE source_id=? GROUP BY timeframe ORDER BY timeframe',[source_id]).fetchall()
        result.extend({'timeframe':r[0],'start':r[1]//scale,'end':r[2]//scale,'rows':r[3],
                       'format_version':version} for r in rows)
    return result
