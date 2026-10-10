"""Read-only Recipe templates and faithful language examples for research.

Preset identifiers select data only. The full canonical meaning, rather than
its generated example, is the source of the editable draft.
"""
from __future__ import annotations

import copy

from .. import catalog as project_catalog
from .schema import validate_intent, recipe_from_intent
from strategy_recipe import registry


LABELS = {
    'LONG': '매수', 'SHORT': '매도', 'BOTH': '매수·매도',
    'SAME_AS_PREVIOUS_DIRECTION': '이전 조건과 같은 방향', 'OPPOSITE': '반대 방향',
    'NORMAL': '일반', 'BLIND': '무지성', 'BREAKER': '브레이커',
    'OZ': '올존', 'NOTIFY': '알림', 'DEFINE': '조건 정의만 저장',
    'ALL': '모든 조건을 만족', 'ANY': '하나 이상의 조건을 만족',
    'INDEPENDENT': '조건별 독립 판단', 'MATCHING_FAMILY': '앞 조건과 같은 지표 계열',
    'SIMULTANEOUS': '동시에 평가', 'SEQUENTIAL': '조건 순서대로 충족',
    'UNORDERED': '순서와 관계없이 사건 충족',
    'CLOSED': '확정봉 기준', 'FORMING': '진행봉 기준', 'UNSPECIFIED': '봉 기준 별도 지정 없음',
    'TREND': '추세', 'TREND_METRIC': '지표 수치 비교', 'CANDLE_STATE': '캔들 양봉·음봉', 'WONBI_TOUCH': 'WONBI 터치',
    'CANDLE_SHAPE': '캔들 모양', 'HAMMER': '망치형', 'INVERTED_HAMMER': '역망치형',
    'FVG_STATE': 'FVG 상태', 'FVG_NEW': 'FVG 신규 발생', 'FVG_TOUCH': 'FVG 터치',
    'MA_STATE': '이동평균 배열', 'MA_PRICE_STATE': '가격과 이동평균 위치',
    'MA_SLOPE_STATE': '이동평균 기울기', 'MA_CROSS': '이동평균 교차',
    'MA_PRICE_CROSS': '가격의 이동평균 교차', 'MA_PRICE_TOUCH': '가격의 이동평균 터치',
    'EXTERNAL_LIQUIDITY_TOUCH': '외부 유동성 터치', 'LIQUIDITY_LEVEL': '유동성 기준',
    'PRICE_LEVEL': '가격 기준', 'SESSION_START': '세션 시작', 'BAR_CLOSE': '봉 마감',
    'OZ_ALERT': '올존 알림 발생', 'REGIME_BAND': '레짐 밴드',
    'PERCENTILE_OUT': '퍼센타일 밴드 밖으로 이탈',
    'PERCENTILE_OUT_IN': '퍼센타일 밴드 이탈 후 안으로 복귀',
    'LOWER': '하단', 'UPPER': '상단', 'LOW': '저점', 'HIGH': '고점',
    'BULL': '상승', 'BEAR': '하락', 'UP': '상승', 'DOWN': '하락',
    'ABOVE': '위에 있음', 'BELOW': '아래에 있음', 'EXISTS': '영역 존재', 'AREA': '영역 내부',
    'IN': '밴드 내부', 'OUT_LOWER': '밴드 하단 밖', 'OUT_UPPER': '밴드 상단 밖',
    'TOUCH': '터치', 'BREAK_UP': '상향 돌파', 'BREAK_DOWN': '하향 돌파',
    'SLOPE_UP': '기울기 상승', 'SLOPE_DOWN': '기울기 하락',
    'GT': '초과', 'GTE': '이상', 'LT': '미만', 'LTE': '이하', 'EQ': '같음', 'NE': '다름',
    'PRICE': '가격', 'RSI': 'RSI', 'STO': '스토캐스틱', 'DI': '이격도',
    'MAIN_ASIA': '아시아장', 'MAIN_LONDON': '런던장', 'MAIN_NEWYORK': '뉴욕장',
    'SOURCE': '선행 조건 시간봉', 'FINAL': '최종 감시 시간봉',
    'SYMBOL': '같은 종목의 이전 감시 교체', 'SYMBOL_DIRECTION': '같은 종목·방향의 이전 감시 교체',
    'FAVORABLE': '진행 방향', 'ADVERSE': '반대 방향',
    'ALL_CONDITIONS': '조건이 모두 모인 때부터', 'FIRST_CONDITION': '처음 조건이 발생한 때부터',
    'WHILE_ACTIVE': '감시하는 동안 계속 검사', 'AT_START': '감시를 시작할 때만 검사',
    'open': '시가', 'high': '고가', 'low': '저가', 'close': '종가',
}
FIELDS = {
    'direction': '방향', 'symbols': '종목', 'steps': '조건', 'branches': '독립 분기',
    'order_mode': '조건 관계', 'global_combine': '조건 연결', 'within_sec': '사건 사이 시간 제한(초)',
    'within': '사건 사이 기간', 'recent': '최근 기간',
    'final_window_sec': '최종 감시 시간 제한(초)', 'final_after': '최종 감시 시작 사건',
    'persistent': '반복 감시', 'final': '최종 행동', 'after_conditions': '선행 사건 충족 후 유지할 조건',
    'final_conditions': '최종 행동 시 다시 확인할 조건', 'cancel_conditions': '감시 취소 조건',
    'lifecycle': '감시 수명과 취소', 'time_filters': '조건 거래시간', 'final_time_filters': '최종 행동 거래시간',
    'symbol_source': '종목 출처', 'preset': '기반 템플릿', 'tfs': '시간봉', 'tf': '시간봉',
    'tf_combine': '시간봉 연결', 'bar_state': '봉 기준', 'side': '위치', 'state': '상태', 'shape': '모양',
    'relation': '비교 관계', 'ma_family': '이동평균 계열', 'fast_period': '빠른 이동평균 기간',
    'slow_period': '이동평균 기간', 'ma_left': '첫 이동평균', 'ma_right': '둘째 이동평균',
    'price_tf': '가격 시간봉', 'lookback': '비교 봉 수', 'session': '세션', 'level': '가격·유동성 기준',
    'families': '지표 계열', 'regime_families': '레짐 지표 계열', 'family_combine': '지표 계열 연결',
    'regime_family': '최종 지표 계열', 'metric': '지표', 'metric_operator': '수치 비교', 'metric_value': '기준값',
    'validation_mode': '감시 모드', 'trigger_mode': '최종 트리거', 'capture': '사건 기록 이름',
    'ref': '동일 사건 참조', 'scope_ref': '선행 사건 범위 참조', 'negated': '조건 불충족 판정',
    'expires': '감시 만료', 'seconds': '초', 'bars': '봉 수', 'snapshots': '발생 시점 고정 기준값',
    'field': '고정할 값', 'excursion': '이동 폭 취소', 'anchor': '시작 기준 참조',
    'snapshot': '고정 값 참조', 'multiplier': '배수', 'replace': '새 조건 발생 시', 'scope': '교체 범위',
    'first_success': '처음 성립한 알림 후 같은 묶음 감시 종료',
    'invalidate_refs': '선행 사건 무효화 시 후속 감시 취소', 'restart_on': '새 감시 주기 시작 조건',
    'enabled': '사용', 'start': '시작', 'end': '종료',
    'final_window_from': '최종 감시 시간을 세기 시작하는 때', 'precondition_check': '선행 상태 조건 검사 방식',
    'bars_setting': '봉 수를 읽을 설정 이름', 'level_gate': '외부유동성 가격과 최종 올존의 ATR 거리 검사',
    'atr_period': 'ATR 기간', 'atr_mult': 'ATR 배수',
}
CONDITION_GROUPS = {'steps', 'after_conditions', 'final_conditions', 'cancel_conditions', 'restart_on'}


