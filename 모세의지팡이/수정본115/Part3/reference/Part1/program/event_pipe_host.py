"""Windows pipe transport for the unchanged STAFF parser and event ingress.

Only this I/O worker blocks in ReadFile. Engine processing never runs here.
The private STAFF receiver loop is not monkeypatched or used.
"""
import ctypes
import logging
import threading
import time
import datetime
import staff_schema as wire


class PipeReceiver:
    def __init__(self,staff,cache,adapter,symbols,stop):
        self.staff=staff;self.cache=cache;self.adapter=adapter;self.symbols=tuple(symbols);self.stop=stop
        self.handle=None;self.thread=None;self.health_thread=None;self.k32=None
        self.last_status={};self.last_source={};self._state_lock=threading.Lock()
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
        # The canonical STAFF boundary decodes, validates the allowlist and
        # publishes atomically once for both stream and replay byte sources.
        reply=self.adapter.receive_one(read,source_time=source_time,allowed_symbols=self.symbols)
        write(reply)
    def _run(self):
        k=self.k32=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateNamedPipeW.argtypes=[ctypes.c_wchar_p,*([ctypes.c_uint32]*6),ctypes.c_void_p];k.CreateNamedPipeW.restype=ctypes.c_void_p
        k.ConnectNamedPipe.argtypes=[ctypes.c_void_p,ctypes.c_void_p];k.ConnectNamedPipe.restype=ctypes.c_int
        for name in ('ReadFile','WriteFile'):
            fn=getattr(k,name);fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p];fn.restype=ctypes.c_int
        k.CloseHandle.argtypes=[ctypes.c_void_p];k.DisconnectNamedPipe.argtypes=[ctypes.c_void_p]
        k.CancelIoEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        k.OpenThread.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];k.OpenThread.restype=ctypes.c_void_p
        k.CancelSynchronousIo.argtypes=[ctypes.c_void_p]
        while not self.stop.is_set():
            try:
                with self.staff.staff_pipe_security() as security:
                    handle=k.CreateNamedPipeW(self.cache.pipe_name,3,0,1,1<<20,1<<20,0,ctypes.byref(security))
                if not handle or handle==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_last_error(),'CreateNamedPipe')
                self.handle=handle
                if not k.ConnectNamedPipe(handle,None) and ctypes.get_last_error()!=535:raise OSError(ctypes.get_last_error(),'ConnectNamedPipe')
                self.cache.reconnect()
                for symbol in self.symbols:self.adapter.health(symbol=symbol,source_time=time.time_ns()//1_000_000,status='RECONNECT')
                while not self.stop.is_set():self.receive(self._read,self._write)
            except Exception as exc:
                if not self.stop.is_set():
                    logging.warning('STAFF pipe reconnect: %s',exc)
                    for symbol in self.symbols:self.adapter.health(symbol=symbol,source_time=time.time_ns()//1_000_000,status='UNAVAILABLE',error=str(exc))
                    self.stop.wait(.2)
            finally:
                if self.handle:
                    k.DisconnectNamedPipe(self.handle);k.CloseHandle(self.handle);self.handle=None
    def close(self):
        self.stop.set()
        if self.k32 and self.thread and self.thread.native_id:
            thread=self.k32.OpenThread(0x0001,False,self.thread.native_id)
            if thread:
                try:self.k32.CancelSynchronousIo(thread)
                finally:self.k32.CloseHandle(thread)
        if self.thread:self.thread.join(timeout=3)
        if self.health_thread:self.health_thread.join(timeout=3)
