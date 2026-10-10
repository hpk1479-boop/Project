from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name,fn):
    p=ROOT/name;b=p.read_bytes();nl='\r\n' if b'\r\n' in b else '\n'
    s=b.decode().replace('\r\n','\n');new=fn(s)
    if s==new:raise ValueError(name)
    p.write_bytes(new.replace('\n',nl).encode())
def domain(s):
    needle='''    def __init__(
        self,
        config: dict[str, str],'''
    replacement='''    @property
    def official_specs(self):
        return self._official_specs

    @official_specs.setter
    def official_specs(self, values):
        from composer_fact_index import SpecRegistry
        self._official_specs = SpecRegistry(values)

    @property
    def manual_specs(self):
        return self._manual_specs

    @manual_specs.setter
    def manual_specs(self, values):
        from composer_fact_index import SpecRegistry
        self._manual_specs = SpecRegistry(values)

'''+needle
    assert needle in s;s=s.replace(needle,replacement,1)
    needle='''        specs = [s for s in self._all_specs_locked() if s.symbol == symbol]
        sources = set()'''
    repl='''        specs = [s for s in self._all_specs_locked() if s.symbol == symbol]
        self._evaluate_specs_locked(symbol, specs)

    def _evaluate_fact_dependents_locked(self, symbol, tf, family):
        specs = (*self.official_specs.affected(symbol, tf, family),
                 *self.manual_specs.affected(symbol, tf, family))
        self._evaluate_specs_locked(symbol, specs)

    def _evaluate_specs_locked(self, symbol, specs):
        if not specs:
            return
        sources = set()'''
    assert needle in s;s=s.replace(needle,repl,1)
    needle="            self._source_bindings[fact_scope(event)] = event.get('source_health') or {}\n            self._evaluate_symbol_locked(symbol)"
    assert needle in s;s=s.replace(needle,"            self._source_bindings[fact_scope(event)] = event.get('source_health') or {}\n            self._evaluate_fact_dependents_locked(symbol, tf, family)",1)
    return s
edit('Part1/program/event_composer_domain.py',domain)
def delta(s):
    s=s.replace('    def __init__(self):self.feeds={}', '    def __init__(self, *, verify_crc=True):self.feeds={};self.verify_crc=verify_crc')
    s=s.replace('def update(self,key,kind,times,values):','def update(self,key,kind,times,values,*,owned=False):')
    s=s.replace('if kind==1:self.feeds[key]=(times.copy(),values.copy())','if kind==1:self.feeds[key]=(times,values) if owned else (times.copy(),values.copy())')
    needle='''                self.update(key,kind,times,values)
            if len(child)!=length or zlib.crc32(child[40:-4])!=struct.unpack('<I',crc)[0]:raise ValueError('delta CRC/length')'''
    repl='''                self.update(key,kind,times,values,owned=True)
            if len(child)!=length or (self.verify_crc and zlib.crc32(memoryview(child)[40:-4])!=struct.unpack('<I',crc)[0]):raise ValueError('delta CRC/length')'''
    assert needle in s;s=s.replace(needle,repl,1)
    s=s.replace("        if zlib.crc32(raw[40:-4])!=struct.unpack('<I',trailer)[0]:raise ValueError('delta bundle CRC')", "        if self.verify_crc and zlib.crc32(memoryview(raw)[40:-4])!=struct.unpack('<I',trailer)[0]:raise ValueError('delta bundle CRC')")
    s=s.replace('def read_delta(path):\n    codec=DeltaCodec()', 'def read_delta(path,*,verify_crc=True):\n    codec=DeltaCodec(verify_crc=verify_crc)')
    return s
edit('Part2/event_backtest/delta.py',delta)
edit('Part2/event_backtest/keyframes.py',lambda s:s.replace('def read_indexed(path,*,start_ms=None,bootstrap=None,day_verified=None):','def read_indexed(path,*,start_ms=None,bootstrap=None,day_verified=None,verify_crc=True):').replace('                codec=DeltaCodec()','                codec=DeltaCodec(verify_crc=verify_crc)'))
edit('Part2/event_backtest/storage.py',lambda s:s.replace('def bundles(root,*,start_ms=None,bootstrap=None):','def bundles(root,*,start_ms=None,bootstrap=None,staff_validation=False):').replace('start_ms=start_ms,bootstrap=bootstrap)','start_ms=start_ms,bootstrap=bootstrap,verify_crc=not staff_validation)').replace("read_delta(root/'capture.delta.gz')", "read_delta(root/'capture.delta.gz',verify_crc=not staff_validation)"))
def staff(s):
    # Inspection is canonical STAFF decoding, reused only for the identical
    # immutable bytes object. Different/remapped/mutable input is revalidated.
    marker='    def receive_publication(self, *, raw=None, read_exact=None, allowed_symbols=None,'
    methods='''    def inspect_publication(self, raw):
        """Validate bytes for header/continuity inspection before publication.

        Inspection never publishes or skips the canonical seq/health checks.
        Only this thread's identical immutable bytes can reuse its decoding.
        """
        packet = wire.decode_v2(raw)
        if type(raw) is bytes:
            self._wire_inspection.value = (raw, packet)
        else:
            self._wire_inspection.value = None
        return packet

    def _decode_publication(self, raw):
        inspected = getattr(self._wire_inspection, 'value', None)
        self._wire_inspection.value = None
        if inspected is not None and type(raw) is bytes and inspected[0] is raw:
            return inspected[1]
        return wire.decode_v2(raw)

'''
    assert marker in s;s=s.replace(marker,methods+marker,1)
    a=s.index('class StaffPipeCache:');b=s.index('    def ',a)
    # Initialization next to cache synchronization, not a process-global cache.
    p=s.index('        self._lock = threading.RLock()',a)
    s=s[:p]+s[p:].replace('        self._lock = threading.RLock()', '        self._lock = threading.RLock()\n        self._wire_inspection = threading.local()',1)
    s=s.replace('packet=(wire.decode_v2(raw) if raw is not None else','packet=(self._decode_publication(raw) if raw is not None else',1)
    return s
edit('Part1/program/THE STAFF OF MOSES.py',staff)
edit('Part2/event_backtest/bridge.py',lambda s:s.replace('packet=wire.decode_v2(raw)','packet=self.cache.inspect_publication(raw)').replace('capture_bundles(path,start_ms=seek,bootstrap=bootstrap)','capture_bundles(path,start_ms=seek,bootstrap=bootstrap,staff_validation=True)'))
