"""Lanes (수정본184): several strategies × OZ triggers replayed together, from one plan file.

A plan file names the strategies (Part3 test strategies, or the shipped and own SPECIALs), the OZ triggers
to try on each, and the symbol, period and replay mode they all share. Every strategy × trigger is a
complete run of its own, as if run alone; the runs share only the reading of the recordings (Part2 lanes).

    plan    the runs, their groups and the recordings they use; runs nothing
    run     starts them as one request (groups of max_lanes, one after another) through the common job;
            the same plan file run again skips the runs that already finished
    status  how far the request is, and the time left once a group has finished
    stop    stops the request (the running group, and no other starts)
    report  the results of every finished run as tables

Everything is kept beside the plan file (<plan>.lanes.json, <plan>.report.md), so a caller only ever names
the plan file. Messages say what to do next, for an AI following the installed AGENTS.md.

Part3's AI (Gemini) starts the same plans: start_saved keeps its plan in the warehouse (runs/lanes/), and
job_report makes the same tables for any finished request by its job ID.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import re
import time

from . import storage

GENERATED = re.compile(r'Test_SPECIAL\d{3}')
KEYS = ('symbol', 'start', 'end', 'mode', 'result_mode', 'strategies', 'triggers', 'virtual_entries',
        'available_only', 'spread_points', 'max_lanes', 'cores', 'special_settings')
SAVED = 'runs/lanes'
REQUIRED = ('symbol', 'start', 'end', 'mode', 'result_mode', 'strategies')
MIN_TRADES = 30
EXAMPLE = 'AGENTS.md의 "여러 조합 한 번에 시험하기" 예시를 보세요.'


def record_path(plan_path):
    path = Path(plan_path)
    return path.with_name(path.stem + '.lanes.json')


def report_path(plan_path):
    path = Path(plan_path)
    return path.with_name(path.stem + '.report.md')


def _name(value):
    text = str(value).strip()
    return text[:-3] if text.endswith('.py') else text


def normalize(data):
    """The checked plan: every field the run uses, with its default."""
    if not isinstance(data, dict):
        raise ValueError('계획 파일은 JSON 객체 하나여야 합니다. ' + EXAMPLE)
    extra = sorted(set(data) - set(KEYS))
    if extra:
        raise ValueError('계획 파일에 모르는 칸이 있습니다: ' + ', '.join(extra) + '. 쓸 수 있는 칸: ' + ', '.join(KEYS))
    missing = [key for key in REQUIRED if data.get(key) in (None, '', [])]
    if missing:
        raise ValueError('계획 파일에 꼭 필요한 칸이 비었습니다: ' + ', '.join(missing) + '. ' + EXAMPLE)
    names = data['strategies']
    if not isinstance(names, list) or any(not isinstance(item, str) or not item.strip() for item in names):
        raise ValueError('strategies는 전략 이름 목록입니다. 예: ["Test_SPECIAL005.py", "Test_SPECIAL006.py"] 또는 ["SPECIAL2", "SPECIAL5"]')
    names = [_name(item) for item in names]
    if len(set(names)) != len(names):
        raise ValueError('strategies에 같은 전략이 두 번 있습니다. 하나만 남기세요.')
    kinds = {bool(GENERATED.fullmatch(name)) for name in names}
    if len(kinds) > 1:
        raise ValueError('Part3 시험 전략(Test_SPECIAL)과 스페셜·내 전략은 한 계획 파일에 섞을 수 없습니다. 계획 파일을 둘로 나누세요.')
    triggers = data.get('triggers') or []
    if not isinstance(triggers, list) or any(not isinstance(item, str) or not item.strip() for item in triggers):
        raise ValueError('triggers는 올존 트리거 이름 목록입니다. 예: ["올존", "무지성 올존", "브레이커 올존", "무지성 브레이커 올존"]')
    if data['result_mode'] not in ('ALERT_ONLY', 'VIRTUAL_ENTRY'):
        raise ValueError('result_mode는 ALERT_ONLY(알림만) 또는 VIRTUAL_ENTRY(가상진입)입니다.')
    if data['mode'] not in ('BAR', 'EVENT', 'TICK'):
        raise ValueError('mode는 BAR / EVENT / TICK입니다. 데이터창고에 녹화가 있는 모드를 쓰세요(lanes plan이 알려 줍니다).')
    available = data.get('available_only', True)
    if type(available) is not bool:
        raise ValueError('available_only는 true/false입니다. 새 녹화를 만들지 않으려면 true로 두세요.')
    spread = data.get('spread_points', 0)
    if isinstance(spread, bool) or not isinstance(spread, (int, float)) or not math.isfinite(spread) or spread < 0:
        raise ValueError('spread_points는 0 이상의 숫자입니다(그 종목의 포인트 단위, 예: 금 20 = 0.2달러).')
    entries = data.get('virtual_entries') or {}
    if not isinstance(entries, dict) or any(not isinstance(value, dict) for value in entries.values()):
        raise ValueError('virtual_entries는 {"전략 이름": 가상진입 정책 객체}입니다.')
    entries = {_name(key): value for key, value in entries.items()}
    unknown = sorted(set(entries) - set(names))
    if unknown:
        raise ValueError('virtual_entries에 strategies에 없는 전략이 있습니다: ' + ', '.join(unknown))
    for key in ('max_lanes', 'cores'):
        if data.get(key) is not None and (type(data[key]) is not int or data[key] < 1):
            raise ValueError(key + '는 1 이상의 정수입니다.')
    own = _special_settings(data.get('special_settings'), names, kinds == {True})
    return {'symbol': str(data['symbol']).strip(), 'start': str(data['start']).strip(), 'end': str(data['end']).strip(),
            'mode': data['mode'], 'result_mode': data['result_mode'], 'strategies': names,
            'triggers': [item.strip() for item in triggers], 'virtual_entries': entries, 'available_only': available,
            'spread_points': float(spread), 'max_lanes': data.get('max_lanes'), 'cores': data.get('cores'),
            'special_settings': own, 'generated': kinds == {True}}


def _special_settings(value, names, generated):
    """A strategy's own trigger and trading time for this plan, as the screen saves them; a strategy left out
    runs its recipe's. Part3's AI fills it from the screen, so a lane is that strategy's screen run alone."""
    if not value:
        return {}
    if not isinstance(value, dict) or any(not isinstance(row, dict) or set(row) - {'trigger', 'time_filters'}
                                          for row in value.values()):
        raise ValueError('special_settings는 {"전략 이름": {"trigger": 트리거, "time_filters": 거래시간}}입니다. 보통은 빼 두세요(레시피 값).')
    if generated:
        raise ValueError('Part3 시험 전략은 레시피의 트리거·거래시간으로 돕니다. special_settings를 빼세요.')
    unknown = sorted(set(map(_name, value)) - set(names))
    if unknown:
        raise ValueError('special_settings에 strategies에 없는 전략이 있습니다: ' + ', '.join(unknown))
    from .unified_live import _validate_time_filters
    own = {}
    for key, row in value.items():
        trigger = row.get('trigger')
        if trigger is not None and (not isinstance(trigger, str) or not trigger.strip()):
            raise ValueError('special_settings의 trigger는 올존 트리거 이름입니다.')
        own[_name(key)] = {'trigger': trigger.strip() if trigger else None,
                           'time_filters': _validate_time_filters(row.get('time_filters'))}
    return own


