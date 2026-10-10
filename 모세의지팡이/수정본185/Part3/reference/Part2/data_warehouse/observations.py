"""Whole-request ALLZONE base tapes. DB is the sole inter-application link.

Runtime records explicit bar snapshots and exact prior numerical state. The
Data Manager independently recalculates these inputs with its packaged LIVE
rules, compares the full result and only then publishes validation PASS.
Unknown call order never falls through into partially restored engine state.
"""
from __future__ import annotations
import gzip
import json
from pathlib import Path
import shutil
import tempfile
from .store import Store,dumps,sha
from .replay import ReplayMismatch
from calculations.identity import calculation_hash,environment_identity
from calculations.state_codec import digest

VERSION='LIVE_ALLZONE_BASE_REQUEST_V1'
DDL='''
CREATE TABLE IF NOT EXISTS observation_jobs (
 job_key VARCHAR PRIMARY KEY, descriptor_json VARCHAR NOT NULL, calc_hash VARCHAR NOT NULL,
 environment_json VARCHAR NOT NULL, status VARCHAR NOT NULL, row_count BIGINT NOT NULL,
 input_hash VARCHAR NOT NULL, output_hash VARCHAR NOT NULL, detail_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS observation_job_blocks (
 job_key VARCHAR NOT NULL, block_number INTEGER NOT NULL, row_count INTEGER NOT NULL,
 inputs BLOB NOT NULL, input_hash VARCHAR NOT NULL, outputs BLOB, output_hash VARCHAR,
 PRIMARY KEY(job_key,block_number));
'''


def pack(value):return gzip.compress(dumps(value).encode('utf8'),compresslevel=1,mtime=0)

def unpack(value):return json.loads(gzip.decompress(bytes(value)))

def job_identity(descriptor):return digest({'version':VERSION,'descriptor':descriptor})

def _chain(items):return digest(items)


class RequestRecorder:
    """Disk-bounded blocks outside generic_cache; publish only a complete run."""
    def __init__(self,descriptor,block_rows=32):
        self.descriptor=descriptor;self.key=job_identity(descriptor);self.block_rows=block_rows
        self.path=Path(tempfile.mkdtemp(prefix='moses-allzone-request-'));self.buffer=[];self.blocks=[];self.count=0;self.closed=False;self.buffer_bytes=0
    def append(self,key,request,expected):
        if self.closed:raise ValueError('OBSERVATION_RECORDER_CLOSED')
        if key['input_hash']!=digest(request):raise ValueError('OBSERVATION_INPUT_HASH')
        value={'key':key,'request':request,'expected':expected}
        size=len(dumps(value).encode('utf8'))
        if self.buffer and self.buffer_bytes+size>8*1024*1024:self.flush()
        self.buffer.append(value);self.count+=1;self.buffer_bytes+=size
        if len(self.buffer)>=self.block_rows:self.flush()
    def flush(self):
        if not self.buffer:return
        payload=pack(self.buffer);name=f'{len(self.blocks):08d}.json.gz';(self.path/name).write_bytes(payload)
        self.blocks.append({'file':name,'sha256':sha(payload),'rows':len(self.buffer)});self.buffer=[];self.buffer_bytes=0
    def finish(self):
        if self.closed:return self.path
        self.flush();self.closed=True
        manifest={'version':VERSION,'descriptor':self.descriptor,'key':self.key,'count':self.count,'blocks':self.blocks,
                  'calc_hash':calculation_hash('ALLZONE_BASE'),'environment':environment_identity()}
        manifest['checksum']=digest(manifest)
        (self.path/'manifest.json').write_text(dumps(manifest),encoding='utf8')
        return self.path
    def abort(self):
        self.closed=True;shutil.rmtree(self.path,ignore_errors=True)


def import_requests(store,path):
    """Atomic data handoff. PENDING is not accepted as an accelerated result."""
    path=Path(path);manifest=json.loads((path/'manifest.json').read_text('utf8'))
    if manifest.get('checksum')!=digest({k:v for k,v in manifest.items() if k!='checksum'}):raise ValueError('REQUEST_MANIFEST_HASH')
    if manifest['version']!=VERSION or manifest['key']!=job_identity(manifest['descriptor']):raise ValueError('REQUEST_IDENTITY')
    if manifest['calc_hash']!=calculation_hash('ALLZONE_BASE') or manifest['environment']!=environment_identity():raise ValueError('CALC_HASH_OR_ENVIRONMENT_MISMATCH')
    if manifest['count']!=sum(b['rows'] for b in manifest['blocks']):raise ValueError('REQUEST_COUNT')
    store.db.execute(DDL)
    with store.transaction():
        store.db.execute('DELETE FROM observation_job_blocks WHERE job_key=?',[manifest['key']])
        checks=[]
        for i,block in enumerate(manifest['blocks']):
            if block['file']!=f'{i:08d}.json.gz':raise ValueError('REQUEST_BLOCK_PATH')
            payload=(path/block['file']).read_bytes()
            if sha(payload)!=block['sha256'] or len(unpack(payload))!=block['rows']:raise ValueError('REQUEST_BLOCK_HASH')
            store.db.execute('INSERT INTO observation_job_blocks VALUES (?,?,?,?,?,?,?)',[manifest['key'],i,block['rows'],payload,sha(payload),None,None])
            back=store.db.execute('SELECT inputs FROM observation_job_blocks WHERE job_key=? AND block_number=?',[manifest['key'],i]).fetchone()
            if not back or bytes(back[0])!=payload:raise ValueError('REQUEST_WRITE_ROUNDTRIP')
            checks.append([i,block['rows'],sha(payload)])
        store.db.execute('INSERT OR REPLACE INTO observation_jobs VALUES (?,?,?,?,?,?,?,?,?)',
            [manifest['key'],dumps(manifest['descriptor']),manifest['calc_hash'],dumps(manifest['environment']),
             'PENDING',manifest['count'],_chain(checks),'',dumps({'validation':'AWAITING_DATA_MANAGER_RECALCULATION','version':VERSION})])
    return {'job_key':manifest['key'],'status':'PENDING','rows':manifest['count']}


