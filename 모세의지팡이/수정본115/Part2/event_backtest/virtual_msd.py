"""Read-only projection of build-verified MSD2 for virtual fills.

This reader never participates in capture verification or engine input. Only
requested feeds/columns have delta state; untouched feeds are structurally
skipped. uint64 assignment preserves every stored bit before normalization.
"""
import io
import json
import struct
from types import SimpleNamespace
import numpy as np
import staff_schema as wire
from .delta import WIRE, RECORD, exact
from . import keyframes

from .virtual_source import NAMES
INDICES=np.array([wire.PIPE_VALUE_COLUMNS.index(n) for n in NAMES])


class Projection:
    def __init__(self, selected):
        self.selected=selected;self.states={};self.views={};self.headers={}

    def header(self, head):
        result=self.headers.get(head[16:])
        if result is not None:return result
        h=WIRE.unpack_from(head);kind,rows,cols=h[-1],h[5],h[6]
        key=(head[40:40+h[3]].decode('utf8'),head[40+h[3]:40+h[3]+h[4]].decode('utf8').lower())
        selected=key in self.selected
        if selected and (h[7]!=wire.WIRE_SCHEMA_ID or cols!=len(wire.PIPE_VALUE_COLUMNS)):
            raise ValueError('가상 진입: 녹화 스키마 불일치')
        result=(key,kind,rows,cols,selected)
        self.headers[head[16:]]=result
        return result

    def put(self,key,kind,times,bits):
        if kind==wire.WIRE_HEARTBEAT:
            if key not in self.views:raise ValueError('가상 진입: FULL 없는 HEARTBEAT')
            return
        values=bits.view('<f8')
        invalid=~np.isfinite(values)|(np.abs(values)>1.e300)
        if invalid.any():values=values.copy();values[invalid]=np.nan
        if kind==wire.WIRE_FULL:
            self.states[key]=(times,bits)
            self.views[key]=SimpleNamespace(time=times,values=values,columns=NAMES)
        elif kind==wire.WIRE_ROW:
            old=self.views.get(key)
            if old is None or old.time[-1]!=times[0]:raise ValueError('가상 진입: FULL과 이어지지 않는 ROW')
            old_t,old_b=self.states[key];keep=old_t!=times[0]
            self.states[key]=(np.concatenate((old_t[keep],times))[-650:],np.concatenate((old_b[keep],bits))[-650:])
            x=old.values.copy();x[-1]=values[0]
            self.views[key]=SimpleNamespace(time=old.time,values=x,columns=NAMES)
        else:raise ValueError('가상 진입: 지원하지 않는 프레임 종류')

    def seed(self,raw):
        outer=WIRE.unpack_from(raw);pos=40+outer[3]
        _,count=struct.unpack_from('<qI',raw,pos);pos+=12
        for _ in range(count):
            length=struct.unpack_from('<I',raw,pos)[0];pos+=4
            h=WIRE.unpack_from(raw,pos);prefix=40+h[3]+h[4]
            key,kind,rows,cols,selected=self.header(raw[pos:pos+prefix])
            if selected:
                times=np.frombuffer(raw,'<i8',rows,pos+prefix)
                bits=np.frombuffer(raw,'<u8',rows*cols,pos+prefix+16*rows).reshape(rows,cols)[:,INDICES]
                self.put(key,kind,times,bits)
            pos+=length
        if pos!=len(raw)-4:raise ValueError('가상 진입: bootstrap 길이 오류')

    def decode(self,structure,bits):
        f=io.BytesIO(structure);prefix=exact(f,struct.unpack('<H',exact(f,2))[0]);exact(f,4)
        outer=WIRE.unpack_from(prefix);count=struct.unpack_from('<I',prefix,40+outer[3]+8)[0];cursor=0
        for _ in range(count):
            size,length=struct.unpack('<HI',exact(f,6));head=exact(f,size);exact(f,4)
            key,kind,rows,cols,selected=self.header(head)
            if kind==wire.WIRE_HEARTBEAT:
                if selected:self.put(key,kind,None,None)
                continue
            if kind not in (wire.WIRE_FULL,wire.WIRE_ROW):raise ValueError('unsupported delta child')
            mapping=np.frombuffer(exact(f,2*rows),'<i2');matched=mapping>=0
            new_times=exact(f,8*int((~matched).sum()))
            n=struct.unpack('<I',exact(f,4))[0];changed_raw=exact(f,4*n)
            if cursor+n>len(bits):raise ValueError('truncated delta values')
            if selected:
                old_t,old_b=self.states.get(key,(np.empty(0,dtype='<i8'),np.empty((0,len(NAMES)),dtype='<u8')))
                if np.any(mapping>=len(old_t)):raise ValueError('invalid delta row map')
                times=np.empty(rows,dtype='<i8');times[matched]=old_t[mapping[matched]]
                times[~matched]=np.frombuffer(new_times,'<i8')
                values=np.zeros((rows,len(NAMES)),dtype='<u8');values[matched]=old_b[mapping[matched]]
                changed=np.frombuffer(changed_raw,'<u4')
                if np.any(changed>=rows*(cols+1)):raise ValueError('invalid delta cell')
                column=changed%(cols+1)
                # Storage column 0 is volume; market columns are offset by one.
                for j,index in enumerate(INDICES+1):
                    mask=column==index
                    values[changed[mask]//(cols+1),j]=bits[cursor:cursor+n][mask]
                self.put(key,kind,times,values)
            cursor+=n
        if f.read(1) or cursor!=len(bits):raise ValueError('delta trailing data')
        return self.views


def verified_index(root):
    """Trust only a published build verification marker + matching manifest."""
    file=root/'capture.delta2'
    if not file.is_file():return None
    try:
        if (root/'complete.txt').read_text(encoding='ascii').strip()!='VERIFIED':return None
        storage=json.loads((root/'storage.json').read_text(encoding='utf8'))
    except (OSError,ValueError):return None
    index=keyframes.read_index(file)
    if storage.get('format')!='MSD2' or any(storage.get(k)!=index.get(k) for k in ('bundles','bundle_sha256')):
        return None
    return index


def read_selected(root,index,selected,start,end,*,check,needed_from,metrics):
    with (root/'capture.delta2').open('rb') as file:
        for entry in index['days']:
            check();needed=needed_from()
            if needed is None:return
            if entry['last_ms']<max(start,needed):continue
            if entry['first_ms']>end:return
            file.seek(entry['offset'])
            metrics['date_restores']=metrics.get('date_restores',0)+1
            with keyframes.gzip.GzipFile(fileobj=keyframes.LimitedReader(file,entry['size']),mode='rb') as stream:
                seed_size,metadata_size=struct.unpack('<II',exact(stream,8))
                if seed_size>32*1024*1024 or metadata_size>8*1024*1024:raise ValueError('MSD2 bootstrap bounds')
                seed=exact(stream,seed_size);exact(stream,metadata_size)
                projection=Projection(selected)
                if seed:projection.seed(seed)
                count=0
                while header:=stream.read(RECORD.size):
                    check()
                    if len(header)!=RECORD.size:raise ValueError('truncated MSD2 record')
                    stamp,size,n=RECORD.unpack(header)
                    if stamp>end:return
                    if size>32*1024*1024 or n>4*1024*1024:raise ValueError('MSD2 record bounds')
                    views=projection.decode(exact(stream,size),np.frombuffer(exact(stream,n*8),'<u8'))
                    count+=1;metrics['delta_records']=metrics.get('delta_records',0)+1
                    needed=needed_from()
                    if needed is None:return
                    if stamp>=max(start,needed):yield stamp,views
                if count!=entry['bundles']:raise ValueError('MSD2 day record count')