def read_plan(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ValueError('계획 파일을 읽지 못했습니다: ' + path.name + ' (' + type(exc).__name__ + ')') from None
    return normalize(data)


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def every_lane(plan):
    """Every strategy × trigger of the plan, in its order: one run each. Without triggers, a strategy's own."""
    own = plan.get('special_settings') or {}
    return [{'strategy': name, 'trigger': trigger or (own.get(name) or {}).get('trigger')}
            for name in plan['strategies'] for trigger in (plan['triggers'] or [None])]


def _root():
    value = storage.project_path()
    if not value:
        raise ValueError('연결 설정에서 프로젝트 위치를 먼저 지정하세요.')
    return Path(value)


def _part2(root):
    from .backtest_adapters import _part2 as load
    return load(root)


def _effective(lane):
    """A lane as a key: its strategy and the trigger it actually runs (the strategy's own when none is named)."""
    import oz_profiles
    from strategy_recipe.registry import default_settings
    trigger = lane.get('trigger')
    if trigger is not None:
        trigger = oz_profiles.canonical_profile_text(trigger)
    return lane['strategy'], trigger or default_settings(lane['strategy'])[0]


def build(plan, lanes, root):
    """The Part2 scenario of these lanes (checked there), and the job kind and adapter that run it."""
    settings = _part2(root)
    current = settings.settings()
    names = list(dict.fromkeys(lane['strategy'] for lane in lanes))
    adapter = {}
    if plan['generated']:
        files = []
        for name in names:
            try:
                storage.reopen(name + '.py')
                files.append(storage.generated_path(name + '.py'))
            except (FileNotFoundError, ValueError):
                raise ValueError(f'시험 전략 {name}.py를 찾지 못했습니다. Part3\\cli.py list로 이름을 확인하세요.') from None
        adapter = {'filename': files[0].name, 'filenames': [path.name for path in files],
                   'strategy_sha256': hashlib.sha256(files[0].read_bytes()).hexdigest(),
                   'strategy_sha256s': [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]}
    if plan['cores'] is not None:
        adapter['cores'] = plan['cores']
    if plan['max_lanes'] is not None:
        adapter['max_lanes'] = plan['max_lanes']
    entries = {name: policy for name, policy in plan['virtual_entries'].items() if name in names}
    times = {name: row['time_filters'] for name, row in (plan.get('special_settings') or {}).items()
             if name in names and row.get('time_filters') is not None}
    s = settings.scenario(
        defaults={key: current[key] for key in ('cores', 'capture_start', 'overlap_trading_days', 'oz_evaluation')},
        symbol=plan['symbol'], start=plan['start'], end=plan['end'], mode=plan['mode'],
        available_only=plan['available_only'], result_mode=plan['result_mode'],
        spread_points={plan['symbol']: plan['spread_points']}, build_only=False, cores=plan['cores'],
        oz_evaluation='all' if plan['generated'] else None, lanes=lanes, virtual_entries=entries or None,
        special_time_filters=times or None)
    return s, ('generated' if plan['generated'] else 'normal'), adapter


def _context(warehouse=None):
    from . import integration
    root = _root()
    _part2(root)            # Part2 and Part1/program on the path: the scenario and trigger rules live there
    store = integration.warehouse({'warehouse': warehouse or ''}, root)
    return {'warehouse': store, 'project_root': root, 'python_executable': integration._python()}


def _load_record(plan_path):
    path = record_path(plan_path)
    if not path.is_file():
        return {'version': 1, 'plans': {}}
    try:
        record = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        raise ValueError('기록 파일 ' + path.name + '을 읽지 못했습니다. 계획 파일을 새 이름으로 저장해 다시 시작하세요.') from None
    if not isinstance(record, dict) or not isinstance(record.get('plans'), dict):
        raise ValueError('기록 파일 ' + path.name + '의 형식이 다릅니다. 계획 파일을 새 이름으로 저장해 다시 시작하세요.')
    return record


def _requests(plan_path, plan):
    return _load_record(plan_path)['plans'].get(fingerprint(plan), {}).get('requests', [])


def _lane_file(store, job_id):
    path = Path(store) / 'runs' / job_id / 'lanes.json'
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None


def _job(job_id, context):
    """The common job's status, or None when its folder was removed (its runs are gone with it)."""
    from . import backtest_jobs
    try:
        return backtest_jobs.status(job_id, **context)
    except ValueError:
        return None


def finished_runs(plan_path, plan, store):
    """{(strategy, trigger it runs): run ID} of every finished run of this plan; a later request's run wins."""
    done = {}
    for request in _requests(plan_path, plan):
        lanes = _lane_file(store, request['job_id'])
        for row in (lanes or {}).get('runs', []):
            result = Path(store) / 'runs' / row['run_id'] / 'result.json'
            if row.get('status') == 'COMPLETE' and result.is_file():
                done[_effective(row)] = row['run_id']
    return done


def preview(plan_path, *, warehouse=None):
    """What `run` would start, without starting it: the runs, their groups and the recordings they use."""
    plan = read_plan(plan_path)
    context = _context(warehouse)
    root = context['project_root']
    lanes = every_lane(plan)
    done = finished_runs(plan_path, plan, context['warehouse'])
    left = [lane for lane in lanes if _effective(lane) not in done]
    settings = _part2(root)
    limit = settings.lane_limit(plan['max_lanes'])
    shown = {'runs': len(lanes), 'finished': len(lanes) - len(left), 'to_run': len(left),
             'groups': math.ceil(len(left) / limit) if left else 0, 'max_lanes': limit,
             'lanes': [{'strategy': lane['strategy'], 'trigger': lane['trigger'] or '전략 기본'} for lane in left]}
    if not left:
        return {**shown, 'message': '이 계획의 실행은 모두 끝났습니다. lanes report로 결과를 보세요.'}
    s, _, _ = build(plan, left, root)
    from event_backtest.workflow import proposal
    try:
        found = proposal(s, context['warehouse'], verify=False, cleanup=False)
    except ValueError as exc:
        return {**shown, 'ready': False, 'message': str(exc) + ' 종목·기간·mode를 데이터창고에 있는 녹화에 맞추세요.'}
    record = found.get('record') or []
    shown['available_periods'] = [{key: row.get(key) for key in ('start', 'end')} for row in found.get('available_periods') or []]
    shown['excluded_periods'] = [{key: row.get(key) for key in ('start', 'end', 'reason')} for row in found.get('excluded_periods') or []]
    shown['ready'] = not record
    shown['message'] = ('새로 녹화할 구간이 있습니다. 사용자 허락 없이 녹화하지 말고, available_only를 true로 두거나 사용자에게 알리세요.'
                        if record else '확인되면 lanes run으로 시작하세요. 걸리는 시간은 첫 묶음이 끝나면 lanes status에 나옵니다.')
    return shown


def start(plan_path, *, warehouse=None):
    """Start the plan's runs that have not finished yet, as one request."""
    from . import backtest_jobs
    plan = read_plan(plan_path)
    context = _context(warehouse)
    root, store = context['project_root'], context['warehouse']
    record = _load_record(plan_path)
    entry = record['plans'].setdefault(fingerprint(plan), {'requests': []})
    for request in entry['requests']:
        info = _job(request['job_id'], context)
        if info and info['active']:
            raise ValueError('이 계획은 이미 돌고 있습니다(실행 ID ' + request['job_id'] + '). lanes status로 확인하세요.')
    done = finished_runs(plan_path, plan, store)
    left = [lane for lane in every_lane(plan) if _effective(lane) not in done]
    if not left:
        return {'job_id': None, 'runs': 0, 'message': '이 계획의 실행은 모두 끝났습니다. lanes report로 결과를 보세요.'}
    s, kind, adapter = build(plan, left, root)
    result = backtest_jobs.start({**context, 'kind': kind, 'scenario': s, 'adapter': adapter, 'rebuild': False,
                                  'plan_only': False})
    entry['requests'].append({'job_id': result['job_id'], 'runs': len(left), 'started_at': time.time()})
    storage.json_write(record_path(plan_path), record)
    return {'job_id': result['job_id'], 'runs': len(left), 'skipped': len(done),
            'message': f'실행 {len(left)}개를 시작했습니다' + (f'(끝난 {len(done)}개는 건너뜀)' if done else '') + '.'}


def _remaining(lanes):
    """Seconds left, from the groups that have ended (None until one has)."""
    times = [row for row in (lanes or {}).get('group_times', []) if row.get('ended')]
    if not times:
        return None
    per_group = sum(row['ended'] - row['started'] for row in times) / len(times)
    total = (lanes or {}).get('groups', 0)
    current = [row for row in lanes.get('group_times', []) if not row.get('ended')]
    elapsed = time.time() - current[-1]['started'] if current else 0
    return max(0.0, per_group * (total - len(times)) - elapsed)


def status(plan_path, *, warehouse=None):
    plan = read_plan(plan_path)
    context = _context(warehouse)
    requests = _requests(plan_path, plan)
    total = len(every_lane(plan))
    done = finished_runs(plan_path, plan, context['warehouse'])
    shown = {'runs': total, 'finished': len(done), 'requests': []}
    for request in requests:
        info = _job(request['job_id'], context) or {'phase': 'removed', 'active': False, 'message': '실행 기록이 지워졌습니다.'}
        lanes = _lane_file(context['warehouse'], request['job_id']) or {}
        times = lanes.get('group_times', [])
        row = {'job_id': request['job_id'], 'phase': info['phase'], 'active': info['active'],
               'runs': request['runs'], 'groups': lanes.get('groups'),
               'group': len(times) if times else None, 'message': info.get('message', '')}
        left = _remaining(lanes) if info['active'] else None
        if left is not None:
            row['seconds_left'] = round(left)
        shown['requests'].append(row)
    active = [row for row in shown['requests'] if row['active']]
    if active:
        row = active[-1]
        text = f"돌고 있습니다: 묶음 {row['group'] or 1}/{row['groups'] or '?'}"
        if 'seconds_left' in row:
            text += f", 남은 시간 약 {row['seconds_left'] // 3600}시간 {row['seconds_left'] % 3600 // 60}분"
        shown['message'] = text + f'. 끝난 실행 {len(done)}/{total}.'
    elif len(done) == total:
        shown['message'] = f'모두 끝났습니다({total}개). lanes report로 결과를 보세요.'
    else:
        shown['message'] = f'끝난 실행 {len(done)}/{total}. 남은 실행은 lanes run을 다시 하면 이어서 돕니다.'
    return shown


def stop(plan_path, *, warehouse=None):
    from . import backtest_jobs
    plan = read_plan(plan_path)
    context = _context(warehouse)
    for request in reversed(_requests(plan_path, plan)):
        if (_job(request['job_id'], context) or {}).get('active'):
            backtest_jobs.stop(request['job_id'], **context)
            return {'job_id': request['job_id'], 'message': '정지를 요청했습니다. 끝난 실행은 남고, 다시 lanes run하면 나머지가 이어서 돕니다.'}
    return {'job_id': None, 'message': '돌고 있는 실행이 없습니다.'}


# ---- report -----------------------------------------------------------------------------------------

def _stats(values):
    values = [value for value in values if math.isfinite(value)]
    n = len(values)
    if not n:
        return {'trades': 0, 'average_r': None, 't': None, 'win_rate': None, 'total_r': 0.0}
    mean = sum(values) / n
    sd = math.sqrt(sum((value - mean) ** 2 for value in values) / (n - 1)) if n > 1 else 0.0
    return {'trades': n, 'average_r': mean, 't': math.sqrt(n) * mean / sd if sd > 0 else None,
            'win_rate': sum(value > 0 for value in values) / n, 'total_r': sum(values)}


def _trades(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            if row.get('result') not in ('WIN', 'LOSS'):
                continue
            try:
                risk = abs(float(row['entry_price']) - float(row['stop_price']))
                yield int(row['alert_time']), float(row['rr']), float(row['r']), risk
            except (KeyError, TypeError, ValueError):
                continue


def rows(plan_path, *, warehouse=None):
    """One row per finished run × RR (virtual entry), or per run (alerts only)."""
    plan = read_plan(plan_path)
    store = Path(_context(warehouse)['warehouse'])
    return plan, _rows(plan, finished_runs(plan_path, plan, store), store)


def _rows(plan, finished, store):
    from event_backtest.settings import milliseconds
    start, end = milliseconds(plan['start']), milliseconds(plan['end'])
    split = start + 0.7 * (end - start)
    out = []
    for (strategy, trigger), run_id in sorted(finished.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        result = json.loads((store / 'runs' / run_id / 'result.json').read_text('utf-8'))
        base = {'strategy': strategy, 'trigger': trigger or '—', 'run_id': run_id,
                'alerts': (result.get('alert_statistics') or {}).get('total')}
        entry = result.get('virtual_entry') or {}
        if plan['result_mode'] != 'VIRTUAL_ENTRY' or not entry.get('trades_csv'):
            out.append(base)
            continue
        spread = float((entry.get('pricing') or {}).get('spread_price') or 0.0)
        by_rr = {}
        for alert, rr, r, risk in _trades(store / entry['trades_csv']):
            by_rr.setdefault(rr, []).append((alert, r, risk))
        for rr, items in sorted(by_rr.items()):
            twice = [r - spread / risk for _, r, risk in items if risk > 0]
            out.append({**base, 'rr': rr, 'spread_price': spread, 'all': _stats([r for _, r, _ in items]),
                        'early': _stats([r for alert, r, _ in items if alert < split]),
                        'late': _stats([r for alert, r, _ in items if alert >= split]),
                        'double_cost': _stats(twice)})
    return out


def _f(value):
    return '—' if value is None else f'{value:+.2f}'


def report(plan_path, *, warehouse=None):
    """The results as Markdown tables, also written beside the plan file (<plan>.report.md)."""
    plan, found = rows(plan_path, warehouse=warehouse)
    body = _table(plan, found, len(every_lane(plan)), '남은 실행이 있으면 lanes run을 다시 하세요.')
    report_path(plan_path).write_text(body, encoding='utf-8')
    return body


def _table(plan, found, total, again):
    runs = {row['run_id'] for row in found}
    text = [f"# 레인 결과 · {plan['symbol']} · {plan['start']}~{plan['end']} · {plan['mode']}", '',
            f'- 끝난 실행 {len(runs)}/{total}. {again}' if len(runs) < total else f'- 실행 {total}개 모두 끝남.']
    if plan['result_mode'] != 'VIRTUAL_ENTRY':
        text += ['', '| 전략 | 트리거 | 알림 |', '|---|---|---:|']
        text += [f"| {row['strategy']} | {row['trigger']} | {row['alerts']} |" for row in found]
    else:
        cost = plan['spread_points']
        text.append(f'- 평균 R은 스프레드 {cost:g}포인트를 넣고 계산한 값입니다(손절 1R 기준). 비용 2배 = 스프레드를 한 번 더 뺀 값.'
                    if cost else '- **스프레드 0으로 돌렸습니다. 비용이 빠지지 않은 값이라 실제보다 좋게 나옵니다.** spread_points를 넣고 다시 돌리세요(알림을 다시 써서 빠릅니다).')
        text.append(f'- 표본: 거래 {MIN_TRADES}건 이상. "꾸준한 조합"은 기간 앞 70%·뒤 30% 모두 평균 R이 플러스인 것을 t 순으로.')
        head = ['| 전략 | 트리거 | RR | 거래 | 평균 R | t | 승률 | 앞 70% → 뒤 30% | 비용 2배 |', '|---|---|---:|---:|---:|---:|---:|---|---:|']

        def line(row):
            a = row['all']
            return (f"| {row['strategy']} | {row['trigger']} | {row['rr']:g} | {a['trades']} | {_f(a['average_r'])} | {_f(a['t'])} | "
                    f"{a['win_rate']:.0%} | {_f(row['early']['average_r'])} → {_f(row['late']['average_r'])} | {_f(row['double_cost']['average_r'])} |")
        big = [row for row in found if 'rr' in row and row['all']['trades'] >= MIN_TRADES]
        text += ['', '## 평균 R 상위 10', '', *head, *[line(row) for row in sorted(big, key=lambda row: -row['all']['average_r'])[:10]]]
        steady = [row for row in big if (row['early']['average_r'] or -1) > 0 and (row['late']['average_r'] or -1) > 0]
        text += ['', '## 꾸준한 조합 10', '', *head, *([line(row) for row in sorted(steady, key=lambda row: -(row['all']['t'] or 0))[:10]] or ['| 없음 | | | | | | | | |'])]
        text += ['', '## 실행마다 가장 나은 RR', '', *head]
        for run_id in sorted(runs, key=lambda key: next((row['strategy'], row['trigger']) for row in found if row['run_id'] == key)):
            choices = [row for row in found if row['run_id'] == run_id and 'rr' in row and row['all']['trades']]
            if choices:
                text.append(line(max(choices, key=lambda row: row['all']['average_r'])))
        text += ['', '- t가 2보다 작으면 우연과 구분하기 어렵습니다. 조합이 많을수록 더 큰 t가 필요합니다.']
    return '\n'.join(text) + '\n'


# ---- Part3's AI ------------------------------------------------------------------------------------

def saved_path(plan, store):
    """Where the AI's plan is kept: in the warehouse whose runs it names, by what it runs."""
    return Path(store) / SAVED / (fingerprint(normalize(plan))[:16] + '.json')


def start_saved(plan, *, warehouse=None):
    """Start an AI plan: kept in the warehouse, so the same plan asked again skips its finished runs."""
    store = _context(warehouse)['warehouse']
    path = saved_path(plan, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        storage.json_write(path, {key: value for key, value in plan.items() if key in KEYS})
    return start(path, warehouse=warehouse)


def job_report(job_id, *, warehouse=None):
    """The tables of a finished request by its job ID: of its whole plan when Part3's AI started it,
    else of the runs it replayed together (or of its one run)."""
    store = Path(_context(warehouse)['warehouse'])
    folder = store / SAVED
    for record in sorted(folder.glob('*.lanes.json')) if folder.is_dir() else ():
        try:
            plans = json.loads(record.read_text('utf-8')).get('plans', {})
        except (OSError, ValueError, AttributeError):
            continue
        if any(row.get('job_id') == job_id for entry in plans.values() for row in entry.get('requests', [])):
            plan_file = record.with_name(record.name[:-len('.lanes.json')] + '.json')
            if plan_file.is_file():
                return report(plan_file, warehouse=warehouse)
    try:
        result = json.loads((store / 'runs' / job_id / 'result.json').read_text('utf-8'))
    except (OSError, ValueError):
        raise ValueError('이 작업의 결과가 아직 없습니다. 끝난 뒤에 다시 요청하세요.') from None
    s = result.get('scenario') or {}
    if s.get('build_only') or s.get('commands'):
        raise ValueError('데이터 구축·WATCH 작업에는 결과 표가 없습니다. 전략 백테스트 작업을 고르세요.')
    together = (_lane_file(store, job_id) or {}).get('runs')
    finished = {}
    if together:
        for row in together:
            if row.get('status') == 'COMPLETE' and (store / 'runs' / row['run_id'] / 'result.json').is_file():
                finished[_effective(row)] = row['run_id']
    elif result.get('status') == 'COMPLETE':
        names = s.get('strategies') or []
        trigger = (s.get('triggers') or {}).get(names[0]) if len(names) == 1 else None
        if len(names) == 1 and trigger is None:
            from strategy_recipe.registry import default_settings
            try:
                trigger = default_settings(names[0])[0]
            except ValueError:
                trigger = None
        finished[(', '.join(names) or '—', trigger)] = job_id
    plan = {key: s.get(key) for key in ('symbol', 'start', 'end', 'mode', 'result_mode')}
    plan['spread_points'] = float((s.get('spread_points') or {}).get(s.get('symbol'), 0) or 0)
    return _table(plan, _rows(plan, finished, store), len(together or [job_id]), '남은 실행은 같은 요청을 다시 하면 이어서 돕니다.')
