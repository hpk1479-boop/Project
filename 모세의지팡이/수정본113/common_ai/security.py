"""One least-privilege policy for every AI Provider.

Requests are rebuilt from public language/strategy contracts, never from
project files or raw diagnostic transcripts, regardless of model location.
This module has no Part1/Part2/Part3 imports and performs no file reads.
"""
from __future__ import annotations

import json
import re

LOCAL_PROVIDERS = frozenset(('ollama', 'local_gguf', 'local_lora'))
SAFE_TOOLS = frozenset(('vocabulary', 'list_specials', 'describe_special'))
DENIED_TOOL_MESSAGE = 'AI는 프로젝트 코드·파일을 조회할 수 없습니다. 제공된 전략 계약만 사용하세요.'
PRIVATE_FIELDS = frozenset(('source_text', 'source_code', 'code', 'python', 'path', 'file_path',
    'project_path', 'runtime', 'logs', 'log', 'data', 'cache', 'debug', 'traceback', 'text', 'excerpt'))


def _private_field(name):
    return sensitive_key(name) or str(name).casefold() in PRIVATE_FIELDS


def provider_scope(provider):
    """Transport location metadata only; it never grants AI permissions."""
    if provider is None:
        return 'external'
    if isinstance(provider, str):
        return 'local' if provider in LOCAL_PROVIDERS else 'external'
    if isinstance(provider, dict):
        return 'local' if provider.get('provider') in LOCAL_PROVIDERS else 'external'
    # SharedProvider resolves transport location from the current settings.
    scope = getattr(provider, 'permission_scope', None)
    if scope in ('local', 'external'):
        return scope
    return provider_scope(getattr(provider, 'settings', {}))


def require_tool(provider, name):
    if name not in SAFE_TOOLS:
        raise ValueError(DENIED_TOOL_MESSAGE)


def filter_tools(provider, specs):
    return [spec for spec in specs if isinstance(spec, dict) and
            (spec.get('function') or {}).get('name') in SAFE_TOOLS]


def sensitive_key(name):
    name = re.sub(r'[^a-z0-9]', '_', str(name).casefold()).strip('_')
    return (name in {'key', 'api_key', 'apikey', 'token', 'access_token', 'accesstoken',
                     'password', 'passwd', 'pwd', 'secret', 'authorization',
                     'connections', 'connection', 'credentials', 'credential',
                     'account', 'accounts', 'broker', 'login'} or
            any(word in name for word in ('api_key', 'apikey', 'password', 'passwd', 'credential',
                                         'secret', 'authorization', 'connection', 'account', 'broker')) or
            name.endswith(('_token', '_login', '_pass', '_pwd', 'accesstoken', 'refreshtoken')) or
            name in ('mt5_server', 'mt5_login', 'mt5_password'))


def secret_values(value):
    found = set()
    def visit(item, secret=False):
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, secret or sensitive_key(key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, secret)
        elif secret and isinstance(item, str) and item:
            found.add(item)
    visit(value)
    return tuple(sorted(found, key=len, reverse=True))


def redact(value, secrets=()):
    secrets = tuple(set(secrets) | set(secret_values(value)))
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items() if not sensitive_key(key)}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, str):
            # Also mask credential values escaped inside JSON response strings.
            try:
                parsed = json.loads(item)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                masked = clean(parsed)
                if masked != parsed:
                    return json.dumps(masked, ensure_ascii=False)
            for secret in sorted(secrets, key=len, reverse=True):
                if secret:
                    item = item.replace(secret, '[REDACTED]')
            item = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [REDACTED]', item)
            def assignment(match):
                return match.group(2) + '=[REDACTED]' if sensitive_key(match.group(2)) else match.group(0)
            item = re.sub(r'''(?i)(?<![A-Za-z0-9_])(["']?)([A-Za-z][A-Za-z0-9_-]*)\1\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''',
                          assignment, item)
        return item
    return clean(value)


_CODE = re.compile(r'(?m)^\s*(?:from\s+\w[\w.]*\s+import\s|import\s+\w|(?:async\s+)?def\s+\w+\s*\(|class\s+\w+\s*[:(]|function\s+\w+\s*\(|(?:const|let|var)\s+\w+\s*=)|```(?:python|javascript|py|js)\b|<script\b', re.I)
_PATH = re.compile(r'(?i)(?:[A-Z]:[\\/][^\s\"\']+|(?:Part[123]|common_ai|runtime|state|logs?|data|cache|settings)[\\/][^\s\"\']+|(?:/home/|/Users/|/tmp/)[^\s\"\']+)')


