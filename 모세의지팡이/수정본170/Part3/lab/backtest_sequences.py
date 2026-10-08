"""Sequential orchestration only; each child uses the existing Backtest Job."""
from __future__ import annotations

import copy
import hashlib
import threading
import time
import uuid
from pathlib import Path

from . import backtest_jobs, storage, unified_backtest
from .ai.backtest_commands import execute

_guard = threading.RLock()
_running = {}


def start(commands, *, executor=None, status_reader=None, result_reader=None, validator=None, folder=None, background=True):
    executor = executor or execute
    status_reader = status_reader or unified_backtest.status
    result_reader = result_reader or unified_backtest.result
    identifier = uuid.uuid4().hex
    folder = Path(folder) if folder is not None else unified_backtest.warehouse() / 'runs' / 'sequences'
    folder.mkdir(parents=True, exist_ok=True)
    digests = {row['request']['filename']: hashlib.sha256(storage.generated_path(row['request']['filename']).read_bytes()).hexdigest()
               for row in commands if row['request']['target_mode'] == 'GENERATED'}
    record = {'sequence_id': identifier, 'phase': 'run', 'commands': copy.deepcopy(commands), 'strategy_digests': digests,
              'jobs': [], 'next_index': 0, 'cancel_requested': False, 'message': '', 'created_at': time.time()}
    state = {'record': record, 'path': folder / (identifier + '.json'), 'executor': executor,
             'status_reader': status_reader, 'result_reader': result_reader, 'validator': validator, 'lock': threading.RLock()}
    # Same publication gate as normal starts: shutdown cannot miss the first child.
    with backtest_jobs._accept_work():
        with _guard:
            _running[identifier] = state
        try:
            _next(state)
        except Exception:
            with _guard:
                _running.pop(identifier, None)
            raise
    if background:
        threading.Thread(target=_watch, args=(state,), daemon=True, name='backtest-sequence').start()
    return status(identifier)


def _save(state):
    storage.json_write(state['path'], state['record'])


def _next(state):
    record = state['record']
    command = record['commands'][record['next_index']]
    if state['validator']:
        state['validator']()
    for filename, expected in record['strategy_digests'].items():
        if hashlib.sha256(storage.generated_path(filename).read_bytes()).hexdigest() != expected:
            raise ValueError('확인한 생성 전략 파일이 변경되어 이후 백테스트를 중단했습니다.')
    result = state['executor'](copy.deepcopy(command))
    job_id = result.get('job_id')
    backtest_jobs._identifier(job_id)
    record['jobs'].append({'job_id': job_id, 'phase': result.get('phase', 'planning'),
                           'symbol': command['request']['symbol'], 'result_ready': False})
    record['next_index'] += 1
    _save(state)


def advance(state):
    """A saved, fully exited successful child is the only trigger for the next."""
    with state['lock']:
        record = state['record']
        if record['phase'] != 'run':
            return
        current = record['jobs'][-1]
        info = state['status_reader'](current['job_id'])
        current.update({key: info.get(key) for key in ('phase', 'active', 'result_ready', 'message')})
        if record['cancel_requested']:
            record['phase'] = 'cancelled'
        elif info.get('phase') in ('cancelled', 'error', 'interrupted', 'planned'):
            record['phase'] = info['phase']
            record['message'] = '앞선 작업이 완료되지 않아 다음 백테스트를 실행하지 않았습니다.'
        elif info.get('phase') == 'complete' and not info.get('active') and not info.get('result_ready'):
            record['phase'] = 'error'
            record['message'] = '완료된 작업의 저장 결과를 확인하지 못해 다음 실행을 중단했습니다.'
        elif info.get('phase') == 'complete' and not info.get('active') and info.get('result_ready'):
            result = state['result_reader'](current['job_id'])
            current['result'] = _summary(result)
            if record['next_index'] == len(record['commands']):
                record['phase'] = 'complete'
            else:
                # A close/stop cannot race publication of the next child.
                with backtest_jobs._accept_work():
                    if not record['cancel_requested']:
                        _next(state)
        _save(state)


def _summary(result):
    # Existing values only: no statistics or outcome calculation here.
    summary = {key: copy.deepcopy(result[key]) for key in
               ('alert_statistics', 'virtual_entry', 'result_mode') if key in result}
    # The whole period's RR table of the result screen (수정본165; before, the whole analytics.json).
    analysis = result.get('analysis') or {}
    summary['by_rr'] = {row['rr']: copy.deepcopy(row.get('summary') or {})
                        for row in analysis.get('rr_table') or [] if isinstance(row, dict) and 'rr' in row}
    return summary


def _watch(state):
    while state['record']['phase'] == 'run':
        try:
            advance(state)
        except Exception as exc:
            if backtest_jobs._closing and not state['record']['cancel_requested']:
                time.sleep(1)
                continue
            with state['lock']:
                state['record']['phase'] = 'error'
                state['record']['message'] = str(exc)
                _save(state)
            return
        time.sleep(1)


def status(identifier):
    backtest_jobs._identifier(identifier)
    with _guard:
        state = _running.get(identifier)
    if state:
        with state['lock']:
            return copy.deepcopy(state['record'])
    path = unified_backtest.warehouse() / 'runs' / 'sequences' / (identifier + '.json')
    import json
    record = json.loads(path.read_text('utf-8'))
    # A fresh process never silently resumes an old queue.
    if record['phase'] == 'run':
        record['phase'] = 'interrupted'
        record['message'] = '이전 실행의 순차 작업입니다. 이미 시작한 작업은 백테스트 목록에서 조회하세요.'
    return record


def stop(identifier):
    with _guard:
        state = _running.get(identifier)
    if not state:
        raise ValueError('현재 실행 중인 순차 작업이 없습니다.')
    with state['lock']:
        record = state['record']
        record['cancel_requested'] = True
        _save(state)
        current = record['jobs'][-1]
        result = unified_backtest.stop(current['job_id'])
        return {'ok': True, 'sequence_id': identifier, 'message': '진행 중 작업과 이후 순차 실행을 중지합니다.', 'result': result}


def begin_shutdown():
    with _guard:
        states = list(_running.values())
    # Called outside the job gate to avoid lock inversion with advance().
    for state in states:
        with state['lock']:
            if state['record']['phase'] == 'run':
                state['record']['cancel_requested'] = True
                state['record']['phase'] = 'cancelled'
                _save(state)
