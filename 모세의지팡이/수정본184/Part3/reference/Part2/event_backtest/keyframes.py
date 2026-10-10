"""MSD2: independently compressed UTC days with validated STAFF continuation.

The delta equations and original bundle bytes are unchanged. Each day starts
with a FULL bootstrap of the preceding feed windows. It is not an observation
and is excluded from the reconstructed stream hash.
"""
from pathlib import Path
import bisect,gzip,hashlib,io,json,struct,zlib
import numpy as np
from .delta import DeltaCodec,RECORD,WIRE,exact,update_hash

MAGIC=b'MSD2\x00\x00\x00\x02'
FOOTER=struct.Struct('<QQ8s')
END=b'MSD2IDX2'
DAY_MS=86400000

def encode_metadata(value):
    if isinstance(value,dict):return {'map':[[encode_metadata(k),encode_metadata(v)] for k,v in value.items()]}
    if isinstance(value,tuple):return {'tuple':[encode_metadata(v) for v in value]}
    if isinstance(value,list):return [encode_metadata(v) for v in value]
    return value

def decode_metadata(value):
    if isinstance(value,dict):
        if set(value)=={'map'}:return {decode_metadata(k):decode_metadata(v) for k,v in value['map']}
        if set(value)=={'tuple'}:return tuple(decode_metadata(v) for v in value['tuple'])
        raise ValueError('invalid keyframe metadata')
    if isinstance(value,list):return [decode_metadata(v) for v in value]
    return value

def full_bootstrap(codec,headers,stamp,bundle_seq):
    if not codec.feeds:return b''
    children=[];symbol=None
    for key,(times,bits) in codec.feeds.items():
        head=headers[key];h=list(WIRE.unpack_from(head));h[5]=len(times);h[-1]=1
        current=head[40:40+h[3]]
        if symbol is not None and current!=symbol:raise ValueError('one capture must contain one symbol')
        symbol=current
        body=head[40:]+times.tobytes()+bits[:,0].tobytes()+bits[:,1:].tobytes()
        child=WIRE.pack(*h)+body+struct.pack('<I',zlib.crc32(body))
        children.append(struct.pack('<I',len(child))+child)
    body=struct.pack('<qI',stamp,len(children))+b''.join(children)
    payload=symbol+body
    return WIRE.pack(h[0],2,bundle_seq,len(symbol),0,len(body),h[6],h[7],5)+payload+struct.pack('<I',zlib.crc32(payload))

def read_index(path):
    with Path(path).open('rb') as f:
        if exact(f,8)!=MAGIC:raise ValueError('MSD2 magic')
        f.seek(0,2);size=f.tell()
        if size<8+FOOTER.size:raise ValueError('truncated MSD2')
        f.seek(-FOOTER.size,2);offset,length,end=FOOTER.unpack(exact(f,FOOTER.size))
        if end!=END or offset<8 or offset+length!=size-FOOTER.size or length>8*1024*1024:raise ValueError('MSD2 index bounds')
        f.seek(offset);index=json.loads(exact(f,length))
    last_day=-2**63;last_offset=8
    for entry in index['days']:
        if entry['day']<=last_day or entry['offset']!=last_offset or entry['size']<=0:raise ValueError('MSD2 index order')
        last_day=entry['day'];last_offset=entry['offset']+entry['size']
    if last_offset!=offset:raise ValueError('MSD2 segment bounds')
    return index

