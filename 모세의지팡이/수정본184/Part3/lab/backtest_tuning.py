"""The settings screen's worker-count measurement: one Part2 `tune` subprocess at a time (수정본167).

Nothing is measured or chosen here. The subprocess replays, measures and saves the count for this
PC in Part2/event_backtest.json; this module starts it and relays its progress lines.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from . import storage, unified_backtest

_LOCK = threading.Lock()
_STATE = {'running': False, 'step': 0, 'steps': 0, 'workers': None, 'result': None, 'error': None}
LIVE_RUNNING = '실시간을 끈 뒤 다시 누르세요.'
BACKTEST_RUNNING = '백테스트가 끝난 뒤 다시 누르세요.'


def _part2():
    unified_backtest._part2()
    from event_backtest import worker_tuning
    import process_priority
    return worker_tuning, process_priority


def _request():
    """The symbol and strategies chosen on the backtest screen; SPECIAL2 when none is chosen."""
    options = unified_backtest.options()
    strategies = [name for name in options.get('selected_specials') or [] if name in options.get('specials', [])]
    if not strategies:
        presets = list(options.get('specials') or [])
        strategies = ['SPECIAL2'] if 'SPECIAL2' in presets else presets[:1]
    rows = options.get('special_settings') or {}
    triggers = {name: rows[name]['trigger'] for name in strategies if (rows.get(name) or {}).get('trigger')}
    return {'symbol': options.get('symbol') or 'XAUUSD+', 'strategies': strategies, 'triggers': triggers}


def _busy(warehouse):
    from event_backtest.warehouse_cleanup import warehouse_idle
    try:
        with warehouse_idle(warehouse):
            return False
    except OSError:
        return True


def status():
    worker_tuning, _ = _part2()
    with _LOCK:
        state = dict(_STATE)
    try:
        saved = worker_tuning.saved_profile()
    except (OSError, ValueError):
        saved = None
    physical, logical = worker_tuning.core_counts()
    return {**state, 'saved': saved, 'physical': physical, 'logical': logical}


def start():
    worker_tuning, process_priority = _part2()
    with _LOCK:
        if _STATE['running']:
            raise ValueError('이미 측정 중입니다.')
        if process_priority.live_running():
            raise ValueError(LIVE_RUNNING)
        warehouse = unified_backtest.warehouse()
        if _busy(warehouse):
            raise ValueError(BACKTEST_RUNNING)
        request = _request()
        if not request['strategies']:
            raise ValueError('측정할 전략이 없습니다.')
        handle, scenario = tempfile.mkstemp(prefix='moses-tune-', suffix='.json')
        with os.fdopen(handle, 'w', encoding='utf-8') as file:
            json.dump(request, file, ensure_ascii=False)
        python = storage.connections().get('python_executable') or sys.executable
        args = [python, '-B', '-X', 'utf8', '-m', 'event_backtest', 'tune', '--scenario', scenario,
                '--warehouse', str(warehouse)]
        try:
            process = subprocess.Popen(args, cwd=str(unified_backtest.PART2), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                       encoding='utf-8', errors='replace',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            Path(scenario).unlink(missing_ok=True)
            raise
        _STATE.update(running=True, step=0, steps=0, workers=None, result=None, error=None)
        threading.Thread(target=_follow, args=(process, scenario), daemon=True).start()
    return status()


def _follow(process, scenario):
    try:
        for line in process.stdout:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            event = row.get('event') if isinstance(row, dict) else None
            with _LOCK:
                if event == 'TUNE_PROGRESS':
                    _STATE.update(step=row.get('step', 0), steps=row.get('steps', 0), workers=row.get('workers'))
                elif event == 'COMPLETE':
                    _STATE['result'] = row.get('result')
                elif event == 'ERROR':
                    _STATE['error'] = str(row.get('message') or '측정에 실패했습니다.')
        process.wait()
    finally:
        with _LOCK:
            _STATE['running'] = False
            if _STATE['result'] is None and not _STATE['error']:
                _STATE['error'] = '측정을 끝내지 못했습니다.'
        Path(scenario).unlink(missing_ok=True)
