"""Immutable real tick acquisition; warm-up uses earlier actual ticks only."""
from pathlib import Path
import hashlib
import os
import uuid
import numpy as np
from .cache import RawChunkWriter,verify_archive
from ..canonical import identity,read_json,write_json,file_hash,encode
from ..contracts import GenericError
from ..paths import output_path


class WarmupPlanner:
    @staticmethod
    def start(requirements,start_ns):
        return max(0,start_ns-requirements.raw_tick_warmup_ns)


class GenericHistoryService:
    def __init__(self,provider,cache_root,emit=lambda *a,**k:None,cancel=lambda:None,commit=None):
        self.provider,self.root,self.emit,self.cancel=provider,Path(cache_root),emit,cancel
        self.commit=commit or (lambda callback:callback())

    def prepare(self,symbol,start_ns,end_ns,*,chunk_ms=3600000,max_chunk_rows=2000000):
        if type(start_ns)!=int or type(end_ns)!=int or not 0<=start_ns<end_ns or start_ns%1000000 or end_ns%1000000:
            raise GenericError('E_PLUGIN_SCHEMA','UTC [S,E) at millisecond precision required')
        instrument=self.provider.instrument(symbol)
        request={'instrument':instrument,'start_ns':start_ns,'end_ns':end_ns,'input_kind':self.provider.input_kind}
        key=identity(request);destination=output_path(self.root/key,'generic_cache',fresh=False)
        if destination.exists():
            manifest=verify_archive(destination);self.emit('CACHE_HIT',{'archive':str(destination)})
            return {'archive':str(destination),'manifest':manifest,'cache_hit':True}
        partial=output_path(self.root/(key+'.partial'),'generic_cache',fresh=False)
        partial.mkdir(parents=True,exist_ok=True)
        # Chunk partition is persisted for resume; it is not the stream identity.
        progress=partial/'progress.jsonl';records=[]
        if progress.exists():
            import json
            for line in progress.read_text(encoding='utf-8').splitlines():
                row=json.loads(line)
                if file_hash(partial/row['file'])!=row['file_sha256']: raise GenericError('E_CHUNK_CORRUPT','resume')
                records.append(row)
        stream=identity(('BROKER_STREAM',instrument['server_fingerprint'],symbol))
        writer=RawChunkWriter(partial,stream)
        cursor=records[-1]['coverage_end_ns']//1000000 if records else start_ns//1000000
        if records and records[0]['coverage_start_ns']!=start_ns: raise GenericError('E_HISTORY_REVISION','resume range')
        finish=end_ns//1000000;gaps=[]
        while cursor<finish:
            self.cancel();end=min(finish,cursor+chunk_ms)
            while True:
                rows=self.provider.ticks(symbol,cursor,end)
                # No truncation/downsampling. Split the whole owned interval.
                if len(rows)<max_chunk_rows: break
                if end-cursor<=1: raise GenericError('E_TICK_GROUP_INCOMPLETE',cursor)
                end=cursor+max(1,(end-cursor)//2)
            desc=writer.write(len(records),rows,cursor*1000000,end*1000000)
            with progress.open('ab') as log:
                log.write(encode(desc));log.flush();os.fsync(log.fileno())
            records.append(desc);cursor=end
            self.emit('PROGRESS',{'phase':'FETCH','done':cursor-start_ns//1000000,'total':finish-start_ns//1000000})
        raw=hashlib.sha256();count=0
        for desc in records:
            rows=np.load(partial/desc['file'],allow_pickle=False,mmap_mode='r')
            if hashlib.sha256(rows.tobytes()).hexdigest()!=desc['raw_sha256']: raise GenericError('E_CHUNK_CORRUPT','resume raw bytes')
            raw.update(rows.tobytes());count+=len(rows)
            if not len(rows): gaps.append({'start_ns':desc['coverage_start_ns'],'end_ns':desc['coverage_end_ns'],'status':'EMPTY_UNVERIFIED'})
            # Windows cannot rename a directory while its .npy is still mapped.
            rows._mmap.close()
        if count==0: raise GenericError('E_HISTORY_PARTIAL','no ticks returned')
        manifest={'kind':'GENERIC_RAW_ARCHIVE_V1','status':'READY','instrument':instrument,
            'stream_namespace':stream,'coverage_start_ns':start_ns,'coverage_end_ns':end_ns,
            'count':count,'raw_sha256':raw.hexdigest(),'chunks':records,'gaps':gaps,
            'coverage':{'completeness':'UNVERIFIED','broker_history_completeness':'UNVERIFIED',
                        'endpoint_contract':'BROAD_SECOND_QUERY_OWNED_HALF_OPEN_MS_NO_DEDUP',
                        'native_endpoint_probe_status':'UNVERIFIED'},
            'input_kind':self.provider.input_kind,'source_build':list(self.provider.build)}
        manifest['archive_identity']=identity(manifest)
        self.cancel()
        def finalize():
            final_manifest=partial/'manifest.json'
            if final_manifest.exists():
                if read_json(final_manifest)!=manifest: raise GenericError('E_HISTORY_REVISION','partial final manifest')
            else: write_json(final_manifest,manifest)
            verify_archive(partial)
            os.rename(partial,destination)
        self.commit(finalize)
        self.emit('PHASE',{'phase':'CACHE_READY','message_code':'BROKER_COMPLETENESS_UNVERIFIED','gaps':gaps})
        return {'archive':str(destination),'manifest':manifest,'cache_hit':False}
