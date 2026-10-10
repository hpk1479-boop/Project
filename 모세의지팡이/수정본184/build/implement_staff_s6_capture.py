from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part2/part1_host/capture.py';s=p.read_text('utf-8')
s=s.replace('CAPTURE_VERSION = 1','CAPTURE_VERSION = 1\nCAPTURE_V2_MAGIC = 0x4D535033\nCAPTURE_V2_POLICY = "STAFF_PIPE_V2"\nV2_RECORD_HEADER = struct.Struct("<qiI")')
s=s.replace("if meta.get('pipe_capture') != CAPTURE_POLICY:","if meta.get('pipe_capture') not in (CAPTURE_POLICY, CAPTURE_V2_POLICY):")
s=s.replace("'feeds': sorted(feeds, key=lambda f: f.index)}","'wire_version': 2 if meta.get('pipe_capture') == CAPTURE_V2_POLICY else 1,\n            'feeds': sorted(feeds, key=lambda f: f.index)}")
s=s.replace('def _read_records(path: Path, expected: Optional[int] = None):','def _read_v1_records(path: Path, expected: Optional[int] = None):')
pos=s.index('\n\nclass FeedReplay:')
s=s[:pos]+'''

def wire_schema():
    # Load the packaged public Part1 registry, independently of whichever frozen
    # BEFORE runtime is currently bound in sys.modules. No mixed-version host.
    import functools
    return _wire_schema()


import functools
@functools.lru_cache(maxsize=1)
def _wire_schema():
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'Part1/program/staff_schema.py'
    spec=importlib.util.spec_from_file_location('part2_wire_schema',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def capture_version(path):
    with Path(path).open('rb') as f:
        head=f.read(FILE_HEADER.size)
    if len(head)!=FILE_HEADER.size:raise CaptureError('PIPE_CAPTURE_HEADER_SHORT')
    magic,version,cols,bars=FILE_HEADER.unpack(head)
    if cols!=45 or not 3<=bars<=650:raise CaptureError('PIPE_CAPTURE_HEADER_MISMATCH')
    if (magic,version)==(CAPTURE_MAGIC,1):return 1
    if (magic,version)==(CAPTURE_V2_MAGIC,2):return 2
    raise CaptureError('PIPE_CAPTURE_HEADER_MISMATCH')


def read_v2_records(path, expected=None):
    """MSP3 record: observed second i64, family flags i32, byte size u32, v2 frame."""
    if capture_version(path)!=2:raise CaptureError('MSP3_REQUIRED')
    w=wire_schema();count=0;last_seq={};previous=None
    with Path(path).open('rb') as f:
        f.read(FILE_HEADER.size)
        while True:
            header=f.read(V2_RECORD_HEADER.size)
            if not header:break
            if len(header)!=V2_RECORD_HEADER.size:raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            observed,flags,length=V2_RECORD_HEADER.unpack(header)
            if not w.WIRE_HEADER.size+4<=length<=w.WIRE_MAX_PAYLOAD+256:
                raise CaptureError('MSP3_RECORD_SIZE')
            raw=f.read(length)
            if len(raw)!=length:raise CaptureError('PIPE_CAPTURE_TRUNCATED')
            try:frame=w.decode_v2(raw)
            except w.WireError as exc:raise CaptureError(str(exc)) from exc
            if frame.kind not in (w.WIRE_FULL,w.WIRE_ROW,w.WIRE_HEARTBEAT):
                raise CaptureError('MSP3_EXPECTS_FEED_RECORD')
            key=(frame.symbol,frame.timeframe)
            if frame.seq<=last_seq.get(key,0):raise CaptureError('MSP3_SEQUENCE_ORDER')
            if previous is not None and observed<=previous:raise CaptureError('PIPE_CAPTURE_TIME_ORDER')
            last_seq[key]=frame.seq;previous=observed;count+=1
            yield int(observed),int(flags),frame,raw
    if expected is not None and count!=expected:raise CaptureError('PIPE_CAPTURE_COUNT_MISMATCH')


def _read_records(path: Path, expected: Optional[int] = None):
    if capture_version(path)==1:
        yield from _read_v1_records(path,expected)
    else:
        for observed,flags,frame,raw in read_v2_records(path,expected):
            yield frame.kind,observed,flags,frame.times,frame.volumes,frame.values


def iter_v2_publications(root, symbol=None, *, start_s=None, end_s=None):
    info=parse_capture_manifest(root);symbol=symbol or info['symbol']
    if symbol!=info['symbol']:raise CaptureError('PIPE_CAPTURE_SYMBOL_MISMATCH')
    heap=[];pending={};w=wire_schema()
    for feed in info['feeds']:
        it=iter(read_v2_records(feed.path,feed.records));item=next(it,None)
        if item:heapq.heappush(heap,(item[0],feed.index,feed.timeframe,item,it))
    while heap:
        observed,index,tf,item,it=heapq.heappop(heap)
        _,_,frame,raw=item
        if (frame.symbol,frame.timeframe)!=(symbol,tf):raise CaptureError('MSP3_FEED_IDENTITY')
        if end_s is not None and observed>=end_s:continue
        if start_s is not None and observed<start_s:
            prior=pending.get(index)
            if frame.kind==w.WIRE_FULL:state=[a.copy() for a in (frame.times,frame.volumes,frame.values)]
            elif prior is None:raise CaptureError('PIPE_CAPTURE_ROW_BEFORE_FULL')
            else:
                state=prior[2]
                if frame.kind==w.WIRE_ROW:
                    if state[0][-1]!=frame.times[0]:raise CaptureError('PIPE_CAPTURE_ROW_BAR_MISMATCH')
                    state[1][-1]=frame.volumes[0];state[2][-1]=frame.values[0]
            pending[index]=(tf,frame.seq,state)
        else:
            if pending:
                for j in sorted(pending):
                    ptf,seq,state=pending[j]
                    yield start_s,ptf,w.pack_v2(symbol,ptf,*state,seq=seq)
                pending={}
            yield observed,tf,raw
        nxt=next(it,None)
        if nxt:heapq.heappush(heap,(nxt[0],index,tf,nxt,it))
    for j in sorted(pending):
        tf,seq,state=pending[j]
        yield start_s,tf,w.pack_v2(symbol,tf,*state,seq=seq)
''' +s[pos:]
s=s.replace('            else:\n                if self.times is None:', '            elif kind == RECORD_ROW:\n                if self.times is None:')
s=s.replace('            yield observed, self.times, self.volumes, self.values', '''            elif self.times is None:
                raise CaptureError(f'PIPE_CAPTURE_HEARTBEAT_BEFORE_FULL {self.timeframe}')
            yield observed, self.times, self.volumes, self.values''')
