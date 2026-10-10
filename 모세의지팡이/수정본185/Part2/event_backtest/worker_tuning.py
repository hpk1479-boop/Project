"""This PC's fastest number of backtest workers, measured when the user asks (수정본167).

The same short replay runs on several worker counts at once, from the PC's cores to its logical
processors; the count that replays the most bundles per second wins. The result is saved with the
PC's identity in Part2/event_backtest.json (kept by updates) and used only on that PC: another PC
keeps its core count until it is measured there. An explicit worker count always wins.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import struct
import sys
import uuid

from .settings import PROGRAM, ROOT, milliseconds

LIVE_RUNNING = '실시간을 끈 뒤 다시 누르세요.'
BACKTEST_RUNNING = '백테스트가 끝난 뒤 다시 누르세요.'
NO_RECORDING = '측정에 쓸 녹화가 없습니다. 백테스트를 한 번 돌린 뒤 다시 누르세요.'
MEMORY_SHARE = 0.8      # of the memory free at the start, for all workers together
CLOSE_SHARE = 0.97      # the fewest workers within this share of the best throughput win


class _Memory(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memory():
    """(total, available) physical memory in bytes; (0, 0) where it cannot be read."""
    if os.name != 'nt':
        return 0, 0
    state = _Memory(); state.dwLength = ctypes.sizeof(state)
    if not ctypes.WinDLL('kernel32').GlobalMemoryStatusEx(ctypes.byref(state)):
        return 0, 0
    return int(state.ullTotalPhys), int(state.ullAvailPhys)


def core_counts():
    """(physical cores, logical processors) of this PC."""
    logical = os.cpu_count() or 1
    if os.name != 'nt':
        return logical, logical
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalProcessorInformationEx.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetLogicalProcessorInformationEx.restype = ctypes.c_int
    size = ctypes.c_ulong(0)
    kernel.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))       # 0: RelationProcessorCore
    buffer = ctypes.create_string_buffer(size.value)
    if not size.value or not kernel.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
        return logical, logical
    raw, offset, cores = buffer.raw, 0, 0
    while offset + 8 <= size.value:
        relationship, length = struct.unpack_from('<II', raw, offset)
        if not length:
            break
        cores += relationship == 0
        offset += length
    return max(1, min(cores or logical, logical)), logical


def _processor_name():
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                return ' '.join(str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).split())
        except OSError:
            pass
    import platform
    return platform.processor() or 'unknown'


def machine():
    """This PC's identity for a saved measurement: processor, cores, logical processors, memory."""
    physical, logical = core_counts()
    total, _ = memory()
    row = {'processor': _processor_name(), 'physical': physical, 'logical': logical,
           'memory_gb': round(total / 2**30)}
    row['key'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode('utf-8')).hexdigest()[:16]
    return row


def saved_profile(config=None):
    """This PC's saved measurement, or None."""
    if config is None:
        from .settings import settings
        config = settings()
    row = (config.get('worker_profiles') or {}).get(machine()['key'])
    if isinstance(row, dict) and type(row.get('workers')) is int and row['workers'] >= 1:
        return row
    return None


def tuned_workers(config=None):
    row = saved_profile(config)
    return None if row is None else row['workers']


