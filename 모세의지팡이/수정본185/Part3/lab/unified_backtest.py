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
    from event_backtest.base_frames import LADDER
    from event_backtest.virtual_defaults import strategy_profile
    from strategy_recipe.registry import list_presets, skipped_builtins
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
            'catalog_errors': [row['notice'] for row in skipped_builtins()],
            'selected_specials': [name for name, row in current.get('specials', {}).items() if row.get('enabled')],
            'special_settings': current.get('specials', {}),
            'trigger_choices': [oz_profiles.profile_label(vm, tm) for vm, tm in oz_profiles.PROFILE_KEYS],
            'default_triggers': {name: unified_live.control()['special_code_default_trigger'](name)
                                 for name in presets},
            'watch_text': current.get('watch', {}).get('text', ''),
            'watch_chat_id': current.get('watch', {}).get('chat_id') or 'BACKTEST',
            'result_mode': current.get('result_mode', 'ALERT_ONLY'),
            'spread_points': current.get('spread_points', {}),
            'commission': current.get('commission', {}),
            'virtual_entry': current.get('virtual_entry'),
            'virtual_entry_target': current.get('virtual_entry_target'),
            'virtual_entry_strategy': current.get('virtual_entry_strategy'),
            'virtual_profiles': {name: strategy_profile(name) for name in presets},
            # Base frame -> [base, middle, upper, top]: every frame of the panel follows the base frame.
            'virtual_ladder': {base: list(row) for base, row in LADDER.items()}}


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
    if virtual_active and target == 'SPECIAL' and len(dict.fromkeys(chosen)) != 1:
        raise ValueError('가상 진입은 전략 1개만 선택할 수 있습니다.')
    ui['virtual_entry'] = request.get('virtual_entry') if virtual_active else None
    ui['virtual_strategy'] = request.get('virtual_strategy') if virtual_active else None
    # Several base frames compared in one replay: one result each (Part2 checks the list).
    ui['virtual_bases'] = (request.get('virtual_bases') or []) if virtual_active else []
    # Several OZ triggers of the one strategy, likewise (수정본171), in either result mode.
    ui['trigger_variants'] = ((request.get('trigger_variants') or [])
                              if target == 'SPECIAL' and not bool(request.get('build_only')) else [])
    spread = cost(request, 'spread_points', '스프레드')
    ui['spread_points'] = {str(request.get('symbol', '')): spread}
    # 수정본185: dollars per lot, round trip; Part2 turns it into a price with the symbol's 1-lot size.
    commission = cost(request, 'commission', '수수료')
    ui['commission'] = {str(request.get('symbol', '')): commission}
    common = {key: str(request.get(key, '')).strip() for key in ('symbol', 'start', 'end', 'mode')}
    common['available_only'] = available_only
    if bool(request.get('build_only')):
        current = settings.settings()
        scenario = settings.scenario(defaults={k: current[k] for k in
            ('cores', 'capture_start', 'oz_evaluation', 'overlap_trading_days')},
            **common, strategies=[], commands=[], build_only=True, result_mode=result_mode,
            spread_points=ui['spread_points'])
    else:
        scenario = ui_model.make_scenario(ui, common)
    if virtual_active:
        ui_model.save_virtual_entry(scenario['virtual_entry'], scenario['strategies'][0],
                                    strategy=scenario['virtual_strategy'])
    return scenario


def cost(request, key, label):
    """A virtual-entry cost of the request: 0 or more, 0 when it is not given."""
    import math
    try:
        value = float(request.get(key, 0))
    except (ValueError, TypeError):
        raise ValueError(label + '는 0 이상의 숫자여야 합니다.') from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(label + '는 0 이상의 유한한 수여야 합니다.')
    return value


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


def delete_leftovers(identifiers):
    from . import backtest_jobs
    return backtest_jobs.delete_leftovers(identifiers)


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
    analysis, analytics_status, analytics_message = files.analysis()
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
            'result_mode': data.get('result_mode'), 'analysis': analysis,
            'analytics_status': analytics_status, 'analytics_message': analytics_message,
            'analytics_path': next((f['path'] for f in files.describe() if f['kind'] == 'analytics'), None),
            'files': files.describe(), 'compared_runs': _compared_runs(job, data),
            'base_frame': _tested_frame(data.get('scenario') or {}) if _together(data) else None,
            'trigger': _tested_trigger(data) if _together(data) else None,
            'strategy': _tested_strategy(data) if _together(data) else None}


