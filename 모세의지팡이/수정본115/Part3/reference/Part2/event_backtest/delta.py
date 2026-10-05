"""Lossless row-time aligned Wire container. No market-value float conversion.

The codec is schema-agnostic storage, not an old-schema engine adapter. Removed
rows are implicit in the target row map. CRC and all envelope bytes are retained.
"""
from pathlib import Path
import gzip
import hashlib
import heapq
import io
import struct
import zlib
import numpy as np

WIRE=struct.Struct('<IIqIIIIII')
RECORD=struct.Struct('<qII')
MAGIC=b'MSD1\x00\x00\x00\x01'

def exact(stream,n):
    value=stream.read(n)
    if len(value)!=n:raise ValueError('truncated delta/capture')
    return value

def source_bundles(directory):
    """MSP3 merge, preserving raw child bytes and original manifest ordering."""
    root=Path(directory);meta={};feeds=[]
    if not (root/'complete.txt').is_file():raise ValueError('incomplete capture')
    for line in (root/'manifest.tsv').read_text('ascii').splitlines()[1:]:
        row=line.split('\t')
        if row[0]=='pipe_feed':feeds.append(row)
        elif len(row)==2:meta[row[0]]=row[1]
    def records(row):
        path=(root/row[3]).resolve()
        if path.parent!=root.resolve():raise ValueError('capture path escape')
        with (gzip.open(path,'rb') if path.suffix=='.gz' else path.open('rb')) as f:
            magic,version,cols,bars=struct.unpack('<IIII',exact(f,16))
            if magic!=0x4D535033 or version!=2:raise ValueError('MSP3 required')
            count=0;previous=None
            while header:=f.read(16):
                if len(header)!=16:raise ValueError('truncated MSP3')
                observed,flags,size=struct.unpack('<qiI',header)
                if not 44<=size<=32*1024*1024:raise ValueError('invalid MSP3 size')
                raw=exact(f,size);h=WIRE.unpack_from(raw)
                if h[6]!=cols or zlib.crc32(raw[40:-4])!=struct.unpack('<I',raw[-4:])[0]:raise ValueError('capture schema/CRC')
                stamp=observed if meta.get('pipe_observation_unit')=='milliseconds' else observed*1000
                if previous is not None and stamp<=previous:raise ValueError('capture order')
                previous=stamp;count+=1;yield stamp,raw
            if count!=int(row[4]):raise ValueError('capture record count')
    heap=[]
    for row in feeds:
        iterator=iter(records(row));first=next(iterator,None)
        if first:heapq.heappush(heap,(first[0],int(row[1]),first[1],iterator))
    seq=0;sym=meta['symbol'].encode()
    while heap:
        stamp=heap[0][0];children=[]
        while heap and heap[0][0]==stamp:
            _,index,raw,iterator=heapq.heappop(heap);children.append(raw)
            following=next(iterator,None)
            if following:heapq.heappush(heap,(following[0],index,following[1],iterator))
        seq+=1;h=WIRE.unpack_from(children[0])
        body=struct.pack('<qI',stamp,len(children))+b''.join(struct.pack('<I',len(c))+c for c in children)
        payload=sym+body
        yield stamp,WIRE.pack(h[0],2,seq,len(sym),0,len(body),h[6],h[7],5)+payload+struct.pack('<I',zlib.crc32(payload))

