from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part1/program/staff_schema.py'
with p.open('ab') as f:
    f.write((ROOT/'검증결과/staff_s6/wire_schema_addition.txt').read_bytes())
p=ROOT/'Part1/program/THE STAFF OF MOSES.py'
s=p.read_text('utf-8')
s=s.replace('import zmq\n','import zmq\nimport staff_schema as wire\n')
s=s.replace('*, health_session: Optional[str] = None, monotonic=None):',
            '*, health_session: Optional[str] = None, monotonic=None, gap_journal=None):')
s=s.replace('        self._health_epochs = {}','''        self._health_epochs = {}
        self._schema_errors = {}
        self._schema_connection_error = None
        self._requires_full = set()
        self._connection_count = 0
        self._reconnect_count = 0
        self._wire_stats = {}
        self._gap_records = []
        self._ea_build_hash = None
        self._gap_journal = Path(gap_journal) if gap_journal is not None else LOG_DIR / ('pipe_gaps_' + self._health_session + '.jsonl')''')
s=s.replace('            entry = self._entries.get((symbol, timeframe.lower()))\n            if entry is None:\n                return None, None',
'''            key = (symbol, timeframe.lower())
            entry = self._entries.get(key)
            if self._schema_connection_error or key in self._schema_errors or key in self._requires_full:
                return None, None
            if entry is None:
                return None, None''')
s=s.replace("'health_epochs': dict(self._health_epochs)}", """'health_epochs': dict(self._health_epochs),
                     'wire_v2': {'schema_errors':dict(self._schema_errors),
                         'connection_error':self._schema_connection_error,
                         'requires_full':tuple(self._requires_full),
                         'connections':self._connection_count,'reconnects':self._reconnect_count,
                         'stats':{k:dict(v) for k,v in self._wire_stats.items()},
                         'gaps':[dict(v) for v in self._gap_records], 'ea_build_hash':self._ea_build_hash}}""")
s=s.replace("            self._health_epochs = dict(state['health_epochs'])", """            self._health_epochs = dict(state['health_epochs'])
            v2=state.get('wire_v2', {})
            self._schema_errors=dict(v2.get('schema_errors', {}))
            self._schema_connection_error=v2.get('connection_error')
            self._requires_full=set(v2.get('requires_full', ()))
            self._connection_count=v2.get('connections', 0)
            self._reconnect_count=v2.get('reconnects', 0)
            self._wire_stats={k:dict(v) for k,v in v2.get('stats', {}).items()}
            self._gap_records=[dict(v) for v in v2.get('gaps', ())]
            self._ea_build_hash=v2.get('ea_build_hash')""")
s=s.replace('            self._snapshot.clear()', '''            if self._connection_count or self._entries:
                self._reconnect_count += 1
            self._connection_count += 1
            self._snapshot.clear()
            self._requires_full.update(self._entries)
            self._ea_build_hash = None''')
s=s.replace("'status': 'CLOSED' if closed else 'UNAVAILABLE' if age is None else", """'status': 'UNAVAILABLE' if (self._schema_connection_error or key in self._schema_errors) else
                              'CLOSED' if closed else 'UNAVAILABLE' if age is None else""")
s=s.replace('        self.receive_one(lambda size: self._read_exact(k32, handle, size))', '''        reply = self.receive_one(lambda size: self._read_exact(k32, handle, size))
        if reply is not None:
            buf = ctypes.create_string_buffer(reply)
            written = ctypes.c_uint32()
            if not k32.WriteFile(handle, buf, len(reply), ctypes.byref(written), None) or written.value != len(reply):
                raise OSError(ctypes.get_last_error(), 'Named Pipe HELLO reply failed')''')
s=s.replace('        self.receive_one(read_exact, finish=finish)', '        return self.receive_one(read_exact, finish=finish)')
s=s.replace('        if version != PIPE_VERSION:', '''        if version == 2:
            try:
                frame = wire.read_v2(header + read_exact(8), read_exact)
                if finish is not None:
                    finish()
            except wire.UnknownWireSchema as exc:
                with self._lock:
                    if exc.symbol and exc.timeframe:
                        self._schema_errors[(exc.symbol, exc.timeframe)] = exc.schema_id
                    else:
                        self._schema_connection_error = exc.schema_id or -1
                raise
            return self._receive_v2(frame)
        if version != PIPE_VERSION:''')
cut=s.index('        # MQL5 EMPTY_VALUE')
s=s[:cut]+'''        self._publish_arrays(symbol, timeframe, snapshot_id, times, volumes, values)

    def _publish_arrays(self, symbol, timeframe, snapshot_id, times, volumes, values, *, advance_epoch=True):
        bars, cols = values.shape
        values = values.copy()
'''+s[cut:]
s=s.replace('            if gap or previous == -1 or old is None or old.snapshot.indicator_validity != valid:',
'''            if not advance_epoch and (old is None or old.snapshot.indicator_validity != valid):
                raise wire.WireError('ROW validity transition requires FULL')
            if advance_epoch and (gap or previous == -1 or old is None or old.snapshot.indicator_validity != valid):''')
