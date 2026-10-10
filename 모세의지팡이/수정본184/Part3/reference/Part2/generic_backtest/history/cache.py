"""Lossless 60-byte chunks. Reader order/IDs do not depend on partition."""
from pit.models import RAW, TickRecord
from pit.contracts import PitError
import hashlib
import os
from pathlib import Path
import numpy as np
from pit.archive.reader import FrozenTickReader,TICK_DTYPE
from ..canonical import identity,file_hash,read_json,write_json,encode
from ..contracts import GenericError
from ..paths import plain_path,internal_path
from .raw_verification import verify_chunk_records


class RawChunkWriter:
    def __init__(self,root,stream):
        self.root=Path(root);self.stream=stream
        (self.root/'chunks').mkdir(parents=True,exist_ok=True)

    def write(self,number,rows,start_ns,end_ns):
        if rows.ndim!=1 or rows.dtype!=TICK_DTYPE: raise GenericError('E_CHUNK_CORRUPT','exact native dtype')
        if len(rows) and (np.any(rows['time_msc'][1:]<rows['time_msc'][:-1])
                or np.any(rows['time_msc']*1000000<start_ns) or np.any(rows['time_msc']*1000000>=end_ns)):
            raise GenericError('E_CHUNK_CORRUPT','order/range')
        path=self.root/'chunks'/f'{number:08d}.npy'
        if path.exists():
            # Preserve an orphan from a interrupted pre-receipt commit; never
            # overwrite it or treat it as a committed/reusable chunk.
            import uuid
            path=self.root/'chunks'/f'{number:08d}_{uuid.uuid4().hex}.npy'
        with path.open('xb') as stream:
            np.save(stream,rows,allow_pickle=False);stream.flush();os.fsync(stream.fileno())
        desc={'file':'chunks/'+path.name,'count':len(rows),'file_sha256':file_hash(path),
            'raw_sha256':hashlib.sha256(rows.tobytes()).hexdigest(),
            'coverage_start_ns':start_ns,'coverage_end_ns':end_ns,'stream_namespace':self.stream}
        return desc


def verify_archive(root):
    root=internal_path(root);m=read_json(root/'manifest.json')
    if m.get('status')!='READY' or m.get('kind')!='GENERIC_RAW_ARCHIVE_V1': raise GenericError('E_HISTORY_PARTIAL','archive not ready')
    expected=identity({k:v for k,v in m.items() if k!='archive_identity'})
    if m.get('archive_identity')!=expected: raise GenericError('E_CHUNK_CORRUPT','manifest identity')
    previous=m['coverage_start_ns'];last_ms=-1;raw_hash=hashlib.sha256();count=0
    for d in m['chunks']:
        if d['coverage_start_ns']!=previous or d['coverage_end_ns']<=previous: raise GenericError('E_HISTORY_PARTIAL','chunk coverage')
        previous=d['coverage_end_ns'];path=plain_path(root/d['file'])
        if not path.is_relative_to(root/'chunks'): raise GenericError('E_CAPABILITY_DENIED','chunk path')
        reader=FrozenTickReader.open(path,d,chunk_size=65536)
        chunk_count,last_ms=verify_chunk_records(reader,raw_hash,last_ms)
        count+=chunk_count
    if previous!=m['coverage_end_ns'] or raw_hash.hexdigest()!=m['raw_sha256'] or count!=m['count']:
        raise GenericError('E_CHUNK_CORRUPT','archive hash/count')
    return m


class GenericArchiveReader:
    def __init__(self,root):
        self.root=internal_path(root);self.manifest=verify_archive(self.root)
    def __iter__(self):
        ordinal=0
        stream=self.manifest['stream_namespace']
        for d in self.manifest['chunks']:
            reader=FrozenTickReader.open(self.root/d['file'],d)
            # The existing verified NumPy mmap is already an exact 60-byte array.
            # Decode only the current record: no numpy row scalar/.tobytes(), no
            # chunk-local TickRecord followed by dataclasses.replace(). Float
            # fields stay uint64 bits (including NaN payloads and signed zero).
            # The buffer/iterator never reaches strategy code; only one immutable
            # TickRecord is published per observation, in original duplicate order.
            last_ms=-1
            start,end=d['coverage_start_ns'],d['coverage_end_ns']
            for local_ordinal,fields in enumerate(RAW.iter_unpack(memoryview(reader._rows)),1):
                ms=fields[5]
                if ms<last_ms:raise PitError('E_RAW_ORDER_REGRESSION',str(local_ordinal))
                if not start<=ms*1_000_000<end:
                    raise PitError('E_RAW_HASH','record outside declared coverage')
                last_ms=ms
                ordinal+=1
                yield TickRecord(stream,ordinal,*fields)