needle="    info = parse_capture_manifest(root)\n    symbol = symbol or info['symbol']"
s=s.replace(needle,"""    info = parse_capture_manifest(root)
    if info['wire_version']==2:
        yield from iter_v2_publications(root,symbol,start_s=start_s,end_s=end_s)
        return
    symbol = symbol or info['symbol']""")
s=s.replace("session: str = 'offline-capture-0001'):","session: str = 'offline-capture-0001', *, wire_version=1):")
s=s.replace('        self.root = Path(root)','        self.wire_version = int(wire_version)\n        if self.wire_version not in (1,2):raise CaptureError("WIRE_VERSION")\n        self.previous_row = {}\n        self.root = Path(root)')
s=s.replace('f.write(FILE_HEADER.pack(CAPTURE_MAGIC, CAPTURE_VERSION, VALUE_COLUMNS, MAX_BARS))',
'''f.write(FILE_HEADER.pack(CAPTURE_V2_MAGIC if self.wire_version==2 else CAPTURE_MAGIC,
                                     self.wire_version, VALUE_COLUMNS, MAX_BARS))''')
needle='''        if full:
            rows = len(times)'''
replacement='''        if self.wire_version==2:
            w=wire_schema()
            full=full or self.flags[index]!=flags
            row=times[-1:].tobytes()+volumes[-1:].tobytes()+values[-1:].tobytes()
            kind=w.WIRE_FULL if full else w.WIRE_HEARTBEAT if self.previous_row.get(index)==row else w.WIRE_ROW
            if kind==w.WIRE_HEARTBEAT:
                raw=w.pack_v2(self.symbol,self.timeframes[index],seq=self.counts[index]+1,kind=kind)
            else:
                data=(times,volumes,values) if full else (times[-1:],volumes[-1:],values[-1:])
                raw=w.pack_v2(self.symbol,self.timeframes[index],*data,seq=self.counts[index]+1,kind=kind)
            f.write(V2_RECORD_HEADER.pack(int(observed),int(flags),len(raw)));f.write(raw)
            self.last_bar[index]=int(times[-1]);self.flags[index]=int(flags)
            self.previous_row[index]=row;self.counts[index]+=1
            return
        if full:
            rows = len(times)'''
