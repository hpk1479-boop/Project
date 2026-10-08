"""Natural language selects existing backtest operations; trusted APIs execute them."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import threading
import uuid


ACTIONS = ('START', 'STOP', 'STATUS', 'RECENT', 'RECONNECT')
REQUEST_FIELDS = {'target_mode', 'specials', 'filename', 'watch_text', 'symbol', 'start', 'end',
                  'mode', 'result_mode', 'spread_points', 'build_only', 'rebuild', 'available_only', 'virtual_entry', 'base_frame'}
MODES = {'BAR': '봉', 'EVENT': '이벤트', 'TICK': '틱'}
RESULT_MODES = {'ALERT_ONLY': '알림만', 'VIRTUAL_ENTRY': '가상 진입'}


def virtual_contract():
    """Read the same Part2 policy used by native and generated backtests."""
    from .. import unified_backtest
    unified_backtest._part2()
    from event_backtest import virtual_contract as contract
    return contract


def _ladder_text():
    """The base-frame table (기준 → 중위·상위·최상위) the prompt gives the model."""
    virtual_contract()
    from event_backtest.base_frames import LADDER
    return ' / '.join(base + '→' + '·'.join(tf or '없음' for tf in row[1:]) for base, row in LADDER.items())


def command_schema():
    nullable_text = {'type': ['string', 'null']}
    request = {'type': ['object', 'null'], 'additionalProperties': False, 'properties': {
        'target_mode': {'enum': ['SPECIAL', 'WATCH', 'GENERATED', None]},
        'specials': {'type': ['array', 'null'], 'items': {'type': 'string'}},
        'filename': nullable_text, 'watch_text': nullable_text, 'symbol': nullable_text,
        'start': nullable_text, 'end': nullable_text, 'mode': {'enum': [*MODES, None]},
        'result_mode': {'enum': [*RESULT_MODES, None]},
        'virtual_entry': {'anyOf': [virtual_contract().schema(), {'type': 'null'}]},
        'base_frame': {'enum': [*virtual_contract().schema()['properties']['tf']['enum'], None]},
        'spread_points': {'type': ['number', 'null'], 'minimum': 0},
        'build_only': {'type': ['boolean', 'null']}, 'rebuild': {'type': ['boolean', 'null']},
        'available_only': {'type': ['boolean', 'null']}}}
    return {'type': 'object', 'additionalProperties': False, 'required':
            ['supported', 'action', 'request', 'job_id', 'needs_clarification', 'clarification_question', 'message_ko'],
            'properties': {'supported': {'type': 'boolean'}, 'action': {'enum': [*ACTIONS, None]},
                'request': request, 'job_id': nullable_text, 'needs_clarification': {'type': 'boolean'},
                'clarification_question': nullable_text, 'message_ko': {'type': 'string'}}}


def context():
    from .. import storage, unified_backtest
    options = unified_backtest.options()
    try:
        recent = unified_backtest.recent()
        jobs = recent.get('items', recent.get('jobs', []))
    except (OSError, ValueError):
        jobs = []
    settings, _, _ = unified_backtest._part2()
    current = settings.settings()
    return {'today': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat(),
            'options': options, 'generated': storage.recent(), 'jobs': jobs,
            'execution_version': hashlib.sha256(json.dumps({'settings': current, 'connections': storage.connections()},
                ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest(),
            'execution_defaults': {key: current.get(key) for key in
                ('cores', 'capture_start', 'oz_evaluation', 'overlap_trading_days')}}


def execute(command):
    from .. import integration, unified_backtest
    action = command['action']
    if action == 'START':
        request = copy.deepcopy(command['request'])
        if request.pop('target_mode') == 'GENERATED' and not request['build_only']:
            request.pop('specials', None)
            request.pop('special_settings', None)
            request.pop('watch_text', None)
            request['action'] = 'run'
            return integration.backtest(request)
        request['target_mode'] = command['request']['target_mode'] if command['request']['target_mode'] != 'GENERATED' else 'SPECIAL'
        return unified_backtest.start(request)
    if action == 'RECENT':
        return unified_backtest.recent()
    return {'STOP': unified_backtest.stop, 'STATUS': unified_backtest.status,
            'RECONNECT': unified_backtest.reconnect}[action](command['job_id'])


def _prompt(snapshot):
    # No code, source paths, executable names or arbitrary model tools are provided.
    return '''너는 모세의 지팡이 백테스트 작업 명령 통역기다. 전략 생성 대화와 독립된 화면이다.
기존 SPECIAL, 이미 생성된 Test 전략, 기존 WATCH 명령의 백테스트와 데이터 구축만 해석한다.
START는 계획 확인을 시작한다. 실제 데이터 구축은 기존 계획 화면의 별도 사용자 확인을 따른다.
STOP은 지정한 작업 하나만 중지, STATUS는 상태 조회, RECENT는 최근 작업, RECONNECT는 기존 작업 화면 다시 연결이다.
새 전략 생성, 계산식 변경, 설정 저장, 파일/코드/실행명령/경로 입력은 지원하지 않는다. 도구를 호출하지 않는다.
출력은 지정된 JSON 객체 하나다. action/request/job_id 이외의 실행 정보를 만들지 않는다.
START request의 target_mode는 SPECIAL/WATCH/GENERATED다. 데이터 구축만 요청하면 build_only=true이며 전략 선택은 필요 없다.
SPECIAL은 현재 specials 목록, GENERATED는 현재 generated filename 목록에서 고른다. WATCH는 사용자가 요청한 기존 명령을 watch_text에 표시한다.
종목·시작일·종료일·백테스트 전략이 부족하면 질문한다. 모델이 종목이나 전략을 임의 추가하지 않는다.
명시한 새 논리 종목명은 유지하고, 금/나스닥 같은 별칭이 현재 목록에서 모호하면 질문한다.
날짜는 YYYY-MM-DD이고 end는 종료일 미포함이다. 예: 2026년 9월 전체는 start=2026-09-01, end=2026-10-01.
'어제까지'의 end는 today다. 날짜를 계산할 때 아래 현재 한국 날짜를 사용한다.
최근 N일/개월/년 백테스트는 today 기준의 요청 기간을 유지하고 available_only=true로 둔다.
available_only=true는 그 기간 안의 검증된 보유 데이터만 사용하며 부족한 구간을 자동 구축하지 않는다.
명시한 날짜를 보유 데이터 날짜로 바꾸지 않는다. 데이터 구축·재구축이나 오늘까지 새 데이터를 채우라고 명시하면 available_only=false다.
일반 명시 기간 요청에서 available_only를 지정하지 않으면 false다. 후속 수정은 기존 데이터 사용 정책도 보존한다.
mode BAR/EVENT/TICK, result_mode ALERT_ONLY/VIRTUAL_ENTRY, spread_points 0 이상, rebuild는 명시한 경우만 true다.
mode/result_mode/spread_points를 말하지 않으면 null로 두며 기존 설정값을 사용자 미리보기에 표시한다.
가상진입 요청은 request.virtual_entry에 공통 가상진입 계약으로 담는다. 전략의 알림 조건과 진입 후 체결·손절 정책을 섞지 않는다.
가상 진입은 전략 1개만 실행한다. 여러 전략을 요청하면 하나를 고르도록 질문한다. 알림만(ALERT_ONLY)은 여러 전략을 함께 실행할 수 있다.
mode는 IMMEDIATE(즉시 진입, 알림 시점 가격, conditions 빈 목록) 또는 CONFIRM(조건 진입)이다.
조건 진입은 conditions를 모두 만족한 확인봉 다음 봉 시가에 진입한다. CANDLE_CLOSE는 확인봉이 매수 양봉/매도 음봉으로 마감한 조건이며 다른 조건처럼 넣고 뺀다.
CANDLE_SHAPE(망치형 HAMMER·역망치형 INVERTED_HAMMER)와 MA_TOUCH_CANDLE(확인봉 자체의 MA 터치)도 확인봉 조건이다.
virtual_entry.tf는 기준 프레임이다. SIGNAL은 전략 레시피의 시간봉 그대로 시험하며, 여러 시간봉 전략은 알림마다 자기 시간봉으로 판정한다.
시간봉은 기준 프레임 하나로만 정한다. 기준 프레임을 바꾸면 그 가상진입 시험 전체(전략 조건과 진입)가 아래 표(기준→중위·상위·최상위)대로 옮겨진다. 전략 시간봉이 하나이고 표 안에 있는 전략만 된다. 예: SPECIAL9를 2분 기준으로 하면 2분 골크·2분 50헐 터치·20분 추세, 진입도 2분봉이다.
기준 프레임을 바꾸라는 요청은 request.base_frame에 목적 시간봉을 넣고 virtual_entry는 null로 둔다. 프로그램이 그 기준으로 쓴 레시피 값으로 전략과 진입 정책을 모두 다시 채운다(변경 전에 바꾼 값은 쓰지 않는다). 기준 변경을 정책 tf에 미리 적용하지 않는다.
virtual_entry의 conditions·filters·atr·stop 시간봉(tf)은 레시피 값(SIGNAL·ENV·레시피에 적힌 시간봉) 그대로 두고, 새로 넣는 조건·제한은 SIGNAL이다. 진입·손절 시간봉만 따로 바꾸라는 요청은 supported=false로 "시간봉은 기준 프레임으로만 바꿀 수 있습니다"라고 답한다.
기존 conditions·filters 행을 수정할 때는 그 행의 recipe_index를 그대로 보존한다. 조건 종류·기간·배수만 고쳐도 그 칸의 시간봉은 유지한다. 삭제 후 새로 추가한 행에는 recipe_index를 넣지 않고 tf=SIGNAL을 쓴다. 다른 칸의 recipe_index를 가져오거나 새로 만들지 않는다.
기준을 바꾸지 않는 후속 요청은 base_frame=null이다. 단, 직전 명령에 미처리 base_frame이 남아 있고 확인 질문에 답하는 요청이면 그 base_frame을 보존한다.
기준 프레임 표: ''' + _ladder_text() + '''
손절은 OZ_B0(올존 전략만), RECENT_EXTREME, ATR이다. 기간·종목만 수정하면 직전 virtual_entry를 보존한다.
사용자가 진입·손절을 지정하지 않으면 virtual_entry는 null이며 전략 레시피의 가상진입 값을 쓴다. 레시피에 값이 없으면 즉시 진입과 ATR 손절이다.
기존 전략 OZ 프로필과 거래시간은 그대로 사용한다. 수정 요청은 직전 전체 command를 기준으로 언급한 부분만 바꾼 전체 command를 반환한다.
STOP/STATUS/RECONNECT의 job_id는 현재 목록의 식별자다. 여러 진행 작업 중 대상을 알 수 없으면 질문한다.
범위 밖이면 supported=false다. 설명은 짧은 한국어 message_ko로만 쓰고 HTML/Markdown/코드를 만들지 않는다.
현재 읽기 전용 선택 정보:
''' + json.dumps(snapshot, ensure_ascii=False, default=str)


def _safe_snapshot(value):
    options = value.get('options') or {}
    allowed = ('symbol', 'symbols', 'specials', 'selected_specials', 'mode', 'result_mode',
               'spread_points', 'special_settings', 'default_triggers', 'trigger_choices')
    job_fields = ('job_id', 'kind', 'phase', 'source', 'symbol', 'start', 'end', 'mode',
                  'active', 'result_ready', 'created_at')
    clean_options = {key: options[key] for key in allowed if key in options}
    profiles = {}
    for name, profile in (options.get('virtual_profiles') or {}).items():
        if name not in options.get('specials', ()) or not isinstance(profile, dict):
            continue
        profiles[name] = {key: copy.deepcopy(profile[key]) for key in
                          ('oz', 'base', 'bases', 'timeframes', 'env_timeframes') if key in profile}
        profiles[name]['default'] = virtual_contract().normalize_virtual_entry(profile.get('default'))
    if profiles:
        clean_options['virtual_profiles'] = profiles
    return {'today': value.get('today'), 'options': clean_options,
            'generated': [{key: row.get(key) for key in ('filename', 'name', 'base')}
                          for row in value.get('generated', [])],
            'jobs': [{key: row.get(key) for key in job_fields} for row in value.get('jobs', [])]}


def _date(value):
    if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
        raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
    return dt.date.fromisoformat(value)


def _settings_version(snapshot):
    options = snapshot.get('options') or {}
    value = {'options': {key: options.get(key) for key in
             ('mode', 'result_mode', 'spread_points', 'special_settings', 'default_triggers')},
             'defaults': snapshot.get('execution_defaults'), 'version': snapshot.get('execution_version')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _normalize(value, snapshot):
    fields = {'supported', 'action', 'request', 'job_id', 'needs_clarification',
              'clarification_question', 'message_ko'}
    if not isinstance(value, dict) or set(value) - fields or type(value.get('supported')) is not bool:
        raise ValueError('AI 명령 형식이 올바르지 않습니다.')
    if type(value.get('needs_clarification', False)) is not bool:
        raise ValueError('AI 확인 질문 형식이 올바르지 않습니다.')
    if not isinstance(value.get('message_ko', ''), str) or (value.get('clarification_question') is not None and
            not isinstance(value['clarification_question'], str)):
        raise ValueError('AI 설명과 확인 질문은 문자열이어야 합니다.')
    if not value['supported']:
        return None, None
    action = value.get('action')
    if action not in ACTIONS:
        raise ValueError('지원하지 않는 백테스트 작업입니다.')
    if action != 'START':
        if value.get('request') not in (None, {}):
            raise ValueError('조회·중지 명령에는 백테스트 실행 설정을 넣을 수 없습니다.')
        if action == 'RECENT':
            if value.get('job_id') is not None:
                raise ValueError('최근 작업 조회에 작업 식별자는 필요하지 않습니다.')
            return {'action': action}, None
        jobs = snapshot.get('jobs', [])
        requested = value.get('job_id')
        if requested is not None and (not isinstance(requested, str) or re.fullmatch('[0-9a-f]{32}', requested) is None):
            raise ValueError('작업 식별자 형식이 올바르지 않습니다.')
        if requested:
            chosen = next((row for row in jobs if row.get('job_id') == requested), None)
            if chosen is None:
                return {'action': action}, '최근 작업 목록에서 대상 작업을 선택해 주세요.'
        else:
            active = [row for row in jobs if row.get('active') or row.get('phase') in
                      ('planning', 'plan', 'starting', 'run', 'confirm')]
            if len(active) > 1:
                return {'action': action}, '진행 중인 작업이 여러 개입니다. 어떤 작업을 선택할까요?'
            chosen = active[0] if active else (jobs[0] if jobs and action != 'STOP' else None)
            if chosen is None:
                return {'action': action}, '대상 작업이 없습니다. 최근 작업에서 작업을 선택해 주세요.'
        return {'action': action, 'job_id': chosen['job_id']}, None
    request = value.get('request')
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS or value.get('job_id') is not None:
        raise ValueError('백테스트 실행 설정 형식이 올바르지 않습니다.')
    options = snapshot.get('options') or {}
    clean = copy.deepcopy(request)
    for key in ('build_only', 'rebuild', 'available_only'):
        if clean.get(key) is None:
            clean[key] = False
        if type(clean[key]) is not bool:
            raise ValueError('데이터 구축·재구축 여부는 참/거짓 값이어야 합니다.')
    if clean['available_only'] and (clean['build_only'] or clean['rebuild']):
        raise ValueError('보유 데이터만 사용하는 백테스트와 데이터 구축·재구축을 함께 선택할 수 없습니다.')
    target = clean.get('target_mode') or ('SPECIAL' if clean['build_only'] else None)
    if target is not None and target not in ('SPECIAL', 'WATCH', 'GENERATED'):
        raise ValueError('지원하지 않는 전략 선택 방식입니다.')
    clean['target_mode'] = target
    mode = clean.get('mode') or ('TICK' if target == 'GENERATED' else options.get('mode') or 'BAR')
    result_mode = clean.get('result_mode') or options.get('result_mode') or 'ALERT_ONLY'
    if mode not in MODES or result_mode not in RESULT_MODES:
        raise ValueError('데이터 모드 또는 결과 모드가 올바르지 않습니다.')
    clean['mode'], clean['result_mode'] = mode, result_mode
    base_change = clean.pop('base_frame', None)
    if base_change is not None:
        choices = virtual_contract().schema()['properties']['tf']['enum']
        if not isinstance(base_change, str) or base_change not in choices:
            raise ValueError('기준 프레임 형식이 올바르지 않습니다.')
        if result_mode != 'VIRTUAL_ENTRY' or clean['build_only']:
            raise ValueError('기준 프레임 변경은 가상 진입 백테스트에서만 사용할 수 있습니다.')
    clean['virtual_entry'] = virtual_contract().normalize_virtual_entry(clean.get('virtual_entry'))
    spread = clean.get('spread_points')
    if spread is None:
        saved = options.get('spread_points', {})
        spread = saved.get(clean.get('symbol'), 0) if isinstance(saved, dict) else saved
    import math
    if type(spread) not in (int, float) or not math.isfinite(spread) or spread < 0:
        raise ValueError('스프레드는 0 이상의 유한한 숫자여야 합니다.')
    clean['spread_points'] = spread
    missing = []
    for key, label in (('symbol', '종목'), ('start', '시작일'), ('end', '종료일(미포함)')):
        if clean.get(key) is None or clean.get(key) == '':
            missing.append(label)
        elif key == 'symbol':
            if not isinstance(clean[key], str) or re.fullmatch(r'[^\s,\x00-\x1f\x7f]{1,64}', clean[key]) is None:
                raise ValueError('종목명 형식이 올바르지 않습니다.')
        else:
            _date(clean[key])
    if clean.get('start') and clean.get('end') and _date(clean['start']) >= _date(clean['end']):
        raise ValueError('종료일은 시작일 이후여야 합니다(종료일 미포함).')
    specials = [] if clean.get('specials') is None else clean['specials']
    if not isinstance(specials, list) or any(not isinstance(item, str) for item in specials):
        raise ValueError('전략 목록 형식이 올바르지 않습니다.')
    if set(specials) - set(options.get('specials', [])):
        raise ValueError('현재 실행 가능한 SPECIAL을 선택하세요.')
    clean['specials'] = list(dict.fromkeys(specials))
    filenames = {row.get('filename') for row in snapshot.get('generated', [])}
    if clean.get('filename') is not None and (not isinstance(clean['filename'], str) or clean['filename'] not in filenames):
        raise ValueError('현재 생성 전략 목록에서 파일을 선택하세요.')
    if clean.get('watch_text') is not None and (not isinstance(clean['watch_text'], str) or len(clean['watch_text']) > 8000):
        raise ValueError('WATCH 명령은 8000자 이하의 문자열이어야 합니다.')
    if clean['build_only'] and target not in (None, 'SPECIAL'):
        raise ValueError('데이터 구축에는 전략을 선택하지 마세요.')
    if not clean['build_only']:
        if target == 'SPECIAL':
            if not specials:
                missing.append('실행할 SPECIAL')
        elif target == 'GENERATED':
            if not clean.get('filename'):
                missing.append('생성 전략')
            elif not isinstance(clean['filename'], str) or clean['filename'] not in filenames:
                raise ValueError('현재 생성 전략 목록에서 파일을 선택하세요.')
        elif target == 'WATCH':
            if not isinstance(clean.get('watch_text'), str) or not clean['watch_text'].strip():
                missing.append('WATCH 명령')
        else:
            missing.append('백테스트할 기존 전략')
    if target != 'GENERATED' and clean.get('filename') is not None:
        raise ValueError('생성 전략 이외에는 파일명을 지정할 수 없습니다.')
    if target != 'WATCH' and clean.get('watch_text') is not None:
        raise ValueError('WATCH 이외에는 감시 명령을 지정할 수 없습니다.')
    if target in ('GENERATED', 'WATCH') and specials:
        raise ValueError('다른 전략 선택 방식을 동시에 지정할 수 없습니다.')
    clean['special_settings'] = {name: copy.deepcopy((options.get('special_settings') or {}).get(name) or
        {'enabled': True, 'trigger': None, 'time_filters': None}) for name in clean['specials']}
    for row in clean['special_settings'].values():
        if not isinstance(row, dict) or row.get('load_error'):
            raise ValueError('선택한 전략의 저장 설정을 불러올 수 없습니다. 설정 화면에서 확인하고 저장하세요.')
        row['enabled'] = True
    question = ' · '.join(missing) + '을 알려 주세요.' if missing else None
    if clean['result_mode'] == 'VIRTUAL_ENTRY' and not clean['build_only']:
        if target == 'WATCH':
            raise ValueError('WATCH 명령은 얼럿 온리만 됩니다.')
        if target == 'SPECIAL' and len(clean['specials']) > 1:
            question = question or '가상 진입은 전략 1개만 실행할 수 있습니다. 어느 전략으로 할까요?'
        profile = _virtual_profile(clean)
        if profile is not None:
            from event_backtest.virtual_defaults import check_frames, recipe_on_base
            if base_change is not None:
                # A base frame refills every value from the recipe on that frame; earlier edits are dropped.
                clean['virtual_entry'] = recipe_on_base(profile, base_change)
            virtual_contract().check_for_strategy(clean['virtual_entry'], profile['oz'])
            if clean['virtual_entry'] is not None:
                check_frames(profile, clean['virtual_entry'])     # frames come only from the base frame
        elif base_change is not None:
            if question:
                clean['base_frame'] = base_change  # Retain the unconsumed move until the strategy is known.
                return {'action': action, 'request': clean}, question
            raise ValueError('기준 프레임을 바꿀 전략의 레시피를 먼저 확인해야 합니다.')
    return {'action': action, 'request': clean}, question


def _virtual_profile(request):
    """The one virtual-entry strategy's recipe policy and timeframes, when it is known yet."""
    virtual_contract()
    from event_backtest import virtual_defaults
    target = request.get('target_mode')
    name = (request['specials'][0] if target == 'SPECIAL' and len(request.get('specials') or ()) == 1 else
            request['filename'].rsplit('.', 1)[0] if target == 'GENERATED' and isinstance(request.get('filename'), str)
            else None)
    try:
        return None if name is None else virtual_defaults.strategy_profile(name)
    except ValueError:
        return None  # A draft that is not written yet; the run checks it against the file.