def _text(value, secrets=(), limit=None):
    if not isinstance(value, str):
        return ''
    value = redact(value, secrets)
    if _CODE.search(value):
        return '[프로젝트 코드 내용 제외]'
    value = _PATH.sub('[내부 경로 제외]', value)
    return value if limit is None else value[:limit]


def _json(value):
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _schema_fields(schema):
    fields = set()
    def visit(node):
        if isinstance(node, dict):
            fields.update((node.get('properties') or {}).keys())
            for item in node.values():
                visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(schema)
    return fields


def safe_schema(schema, secrets=()):
    """Only JSON schema keywords; annotations cannot carry source/documents."""
    if schema is None:
        return None
    maps = {'properties', '$defs', 'definitions', 'dependentSchemas', 'patternProperties'}
    children = {'items', 'contains', 'additionalProperties', 'not', 'if', 'then', 'else', 'propertyNames'}
    arrays = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    literals = {'type', 'enum', 'const', 'required', 'minimum', 'maximum', 'exclusiveMinimum',
                'exclusiveMaximum', 'multipleOf', 'minLength', 'maxLength', 'pattern',
                'minItems', 'maxItems', 'uniqueItems', 'minContains', 'maxContains',
                'minProperties', 'maxProperties', 'dependentRequired', 'format'}
    def visit(node):
        if isinstance(node, bool):
            return node
        if not isinstance(node, dict):
            return {}
        result = {}
        for key, item in node.items():
            if key in maps and isinstance(item, dict):
                result[key] = {name: visit(sub) for name, sub in item.items() if not _private_field(name)}
            elif key in children:
                result[key] = visit(item)
            elif key in arrays and isinstance(item, list):
                result[key] = [visit(sub) for sub in item]
            elif key == '$ref' and isinstance(item, str) and item.startswith('#/'):
                result[key] = item
            elif key in literals:
                # Contract literals and field names are data, never annotations.
                result[key] = _literal(item, secrets)
        return result
    return visit(schema)


def _literal(value, secrets=()):
    if isinstance(value, dict):
        return {str(key): _literal(item, secrets) for key, item in value.items() if not _private_field(key)}
    if isinstance(value, list):
        return [_literal(item, secrets) for item in value]
    return _text(value, secrets) if isinstance(value, str) else value


RECIPE_FIELDS = {'schema_version', 'base', 'name', 'description', 'symbols', 'strategy_intent'}
VOCAB_FIELDS = {'intent_kinds', 'intent_parameters', 'strategy_fields', 'independent_branch_fields',
                'timeframes', 'oz_timeframes', 'ma', 'sequential', 'symbols', 'metrics', 'profiles',
                'validation_modes', 'session_names', 'presets', 'lifecycle_fields', 'candle_state'}
MA_FIELDS = {'families', 'period', 'cross', 'ma_left', 'ma_right', 'price_cross', 'ma_family',
             'slow_period', 'relation', 'price_touch', 'event_default_bar_state'}
CANDLE_FIELDS = {'kind', 'sides', 'bar_states', 'default_bar_state', 'bull', 'bear', 'flat',
                 'forming', 'closed', 'trade_direction', 'final_conditions', 'colors'}


def _semantic(value, fields, secrets=()):
    if isinstance(value, dict):
        return {key: _semantic(item, fields, secrets) for key, item in value.items()
                if key in fields and not _private_field(key)}
    if isinstance(value, list):
        return [_semantic(item, fields, secrets) for item in value]
    return _text(value, secrets) if isinstance(value, str) else value


