"""Read-only projection of build-verified MSD2 for virtual fills.

This reader never participates in capture verification or engine input. Only
requested feeds/columns have delta state; untouched feeds are structurally
skipped. uint64 assignment preserves every stored bit before normalization.
"""
import json
import struct
from types import SimpleNamespace
import numpy as np
import staff_schema as wire
from .delta import WIRE, RECORD, exact
from . import keyframes

from .virtual_source import NAMES, _EndWindow
INDICES=np.array([wire.PIPE_VALUE_COLUMNS.index(n) for n in NAMES])
# Storage column (0 is volume) -> position in NAMES, -1 for a column the fills never read.
SLOTS=np.full(len(wire.PIPE_VALUE_COLUMNS)+1,-1,dtype=np.intp);SLOTS[INDICES+1]=np.arange(len(NAMES))
KINDS=(wire.WIRE_FULL,wire.WIRE_ROW,wire.WIRE_APPEND)
H=struct.Struct('<H');HI=struct.Struct('<HI');U32=struct.Struct('<I');ONE=struct.Struct('<h');TWO=struct.Struct('<hh')
TRUNCATED='truncated delta/capture'


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
            old_t,old_b=self.states[key]
            if len(times)==1 and old_t[-1]==times[0] and not (old_t[:-1]==times[0]).any():
                # The ROW replaces only the last row: the same window the general rebuild below makes.
                new_b=old_b.copy();new_b[-1]=bits[0];self.states[key]=(old_t,new_b)
            else:
                keep=old_t!=times[0]
                self.states[key]=(np.concatenate((old_t[keep],times))[-650:],np.concatenate((old_b[keep],bits))[-650:])
            x=old.values.copy();x[-1]=values[0]
            self.views[key]=SimpleNamespace(time=old.time,values=x,columns=NAMES)
        elif kind==wire.WIRE_APPEND:
            # The closed bar's final row, then the new bar: the window STAFF builds from it.
            old=self.views.get(key)
            if old is None or old.time[-1]!=times[0] or times[1]<=times[0]:raise ValueError('가상 진입: FULL과 이어지지 않는 APPEND')
            old_t,old_b=self.states[key];keep=old_t!=times[0]
            new_t=np.concatenate((old_t[keep],times))[-650:]
            self.states[key]=(new_t,np.concatenate((old_b[keep],bits))[-650:])
            x=np.concatenate((old.values[old.time!=times[0]],values))[-650:]
            self.views[key]=SimpleNamespace(time=new_t,values=x,columns=NAMES)
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
        # Offsets into the record instead of a stream: a feed the fills never read is
        # stepped over by its lengths alone, with the same bounds checks.
        total=len(structure)
        if total<2:raise ValueError(TRUNCATED)
        pos=2+H.unpack_from(structure)[0]
        if pos+4>total:raise ValueError(TRUNCATED)
        prefix=structure[2:pos];pos+=4
        outer=WIRE.unpack_from(prefix);count=U32.unpack_from(prefix,40+outer[3]+8)[0];cursor=0;available=len(bits)
        for _ in range(count):
            if pos+6>total:raise ValueError(TRUNCATED)
            size=HI.unpack_from(structure,pos)[0];pos+=6
            if pos+size+4>total:raise ValueError(TRUNCATED)
            head=structure[pos:pos+size];pos+=size+4
            key,kind,rows,cols,selected=self.header(head)
            if kind==wire.WIRE_HEARTBEAT:
                if selected:self.put(key,kind,None,None)
                continue
            if kind not in KINDS:raise ValueError('unsupported delta child')
            times_at=pos+2*rows
            if times_at>total:raise ValueError(TRUNCATED)
            # New rows (row map < 0) carry their times; a ROW has one row, an APPEND two.
            if rows==1:new=int(ONE.unpack_from(structure,pos)[0]<0)
            elif rows==2:a,b=TWO.unpack_from(structure,pos);new=(a<0)+(b<0)
            else:new=int(np.count_nonzero(np.frombuffer(structure,'<i2',rows,pos)<0))
            changed_at=times_at+8*new+4
            if changed_at>total:raise ValueError(TRUNCATED)
            n=U32.unpack_from(structure,changed_at-4)[0];end=changed_at+4*n
            if end>total:raise ValueError(TRUNCATED)
            if cursor+n>available:raise ValueError('truncated delta values')
            if selected:
                mapping=np.frombuffer(structure,'<i2',rows,pos);matched=mapping>=0
                old_t,old_b=self.states.get(key,(np.empty(0,dtype='<i8'),np.empty((0,len(NAMES)),dtype='<u8')))
                if np.any(mapping>=len(old_t)):raise ValueError('invalid delta row map')
                times=np.empty(rows,dtype='<i8');times[matched]=old_t[mapping[matched]]
                times[~matched]=np.frombuffer(structure,'<i8',new,times_at)
                values=np.zeros((rows,len(NAMES)),dtype='<u8');values[matched]=old_b[mapping[matched]]
                changed=np.frombuffer(structure,'<u4',n,changed_at)
                if np.any(changed>=rows*(cols+1)):raise ValueError('invalid delta cell')
                # Storage column 0 is volume; market columns are offset by one.
                slot=SLOTS[changed%(cols+1)];read=slot>=0
                values[changed[read]//(cols+1),slot[read]]=bits[cursor:cursor+n][read]
                self.put(key,kind,times,values)
            pos=end;cursor+=n
        if pos!=total or cursor!=available:raise ValueError('delta trailing data')
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


def read_selected(root,index,selected,start,end,*,check,needed_from,metrics,past_end=False,read_limit=None):
    """The optional finalization tail ends within its first recorded server day (수정본178)."""
    limit=read_limit or _EndWindow(end,past_end)
    with (root/'capture.delta2').open('rb') as file:
        for entry in index['days']:
            check();needed=needed_from()
            if needed is None:return
            if entry['last_ms']<max(start,needed):continue
            if not limit.allows(entry['first_ms']):return
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
                    if not limit.allows(stamp):return
                    if size>32*1024*1024 or n>4*1024*1024:raise ValueError('MSD2 record bounds')
                    views=projection.decode(exact(stream,size),np.frombuffer(exact(stream,n*8),'<u8'))
                    count+=1;metrics['delta_records']=metrics.get('delta_records',0)+1
                    needed=needed_from()
                    if needed is None:return
                    if stamp>=max(start,needed):yield stamp,views
                if count!=entry['bundles']:raise ValueError('MSD2 day record count')