def _frame_text(value):
    found = re.fullmatch(r'(\d+)([mhdw])', value)
    return found[1] + {'m': '분', 'h': '시간', 'd': '일', 'w': '주'}[found[2]] if found else value


def _virtual_preview(policy, profile):
    """Describe the checked execution choices with real timeframes."""
    if policy is None:
        if profile is None:
            return ['가상진입: 전략 레시피 값']
        policy = profile['default']
    if profile is not None:
        from event_backtest.virtual_defaults import on_base
        try:
            profile = on_base(profile, policy['tf'])      # the frames the whole test runs on
        except ValueError:
            pass                                          # the run refuses a base frame the strategy cannot use
    frames = '·'.join(_frame_text(tf) for tf in (profile or {}).get('timeframes', ())) or '전략 시간봉'
    env_frames = '·'.join(_frame_text(tf) for tf in (profile or {}).get('env_timeframes', ())) or '환경 프레임'
    def tf(value):
        return frames if value == 'SIGNAL' else env_frames if value == 'ENV' else _frame_text(value)
    def ma(item):
        return tf(item['tf']) + ' ' + item['family'] + str(item['period'])
    names = {'CANDLE_CLOSE': lambda item: '양봉·음봉 마감',
             'CANDLE_SHAPE': lambda item: '망치형' if item['shape'] == 'HAMMER' else '역망치형',
             'MA_POSITION': lambda item: ma(item) + ' 위·아래',
             'MA_CROSS': lambda item: ma(item) + ' 종가 돌파',
             'MA_TOUCH': lambda item: ma(item) + ' 터치 후 확인',
             'MA_TOUCH_CANDLE': lambda item: '확인봉 ' + ma(item) + ' 터치',
             'ENGULFING': lambda item: '인걸핑',
             'NECKLINE_BREAK': lambda item: '넥라인 종가 돌파'}
    environments = {'TREND': lambda item: '추세',
                    'MA_STATE': lambda item: f"{item['family']}{item['fast']}·{item['slow']} 정배열",
                    'MA_SLOPE_STATE': lambda item: f"{item['family']}{item['period']} 기울기",
                    'MA_PRICE_STATE': lambda item: f"가격과 {item['family']}{item['period']}"}
    if policy['mode'] == 'IMMEDIATE':
        rows = ['진입: 즉시 진입' + ('' if policy['tf'] == 'SIGNAL' else ' · 기준 프레임 ' + tf(policy['tf']))]
    else:
        rows = ['진입: 조건 진입 · 기준 프레임 ' + tf(policy['tf']),
                '진입 조건: ' + ' + '.join(names[item['kind']](item) for item in policy['conditions'])]
    for item in policy['filters']:
        if item['kind'] == 'ENVIRONMENT':
            bar = '확정봉' if item['bar_state'] == 'CLOSED' else '진행봉'
            rows.append(f"진입 제한: {tf(item['tf'])} {environments[item['condition']](item)} 유지 ({bar})")
            continue
        if item['kind'] == 'MAX_BARS':
            rows.append(f"진입 제한: 알림 후 {item['bars']}봉 안에 진입")
            continue
        title = ('확인봉 ' + {'BODY': '몸통', 'RANGE': '고가~저가'}[item['measure']] if item['kind'] == 'CANDLE_ATR'
                 else '시가와 ' + ma(item) + ' 거리')
        limits = [f"{item[key]:g}배 {'이상' if key == 'min' else '미만'}" for key in ('min', 'max') if item[key] is not None]
        rows.append(f"진입 제한: {title} · {tf(policy['atr']['tf'])} ATR{policy['atr']['period']} " + ' / '.join(limits))
    stop = policy['stop']
    rows.append('손절: ' + {'OZ_B0': lambda: '올존 B0',
        'RECENT_EXTREME': lambda: tf(stop['tf']) + f" 직전 {stop['bars']}봉 저점·고점",
        'ATR': lambda: tf(stop['tf']) + f" ATR{stop['period']} × {stop['multiplier']:g}배"}[stop['kind']]())
    rows.append('익절: 손익비 1:1 ~ 1:5 (0.5 간격) 비교')
    return rows


