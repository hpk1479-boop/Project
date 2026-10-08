"""CPU order on one Windows PC: LIVE alerts first, backtests yield, neither slowed in the background.

Only process scheduling changes. No market input, decision or output depends on
it. Other operating systems, or a refused Windows call, keep the order as it was.

A running LIVE engine holds a named mark (수정본167). A backtest checks it every
few seconds with one system call and runs below normal only while LIVE runs on
the PC (LIVE and MT5, at normal priority, come first), at normal otherwise.
"""
import ctypes
import os
import time

ABOVE_NORMAL = 0x00008000
NORMAL = 0x00000020
BELOW_NORMAL = 0x00004000
_POWER_THROTTLING = 4        # ProcessPowerThrottling
_EXECUTION_SPEED = 0x1       # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
LIVE_MARK = r'Global\MOSES_LIVE_ENGINE_RUNNING_v1'
_SYNCHRONIZE = 0x00100000
_SET_INFORMATION = 0x0200 | 0x1000     # PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
_held = []


class _Throttling(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_ulong), ('ControlMask', ctypes.c_ulong), ('StateMask', ctypes.c_ulong)]


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.SetPriorityClass.restype = ctypes.c_int
    kernel.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel.SetProcessInformation.restype = ctypes.c_int
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    return kernel


def _set(kernel, priority):
    """Priority class (None keeps it) and no efficiency throttling.

    Windows runs processes of an app that is not in front on efficiency cores at a
    low clock; a backtest behind other windows measured 4-5x slower. Opting out is
    per process (worker processes inherit the priority class, not this choice).
    """
    if kernel is None and os.name != 'nt':
        return None
    result = {} if priority is None else {'priority': False}
    result['throttling_off'] = False
    try:
        kernel = kernel or _kernel()
        process = kernel.GetCurrentProcess()
        if priority is not None:
            result['priority'] = bool(kernel.SetPriorityClass(process, priority))
        state = _Throttling(1, _EXECUTION_SPEED, 0)   # execution speed controlled: throttling off
        result['throttling_off'] = bool(kernel.SetProcessInformation(process, _POWER_THROTTLING, ctypes.byref(state),
                                                                     ctypes.sizeof(state)))
    except (OSError, AttributeError):
        pass
    return result


def prefer_live(kernel=None):
    """LIVE engine: above-normal priority, full speed when its window is not in front."""
    return _set(kernel, ABOVE_NORMAL)


def yield_to_live(kernel=None):
    """Backtest: below-normal priority (LIVE first), full speed when its window is not in front."""
    return _set(kernel, BELOW_NORMAL)


def full_speed(kernel=None):
    """Backtest worker process: keep the inherited priority, opt out of efficiency throttling."""
    return _set(kernel, None)


def mark_live_running(kernel=None):
    """LIVE engine: hold the named mark for the life of this process.

    Windows drops it when the process ends, also after a crash, so no notice is needed."""
    if kernel is None and os.name != 'nt':
        return None
    if _held:
        return True
    try:
        kernel = kernel or _kernel()
        handle = kernel.CreateMutexW(None, False, LIVE_MARK)
    except (OSError, AttributeError):
        return False
    if handle:
        _held.append(handle)
    return bool(handle)


def live_running(kernel=None):
    """Whether a LIVE engine on this PC holds its mark: one system call, no process scan."""
    if kernel is None and os.name != 'nt':
        return False
    try:
        kernel = kernel or _kernel()
        handle = kernel.OpenMutexW(_SYNCHRONIZE, False, LIVE_MARK)
    except (OSError, AttributeError):
        return False
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def backtest_class(kernel=None):
    """Below normal while LIVE runs on this PC, normal otherwise."""
    return BELOW_NORMAL if live_running(kernel) else NORMAL


def backtest_priority(kernel=None):
    """Backtest: the class backtest_class chooses, never slowed in the background."""
    return _set(kernel, backtest_class(kernel))


def set_priority_of(pids, priority, kernel=None):
    """Give processes made earlier (workers inherit the class only when created) the current class."""
    if kernel is None and os.name != 'nt':
        return
    try:
        kernel = kernel or _kernel()
    except (OSError, AttributeError):
        return
    for pid in pids:
        handle = kernel.OpenProcess(_SET_INFORMATION, False, int(pid))
        if not handle:
            continue
        try:
            kernel.SetPriorityClass(handle, priority)
        finally:
            kernel.CloseHandle(handle)


class BacktestPriority:
    """A backtest's class follows LIVE: checked at most every interval, applied to it and its workers."""
    def __init__(self, interval=2.0, *, kernel=None, clock=time.monotonic):
        self.interval, self.kernel, self.clock = interval, kernel, clock
        self.live = None
        self._next = float('-inf')

    def update(self, pids=()):
        now = self.clock()
        if now < self._next:
            return self.live
        self._next = now + self.interval
        live = live_running(self.kernel)
        if live != self.live:
            priority = BELOW_NORMAL if live else NORMAL
            _set(self.kernel, priority)
            set_priority_of(pids, priority, self.kernel)
            self.live = live
        return live