def _nodes(schema, root):
    """Resolve only local schema references and collect structural variants."""
    if not isinstance(schema, dict):
        return []
    reference = schema.get('$ref')
    if isinstance(reference, str) and reference.startswith('#/'):
        target = root
        try:
            for name in reference[2:].split('/'):
                target = target[name.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            return []
        return _nodes(target, root)
    nodes = [schema]
    for key in ('anyOf', 'oneOf', 'allOf'):
        for branch in schema.get(key) or []:
            nodes.extend(_nodes(branch, root))
    return nodes


def _field_schema(schema, name):
    nodes = _nodes(schema, schema)
    found = [node['properties'][name] for node in nodes if name in (node.get('properties') or {})]
    return {'anyOf': found} if found else None


def _schema_data(value, schema, secrets=(), *, root=None):
    """Project data along schema edges, including permitted dynamic maps.

    Snapshot names and session names are user data in additionalProperties.
    A flat field-name whitelist would silently erase valid lifecycle rules.
    """
    root = schema if root is None else root
    nodes = _nodes(schema, root)
    if isinstance(value, dict):
        properties = {}
        extras = []
        for node in nodes:
            for key, child in (node.get('properties') or {}).items():
                properties.setdefault(key, []).append(child)
            if isinstance(node.get('additionalProperties'), dict):
                extras.append(node['additionalProperties'])
        result = {}
        for key, item in value.items():
            if _private_field(key):
                continue
            children = properties.get(key)
            if children is None and extras and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', key):
                children = extras
            if children:
                result[key] = _schema_data(item, {'anyOf': children}, secrets, root=root)
        return result
    if isinstance(value, list):
        children = [node['items'] for node in nodes if isinstance(node.get('items'), dict)]
        return [_schema_data(item, {'anyOf': children}, secrets, root=root) for item in value] if children else _literal(value, secrets)
    return _text(value, secrets) if isinstance(value, str) else value


def _recipe(value, schema, secrets):
    if not isinstance(value, dict):
        return None
    meaning_schema = _field_schema(schema, 'interpretation') or schema
    return {key: (_schema_data(item, meaning_schema, secrets, root=schema) if key == 'strategy_intent' else _literal(item, secrets))
            for key, item in value.items() if key in RECIPE_FIELDS}


def safe_tool_result(name, result, *, schema=None, secrets=()):
    if name in ('vocabulary', 'describe_special') and not isinstance(result, dict):
        raise ValueError('전략 조회 결과 형식이 올바르지 않습니다.')
    if name == 'list_specials' and not isinstance(result, list):
        raise ValueError('전략 조회 결과 형식이 올바르지 않습니다.')
    if name == 'vocabulary':
        clean = {}
        for key, value in (result or {}).items():
            if key not in VOCAB_FIELDS:
                continue
            if key == 'ma':
                clean[key] = _semantic(value, MA_FIELDS, secrets)
            elif key == 'sequential':
                clean[key] = _semantic(value, {'within_sec'}, secrets)
            elif key == 'candle_state':
                clean[key] = _semantic(value, CANDLE_FIELDS, secrets)
            elif key == 'intent_parameters' and isinstance(value, dict):
                kinds = set((result or {}).get('intent_kinds') or [])
                clean[key] = {kind: [_text(item, secrets, 100) for item in fields if isinstance(item, str)]
                              for kind, fields in value.items() if kind in kinds and isinstance(fields, list)}
            elif isinstance(value, list):
                clean[key] = [_text(item, secrets, 100) for item in value if isinstance(item, str)]
        return clean
    if name == 'list_specials':
        return [{key: _text(row.get(key), secrets, 200) for key in ('id', 'name')}
                for row in (result or []) if isinstance(row, dict)]
    if name == 'describe_special':
        return {key: (_recipe(value, schema, secrets) if key == 'recipe' else _text(value, secrets, 200))
                for key, value in (result or {}).items() if key in ('id', 'name', 'recipe')}
    raise ValueError(DENIED_TOOL_MESSAGE)


def _selection(value, secrets):
    if not isinstance(value, dict):
        return {}
    options = value.get('options') or {}
    names = {'symbol', 'symbols', 'specials', 'selected_specials', 'mode', 'result_mode', 'spread_points',
             'special_settings', 'default_triggers', 'trigger_choices', 'virtual_entry'}
    result = {'today': _text(value.get('today'), secrets, 10),
              'options': {key: _literal(item, secrets) for key, item in options.items() if key in names}}
    # These are the existing BACKTEST command selectors, not files, logs or
    # calculated result data. Absolute/relative paths are never selectors.
    result['generated'] = [{key: _text(row[key], secrets, 200) for key in ('filename', 'name', 'base') if key in row
                            and isinstance(row[key], str) and '/' not in row[key] and '\\' not in row[key]}
                           for row in value.get('generated', []) if isinstance(row, dict)]
    job_keys = {'job_id', 'kind', 'phase', 'symbol', 'start', 'end', 'mode', 'active', 'result_ready', 'created_at'}
    result['jobs'] = [{key: _literal(item, secrets) for key, item in row.items() if key in job_keys}
                      for row in value.get('jobs', [])[:20] if isinstance(row, dict)]
    return result


def _context(value, schema, secrets):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key == 'recipe':
            result[key] = _recipe(item, schema, secrets)
        elif key in ('current_strategy', 'previous_plan', 'previous_command'):
            node = (_field_schema(schema, 'strategy') or schema) if key == 'current_strategy' else (
                _field_schema(schema, 'plan') or schema) if key == 'previous_plan' else schema
            result[key] = _schema_data(item, node, secrets, root=schema)
        elif key == 'selection':
            result[key] = _selection(item, secrets)
        elif key in ('question', 'previous_kind', 'mode'):
            result[key] = _text(item, secrets, 2000)
        elif key == 'backtest_has_draft' and type(item) is bool:
            result[key] = item
    return result


_POLICY_PROMPT = '''너는 모세의 지팡이 전략 연구 통역기다. 제공된 공통 schema와 vocabulary만 사용한다.
사용자가 요청한 조건·봉 기준·방향·순서·객체 참조·수명 규칙을 보존한다. 조건과 시간을 임의 추가하지 않는다.
현재 전략이 있으면 수정 요청은 그 전체 intent의 언급한 부분만 변경한다. 상태와 신규 사건을 구분한다.
TREND는 추세, MA_STATE는 현재 배열, MA_CROSS는 새 MA 교차, MA_PRICE_CROSS/TOUCH는 가격과 MA의 사건이다.
FVG_NEW는 신규 생성, FVG_TOUCH는 접촉이며 ref/scope_ref는 캡처한 동일 객체/활성 범위다.
capture는 발생한 객체에 이름을 붙여 저장한다. ref/scope_ref는 이미 정의된 이름을 참조한다.
새 객체를 생성하는 단계에는 capture를 쓰고, 그 객체를 사용하는 후속 단계에 ref/scope_ref를 쓴다.
SEQUENTIAL의 시간 제한 생략은 무제한이다. 최종 window와 사건 간 within_sec를 혼동하지 않는다.
일반은 NORMAL, 무지성은 BLIND, 브레이커는 trigger_mode=BREAKER다. 시간봉은 원문 그대로 유지한다.
사용자가 무지성을 요청하지 않으면 최종 validation_mode는 NORMAL이다. 브레이커만 요청하면 NORMAL+BREAKER다.
최종 올존은 interpretation.final에만 표현한다. steps의 OZ_ALERT는 사용자가 별도로 요청한 선행 올존 사건이며 최종 올존을 중복해서 넣지 않는다.
필요한 의미만 JSON으로 반환한다. 핵심 의미가 모호할 때만 짧은 확인 질문을 한다.
전략 출력은 기존 supported/intent/interpretation canonical 자체이며 불필요한 외부 envelope를 붙이지 않는다.
연구 schema에 BACKTEST가 있으면 백테스트·데이터 구축·조회·중지 요청을 kind=BACKTEST 전체 계획으로 반환한다.
새 조건의 백테스트는 전체 canonical strategy와 draft=true 계획을 함께 반환하며 plan.strategy_text=null이다.
기존 preset/생성 전략 요청은 draft=false, 현재 연구 전략은 draft=true다. steps의 START는 순차 작업이다.
START/STOP/STATUS/RECENT/RECONNECT를 사용한다. 날짜는 YYYY-MM-DD, 종료일 미포함, 선택 정보의 today 기준이다.
최근 N일/개월/년 백테스트는 today 기준의 요청 기간을 유지하고 command.request.available_only=true로 둔다.
이는 요청 기간 안의 검증된 보유 데이터만 사용하고 부족한 구간은 제외하며 자동 구축하지 않는 정책이다. 보유 마지막 날짜로 요청 기간을 옮기지 않는다.
데이터 구축·재구축이나 오늘까지 새 데이터를 채우라고 명시하면 available_only=false다. 일반 명시 기간 요청의 기본값은 false다.
조건 수정과 기간/종목 수정을 구분하며 직전 전체 계획의 언급하지 않은 값을 보존한다.
가상진입 정책은 command.request.virtual_entry에만 표현하며 strategy/interpretation의 알림 조건을 바꾸지 않는다.
virtual_entry.mode는 AUTO/IMMEDIATE/CONFIRM이다. IMMEDIATE는 알림 시점 가격 진입이며 conditions는 빈 목록이다.
AUTO는 공통 기본 설정을 선택하므로 conditions는 빈 목록이다. 사용자 지정 확인 조건은 CONFIRM에서만 사용한다.
CONFIRM은 tf의 직전 확정봉이 매수 양봉/매도 음봉으로 마감되고 추가 conditions와 filters를 모두 만족하면 현재봉 시가에 진입한다.
tf=SIGNAL은 각 실제 알림의 시간봉이며 종목·전략 전체에서 임의로 가장 낮은 시간봉을 추측하지 않는다.
추가 확인 MA_POSITION은 현재봉 시가가 MA 위(매수)/아래(매도), MA_CROSS는 직전 확정봉 종가가 MA 상향(매수)/하향(매도) 돌파,
MA_TOUCH는 MA 터치 후 방향 확인, ENGULFING은 확정봉 인걸핑, NECKLINE_BREAK는 해당 올존 넥라인을 확정봉 종가로 돌파다.
MA 조건에는 family=SMA/EMA/WMA/HMA, period, tf를 사용한다. ENGULFING/NECKLINE_BREAK에는 MA 필드를 넣지 않는다.
atr에는 tf와 period를 넣는다. filters의 CANDLE_ATR는 직전 확정봉 BODY(몸통)/RANGE(전체길이)의 ATR 최소·최대 배수,
MA_DISTANCE_ATR는 현재봉 시가와 지정 MA의 거리의 ATR 최소·최대 배수다. min/max는 미지정 경계만 null이며 최소 또는 최대는 하나 이상 필요하다.
stop.kind는 AUTO/OZ_B0/RECENT_EXTREME/ATR다. OZ_B0는 해당 올존 B0, RECENT_EXTREME는 지정 tf의 직전 bars개 확정봉 극값,
ATR은 지정 tf의 ATR(period는 atr.period) multiplier배다. 손익비 1:1/1:1.5/1:2는 공통 결과다.
AUTO 기본은 올존 확인 진입·신호봉 HMA6 시가 위치·B0 손절, 그 외 알림 즉시 진입·ATR 1배 손절이다. 이것은 동일 계약의 기본 설정이며 별도 전략 조건이 아니다.
올존은 해당 패턴 유효기간 안에서만 진입하며 다른 알림에는 임의 만료시간을 추가하지 않는다.
사용자가 지정하지 않은 virtual_entry는 null로 두어 저장 설정을 사용한다. 후속 기간·종목 수정에서는 직전 virtual_entry 전체를 보존한다.
mode=chat에서 설명·인사·아이디어 논의는 schema가 허용한 CHAT message_ko로 답한다.
mode=strategy에서는 대화 답변으로 대체하지 않는다. 파일 생성·적용·실행은 프로그램이 사용자 확인 뒤 한다.
코드·파일·계정·인증정보는 조회하지 않는다. 허용된 읽기 전용 용어/preset 도구만 사용한다.'''


def external_payload(messages, tools, response_schema, *, role='strategy', settings=None):
    """Rebuild every Provider payload by purpose under the same allowlist.

    Unknown system strings and old local assistant/tool output are discarded.
    Typed contracts are filtered again even when callers already sanitized them.
    Applying this at the service and transport boundaries is idempotent.
    """
    secrets = secret_values(settings or {})
    schema = safe_schema(response_schema, secrets)
    contract = {}
    clean = []
    for row in messages:
        if not isinstance(row, dict):
            continue
        content = row.get('content') or ''
        value = _json(content)
        kind = row.get('role')
        if kind == 'system':
            if isinstance(value, dict) and isinstance(value.get('moses_contract'), dict):
                data = value['moses_contract']
                if value.get('purpose') in ('watch', 'backtest', 'strategy'):
                    role = value['purpose']
                if 'vocabulary' in data:
                    contract['vocabulary'] = safe_tool_result('vocabulary', data['vocabulary'], schema=schema, secrets=secrets)
                if 'presets' in data:
                    contract['presets'] = safe_tool_result('list_specials', data['presets'], schema=schema, secrets=secrets)
                if 'selection' in data:
                    contract['selection'] = _selection(data['selection'], secrets)
            elif role == 'backtest' and '현재 읽기 전용 선택 정보:\n' in content:
                snapshot = _json(content.split('현재 읽기 전용 선택 정보:\n', 1)[1])
                contract['selection'] = _selection(snapshot, secrets)
            continue
        if kind == 'user':
            if isinstance(value, dict) and any(key in value for key in ('message', 'request', 'context')):
                data = {key: _text(value[key], secrets) for key in ('message', 'request', 'mode') if key in value}
                data['context'] = _context(value.get('context'), schema, secrets)
                discussion = []
                for item in (value.get('discussion') or [])[-4:]:
                    if isinstance(item, dict) and item.get('role') == 'user':
                        discussion.append({'role': 'user', 'content': _text(item.get('content'), secrets, 2000)})
                    elif isinstance(item, dict) and item.get('role') == 'assistant':
                        summary = _json(item.get('content'))
                        if isinstance(summary, dict) and 'message_ko' in summary:
                            discussion.append({'role': 'assistant', 'content': _text(summary['message_ko'], secrets, 2000)})
                if discussion:
                    data['discussion'] = discussion
                content = json.dumps(data, ensure_ascii=False)
            else:
                content = _text(content, secrets)
            clean.append({'role': 'user', 'content': content})
        elif kind == 'assistant':
            calls = []
            for call in row.get('tool_calls') or []:
                function = call.get('function', call)
                name = function.get('name')
                if name not in SAFE_TOOLS:
                    continue
                arguments = function.get('arguments') or {}
                arguments = _json(arguments) if isinstance(arguments, str) else arguments
                arguments = {'number': _literal(arguments.get('number'), secrets)} if name == 'describe_special' and isinstance(arguments, dict) else {}
                calls.append({'id': _text(call.get('id') or name, secrets, 128), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}})
            if calls:
                clean.append({'role': 'assistant', 'content': '', 'tool_calls': calls})
            elif isinstance(value, dict) and any(key in value for key in ('interpretation', 'supported', 'plan', 'kind')):
                clean.append({'role': 'assistant', 'content': json.dumps(_schema_data(value, schema, secrets), ensure_ascii=False)})
        elif kind == 'tool':
            name = row.get('name')
            if name not in SAFE_TOOLS:
                clean.append({'role': 'user', 'content': DENIED_TOOL_MESSAGE})
            elif isinstance(value, dict):
                data = {'ok': value.get('ok') is True}
                if data['ok']:
                    data['result'] = safe_tool_result(name, value.get('result'), schema=schema, secrets=secrets)
                else:
                    data['error'] = '허용된 전략 조회를 완료하지 못했습니다.'
                clean.append({'role': 'tool', 'name': name, 'tool_call_id': _text(row.get('tool_call_id') or name, secrets, 128),
                              'content': json.dumps(data, ensure_ascii=False)})
    # Derive capabilities from the approved schema and actual advertised tools;
    # arbitrary manifest contents cannot enter model context through this field.
    contract['capabilities'] = {'fields': sorted(_schema_fields(schema)),
        'tools': sorted(spec['function']['name'] for spec in filter_tools('external', tools))}
    instruction = _POLICY_PROMPT
    if role == 'watch':
        instruction = 'WATCH 보조 해석이다. 사용자 조건을 추가·삭제하지 말고 제공된 canonical_text schema의 정규화 문장만 반환한다.'
    elif role == 'backtest':
        instruction += '\n백테스트 명령 전용이다. tools가 없으면 조회 도구를 호출하지 않는다. 제공된 선택 정보와 command/plan schema만 사용한다.'
    system = {'role': 'system', 'content': json.dumps({'purpose': role, 'instructions': instruction,
                 'moses_contract': contract}, ensure_ascii=False)}
    safe_tools = []
    descriptions = {'vocabulary': '공통 전략 용어와 지원 기능 조회', 'list_specials': '등록된 Recipe preset 이름 목록',
                    'describe_special': '등록된 Recipe preset의 공통 전략 의미 조회'}
    for spec in filter_tools('external', tools):
        function = spec['function']
        safe_tools.append({'type': 'function', 'function': {'name': function['name'],
            'description': descriptions[function['name']],
            'parameters': safe_schema(function.get('parameters') or {'type': 'object'}, secrets)}})
    return [system, *clean], safe_tools, schema