def _preview(command, snapshot):
    if command['action'] != 'START':
        labels = {'STOP': '작업 중지', 'STATUS': '작업 상태 조회', 'RECENT': '최근 작업 조회', 'RECONNECT': '작업 다시 연결'}
        return labels[command['action']] + (('\n작업: ' + command['job_id']) if command.get('job_id') else '')
    request = command['request']
    target = request['target_mode']
    selected = request.get('filename') if target == 'GENERATED' else ', '.join(request['specials'])
    if target == 'WATCH':
        selected = 'WATCH: ' + request['watch_text']
    start, end = request['start'], request['end']
    rows = [f"목적: {'데이터 구축만' if request['build_only'] else '백테스트'}", f"종목: {request['symbol']}",
            f"기간 (UTC): {start} ~ {(_date(end) - dt.timedelta(days=1)).isoformat()} (종료 경계 {end}, 미포함)",
            f"전략: {selected or '데이터 구축에는 전략을 사용하지 않음'}",
            f"데이터 모드: {MODES[request['mode']]}", f"결과: {RESULT_MODES[request['result_mode']]}",
            f"스프레드: {request['spread_points']} 포인트", f"재구축: {'사용' if request['rebuild'] else '사용 안 함'}"]
    if request['result_mode'] == 'VIRTUAL_ENTRY' and not request['build_only']:
        rows.extend(_virtual_preview(request['virtual_entry'], _virtual_profile(request)))
    defaults = snapshot.get('options') or {}
    from .. import catalog
    from special_settings_model import time_summary
    for name, item in request['special_settings'].items():
        profile = item.get('trigger') or (defaults.get('default_triggers') or {}).get(name)
        if profile is not None:
            profile = catalog.PROFILES.canonical_profile_text(profile)
        else:
            profile = '코드 기본값'
        time_text = time_summary(item.get('time_filters'), (defaults.get('default_time_filters') or {}).get(name, 0),
                                 defaults.get('session_times'))
        rows.append(f'{name}: 최종 OZ {profile} / 거래시간 {time_text}')
    runtime = dict(snapshot.get('execution_defaults') or {})
    if target == 'GENERATED':
        runtime['oz_evaluation'] = 'all'
    if runtime:
        from event_backtest.system import physical_cores
        rows.append(f"실행 설정: CPU {runtime.get('cores') or physical_cores()}개 / 겹침 {runtime.get('overlap_trading_days', 3)} 거래일")
        rows.append('녹화 시작: ' + {'keyframe': '키프레임', 'beginning': '처음부터'}.get(runtime.get('capture_start'), '기존 설정') +
                    ' / OZ 계산 범위: ' + {'selected': '선택한 전략', 'all': '전체'}.get(runtime.get('oz_evaluation'), '기존 설정'))
    if request.get('available_only'):
        rows.append('데이터 사용: 요청 기간 안의 보유 데이터만 사용합니다. 부족한 구간은 제외하며 자동 구축하지 않습니다.')
        rows.append('확인하면 실제 사용 구간을 검사하여 계획 화면에 표시합니다.')
    else:
        rows.append('확인하면 계획을 검사합니다. 새 데이터 녹화가 필요하면 계획 화면에서 다시 확인합니다.')
    return '\n'.join(rows)


