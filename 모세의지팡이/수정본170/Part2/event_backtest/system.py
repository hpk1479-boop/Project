"""Host-only resource and network boundaries."""
import ctypes
import json
import os
import socket
import sys
from pathlib import Path

_write_roots=()
_write_guard_installed=False
def restrict_writes(*roots):
    """Worker boundary: accidental legacy state writes fail before touching disk.

    Several roots when one chunk replays several runs at once (their run directories)."""
    global _write_roots,_write_guard_installed
    _write_roots=tuple(Path(root).resolve() for root in roots)
    if _write_guard_installed:return
    def check(path):
        if isinstance(path,int):return
        target=Path(path).resolve()
        if not any(target.is_relative_to(root) for root in _write_roots):raise PermissionError('backtest write outside run directory')
    def audit(event,args):
        if event=='open':
            path,mode,flags=args
            if (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):check(path)
        elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.utime','os.truncate'):check(args[0])
        elif event in ('os.rename','os.link','os.symlink'):check(args[0]);check(args[1])
        elif event=='subprocess.Popen':raise PermissionError('backtest subprocess execution prohibited')
    sys.addaudithook(audit);_write_guard_installed=True

def write_progress(path,data):
    """Best effort telemetry. Windows readers may temporarily deny replacement."""
    path=Path(path);temporary=path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
        return True
    except PermissionError:
        # Never lose market input or abort a run because a progress reader holds
        # the previous JSON open. The next scheduled update will retry.
        return False

def deny_network():
    def denied(*a,**k):raise RuntimeError('백테스트 네트워크 사용 금지')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied
    socket.create_connection=denied

def physical_cores():
    """This PC's physical cores. Windows lists one record per core (수정본170: asked directly, not through a
    PowerShell query that took over a second); elsewhere, or when that fails, half the logical processors."""
    if os.name=='nt':
        try:
            count=_windows_cores()
            if count>0:return count
        except OSError:pass
    return max(1,(os.cpu_count() or 2)//2)

def _windows_cores():
    """The processor core records of GetLogicalProcessorInformationEx (RelationProcessorCore, all groups)."""
    import struct
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    query=kernel.GetLogicalProcessorInformationEx
    query.argtypes=(ctypes.c_int,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD))
    query.restype=wintypes.BOOL
    size=wintypes.DWORD(0)
    query(0,None,ctypes.byref(size))  # the needed buffer size
    if not size.value:raise ctypes.WinError(ctypes.get_last_error())
    buffer=ctypes.create_string_buffer(size.value)
    if not query(0,buffer,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
    count=offset=0
    while offset<size.value:
        relationship,length=struct.unpack_from('<iI',buffer,offset)
        if length<=0:break
        count+=relationship==0
        offset+=length
    return count

def process_memory(pid=None,peak=True):
    if os.name!='nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in
          ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    c=Counters();c.cb=ctypes.sizeof(c)
    k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p
    ps=ctypes.WinDLL('psapi');ps.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=[ctypes.c_void_p]
    h=k.GetCurrentProcess() if pid is None else k.OpenProcess(0x410,False,int(pid))
    if not h:return 0
    try:
        if not ps.GetProcessMemoryInfo(h,ctypes.byref(c),c.cb):return 0
        return c.PeakWorkingSetSize if peak else c.WorkingSetSize
    finally:
        if pid is not None:k.CloseHandle(h)

def peak_memory():return process_memory()

if os.name=='nt':
    _current_processor=ctypes.WinDLL('kernel32').GetCurrentProcessorNumber
    _current_processor.argtypes=[];_current_processor.restype=ctypes.c_ulong
else:
    _current_processor=lambda:None

def current_processor():
    """Logical CPU sampled at the end of a bundle; workers may migrate."""
    return _current_processor()
