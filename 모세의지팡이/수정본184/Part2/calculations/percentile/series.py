from dataclasses import asdict, replace
from .contracts import NativeCell, KernelWrite, PercentileError
from .frozen_buffers import TrackedCells


class SeriesAdapter:
    def __init__(self, chronological):self.values=tuple(chronological)
    def __len__(self):return len(self.values)
    def __getitem__(self,shift):
        if not 0<=shift<len(self.values):raise PercentileError('E_INVALID_ARRAY_LAYOUT',str(shift))
        return self.values[-1-shift]

class SourceStateStore:
    def __init__(self,names=()):
        self.buffers={name:TrackedCells() for name in names};self.bar_ids=();self.allocated=0;self.raw=TrackedCells()
        self.freeze_versions=False
        self.writes=[];self.event_id=None;self.history_epoch=None;self.last_return=0
        # Diagnostic only: never assign a value to an unread native cell.
        self.initialization_gap=None
        self._call_shape=None
    def begin(self,bars,event):
        ids=tuple(str(getattr(b,'bar_id', b.get('bar_id', b.get('time',i)) if isinstance(b,dict) else i)) for i,b in enumerate(bars))
        if len(ids)!=event.R:raise PercentileError('E_INVALID_ARRAY_LAYOUT','bars length != R')
        if len(set(ids))!=len(ids):raise PercentileError('E_INVALID_ARRAY_LAYOUT','duplicate bar identity')
        if self.history_epoch != event.history_epoch:
            self.initialization_gap=None
        self._call_shape=(event.R,event.P)
        self.layout_kind = 'SAME'
        if ids != self.bar_ids:
            if ids[:len(self.bar_ids)] == self.bar_ids:
                self.layout_kind = 'APPEND'
                # Pure native history append: old AS_SERIES values retain order.
                added = ids[len(self.bar_ids):]
                for name, old in self.buffers.items():
                    self.buffers[name] = TrackedCells(
                        [NativeCell.unknown(f'{name}:NEW_STORAGE:{b}') for b in reversed(added)] + old)
            else:
                self.layout_kind = 'REINDEX'
                old_index={bar:i for i,bar in enumerate(reversed(self.bar_ids))}
                for name,old in self.buffers.items():
                    self.buffers[name]=TrackedCells(old[old_index[b]] if b in old_index else NativeCell.unknown(f'{name}:NEW_STORAGE:{b}') for b in reversed(ids))
        self.bar_ids=ids;self.event_id=event.calc_event_id;self.history_epoch=event.history_epoch;self.writes=[]
    def read(self,name,i):
        values=self.raw if name=='raw' else self.buffers[name]
        if not 0<=i<len(values):return NativeCell.unknown(f'{name}:OUT_OF_RANGE:{i}')
        value=values[i]
        if self.initialization_gap is None and not value.available and value.last_write_event is None:
            # First actual read-before-write, not a guessed seed or a dump of
            # every downstream UNKNOWN. AS_SERIES 0 is the forming candle.
            r,p=self._call_shape or (len(self.bar_ids),self.last_return)
            self.initialization_gap={'buffer':name,'shift':i,'rates_total':r,
                'prev_calculated':p,'bar_id':self.bar_ids[-1-i] if i<len(self.bar_ids) else None,
                'chronological_index':r-1-i,'origin':value.origin,'event_id':self.event_id,
                'source_reason':value.taint[0] if value.taint else value.origin,
                'reason':'NATIVE_BUFFER_READ_BEFORE_WRITE'}
        return value
    def write(self,name,i,value,pass_name=''):
        values=self.raw if name=='raw' else self.buffers[name]
        if not 0<=i<len(values):raise PercentileError('E_INVALID_ARRAY_LAYOUT',f'{name}[{i}] size={len(values)}')
        from .numeric import cell
        value=cell(value)
        origin='EXPLICIT_EMPTY' if value.numeric_class=='EMPTY_VALUE' and not value.taint else 'SOURCE_WRITTEN'
        value=replace(value,origin=origin,last_write_event=self.event_id)
        values[i]=value;self.writes.append(KernelWrite(name,i,value,pass_name))
    def allocate_raw(self,size):
        # ArrayResize keeps physical prefix. Logical AS_SERIES indices move on growth.
        if size>len(self.raw):self.raw=TrackedCells([NativeCell.unknown('raw:NEW_STORAGE')]*(size-len(self.raw))+self.raw)
        elif size<len(self.raw):self.raw=TrackedCells(self.raw[len(self.raw)-size:] if size else [])
    def control(self,name,i,value):
        """Record uncertainty of a conditional write without inventing a source assignment."""
        prior=self.buffers[name][i]
        self.buffers[name][i]=replace(value,origin=prior.origin if prior.bits==value.bits else 'SOURCE_WRITTEN',last_write_event=prior.last_write_event)
    def capture(self,prestate,policy):
        if prestate is None:return
        if policy!='NATIVE_STATE_CONDITIONED':raise PercentileError('E_UNDEFINED_SOURCE_STATE','captured seed requires diagnostic policy')
        for name,values in prestate.items():
            if name=='raw' and len(values)!=len(self.raw):
                self.raw=TrackedCells(NativeCell.unknown('raw:CAPTURE_LAYOUT') for _ in values)
            target=self.raw if name=='raw' else self.buffers.get(name)
            if target is None:raise PercentileError('E_INVALID_ARRAY_LAYOUT',name)
            if len(values)>len(target):raise PercentileError('E_INVALID_ARRAY_LAYOUT',f'capture {name}')
            for i,v in enumerate(values):
                v=v if isinstance(v,NativeCell) else NativeCell(int(v),'CAPTURED_NATIVE_STATE')
                target[i]=NativeCell(v.bits,'CAPTURED_NATIVE_STATE',tuple(sorted(set(v.taint+('CAPTURED_NATIVE_STATE',)))),v.last_write_event)
    def snapshot(self):
        return {'buffers':{n:[asdict(c) for c in a] for n,a in self.buffers.items()},'raw':[asdict(c) for c in self.raw],
                'bar_ids':list(self.bar_ids),'allocated':self.allocated,'history_epoch':self.history_epoch,'last_return':self.last_return,
                'initialization_gap':None if self.initialization_gap is None else dict(self.initialization_gap)}
    def restore(self,data):
        def decode(v):return NativeCell(v['bits'],v['origin'],tuple(v['taint']),v.get('last_write_event'))
        self.buffers={n:TrackedCells(decode(v) for v in a) for n,a in data['buffers'].items()};self.raw=TrackedCells(decode(v) for v in data['raw'])
        self.bar_ids=tuple(data['bar_ids']);self.allocated=data['allocated'];self.history_epoch=data['history_epoch'];self.last_return=data['last_return']

        self.initialization_gap=data.get('initialization_gap')
        self._call_shape=None