class Session:
    def __init__(self, provider, context_loader=None, executor=None):
        self.provider = provider
        self.context_loader = context_loader or context
        self.executor = executor or execute
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self._prefix = uuid.uuid4().hex
            self._number = 0
            self.revision = self._prefix + ':0'
            self.pending = None
            self.pending_version = None
            self.last_command = None
            self.question = None
        return {'ok': True}

    def cancel(self):
        with self.lock:
            self.pending = None
            self.pending_version = None
        return {'ok': True}

    def send(self, message):
        with self.lock:
            if not isinstance(message, str) or not message.strip() or len(message) > 8000:
                raise ValueError('백테스트 요청은 1~8000자의 문장으로 입력하세요.')
            self._number += 1
            self.revision = self._prefix + ':' + str(self._number)
            self.pending = None
            self.pending_version = None
            snapshot = self.context_loader()
            prior = copy.deepcopy(self.last_command)
            if prior and 'request' in prior:
                prior['request'] = {key: item for key, item in prior['request'].items() if key in REQUEST_FIELDS}
            previous = ('\n직전 명령(요청한 부분만 수정하여 전체 명령 반환):\n' +
                        json.dumps(prior, ensure_ascii=False)) if prior else ''
            messages = [{'role': 'system', 'content': _prompt(_safe_snapshot(snapshot))},
                        {'role': 'user', 'content': previous +
                         ('\n직전 확인 질문: ' + self.question if self.question else '') + '\n현재 요청: ' + message.strip()}]
            reply = self.provider.chat(messages, [], response_schema=command_schema())
            try:
                if reply.get('tool_calls'):
                    raise ValueError('백테스트 통역에는 실행 도구가 제공되지 않습니다.')
                value = json.loads(reply.get('content') or '')
                command, question = _normalize(value, snapshot)
                if command and value.get('needs_clarification'):
                    question = question or value.get('clarification_question') or '백테스트 요청을 조금 더 구체적으로 알려 주세요.'
            except (ValueError, TypeError, KeyError) as exc:
                self.question = None
                return {'action': None, 'command': None, 'preview': '', 'needs_clarification': False,
                        'clarification_question': None, 'message_ko': 'AI 명령을 확인할 수 없습니다. ' + str(exc),
                        'can_confirm': False, 'revision': self.revision, 'result': None}
            self.question = question
            if command:
                self.last_command = copy.deepcopy(command)
            result, preview = None, ''
            if command and not question:
                preview = _preview(command, snapshot)
                if command['action'] in ('START', 'STOP'):
                    self.pending = copy.deepcopy(command)
                    self.pending_version = _settings_version(snapshot) if command['action'] == 'START' else None
                else:
                    result = self.executor(copy.deepcopy(command))
            return {'action': command.get('action') if command else None, 'command': command, 'preview': preview,
                    'needs_clarification': bool(question), 'clarification_question': question,
                    'message_ko': question or value.get('message_ko') or ('해석을 확인해 주세요.' if command else '지원하는 백테스트 작업이 아닙니다.'),
                    'can_confirm': self.pending is not None, 'revision': self.revision, 'result': result}

    def confirm(self, revision):
        with self.lock:
            if not isinstance(revision, str) or revision != self.revision:
                raise ValueError('해석 결과가 변경되었습니다. 최신 요청을 다시 확인하세요.')
            if self.pending is None:
                raise ValueError('확인할 백테스트 요청이 없습니다.')
            command = self.pending
            self.pending = None  # Consume before calling a mutating API; duplicate clicks cannot execute twice.
            expected = self.pending_version
            self.pending_version = None
            if expected is not None and _settings_version(self.context_loader()) != expected:
                raise ValueError('실행 설정이 변경되었습니다. 요청을 다시 보내 최신 미리보기를 확인하세요.')
            result = self.executor(copy.deepcopy(command))
            return {'action': command['action'], 'command': command, 'can_confirm': False,
                    'revision': self.revision, 'result': result}