class DeltaCodec:
    def __init__(self):self.feeds={}
    def update(self,key,kind,times,values):
        if kind==1:self.feeds[key]=(times.copy(),values.copy())
        elif kind==2:
            old_t,old_v=self.feeds.get(key,(times[:0],values[:0]))
            keep=old_t!=times[0]
            self.feeds[key]=(np.concatenate((old_t[keep],times))[-650:],np.concatenate((old_v[keep],values))[-650:])
    def encode(self,raw):
        outer=WIRE.unpack_from(raw);ns=outer[3];pos=40+ns
        sent,count=struct.unpack_from('<qI',raw,pos);pos+=12
        out=io.BytesIO();out.write(struct.pack('<H',pos));out.write(raw[:pos]);out.write(raw[-4:])
        bits=[]
        for _ in range(count):
            length=struct.unpack_from('<I',raw,pos)[0];pos+=4;child=raw[pos:pos+length];pos+=length
            h=WIRE.unpack_from(child);kind=h[-1];rows=h[5];cols=h[6];prefix=40+h[3]+h[4];key=child[40:prefix]
            out.write(struct.pack('<HI',prefix,length));out.write(child[:prefix]);out.write(child[-4:])
            if kind==3:continue
            if kind not in (1,2):raise ValueError('unsupported child')
            times=np.frombuffer(child,'<i8',rows,prefix)
            values=np.empty((rows,cols+1),dtype='<u8')
            values[:,0]=np.frombuffer(child,'<u8',rows,prefix+8*rows)
            values[:,1:]=np.frombuffer(child,'<u8',rows*cols,prefix+16*rows).reshape(rows,cols)
            old_t,old_v=self.feeds.get(key,(np.empty(0,dtype='<i8'),np.empty((0,cols+1),dtype='<u8')))
            mapping=np.searchsorted(old_t,times)
            matched=(mapping<len(old_t))
            if len(old_t):matched &=old_t[np.minimum(mapping,len(old_t)-1)]==times
            else:matched[:]=False
            mapping=np.where(matched,mapping,-1).astype('<i2')
            base=np.zeros_like(values);base[matched]=old_v[mapping[matched]]
            changed=np.flatnonzero(values.ravel()!=base.ravel()).astype('<u4')
            out.write(mapping.tobytes());out.write(times[~matched].tobytes())
            out.write(struct.pack('<I',len(changed)));out.write(changed.tobytes());bits.append(values.ravel()[changed])
            self.update(key,kind,times,values)
        if pos!=len(raw)-4:raise ValueError('bundle trailing bytes')
        return out.getvalue(),np.concatenate(bits) if bits else np.empty(0,dtype='<u8')
    def decode(self,structure,bits):
        f=io.BytesIO(structure);prefix=exact(f,struct.unpack('<H',exact(f,2))[0]);trailer=exact(f,4)
        outer=WIRE.unpack_from(prefix);count=struct.unpack_from('<I',prefix,40+outer[3]+8)[0];children=[];cursor=0
        for _ in range(count):
            size,length=struct.unpack('<HI',exact(f,6));head=exact(f,size);crc=exact(f,4);h=WIRE.unpack_from(head)
            kind=h[-1];rows=h[5];cols=h[6];key=head[40:]
            if kind==3:child=head+crc
            else:
                old_t,old_v=self.feeds.get(key,(np.empty(0,dtype='<i8'),np.empty((0,cols+1),dtype='<u8')))
                mapping=np.frombuffer(exact(f,2*rows),'<i2');matched=mapping>=0
                if np.any(mapping>=len(old_t)):raise ValueError('invalid delta row map')
                times=np.empty(rows,dtype='<i8');times[matched]=old_t[mapping[matched]]
                times[~matched]=np.frombuffer(exact(f,8*int((~matched).sum())),'<i8')
                values=np.zeros((rows,cols+1),dtype='<u8');values[matched]=old_v[mapping[matched]]
                n=struct.unpack('<I',exact(f,4))[0];changed=np.frombuffer(exact(f,4*n),'<u4')
                values.ravel()[changed]=bits[cursor:cursor+n];cursor+=n
                child=head+times.tobytes()+values[:,0].tobytes()+values[:,1:].tobytes()+crc
                self.update(key,kind,times,values)
            if len(child)!=length or zlib.crc32(child[40:-4])!=struct.unpack('<I',crc)[0]:raise ValueError('delta CRC/length')
            children.append(struct.pack('<I',length)+child)
        if f.read(1) or cursor!=len(bits):raise ValueError('delta trailing data')
        raw=prefix+b''.join(children)+trailer
        if zlib.crc32(raw[40:-4])!=struct.unpack('<I',trailer)[0]:raise ValueError('delta bundle CRC')
        return raw

def update_hash(hasher,stamp,raw):hasher.update(struct.pack('<qI',stamp,len(raw)));hasher.update(raw)

def write_delta(bundles,path):
    codec=DeltaCodec();hashes=hashlib.sha256();count=0
    with gzip.open(path,'wb',compresslevel=1) as f:
        f.write(MAGIC)
        for stamp,raw in bundles:
            structure,bits=codec.encode(raw);f.write(RECORD.pack(stamp,len(structure),len(bits)))
            f.write(structure);f.write(bits.tobytes());update_hash(hashes,stamp,raw);count+=1
    return {'bundles':count,'bundle_sha256':hashes.hexdigest()}

def read_delta(path):
    codec=DeltaCodec()
    with gzip.open(path,'rb') as f:
        if exact(f,len(MAGIC))!=MAGIC:raise ValueError('delta magic')
        while header:=f.read(RECORD.size):
            if len(header)!=RECORD.size:raise ValueError('truncated delta record')
            stamp,size,n=RECORD.unpack(header)
            if size>32*1024*1024 or n>4*1024*1024:raise ValueError('invalid delta lengths')
            structure=exact(f,size);bits=np.frombuffer(exact(f,n*8),'<u8')
            yield stamp,codec.decode(structure,bits)

def verify_delta(path,expected):
    h=hashlib.sha256();count=0
    for stamp,raw in read_delta(path):update_hash(h,stamp,raw);count+=1
    result={'bundles':count,'bundle_sha256':h.hexdigest()}
    if result!=expected:raise ValueError('lossless reconstruction hash mismatch')
    return result