assert needle in s;s=s.replace(needle,replacement)
s=s.replace("'pipe_capture\\t' + CAPTURE_POLICY]","'pipe_capture\\t' + (CAPTURE_V2_POLICY if self.wire_version==2 else CAPTURE_POLICY)]")
p.write_text(s,encoding='utf-8')
p=ROOT/'Part2/part1_host/engine.py';s=p.read_text('utf-8')
s=s.replace('from .capture import FeedReplay, parse_capture_manifest, pack_wire',
            'from .capture import FeedReplay, parse_capture_manifest, pack_wire, iter_v2_publications, wire_schema')
s=s.replace("        self.feeds = info['feeds']", """        self.wire_version=info['wire_version']
        self._pending=[]
        if self.wire_version==2:
            self._wire_iter=iter(iter_v2_publications(root,self.symbol))
            self._wire_next=next(self._wire_iter,None)
        self.feeds = info['feeds']""")
s=s.replace('''        for i, it in enumerate(self._iters):''','''        if self.wire_version==2:
            while self._wire_next is not None and self._wire_next[0]<=second:
                self._pending.append(self._wire_next)
                self._wire_next=next(self._wire_iter,None)
        for i, it in enumerate(self._iters):''')
s=s.replace('    def wires(self):\n        for feed, state', '''    def wires(self):
        if self.wire_version==2:
            # Preserve every record, ordered by observed second/feed index.
            # Bundle only equal observation instants, never replace older ones.
            import itertools
            pending,self._pending=self._pending,[]
            for observed,items in itertools.groupby(pending,key=lambda x:x[0]):
                items=list(items)
                for first in range(0,len(items),64):
                    yield '*',wire_schema().pack_bundle(self.symbol,[x[2] for x in items[first:first+64]])
            return
        for feed, state''')
p.write_text(s,encoding='utf-8')
p=ROOT/'Part2/part1_host/synthetic.py';s=p.read_text('utf-8')
s+='''

def capture_market(data, root, *, wire_version=2, timeframes=TIMEFRAMES):
    """Same synthetic inputs in MSP2 or MSP3; formulas above remain untouched."""
    from .capture import CaptureWriter
    writer=CaptureWriter(root,data.symbol,timeframes,wire_version=wire_version)
    try:
        for second in range(data.start,data.end):
            for index,tf in enumerate(timeframes):
                writer.write(index,second,*data.payload(tf,second))
    finally:
        writer.close()
    return root
'''
p.write_text(s,encoding='utf-8')
# Every normal Part2 MT5 build must install the new EA includes as well.
p=ROOT/'Part2/generic_backtest/native_mt5.py';s=p.read_text('utf-8')
s=s.replace("    shutil.copy2(identity,experts/'STAFF_Identity_Status.mqh')", """    shutil.copy2(identity,experts/'STAFF_Identity_Status.mqh')
    for name in ('STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh'):
        header=source_root/name
        if not header.is_file():raise ValueError('NATIVE_MQL_SOURCE_REQUIRED: '+name)
        shutil.copy2(header,experts/name)""")
p.write_text(s,encoding='utf-8')
