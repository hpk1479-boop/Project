from pathlib import Path
import hashlib
import numpy as np
from ..contracts import PitError,file_hash
from ..models import TickRecord

TICK_DTYPE=np.dtype([('time','<i8'),('bid','<f8'),('ask','<f8'),('last','<f8'),
                    ('volume','<u8'),('time_msc','<i8'),('flags','<u4'),('volume_real','<f8')])

class FrozenTickReader:
    @classmethod
    def open(cls,path,descriptor,chunk_size=256): return cls(path,descriptor,chunk_size)
    def __init__(self,path,descriptor,chunk_size=256):
        self.path=Path(path);self.descriptor=dict(descriptor)
        if not self.path.is_file():raise PitError('E_REAL_INPUT_MISSING',str(self.path))
        if type(chunk_size)!=int or chunk_size<1:raise PitError('E_RESOURCE_LIMIT','chunk size')
        if file_hash(self.path)!=descriptor['file_sha256']:raise PitError('E_RAW_HASH','file')
        self._rows=np.load(self.path,allow_pickle=False,mmap_mode='r')
        if self._rows.ndim!=1 or self._rows.dtype!=TICK_DTYPE:raise PitError('E_RAW_HASH','exact dtype required')
        h=hashlib.sha256()
        for i in range(0,len(self._rows),chunk_size):h.update(self._rows[i:i+chunk_size].tobytes())
        if h.hexdigest()!=descriptor['raw_sha256'] or len(self._rows)!=descriptor['count']:
            raise PitError('E_RAW_HASH','canonical raw records')
        if not descriptor.get('stream_namespace'):raise PitError('E_ID_COLLISION','missing stream namespace')
        self._cursor=0;self._last_ms=-1;self.chunk_size=chunk_size
    def __iter__(self):return self
    def __next__(self):
        if self._cursor==len(self._rows):raise StopIteration
        tick=TickRecord.from_bytes(self.descriptor['stream_namespace'],self._cursor+1,self._rows[self._cursor].tobytes())
        if tick.time_msc<self._last_ms:raise PitError('E_RAW_ORDER_REGRESSION',str(tick.source_ordinal))
        if not self.descriptor['coverage_start_ns']<=tick.time_msc*1_000_000<self.descriptor['coverage_end_ns']:
            raise PitError('E_RAW_HASH','record outside declared coverage')
        self._cursor+=1;self._last_ms=tick.time_msc
        return tick
    next=__next__
    def verify_prefix(self,other,count):
        if type(count)!=int or count<0 or count>min(len(self._rows),len(other._rows)):raise PitError('E_RAW_HASH','invalid prefix length')
        return self._rows[:count].tobytes()==other._rows[:count].tobytes()
    @property
    def cursor(self):return self._cursor