def _value(value, field=None):
    if value is None:
        return '제한 없음' if field in ('within_sec', 'final_window_sec') else '별도 지정 없음'
    if type(value) is bool:
        return '예' if value else '아니오'
    if isinstance(value, list):
        return ' · '.join(_value(item, field) for item in value) if value else '없음'
    if isinstance(value, dict):
        return '; '.join(f'{key if field == "snapshots" else FIELDS.get(key, LABELS.get(key, key))}: {_value(item, key)}'
                         for key, item in value.items()) if value else '없음'
    if field in ('symbols', 'capture', 'ref', 'scope_ref', 'anchor', 'snapshot', 'bars_setting'):
        return str(value)
    if field in ('time_filters', 'final_time_filters') and value == 0:
        return '거래시간 제한 없음'
    if field == 'relation' and value == 'BOTH':
        return '상향 또는 하향 교차'
    if field == 'level' and value == 'ALL':
        return '전체 유동성'
    if field in ('tf', 'tfs', 'price_tf'):
        if value in ('SOURCE', 'FINAL'):
            return LABELS[value]
        text = str(value)
        if text[:-1].isdigit() and text[-1:] in ('m', 'h', 'd', 'w'):
            return text[:-1] + {'m': '분봉', 'h': '시간봉', 'd': '일봉', 'w': '주봉'}[text[-1]]
    return LABELS.get(value, str(value)) if isinstance(value, str) else str(value)