def jobs(store):
    try:
        rows=store.db.execute('SELECT job_key,descriptor_json,calc_hash,environment_json,status,row_count,detail_json FROM observation_jobs ORDER BY job_key').fetchall()
    except Exception:return []
    result=[]
    for key,desc,h,env,status,n,detail in rows:
        stale=h!=calculation_hash('ALLZONE_BASE') or json.loads(env)!=environment_identity()
        result.append(dict(job_key=key,descriptor=json.loads(desc),status='STALE' if stale else status,rows=n,detail=json.loads(detail)))
    return result




class ObservationSnapshot:
    """Full request preflight, then strict stream replay; mismatch restarts run."""
    def __init__(self,path,descriptor):
        self.store=Store(path,read_only=True);self.closed=False;self.key=job_identity(descriptor)
        self.block_index=0;self.buffer=[];self.index=0;self.consumed=0
        try:
            self.store.db.execute('BEGIN TRANSACTION')
            row=self.store.db.execute('SELECT descriptor_json,calc_hash,environment_json,status,row_count,output_hash FROM observation_jobs WHERE job_key=?',[self.key]).fetchone()
            if not row:raise ValueError('ALLZONE_FULL_REQUEST_MISS')
            if json.loads(row[0])!=descriptor:raise ValueError('ALLZONE_DESCRIPTOR_MISMATCH')
            if row[1]!=calculation_hash('ALLZONE_BASE'):raise ValueError('CALC_HASH_MISMATCH')
            if json.loads(row[2])!=environment_identity():raise ValueError('CALCULATION_ENVIRONMENT_MISMATCH')
            if row[3]!='PASS':raise ValueError('VALIDATION_NOT_PASS:'+row[3])
            self.expected_count=row[4]
            self.blocks=self.store.db.execute('SELECT block_number,row_count,output_hash FROM observation_job_blocks WHERE job_key=? ORDER BY block_number',[self.key]).fetchall()
            if [b[0] for b in self.blocks]!=list(range(len(self.blocks))) or sum(b[1] for b in self.blocks)!=row[4] or _chain([list(b) for b in self.blocks])!=row[5]:raise ValueError('ALLZONE_BLOCK_MANIFEST_HASH')
            for block in self.blocks:self._load(block)
        except BaseException:self.close();raise
    def _load(self,block):
        row=self.store.db.execute('SELECT outputs,output_hash FROM observation_job_blocks WHERE job_key=? AND block_number=?',[self.key,block[0]]).fetchone()
        if not row or row[0] is None or sha(bytes(row[0]))!=row[1] or row[1]!=block[2]:raise ReplayMismatch('ALLZONE_BLOCK_HASH_MISMATCH')
        values=unpack(row[0])
        if len(values)!=block[1] or any(set(x)!={'key','result'} for x in values):raise ReplayMismatch('ALLZONE_BLOCK_LAYOUT')
        return values
    def read(self,key):
        try:return self._read(key)
        except ReplayMismatch:raise
        except Exception as exc:raise ReplayMismatch('ALLZONE_REPLAY_READ_FAILED: '+str(exc)) from exc
    def _read(self,key):
        if self.index>=len(self.buffer):
            if self.block_index>=len(self.blocks):raise ReplayMismatch('ALLZONE_UNEXPECTED_EOF')
            self.buffer=self._load(self.blocks[self.block_index]);self.block_index+=1;self.index=0
        value=self.buffer[self.index]
        if value['key']!=key:raise ReplayMismatch('ALLZONE_OBSERVATION_OR_PRESTATE_MISMATCH')
        self.index+=1;self.consumed+=1;return value['result']
    def finish(self):
        try:
            if self.consumed!=self.expected_count:raise ReplayMismatch('ALLZONE_REQUEST_INCOMPLETE')
        finally:self.close()
    def close(self):
        if not getattr(self,'closed',True):
            self.closed=True
            try:self.store.db.execute('ROLLBACK')
            except Exception:pass
            self.store.close()
    def __del__(self):self.close()
