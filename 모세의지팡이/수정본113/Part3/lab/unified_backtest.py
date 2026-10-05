"""Web transport for Part2's existing scenario, CLI, progress and result contracts.

No strategy or replay calculation occurs here. A job is only a Part2 subprocess.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from . import catalog, storage

ROOT = catalog.ROOT.parent
PART2 = ROOT / 'Part2'
PROGRAM = ROOT / 'Part1' / 'program'
JOBS = {}


def _part2():
    for path in (PART2, PROGRAM):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from event_backtest import settings, ui_model
    from event_backtest.progress_view import ProgressView
    return settings, ui_model, ProgressView


def warehouse():
    settings, _, _ = _part2()
    selected = storage.connections().get('warehouse') or settings.settings()['warehouse']
    return storage.resolve_warehouse(selected, ROOT)


def _default_symbol(symbols):
    for prefixes in (('XAU', 'GOLD'), ('USTEC', 'NAS100', 'NASDAQ', 'US100', 'NDX')):
        for symbol in symbols:
            if symbol.upper().startswith(prefixes):
                return symbol
    return symbols[0] if symbols else ''


def options():
    settings, ui_model, _ = _part2()
    current = ui_model.load()
    from event_backtest.virtual_contract import normalize_virtual_entry
    from indicator_facts import MT5_TIMEFRAMES
    from strategy_recipe.registry import list_presets
    presets=list_presets('Part2')
    import oz_profiles
    from . import unified_live
    store = warehouse()
    captures = store / 'captures'
    from event_backtest.capture_layout import folder_symbol
    symbols = set()
    if captures.is_dir():
        for folder in captures.iterdir():
            if not folder.is_dir():
                continue
            try:
                symbol = folder_symbol(folder.name)
            except ValueError:
                continue
            if settings.SYMBOL_FORM.fullmatch(symbol):
                symbols.add(symbol)
    symbols = sorted(symbols)
    start, end = settings.default_dates()
    return {**unified_live.strategy_settings_metadata(list(presets)),
            'symbol': _default_symbol(symbols), 'symbols': symbols,
            'start': start, 'end': end, 'mode': 'BAR', 'warehouse_set': store.exists(),
            'specials': list(presets),
            'selected_specials': [name for name, row in current.get('specials', {}).items() if row.get('enabled')],
            'special_settings': current.get('specials', {}),
            'trigger_choices': [oz_profiles.profile_label(vm, tm) for vm, tm in oz_profiles.PROFILE_KEYS],
            'default_triggers': {name: unified_live.control()['special_code_default_trigger'](name)
                                 for name in presets},
            'watch_text': current.get('watch', {}).get('text', ''),
            'watch_chat_id': current.get('watch', {}).get('chat_id') or 'BACKTEST',
            'result_mode': current.get('result_mode', 'ALERT_ONLY'),
            'spread_points': current.get('spread_points', {}),
            'virtual_entry': normalize_virtual_entry(current.get('virtual_entry')),
            'virtual_timeframes': list(MT5_TIMEFRAMES)}


def _scenario(request):
    if not isinstance(request, dict):
        raise ValueError('백테스트 입력 형식을 확인하세요.')
    available_only = _available_only(request)
    settings, ui_model, _ = _part2()
    target = request.get('target_mode', 'SPECIAL')
    if target not in ('SPECIAL', 'WATCH'):
        raise ValueError('SPECIAL 또는 WATCH를 선택하세요.')
    result_mode = request.get('result_mode', 'ALERT_ONLY')
    if result_mode not in ('ALERT_ONLY', 'VIRTUAL_ENTRY'):
        raise ValueError('알림 온리 또는 가상 진입을 선택하세요.')
    chosen = request.get('specials', [])
    if not isinstance(chosen, list) or any(not isinstance(x, str) for x in chosen):
        raise ValueError('전략 선택 형식이 올바르지 않습니다.')
    from event_selection import strategy_dependencies
    from strategy_recipe.registry import list_presets
    registered = {name: row for name, row in strategy_dependencies().items() if name in list_presets('Part2')}
    if set(chosen) - set(registered):
        raise ValueError('지원하지 않는 SPECIAL을 선택했습니다.')
    provided = request.get('special_settings')
    if provided is not None and (not isinstance(provided, dict) or set(provided) - set(registered)):
        raise ValueError('SPECIAL 설정 형식이 올바르지 않습니다.')
    if provided is not None:
        from .unified_live import _validate_time_filters
        import oz_profiles
        allowed = {oz_profiles.profile_label(vm, tm) for vm, tm in oz_profiles.PROFILE_KEYS}
        for name, row in provided.items():
            if not isinstance(row, dict) or type(row.get('enabled')) is not bool:
                raise ValueError(name + ' 실행 여부를 확인하세요.')
            trigger = row.get('trigger')
            if trigger is not None:
                trigger = catalog.PROFILES.canonical_profile_text(trigger)
                row['trigger'] = trigger
            if trigger is not None and trigger not in allowed:
                raise ValueError(name + ' OZ 트리거가 현재 계약에 없습니다.')
            row['time_filters'] = _validate_time_filters(row.get('time_filters'))
    ui = ui_model.load()
    for name in chosen:
        row=(provided or {}).get(name) or ui.get('specials',{}).get(name) or {}
        if row.get('load_error'):
            raise ValueError(name + ': 저장 설정을 불러오지 못했습니다. 현재 프로필을 명시적으로 선택하세요.')
    ui['target_mode'] = target
    ui['specials'] = {name: {'enabled': True,
                             'trigger': ((provided or {}).get(name) or ui.get('specials', {}).get(name) or {}).get('trigger'),
                             'time_filters': ((provided or {}).get(name) or ui.get('specials', {}).get(name) or {}).get('time_filters')}
                      for name in dict.fromkeys(chosen)}
    chat_id = request.get('watch_chat_id', 'BACKTEST')
    if not isinstance(chat_id, str) or len(chat_id) > 128 or any(ord(c) < 32 for c in chat_id):
        raise ValueError('WATCH 수신자 ID를 확인하세요.')
    ui['watch'] = {'text': str(request.get('watch_text', '')).strip(), 'chat_id': chat_id.strip() or 'BACKTEST'}
    ui['result_mode'] = result_mode
    virtual_active = result_mode == 'VIRTUAL_ENTRY' and not bool(request.get('build_only'))
    if virtual_active:
        from event_backtest.virtual_contract import normalize_virtual_entry
        ui['virtual_entry'] = normalize_virtual_entry(request.get('virtual_entry', ui.get('virtual_entry')))
    try:
        spread = float(request.get('spread_points', 0))
    except (ValueError, TypeError):
        raise ValueError('스프레드는 0 이상의 숫자여야 합니다.') from None
    import math
    if not math.isfinite(spread) or spread < 0:
        raise ValueError('스프레드는 0 이상의 유한한 수여야 합니다.')
    ui['spread_points'] = {str(request.get('symbol', '')): spread}
    common = {key: str(request.get(key, '')).strip() for key in ('symbol', 'start', 'end', 'mode')}
    common['available_only'] = available_only
    if bool(request.get('build_only')):
        current = settings.settings()
        scenario = settings.scenario(defaults={k: current[k] for k in
            ('cores', 'work_size', 'capture_start', 'oz_evaluation', 'overlap_trading_days')},
            **common, strategies=[], commands=[], build_only=True, result_mode=result_mode,
            spread_points=ui['spread_points'])
    else:
        scenario = ui_model.make_scenario(ui, common)
    if virtual_active:
        ui_model.save_virtual_entry(ui['virtual_entry'])
    return scenario


def _available_only(request):
    value = request.get('available_only')
    if value is None:
        value = False
    if type(value) is not bool:
        raise ValueError('보유 데이터만 사용 여부는 참/거짓 값이어야 합니다.')
    if value and (request.get('build_only') or request.get('rebuild')):
        raise ValueError('보유 데이터만 사용하는 백테스트와 데이터 구축·재구축을 함께 선택할 수 없습니다.')
    return value


def start(request):
    from . import backtest_jobs
    scenario = _scenario(request)
    return backtest_jobs.start({'kind': 'normal', 'scenario': scenario,
        'warehouse': warehouse(), 'project_root': ROOT,
        'python_executable': storage.connections().get('python_executable') or sys.executable,
        'rebuild': bool(request.get('rebuild')), 'plan_only': bool(request.get('plan_only')),
        'auto_run': request.get('auto_run', True)})


def _job(identifier):
    # Historical dashboard fixtures can inject a read-only result object.
    # Production start never writes this mapping; warehouse records own jobs.
    if identifier in JOBS:
        return JOBS[identifier]
    from . import backtest_jobs
    return backtest_jobs.get(identifier)


def confirm(identifier, plan_revision=None):
    from . import backtest_jobs
    return backtest_jobs.confirm(identifier, plan_revision=plan_revision)


def stop(identifier, *, force=False):
    from . import backtest_jobs
    return backtest_jobs.stop(identifier, force=force)


def status(identifier):
    from . import backtest_jobs
    return backtest_jobs.status(identifier)


def recent(limit=20):
    from . import backtest_jobs
    return backtest_jobs.recent(limit)


def delete_selected(identifiers):
    from . import backtest_jobs
    return backtest_jobs.delete_selected(identifiers)


def reconnect(identifier):
    from . import backtest_jobs
    return backtest_jobs.reconnect(identifier)


def log(identifier):
    from .log_tail import read_tail
    folder = _job(identifier)['folder']
    path = folder / 'console.log'
    if not path.is_file():
        path = folder / 'progress.jsonl'
    return {'text': read_tail(path), 'available': path.is_file()}


def result(identifier):
    job = _job(identifier)
    files = _result_files(job)
    data = files.data
    analytics, analytics_status, analytics_message = files.analytics()
    alerts = []
    warnings = list(data.get('warnings') or [])
    relative = data.get('alerts_csv')
    if relative:
        settings, _, _ = _part2()
        try:
            with settings.warehouse_path(job['warehouse'], relative).open(encoding='utf-8-sig', newline='') as handle:
                alerts = list(__import__('itertools').islice(csv.DictReader(handle), 200))
        except (ValueError, OSError):
            warnings.append('알림 CSV 미리보기를 읽지 못했습니다. 원본 파일의 경로를 확인하세요.')
    virtual = data.get('virtual_entry') or {}
    if not isinstance(virtual, dict):
        virtual = {}
    return {'run_id': identifier, 'status': data.get('status_label', data.get('status', '완료')),
            'processed_periods': data.get('processed_periods', []),
            'period_adjustment': data.get('period_adjustment'),
            'excluded_periods': data.get('excluded_periods', []),
            'available_periods': data.get('available_periods', []),
            'alert_statistics': data.get('alert_statistics'),
            'virtual_entry': {'summary': virtual.get('summary', []),
                              'cancelled': virtual.get('cancelled', False)},
            'pieces': data.get('pieces', []), 'warnings': warnings,
            'alerts_preview': alerts, 'alerts_csv': relative,
            'summary_csv': files.paths['summary'], 'trades_csv': files.paths['trades'],
            'result_mode': data.get('result_mode'), 'analytics': analytics,
            'analytics_status': analytics_status, 'analytics_message': analytics_message,
            'analytics_path': next((f['path'] for f in files.describe() if f['kind'] == 'analytics'), None),
            'files': files.describe()}


def _result_files(job):
    from .backtest_results import ResultFiles
    settings, _, _ = _part2()
    return ResultFiles(job['warehouse'], job['folder'], settings.warehouse_path)


def trades(identifier, rr=None, offset=0, limit=100):
    return _result_files(_job(identifier)).trades(rr, offset, limit)


def download(identifier, kind):
    return _result_files(_job(identifier)).artifact(kind)
