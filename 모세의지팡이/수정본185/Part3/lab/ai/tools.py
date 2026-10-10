"""The model's complete tool surface: bounded, read-only project lookup.

These functions never execute project code. In particular no filesystem or
subprocess object is handed to the model, and no Recipe mutation is exposed.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import catalog

ROOT = catalog.ROOT.parent.resolve()
ALLOWED_PARTS = {'Part1', 'Part2', 'Part3'}
TEXT_SUFFIXES = {'.py', '.pyw', '.md', '.json', '.txt'}
DENIED_NAMES = {'config.txt', 'connections.json', 'ai_settings.json', 'last_draft.json'}
SENSITIVE_NAME_PARTS = ('secret', 'token', 'credential', 'api_key', 'password')
DENIED_PARTS = {'reference', 'generated', 'test_special', 'projects', 'build', 'data', '검증결과', '__pycache__'}
MAX_FILE_BYTES = 800_000
MAX_RESULT_CHARS = 12_000


def _project_file(relative_path: str) -> Path:
    raw = Path(str(relative_path).replace('\\', '/'))
    if raw.is_absolute() or len(raw.parts) < 2 or raw.parts[0] not in ALLOWED_PARTS:
        raise ValueError('현재 Part1/Part2/Part3의 상대 경로만 읽을 수 있습니다.')
    if any(part in ('', '.', '..') or part.lower() in DENIED_PARTS for part in raw.parts):
        raise ValueError('읽을 수 없는 경로입니다.')
    path = (ROOT / raw).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError('읽을 수 있는 코드/문서 파일이 아닙니다.')
    if (path.name.lower() in DENIED_NAMES or any(word in path.name.lower() for word in SENSITIVE_NAME_PARTS)
            or path.stat().st_size > MAX_FILE_BYTES):
        raise ValueError('설정 비밀 또는 큰 파일은 AI가 읽을 수 없습니다.')
    return path


class ReadOnlyWorkspace:
    def vocabulary(self):
        from .schema import KINDS, PARAMS, session_keys, LIFECYCLE_KEYS, MEANING_KEYS, BRANCH_KEYS
        from strategy_recipe.registry import list_presets
        return {
            'intent_kinds': sorted(KINDS),
            'intent_parameters': {k: sorted(v) for k, v in PARAMS.items()},
            'strategy_fields': sorted(MEANING_KEYS),
            'independent_branch_fields': sorted(BRANCH_KEYS),
            'python_condition_contract': sorted(catalog.CONDITIONS),
            'timeframes': list(catalog.TF_LABELS),
            'oz_timeframes': catalog.OZ_TFS,
            'ma': {'families': ['SMA', 'WMA', 'EMA', 'HMA'], 'period': 'positive_integer',
                   'cross': {'ma_left': 'family+period', 'ma_right': 'family+period'},
                   'price_cross': {'ma_family': 'family', 'slow_period': 'positive_integer',
                       'relation': ['BREAK_UP', 'BREAK_DOWN', 'BOTH']},
                   'price_touch': {'ma_family': 'family', 'slow_period': 'positive_integer'},
                   'event_default_bar_state': 'CLOSED'},
            'sequential': {'within_sec': 'optional_positive_integer; absent/null means no timeout',
                'within': '{"seconds": S} or {"bars": N, "tf": TF}; bars count the actual bars after the bar the first '
                    'condition was judged on; never together with within_sec'},
            'recent': '{"seconds": S} or {"bars": N, "tf": TF} on an event condition: it occurred within that window. '
                'In cancel_conditions it cancels for the window; with negated=true in steps it requires no such event. '
                'An OZ counts from the moment the OZ engine completed it.',
            'step_directions': ['LONG', 'SHORT', 'BOTH', 'SAME_AS_PREVIOUS_DIRECTION', 'OPPOSITE'],
            'candle_state': {'kind': 'CANDLE_STATE', 'sides': ['BULL', 'BEAR'],
                'bar_states': ['FORMING', 'CLOSED'], 'default_bar_state': 'FORMING',
                'bull': 'BULL means selected candle close > its own open.',
                'bear': 'BEAR means selected candle close < its own open.',
                'flat': 'close == open is neither BULL nor BEAR.',
                'forming': 'FORMING compares the current candle price to its own open on every observation.',
                'closed': 'CLOSED compares the most recently completed candle close to its own open.',
                'trade_direction': 'Candle side is independent of trade LONG/SHORT. It is not TREND or MA state.',
                'final_conditions': 'For a candle state that must still hold when final OZ fires, use final_conditions. '
                    'Earlier candle truth must not authorize a later OZ after the candle changes side. '
                    'Use steps only for requested prerequisite conditions or ordered events.',
                'colors': 'Chart colors are configurable; do not infer BULL/BEAR from color alone.'},
            'symbols': catalog.SYMBOLS,
            'metrics': sorted(catalog.METRICS),
            'profiles': sorted(catalog.PROFILES.TRIGGER_MODES),
            'validation_modes': sorted(catalog.PROFILES.VALIDATION_MODES),
            'session_names': session_keys(),
            'presets': list(list_presets()),
            'lifecycle_fields': sorted(LIFECYCLE_KEYS),
        }

    def list_specials(self):
        from strategy_recipe.registry import entries
        return [{'id': name, 'name': row['name']} for name, row in entries().items()]

    def describe_special(self, number: int):
        return catalog.describe_special(number)

    def read_doc(self, name: str, start: int = 0, length: int = 6000):
        path = _project_file(name)
        if path.suffix.lower() != '.md':
            raise ValueError('read_doc은 Markdown 문서만 읽습니다.')
        content = path.read_text('utf-8-sig', errors='replace')
        begin = max(0, int(start))
        return {'path': name, 'start': begin, 'total': len(content),
                'text': content[begin:begin + min(max(1, int(length)), MAX_RESULT_CHARS)]}

    def read_project_code(self, path: str, start_line: int = 1, line_count: int = 80):
        source = _project_file(path).read_text('utf-8-sig', errors='replace').splitlines()
        first = max(1, int(start_line))
        count = min(max(1, int(line_count)), 120)
        return {'path': path, 'start_line': first, 'total_lines': len(source),
                'text': '\n'.join(f'{i}: {source[i-1]}' for i in range(first, min(len(source), first + count - 1) + 1))[:MAX_RESULT_CHARS]}

    def search_project_code(self, query: str, limit: int = 12):
        phrase = str(query).strip()
        if not 2 <= len(phrase) <= 100 or '\n' in phrase:
            raise ValueError('검색어는 2~100자 한 줄이어야 합니다.')
        # Fixed priority; no glob/regex supplied by the model.
        found = []
        for part in ('Part1', 'Part2', 'Part3'):
            for path in sorted((ROOT / part).rglob('*')):
                relative = path.relative_to(ROOT).as_posix()
                try:
                    if not path.is_file() or not path.resolve().is_relative_to(ROOT) or path.suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    if any(p.lower() in DENIED_PARTS for p in path.relative_to(ROOT).parts):
                        continue
                    if (path.name.lower() in DENIED_NAMES or any(word in path.name.lower() for word in SENSITIVE_NAME_PARTS)
                            or path.stat().st_size > MAX_FILE_BYTES):
                        continue
                    for i, line in enumerate(path.read_text('utf-8-sig', errors='replace').splitlines(), 1):
                        if phrase.casefold() in line.casefold():
                            found.append({'path': relative, 'line': i, 'excerpt': line.strip()[:200]})
                            if len(found) >= min(max(1, int(limit)), 20):
                                return {'matches': found}
                except (OSError, UnicodeError):
                    continue
        return {'matches': found}


def _fn(name: str, description: str, properties=None, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties or {}, 'required': list(required)}}}


TOOL_SPECS = [
    _fn('vocabulary', '현재 코드에서 읽은 TF·지표·조건·프로필 계약. 내부 조회 후 원래 요청을 계속 수행.'),
    _fn('list_specials', '현재 registry에 등록된 공통 Recipe 프리셋을 조회.'),
    _fn('describe_special', '등록된 프리셋의 공통 Recipe 구조를 조회.',
        {'number': {'type': ['string', 'integer']}}, ('number',)),
    _fn('read_doc', '현재 Part3 문서 일부를 읽음. 경로는 Part3/... 상대 경로.',
        {'name': {'type': 'string'}, 'start': {'type': 'integer'}, 'length': {'type': 'integer'}}, ('name',)),
    _fn('search_project_code', '현재 Part1→Part2→Part3의 허용 코드/문서를 텍스트로 검색.',
        {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, ('query',)),
    _fn('read_project_code', '현재 허용 코드/문서의 지정 줄만 읽음. 실행하지 않음.',
        {'path': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'line_count': {'type': 'integer'}}, ('path',)),
]
