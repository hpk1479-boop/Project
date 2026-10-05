"""Headless web access to Part1 live control and diagnostics IPC.

Part1 owns process startup, SPECIAL selection, and status semantics. This
module does not import the legacy Tk application.
"""
from __future__ import annotations

import runpy
import sys
import threading
from pathlib import Path

from . import catalog

PART1 = catalog.ROOT.parent / 'Part1'
CONTROL = PART1 / 'live_control.py'
PROGRAM = PART1 / 'program'
MODULES = ('STAFF', 'ENGINE', 'OZ', 'SWEEP', 'FVG', 'INDICATOR', 'WATCH', 'KIM')
_lock = threading.RLock()
_control_cache = None
_closing = False


def control():
    global _control_cache
    with _lock:
        if _control_cache is None:
            if not CONTROL.is_file():
                raise ValueError('현재 Part1 라이브 제어 모듈을 찾지 못했습니다.')
            _control_cache = runpy.run_path(str(CONTROL), run_name='moses_web_live_bridge')
        return _control_cache


def _modules():
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    import module_diagnostics
    import module_status_state
    return module_diagnostics, module_status_state


def status(module='ALL', *, detail=None):
    diagnostics, ui = _modules()
    from strategy_recipe.registry import entries
    presets=entries('Part1')
    names=(*MODULES, *presets)
    if module not in (*names, 'ALL'):
        raise ValueError('모듈 선택을 확인하세요.')
    try:
        data = diagnostics.read_snapshot(PART1, module, detail=detail,
                                         modules=names if module == 'ALL' else None)
        error = None
    except (OSError, EOFError, TimeoutError, ValueError) as exc:
        data, error = None, str(exc)
    rows = {}
    for name in names:
        current = (data or {}).get('modules', {}).get(name, {})
        rows[name] = {'name': presets[name].get('name',name) if name in presets else ui.display_name(name),
                      'preset': name in presets, 'state': ui.display_state(name, data, error),
                      'errors': current.get('errors', 0), 'last': current.get('last'),
                      'monitoring': bool(current.get('monitoring', False))}
    lines = (data or {}).get('lines', ())
    return {'modules': rows, 'selected': module,
            'lines': [str(line[1]) for line in lines if isinstance(line, (list, tuple)) and len(line) > 1][-2000:],
            'detail_enabled': bool((data or {}).get('detail_enabled')),
            'pipe_connected': bool((data or {}).get('pipe_connected')),
            'enabled_specials': list((data or {}).get('enabled_specials', ())),
            'error': error}


def strategy_settings_metadata(names):
    """Expose Part1's read-only settings vocabulary without running strategies."""
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    from special_settings_model import SESSIONS
    from strategy_recipe.registry import default_settings, preset_entry
    from .unified_settings import live_session_times
    defaults = {}
    for name in names:
        defaults[name] = default_settings(name)[1]
    return {'session_labels': dict(SESSIONS),
            'session_times': live_session_times(SESSIONS),
            'default_time_filters': defaults,
            'strategy_names': {name:preset_entry(name).get('name',name) for name in names}}


def specials():
    owner = control()
    _modules()
    from strategy_recipe.registry import list_presets
    available = list(list_presets('Part1'))
    error = None
    try:
        saved = owner['load_special_settings']()
    except owner.get('SpecialSettingsError', ()) as exc:
        # A repair preview is never a runtime fallback or an automatic write.
        saved = {name: {'enabled': False} for name in available}
        error = str(exc)
    profiles = owner['load_oz_profiles']()
    choices = ([profiles.profile_label(vm, tm) for vm, tm in profiles.PROFILE_KEYS]
               if profiles is not None else [])
    metadata = strategy_settings_metadata(available)
    from strategy_recipe.user_catalog import generated_errors
    return {**metadata, 'items': {name: {'name': metadata['strategy_names'][name],
                             'enabled': bool((saved or {}).get(name, {}).get('enabled', not name.startswith('TEST_SPECIAL'))),
                             'trigger': (saved or {}).get(name, {}).get('trigger'),
                             'default_trigger': owner['special_code_default_trigger'](name),
                             'time_filters': (saved or {}).get(name, {}).get('time_filters'),
                             'default_time_filters': metadata['default_time_filters'][name],
                             'load_error': (saved or {}).get(name, {}).get('load_error')}
                      for name in available},
            'trigger_choices': choices, 'uses_code_defaults': saved is None,
            'settings_error': error, 'needs_recovery': error is not None,
            'catalog_errors': generated_errors(PART1.parent)}


def change_strategy_list(data):
    from .strategy_library import change_registration
    return change_registration(data)