def _condition(item):
    description = _value(item.get('kind'))
    candle = item.get('kind') == 'CANDLE_STATE'
    fields = [f'{"캔들 상태" if candle and key == "side" else FIELDS.get(key, key)}: '
              + ({'BULL': '양봉', 'BEAR': '음봉'}.get(value, _value(value, key))
                 if candle and key == 'side' else _value(value, key))
              for key, value in item.items() if key != 'kind']
    return description + (' · ' + '; '.join(fields) if fields else '')


def example(meaning, name):
    """Render every stored field; never use an ID-specific strategy description."""
    lines = [f'「{name}」를 바탕으로 다음 조건의 새 전략을 만들어 주세요.']

    def render(item, indent=''):
        for key, value in item.items():
            title = FIELDS.get(key, key)
            if key == 'branches':
                lines.append(indent + '아래 각 분기는 서로 독립적으로 판단합니다:')
                for index, branch in enumerate(value, 1):
                    lines.append(indent + f'분기 {index}')
                    render(branch, indent + '  ')
            elif key in CONDITION_GROUPS:
                lines.append(indent + title + ':')
                if not value:
                    lines.append(indent + '  없음')
                for index, condition in enumerate(value, 1):
                    lines.append(indent + f'  {index}. ' + _condition(condition))
            elif key == 'final':
                lines.append(indent + title + ': ' + _condition(value))
            elif key == 'lifecycle':
                lines.append(indent + title + ':')
                render(value, indent + '  ')
            else:
                lines.append(indent + title + ': ' + _value(value, key))

    render(meaning)
    return '\n'.join(lines)


def _meaning(entry):
    # The same overlay order as registry.preset_meaning/load_plugins.
    meaning = copy.deepcopy(entry['recipe']['strategy_intent'])
    meaning['time_filters'] = copy.deepcopy(entry.get('time_filters', []))
    meaning['final_time_filters'] = copy.deepcopy(entry.get('final_time_filters', 0))
    if entry.get('symbol_source') == 'CONFIG':
        meaning['symbols'] = list(project_catalog.current_symbols())
    return meaning


def catalog():
    """Registry is signature-refreshed by its public API on every listing."""
    from strategy_recipe import user_catalog
    root = registry.REGISTRY.parent.parent
    builtins = registry.builtin_entries()
    generated = user_catalog.generated_entries(root)
    registrations = user_catalog.registrations(root)
    if set(builtins) & set(generated):
        raise ValueError('새 전략 ID가 기본 스페셜과 중복됩니다.')
    return [{'id': identifier, 'name': entry['name'],
             'example': example(_meaning(entry), entry['name']),
             'source': 'generated' if identifier in generated else 'builtin',
             'registrations': list(registrations.get(identifier, [])) if identifier in generated else []}
            for identifier, entry in {**builtins, **generated}.items()]


def load(preset_id):
    if not isinstance(preset_id, str):
        raise ValueError('불러올 전략을 선택해 주세요.')
    entry = registry.preset_entry(preset_id)
    meaning = _meaning(entry)
    if not meaning.get('symbols'):
        raise ValueError('현재 설정에 종목이 없습니다. 종목을 설정한 뒤 전략을 불러오세요.')
    value = validate_intent({'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': meaning, 'needs_clarification': False, 'clarification_question': None,
        'message_ko': entry['name'] + '을 새 전략 초안으로 불러왔습니다. 원본은 변경하지 않습니다.'})
    # The public validator has already expanded any inherited preset meaning.
    # A research copy keeps that meaning, without a live link to its source ID.
    value['interpretation'].pop('preset', None)
    value = validate_intent(value)
    recipe_from_intent(value)
    return {'id': preset_id, 'name': entry['name'], 'example': example(value['interpretation'], entry['name']),
            'strategy': value}
