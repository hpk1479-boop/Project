"""Windows pipe transport for the unchanged STAFF parser and event ingress.

Only this I/O worker blocks in ReadFile. Engine processing never runs here.
The private STAFF receiver loop is not monkeypatched or used.
"""
import ctypes
import logging
import threading
import time
import datetime
import re
import staff_schema as wire


# The EA owns the symbol list. The pipe accepts any well-formed symbol
# instead of a config allowlist; CSV/state files cannot hold commas or spaces.
_WIRE_SYMBOL=re.compile(r'[^\s,\x00-\x1f\x7f]{1,64}')


class WireSymbolRule:
    def __contains__(self,symbol):
        return isinstance(symbol,str) and _WIRE_SYMBOL.fullmatch(symbol) is not None


WIRE_SYMBOL_RULE=WireSymbolRule()


class PipeReceiver:
    def __init__(self,staff,cache,adapter,symbols,stop,*,diagnostics=None,
                 on_bound=None,on_connected=None,on_fatal=None):
        self.staff=staff;self.cache=cache;self.adapter=adapter;self.seed_symbols=tuple(symbols);self.stop=stop
        self.handle=None;self.thread=None;self.health_thread=None;self.k32=None
        self.diagnostics=diagnostics
        self.on_bound=on_bound;self.on_connected=on_connected;self.on_fatal=on_fatal
        self.last_status={};self.last_source={};self._state_lock=threading.Lock()
        self._observed=();self._observed_keys=0
    @property
    def symbols(self):
        return tuple(dict.fromkeys((*self.seed_symbols,*self._observed)))
    def _observe(self):
        # Only validated publications reach the cache, so its keys are what the EA really sent.
        keys=self.cache.keys()
        if len(keys)==self._observed_keys:return
        self._observed_keys=len(keys)
        seen=tuple(dict.fromkeys((*self._observed,*(symbol for symbol,_ in keys))))
        if seen!=self._observed:self._observed=seen
    def start(self):
        self.thread=threading.Thread(target=self._run,name='STAFF-event-input',daemon=True);self.thread.start()
        self.health_thread=threading.Thread(target=self._health_loop,name='STAFF-health-input',daemon=True);self.health_thread.start()
    def _health_loop(self):
        while not self.stop.wait(1):
            now_ms=time.time_ns()//1_000_000
            keys=self.cache.keys()
            for symbol in self.symbols:
                tfs=[tf for sym,tf in keys if sym==symbol]
                closed=datetime.datetime.now(datetime.timezone.utc).weekday()>=5 and symbol!='BTCUSD'
                feeds=self.cache.health(symbol,tfs,closed=closed)
                statuses={x['status'] for x in feeds.values()}
                status=('CLOSED' if closed else 'UNAVAILABLE' if not statuses or 'UNAVAILABLE' in statuses else
                        'STALE' if 'STALE' in statuses else 'FRESH')
                with self._state_lock:
                    previous=self.last_status.get(symbol);self.last_status[symbol]=status
                if self.diagnostics is not None:self.diagnostics.health(symbol,status)
                if status!=previous:self.adapter.health(symbol=symbol,source_time=now_ms,status=status,feeds=feeds)
    def _read(self,size):
        out=bytearray(size);offset=0
        while offset<size:
            chunk=min(size-offset,1<<20);buf=(ctypes.c_ubyte*chunk).from_buffer(out,offset);got=ctypes.c_uint32()
            if not self.k32.ReadFile(self.handle,buf,chunk,ctypes.byref(got),None) or not got.value:
                raise OSError(ctypes.get_last_error(),'pipe read interrupted')
            offset+=got.value
        return bytes(out)
    def _write(self,data):
        if not data:return
        buf=ctypes.create_string_buffer(data);written=ctypes.c_uint32()
        if not self.k32.WriteFile(self.handle,buf,len(data),ctypes.byref(written),None) or written.value!=len(data):
            raise OSError(ctypes.get_last_error(),'pipe HELLO reply failed')
    def receive(self,read,write,*,source_time=None):
        # The canonical STAFF boundary decodes, validates the symbol form and
        # publishes atomically once for both stream and replay byte sources.
        reply=self.adapter.receive_one(read,source_time=source_time,allowed_symbols=WIRE_SYMBOL_RULE)
        self._observe()
        write(reply)
        if self.diagnostics is not None:
            # HELLO acknowledges the connection; only a market frame means
            # the receiver has actually started supplying monitored input.
            if not reply:self.diagnostics.touch("STAFF")
            self.diagnostics.log("STAFF",logging.INFO,"수신·검증·전광판 전달 완료 · source_time=%s",source_time,detail=True)
    def _run(self):
        try:self._run_owned_pipe()
        except Exception as exc:
            if not self.stop.is_set():
                logging.error('STAFF 파이프를 준비하지 못해 엔진을 종료합니다: %s',exc)
                self.stop.set()
                if self.on_fatal is not None:
                    self.on_fatal('MT5 파이프를 준비하지 못했습니다. 다른 엔진의 실행 여부와 파이프 권한을 확인하세요.')
        finally:
            if self.diagnostics is not None:self.diagnostics.pipe_state(False)
            if self.on_connected is not None:self.on_connected(False)
            if self.handle and self.k32:
                self.k32.DisconnectNamedPipe(self.handle);self.k32.CloseHandle(self.handle);self.handle=None
            if self.on_bound is not None:self.on_bound(False)
    def _run_owned_pipe(self):
        k=self.k32=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateNamedPipeW.argtypes=[ctypes.c_wchar_p,*([ctypes.c_uint32]*6),ctypes.c_void_p];k.CreateNamedPipeW.restype=ctypes.c_void_p
        k.ConnectNamedPipe.argtypes=[ctypes.c_void_p,ctypes.c_void_p];k.ConnectNamedPipe.restype=ctypes.c_int
        for name in ('ReadFile','WriteFile'):
            fn=getattr(k,name);fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p];fn.restype=ctypes.c_int
        k.CloseHandle.argtypes=[ctypes.c_void_p];k.DisconnectNamedPipe.argtypes=[ctypes.c_void_p]
        k.CancelIoEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        k.OpenThread.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];k.OpenThread.restype=ctypes.c_void_p
        k.CancelSynchronousIo.argtypes=[ctypes.c_void_p]
        # Keep one handle for the whole engine lifetime. A reconnect must not
        # release the pipe name for another engine to take between EA clients.
        with self.staff.staff_pipe_security() as security:
            handle=k.CreateNamedPipeW(self.cache.pipe_name,3|0x00080000,0,1,1<<20,1<<20,0,ctypes.byref(security))
        if not handle or handle==ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(),'CreateNamedPipe')
        self.handle=handle
        if self.on_bound is not None:self.on_bound(True)
        while not self.stop.is_set():
            try:
                if not k.ConnectNamedPipe(handle,None) and ctypes.get_last_error()!=535:raise OSError(ctypes.get_last_error(),'ConnectNamedPipe')
                if self.diagnostics is not None:self.diagnostics.pipe_state(True)
                if self.on_connected is not None:self.on_connected(True)
                self.cache.reconnect()
                for symbol in self.symbols:self.adapter.health(symbol=symbol,source_time=time.time_ns()//1_000_000,status='RECONNECT')
                while not self.stop.is_set():self.receive(self._read,self._write)
            except Exception as exc:
                if not self.stop.is_set():
                    logging.warning('STAFF pipe reconnect: %s',exc,exc_info=True)
                    for symbol in self.symbols:self.adapter.health(symbol=symbol,source_time=time.time_ns()//1_000_000,status='UNAVAILABLE',error=str(exc))
                    self.stop.wait(.2)
            finally:
                if self.diagnostics is not None:self.diagnostics.pipe_state(False)
                if self.on_connected is not None:self.on_connected(False)
                k.DisconnectNamedPipe(handle)
    def close(self):
        self.stop.set()
        if self.k32 and self.thread and self.thread.native_id:
            thread=self.k32.OpenThread(0x0001,False,self.thread.native_id)
            if thread:
                try:self.k32.CancelSynchronousIo(thread)
                finally:self.k32.CloseHandle(thread)
        if self.thread:self.thread.join(timeout=3)
        if self.health_thread:self.health_thread.join(timeout=3)
        return not any(worker and worker.is_alive() for worker in (self.thread,self.health_thread))