def _validate_time_filters(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('거래시간은 세션별 설정이어야 합니다.')
    if str(PROGRAM) not in sys.path:
        sys.path.insert(0, str(PROGRAM))
    from special_settings_model import SESSIONS
    from special_time_slot import _hhmm
    if set(value) - set(SESSIONS):
        raise ValueError('지원하지 않는 거래시간 세션입니다.')
    output = {}
    for key, item in value.items():
        if not isinstance(item, dict) or type(item.get('enabled')) is not bool:
            raise ValueError('거래시간 체크값을 확인하세요.')
        row = {'enabled': item['enabled']}
        for field in ('start', 'end'):
            if item.get(field) is not None:
                row[field] = _hhmm(str(item[field]).replace(':', ''))
        output[key] = row
    return output


def save_specials(items, *, recover=False):
    if not isinstance(items, dict):
        raise ValueError('전략 설정 형식이 올바르지 않습니다.')
    owner = control()
    current = specials()
    if current.get('needs_recovery') and recover is not True:
        raise ValueError('전략 설정 파일이 손상되어 저장을 중단했습니다. 전략 설정을 새로고침하고 복구 저장을 확인하세요.')
    if set(items) != set(current['items']):
        raise ValueError('현재 등록된 전략 목록 전체를 제출해야 합니다.')
    allowed = set(current['trigger_choices'])
    clean = {}
    for name, value in items.items():
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError(name + ' 실행 여부를 확인하세요.')
        trigger = value.get('trigger')
        if trigger is not None:trigger = catalog.PROFILES.canonical_profile_text(trigger)
        if trigger is not None and trigger not in allowed:
            raise ValueError(name + ' OZ 트리거가 현재 계약에 없습니다.')
        clean[name] = {'enabled': value['enabled'], 'trigger': trigger,
                       'time_filters': _validate_time_filters(value.get('time_filters'))}
    with _lock:
        if _closing:
            raise ValueError('웹 창을 닫기 위해 엔진을 종료 중입니다.')
        owner['save_special_settings'](clean)
        running = [row for row in owner['list_live_engines']() if row['current_copy']]
        if not running:
            return {'ok': True, 'message': '전략 설정을 저장했습니다.', 'warnings': []}
        expected = [{'pid': row['pid'], 'created': row['created']} for row in running]
        try:
            result = start({'restart': True, 'expected_engines': expected})
            if not result['ok']:
                raise ValueError(result['message'])
        except Exception as exc:
            raise ValueError('설정은 저장했지만 라이브 엔진 재시작을 완료하지 못했습니다. ' + str(exc)) from exc
        return {**result, 'message': '전략 설정 저장 및 라이브 엔진 재시작 완료 · ' + result['message']}


def start(request=None):
    request = {} if request is None else request
    if not isinstance(request, dict):
        raise ValueError('라이브 실행 요청 형식을 확인하세요.')
    restart = request.get('restart', False)
    if type(restart) is not bool:
        raise ValueError('엔진 재시작 확인 값은 참/거짓이어야 합니다.')
    expected = request.get('expected_engines')
    if expected is not None and not restart:
        raise ValueError('엔진 재시작 확인 후 실행하세요.')
    from .live_processes import current_process_identity
    with _lock:
        if _closing:
            raise ValueError('웹 창을 닫기 위해 엔진을 종료 중입니다.')
        owner = control()
        try:
            result = owner['start_live'](restart=restart, expected_engines=expected,
                                           ui_owner=current_process_identity())
            ok, message = result
        except owner.get('EngineConflictError', ()) as exc:
            return {'ok': False, 'confirmation_required': True,
                    'engines': exc.engines, 'message': str(exc)}
    if not ok:
        raise ValueError(message)
    return {'ok': True, 'message': message, 'confirmation_required': False,
            'warnings': list(getattr(result, 'warnings', ()))}


def engines():
    return {'engines': control()['list_live_engines']()}


def stop():
    from .live_processes import stop_all_engines
    with _lock:
        return stop_all_engines()


def shutdown():
    """Serialize window shutdown with live startup and block subsequent starts."""
    global _closing
    from .live_processes import stop_all_engines
    with _lock:
        _closing = True
        try:
            return stop_all_engines()
        except Exception:
            _closing = False
            raise


def cancel_shutdown():
    """Restore normal controls when another runtime could not finish closing."""
    global _closing
    with _lock:
        _closing = False


def force_shutdown():
    """Interrupt graceful shutdown without waiting for its control lock."""
    global _closing
    from .live_processes import force_stop_engines
    _closing = True
    return force_stop_engines()