def _json_file(path):
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def tested(identifier):
    """What a backtest ran with, as it was when it started (수정본169).

    Part2 keeps it with the run (tested_settings): the strategy conditions it replayed, its OZ trigger
    and alert times and the configuration values it read. A running run has it in its folder. Runs
    from before 169 kept their request and the trigger and alert times they applied, not their
    recipe conditions.
    """
    _part2()
    from event_backtest.tested_settings import FILE
    from special_settings_model import SESSIONS, time_summary
    job = _job(identifier)
    data = _json_file(job['folder'] / 'result.json')
    kept = data.get('tested_settings')
    if not isinstance(kept, dict):
        kept = _json_file(job['folder'] / FILE) or None
    scenario = data.get('scenario') or job.get('scenario') or {}
    applied = data.get('applied_special_settings') or {}
    if kept:
        config = dict(kept.get('config') or {})
    else:
        config = {'WONBI_SIGMA': data['wonbi_sigma']} if data.get('wonbi_sigma') is not None else {}
        config.update(next(iter(applied.values()), {}).get('session_config') or {})
    strategies = []
    for row in _tested_strategies(kept, scenario, applied):
        if row['time_source'] is not None:
            override = row['time_source'] == 'override'
            row['times'] = time_summary(row['time_filters'] if override else None,
                                        0 if override else row['time_filters'], config)
        strategies.append({key: value for key, value in row.items() if key not in ('time_filters', 'time_source', 'recipe_sha256')})
    from event_backtest.virtual_contract import SCHEMA
    symbol = scenario.get('symbol')
    virtual = kept.get('virtual_entry') if kept else scenario.get('virtual_entry')
    # The recordings' broker clock the run read them in (수정본172); older runs kept none.
    recorded = kept.get('server_time') if kept else None
    if recorded:
        from server_time import ServerTime
        recorded = ServerTime.from_record(recorded).label()
    return {'run_id': identifier, 'kept': kept is not None, 'strategies': strategies, 'server_time': recorded or None,
            'commands': [str(c.get('text', '')) for c in scenario.get('commands') or [] if isinstance(c, dict)],
            'result_mode': scenario.get('result_mode') or 'ALERT_ONLY', 'virtual_entry': virtual,
            # An older policy form than the virtual entry panel reads is shown as saved.
            'virtual_entry_current': not isinstance(virtual, dict) or virtual.get('schema') == SCHEMA,
            'symbol': symbol, 'start': scenario.get('start'), 'end': scenario.get('end'), 'mode': scenario.get('mode'),
            'spread_points': (scenario.get('spread_points') or {}).get(symbol),
            'commission': (scenario.get('commission') or {}).get(symbol),
            'overlap_trading_days': scenario.get('overlap_trading_days'),
            'available_only': bool(scenario.get('available_only')), 'oz_evaluation': scenario.get('oz_evaluation'),
            'config': config, 'session_labels': dict(SESSIONS)}


def _tested_strategies(kept, scenario, applied):
    """Each tested strategy, and whether its recipe file is the same now ('same', 'changed', 'missing')."""
    from event_backtest.base_frames import recipe_entry
    from event_backtest.settings import digest
    from event_backtest.virtual_defaults import strategy_frames
    rows = []
    if kept:
        for name, row in (kept.get('strategies') or {}).items():
            try:
                now = 'same' if digest(recipe_entry(name)) == row.get('recipe_sha256') else 'changed'
            except ValueError:
                now = 'missing'
            rows.append({'name': name, **row, 'now': now})
        return rows
    # Before 169: the request and what Part2 applied (applied_special_settings) only.
    names = list(applied) or [name for name in scenario.get('strategies') or [] if name != 'WATCH']
    first = (scenario.get('strategies') or [None])[0]
    for name in names:
        edited = scenario.get('virtual_strategy') is not None and name == first
        conditions = scenario['virtual_strategy'] if edited else None
        timing = applied.get(name) or {}
        rows.append({'name': name, 'title': None, 'folder': None, 'conditions': conditions, 'edited': edited,
                     'base_frame': _tested_frame(scenario) if name == first else None,
                     'frames': strategy_frames(conditions) if conditions else None,
                     'trigger': timing.get('trigger'), 'time_filters': timing.get('final_alert_time_filters'),
                     'time_source': timing.get('time_source'), 'now': None})
    return rows


def _tested_frame(scenario):
    """The frame a virtual-entry run was tested on, the recipe's own frame (SIGNAL) included."""
    from .backtest_jobs import _base_frame
    frame = _base_frame(scenario)
    if frame is None and (scenario.get('virtual_entry') or {}).get('tf') == 'SIGNAL':
        _part2()
        from event_backtest.virtual_defaults import strategy_profile
        try:
            frame = strategy_profile((scenario.get('strategies') or [None])[0])['base']
        except (ValueError, KeyError, TypeError):
            frame = None
    return frame


def _tested_trigger(data):
    """The OZ trigger a run of one strategy applied, its strategy's own included (수정본171)."""
    strategies = (data.get('scenario') or {}).get('strategies') or []
    if len(strategies) != 1:
        return None
    return ((data.get('applied_special_settings') or {}).get(strategies[0]) or {}).get('trigger')


def _tested_strategy(data):
    """The strategy a run of one strategy replayed; the runs of a lanes request differ in it (수정본184)."""
    strategies = (data.get('scenario') or {}).get('strategies') or []
    return strategies[0] if len(strategies) == 1 else None


def _together(data):
    """The other runs of the same request (수정본163); before 163 only those replayed together were kept."""
    return data.get('tested_with') or data.get('replayed_with') or []


def _compared_runs(job, data):
    """The other base frames (수정본161) or OZ triggers (수정본171) tested in the same request, each saved
    as its own run.

    A run that reused a finished run's alerts is one of them too (수정본163).
    """
    rows = []
    for run_id in _together(data):
        try:
            other = json.loads((job['folder'].parent / str(run_id) / 'result.json').read_text('utf-8'))
        except (OSError, ValueError):
            continue
        rows.append({'run_id': run_id, 'base_frame': _tested_frame(other.get('scenario') or {}),
                     'trigger': _tested_trigger(other), 'strategy': _tested_strategy(other)})
    return rows


def _result_files(job):
    from .backtest_results import ResultFiles
    settings, _, _ = _part2()
    return ResultFiles(job['warehouse'], job['folder'], settings.warehouse_path)


def analysis(identifier, period='all', rr=None, table=False):
    """The result screen for another period or RR of a finished run (수정본165); table=True gives the
    period's RR table alone (수정본170)."""
    shown, status, message = _result_files(_job(identifier)).analysis(period, rr, table=table)
    if shown is None:
        raise ValueError(message)
    return shown


def trades(identifier, rr=None, offset=0, limit=100, period=None):
    return _result_files(_job(identifier)).trades(rr, offset, limit, period)


def download(identifier, kind):
    return _result_files(_job(identifier)).artifact(kind)