def write_indexed(bundles,path,*,staff_cache,receive_clock=None):
    """Single pass with bounded gzip buffers; original frame digest retained."""
    import staff_schema as wire
    from .bridge import remap_bundle
    codec=DeltaCodec();headers={};offsets={};clock=0.;total=0;last_stamp=0
    full_hash=hashlib.sha256();days=[];day=None;segment=None;day_hash=None;offset=0;day_count=0;day_start=0
    def begin(f,stamp):
        nonlocal segment,day_hash,day_count,day_start,offset
        offset=f.tell();segment=gzip.GzipFile(fileobj=f,mode='wb',compresslevel=1,mtime=0)
        seed=full_bootstrap(codec,headers,last_stamp,total)
        metadata={'staff':staff_cache.export_state(metadata_only=True),'offsets':offsets,'clock':clock,
                  'prefix_bundles':total,'last_stamp':last_stamp}
        encoded=json.dumps(encode_metadata(metadata),ensure_ascii=False,separators=(',',':')).encode()
        segment.write(struct.pack('<II',len(seed),len(encoded)));segment.write(seed);segment.write(encoded)
        day_hash=hashlib.sha256();day_count=0;day_start=stamp
    def finish(f):
        if segment is None:return
        segment.close()
        days.append({'day':day,'first_ms':day_start,'last_ms':last_stamp,'offset':offset,'size':f.tell()-offset,
                     'bundles':day_count,'prefix_bundles':total-day_count,'bundle_sha256':day_hash.hexdigest()})
    with Path(path).open('wb') as f:
        f.write(MAGIC)
        for stamp,raw in bundles:
            if total and stamp<=last_stamp:raise ValueError('non-monotone capture')
            current_day=stamp//DAY_MS
            if day!=current_day:
                finish(f);day=current_day;begin(f,stamp)
            packet=wire.decode_v2(raw);sequences=[]
            for frame in packet.children:
                key=(frame.symbol,frame.timeframe)
                if key not in offsets:
                    if frame.kind!=wire.WIRE_FULL:raise ValueError('first feed must be FULL')
                    offsets[key]=1-frame.seq
                sequences.append(frame.seq+offsets[key])
            # Run the same canonical STAFF validation as an ordinary prefix scan.
            total+=1;clock+=.001
            if receive_clock is not None:receive_clock[0]=clock
            staff_cache.receive_publication(raw=remap_bundle(raw,packet,sequences,total,stamp),source_time=stamp,require_observation=True)
            structure,bits=codec.encode(raw)
            segment.write(RECORD.pack(stamp,len(structure),len(bits)));segment.write(structure);segment.write(bits.tobytes())
            pos=40+WIRE.unpack_from(raw)[3]+12
            for frame in packet.children:
                length=struct.unpack_from('<I',raw,pos)[0];pos+=4
                n=40+len(frame.symbol.encode())+len(frame.timeframe.encode())
                head=raw[pos:pos+n];headers[head[40:]]=head;pos+=length
            last_stamp=stamp;day_count+=1;update_hash(day_hash,stamp,raw);update_hash(full_hash,stamp,raw)
        finish(f)
        index={'format':'MSD2','version':2,'bundles':total,'bundle_sha256':full_hash.hexdigest(),'days':days}
        encoded=json.dumps(index,separators=(',',':')).encode();offset=f.tell();f.write(encoded);f.write(FOOTER.pack(offset,len(encoded),END))
    return index

def read_indexed(path,*,start_ms=None,bootstrap=None):
    """Yield exact original bytes. Only the initial seek invokes bootstrap."""
    index=read_index(path);days=index['days'];first=0
    if start_ms is not None and days:
        first=max(0,bisect.bisect_right([d['day'] for d in days],start_ms//DAY_MS)-1)
    with Path(path).open('rb') as file:
        for number,entry in enumerate(days[first:]):
            file.seek(entry['offset'])
            with gzip.GzipFile(fileobj=LimitedReader(file,entry['size']),mode='rb') as f:
                seed_size,metadata_size=struct.unpack('<II',exact(f,8))
                if seed_size>32*1024*1024 or metadata_size>8*1024*1024:raise ValueError('MSD2 bootstrap bounds')
                seed=exact(f,seed_size);metadata=decode_metadata(json.loads(exact(f,metadata_size)))
                codec=DeltaCodec()
                if seed:codec.encode(seed)
                if number==0 and bootstrap and start_ms is not None and seed:bootstrap(seed,metadata)
                count=0;hashes=hashlib.sha256()
                while header:=f.read(RECORD.size):
                    if len(header)!=RECORD.size:raise ValueError('truncated MSD2 record')
                    stamp,size,n=RECORD.unpack(header)
                    if size>32*1024*1024 or n>4*1024*1024:raise ValueError('MSD2 record bounds')
                    raw=codec.decode(exact(f,size),np.frombuffer(exact(f,n*8),'<u8'))
                    count+=1;update_hash(hashes,stamp,raw);yield stamp,raw
                if count!=entry['bundles'] or hashes.hexdigest()!=entry['bundle_sha256']:raise ValueError('MSD2 day reconstruction hash')

class LimitedReader:
    def __init__(self,file,size):self.file=file;self.remaining=size
    def read(self,n=-1):
        n=self.remaining if n<0 else min(n,self.remaining)
        value=self.file.read(n);self.remaining-=len(value);return value

def verify_indexed(path,expected):
    count=0;hashes=hashlib.sha256()
    for stamp,raw in read_indexed(path):count+=1;update_hash(hashes,stamp,raw)
    result={'bundles':count,'bundle_sha256':hashes.hexdigest()}
    if any(result[k]!=expected[k] for k in result):raise ValueError('MSD2 complete reconstruction hash')
    return result