def candidates(physical, logical):
    """Worker counts measured: the cores, the logical processors and the middle."""
    return sorted({max(1, physical), max(1, (physical + logical) // 2), max(1, logical)})


def _task_day(catalog, symbol):
    """The last recorded trading day with one recorded trading day before it, as (day, next day)."""
    rows = [c for c in catalog.available(symbol, 'BAR') if c.get('reconstruction_verified')]
    if not rows:
        raise ValueError(NO_RECORDING)
    end = max(dt.date.fromisoformat(str(c['end'])[:10]) for c in rows)
    day = end - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat(), (day + dt.timedelta(days=1)).isoformat()


def _template(warehouse, symbol, strategies, triggers):
    from .settings import scenario
    from .warehouse import Warehouse
    from . import runner
    from .calendar import warm_start
    catalog = Warehouse(warehouse)
    try:
        start, end = _task_day(catalog, symbol)
        s = scenario(symbol=symbol, start=start, end=end, mode='BAR', overlap_trading_days=1,
                     strategies=list(strategies), triggers=dict(triggers or {}), commands=[])
        chosen, missing = runner.resolve_captures(catalog, s)
    finally:
        catalog.close()
    if not chosen or missing:
        raise ValueError(NO_RECORDING)
    warm = warm_start(start, 1, chosen)
    pieces = [c for c in chosen if c.get('end', end) > warm and c.get('start', start) < end]
    return {'scenario': s, 'config': runner.runtime_config(s), 'start': start, 'end': end, 'warm_start': warm,
            'captures': pieces, 'transport': 'replay', 'warehouse': str(warehouse)}


def _round(task, folder, count):
    """Throughput of `count` workers replaying the same day at once."""
    from . import runner
    tasks = []
    for index in range(count):
        out = folder / f'{count:03d}_{index:03d}' / 'chunk_000'
        out.mkdir(parents=True)
        tasks.append({**task, 'run_id': uuid.uuid4().hex, 'out': str(out)})
    with concurrent.futures.ProcessPoolExecutor(max_workers=count, initializer=runner._worker_started) as pool:
        results = list(pool.map(runner.run_chunk, tasks))
    per_bundle = [1000 * r['elapsed_seconds'] / max(1, r['bundles']) for r in results]
    median = statistics.median(per_bundle)
    return {'workers': count, 'bundles_per_second': round(count * 1000 / median, 1),
            'ms_per_bundle': round(median, 1),
            'worker_memory_mb': round(max(r['max_memory_bytes'] for r in results) / 2**20)}


def measure(warehouse, *, symbol, strategies, triggers=None, emit=lambda *a: None):
    """Measure every candidate on this PC and return the chosen count with the measured rows."""
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    import process_priority
    from .warehouse_cleanup import warehouse_activity, warehouse_idle
    if process_priority.live_running():
        raise ValueError(LIVE_RUNNING)
    if not strategies:
        raise ValueError('측정할 전략을 백테스트 화면에서 고르세요.')
    try:
        with warehouse_idle(warehouse):
            pass
    except OSError:
        raise ValueError(BACKTEST_RUNNING) from None
    pc = machine()
    _, free = memory()
    process_priority._set(None, process_priority.NORMAL)
    rows, budget = [], None
    with warehouse_activity(warehouse):
        task = _template(warehouse, symbol, strategies, triggers)
        folder = Path(warehouse) / '.tuning' / uuid.uuid4().hex
        try:
            counts = candidates(pc['physical'], pc['logical'])
            for step, count in enumerate(counts, 1):
                if budget is not None and count > budget:
                    rows.append({'workers': count, 'skipped': '메모리 부족'})
                    continue
                if process_priority.live_running():
                    raise ValueError(LIVE_RUNNING)
                emit('TUNE_PROGRESS', {'step': step, 'steps': len(counts), 'workers': count})
                row = _round(task, folder, count)
                rows.append(row)
                if free:
                    budget = int(free * MEMORY_SHARE // (row['worker_memory_mb'] * 2**20 or 1))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                folder.parent.rmdir()
            except OSError:
                pass
    measured = [row for row in rows if 'bundles_per_second' in row]
    best = max(row['bundles_per_second'] for row in measured)
    workers = min(row['workers'] for row in measured if row['bundles_per_second'] >= best * CLOSE_SHARE)
    return {'workers': workers, 'rows': rows, 'day': task['start'], 'strategies': list(strategies),
            'measured_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'machine': pc}


def save(result, path=None):
    """Keep this PC's measurement in the backtest settings, beside other PCs' ones."""
    path = Path(path or ROOT / 'Part2/event_backtest.json')
    data = json.loads(path.read_text('utf-8-sig')) if path.exists() else {}
    profiles = dict(data.get('worker_profiles') or {})
    pc = result['machine']
    profiles[pc['key']] = {'workers': result['workers'], 'measured_at': result['measured_at'], 'day': result['day'],
                           'strategies': result['strategies'], 'rows': result['rows'],
                           **{key: pc[key] for key in ('processor', 'physical', 'logical', 'memory_gb')}}
    data['worker_profiles'] = profiles
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return profiles[pc['key']]
