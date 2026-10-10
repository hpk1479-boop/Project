"""Application shutdown policy; calculation engines keep their own stop logic."""
from __future__ import annotations

from . import backtest_jobs, unified_live
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import threading

_LOCK = threading.Lock()


def prepare_shutdown():
    backtest_jobs.begin_shutdown()


def cancel_shutdown():
    backtest_jobs.cancel_shutdown()
    unified_live.cancel_shutdown()


def shutdown(timeout=45, *, force_requested=None):
    with _LOCK:
        return _shutdown(timeout, force_requested=force_requested)


def force_shutdown(timeout=5):
    """Do not queue an explicit force request behind cooperative saving."""
    operations = (
        lambda: backtest_jobs.force_shutdown(timeout=timeout),
        unified_live.force_shutdown,
        lambda: _force_ai_service(timeout),
    )
    warnings, errors = [], []
    with ThreadPoolExecutor(max_workers=len(operations), thread_name_prefix='MOSES force') as pool:
        tasks = [pool.submit(operation) for operation in operations]
        for task in tasks:
            try:
                result = task.result()
                warnings.extend((result or {}).get('warnings', ()))
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        raise RuntimeError('강제 종료를 완료하지 못했습니다. ' + ' · '.join(errors))
    return {'ok': True, 'warnings': warnings}


def _force_ai_service(timeout):
    """Terminate only this installation's identity-verified AI service tree."""
    from common_ai.client import Client
    from common_ai.process_identity import identity
    from . import catalog, live_processes
    client = Client(catalog.ROOT.parent)
    try:
        record = client._read_record()
        if record is None:
            return {'ok': True}
        pid, created = int(record['pid']), str(record['created'])
        if pid == os.getpid():
            raise RuntimeError('AI 서비스 프로세스의 소유 정보를 확인하지 못했습니다.')
        api = live_processes.process_api()
        with api.hold_process_identity(pid, created) as active:
            if not active:
                return {'ok': True}
            result = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                capture_output=True, creationflags=live_processes.CREATE_NO_WINDOW,
                timeout=max(.1, timeout))
            if identity(pid) == created:
                raise RuntimeError('공통 AI 서비스를 강제로 종료하지 못했습니다.'
                                   + (' 종료 명령이 거절되었습니다.' if result.returncode else ''))
        return {'ok': True}
    finally:
        client.close()


def _shutdown(timeout, *, force_requested=None):
    from .ai.model_runtime import RUNTIME
    def check_choice():
        if force_requested is not None and force_requested.is_set():
            raise backtest_jobs.ForceShutdownRequested('강제 종료가 요청되었습니다.')

    try:
        check_choice()
        if force_requested is not None:
            backtests = backtest_jobs.shutdown(timeout=timeout, force_requested=force_requested)
        else:
            backtests = backtest_jobs.shutdown(timeout=timeout)
        check_choice()
        live = unified_live.shutdown()
        check_choice()
        from common_ai.client import Client
        from . import catalog
        client=Client(catalog.ROOT.parent)
        try:client.shutdown(timeout=timeout)
        finally:client.close()
        check_choice()
        # The common service owns the active model. Close any remaining direct
        # provider cache used by legacy callers and diagnostic tests as well.
        RUNTIME.shutdown(timeout=timeout)
        check_choice()
        warnings = list((backtests or {}).get('warnings', ()))
        warnings.extend((live or {}).get('warnings', ()))
        return {'ok': True, 'warnings': warnings}
    except (backtest_jobs.ShutdownPending, backtest_jobs.ForceShutdownRequested):
        raise
    except Exception:
        check_choice()
        cancel_shutdown()
        raise