s=s.replace('            self._snapshot[key] = snapshot_id','''            self._snapshot[key] = snapshot_id
            self._requires_full.discard(key)
            self._schema_errors.pop(key, None)''')
at=s.index('    def _receiver_loop(')
s=s[:at]+'''    def wire_diagnostics(self):
        """Copyable diagnostics, separate from the unchanged SOURCE_HEALTH contract."""
        with self._lock:
            return {'schema':'staff-wire-diagnostics/v1', 'connections':self._connection_count,
                    'reconnects':self._reconnect_count, 'ea_build_hash':self._ea_build_hash,
                    'feeds':{k:dict(v) for k,v in self._wire_stats.items()},
                    'gaps':[dict(v) for v in self._gap_records], 'journal':str(self._gap_journal)}

    def _receive_v2(self, packet):
        if packet.kind == wire.WIRE_HELLO:
            with self._lock:
                self._ea_build_hash = packet.build_hash
                self._schema_connection_error = None
            return wire.pack_hello(packet.build_hash, ack=True)
        if packet.kind == wire.WIRE_ACK:
            raise wire.WireError('unexpected HELLO acknowledgement at server')
        children = packet.children if packet.kind == wire.WIRE_BUNDLE else (packet,)
        now_ms = time.time_ns() // 1000000
        sent = packet.sent_at_ms
        pending_gaps = []
        # Decode/CRC finishes before publication. Hold a single lock for the entire
        # bundle; rollback all mutations on a bad child. No latest-only queue.
        with self._lock:
            saved = (dict(self._entries),dict(self._updated),dict(self._snapshot),
                     dict(self._health_epochs),set(self._requires_full),dict(self._schema_errors))
            stats = {k:dict(v) for k,v in self._wire_stats.items()}
            try:
                for frame in children:
                    key=(frame.symbol,frame.timeframe)
                    previous=self._snapshot.get(key, -1)
                    stat=stats.setdefault(key, {'received':0,'accepted':0,'duplicate_or_reverse':0,
                                                'missing_sequences':0,'last_receive_delay_ms':None})
                    stat['received'] += 1
                    if frame.seq <= previous:
                        stat['duplicate_or_reverse'] += 1
                        continue
                    if previous >= 0 and frame.seq > previous + 1:
                        missing={'schema':'staff-seq-gap/v1','received_at_unix_ms':now_ms,
                            'sent_at_unix_ms':sent or None, 'symbol':frame.symbol,'timeframe':frame.timeframe,
                            'first_missing_seq':previous+1,'last_missing_seq':frame.seq-1,
                            'next_received_seq':frame.seq,'connection':self._connection_count,
                            'ea_build_hash':self._ea_build_hash}
                        pending_gaps.append(missing)
                        stat['missing_sequences'] += frame.seq-previous-1
                    if frame.kind == wire.WIRE_FULL:
                        self._publish_arrays(*key, frame.seq, frame.times, frame.volumes, frame.values)
                    else:
                        if previous < 0 or key in self._requires_full or key not in self._entries:
                            raise wire.WireError('ROW/HEARTBEAT requires FULL after connect')
                        old=self._entries[key].snapshot
                        if frame.kind == wire.WIRE_ROW:
                            if frame.times[0] != old.time[-1]:
                                raise wire.WireError('ROW new bar requires FULL')
                            volumes=old.volume.copy(); values=old.values.copy()
                            volumes[-1]=frame.volumes[0]; values[-1]=frame.values[0]
                            self._publish_arrays(*key,frame.seq,old.time,volumes,values,advance_epoch=False)
                        else:
                            snapshot=old._replace(seq=frame.seq,received_at=self._monotonic())
                            self._entries[key]=_SnapshotEntry(snapshot)
                            self._updated[key]=snapshot.received_at
                            self._snapshot[key]=frame.seq
                    stat['accepted'] += 1
                    stat['last_seq'] = frame.seq
                    stat['last_receive_delay_ms'] = max(0,now_ms-sent) if sent else None
                # Rare gap records are append-only JSONL: failure is visible and
                # aborts publication; it never silently drops an event.
                if pending_gaps:
                    self._gap_journal.parent.mkdir(parents=True,exist_ok=True)
                    with self._gap_journal.open('a',encoding='utf-8') as log:
                        for gap in pending_gaps:
                            log.write(json.dumps(gap,ensure_ascii=False,separators=(',',':'))+'\\n')
                        log.flush()
                    self._gap_records.extend(pending_gaps)
                self._wire_stats=stats
            except Exception:
                (self._entries,self._updated,self._snapshot,self._health_epochs,
                 self._requires_full,self._schema_errors)=saved
                raise

'''+s[at:]
s=s.replace('        k32.ReadFile.restype = ctypes.c_int', '''        k32.ReadFile.restype = ctypes.c_int
        k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k32.WriteFile.restype = ctypes.c_int''')
p.write_text(s,encoding='utf-8')
