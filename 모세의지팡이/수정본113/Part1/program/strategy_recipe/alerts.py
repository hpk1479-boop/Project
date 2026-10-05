"""Pure SPECIAL-style presentation for every Recipe strategy.

Only the declared rule and already accepted output/observation fields are read.
No indicators, strategy decisions, clocks, storage or delivery live here.
"""
from __future__ import annotations

import math
import re

SEPARATOR = '──────────────────'


def _text(value):
    return ' '.join(str(value or '').split())


def tf_label(value):
    raw = str(value or '')
    match = re.fullmatch(r'(\d+)([mhdw])', raw)
    return match[1] + {'m': '분', 'h': '시간', 'd': '일', 'w': '주'}[match[2]] if match else raw


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _price(value):
    number = _number(value)
    return f'{number:,.2f}' if number is not None else '-'


def _profile(item):
    from oz_profiles import profile_label
    # Unknown output data is displayed as absent, never as an invented profile.
    vm, tm = item.get('validation_mode'), item.get('trigger_mode')
    return profile_label(vm, tm) if vm and tm else ''


def _bound_tfs(step, machine, observation=None):
    reference = machine.captures.get(step.get('ref')) or {}
    actual = (observation or {}).get('source_tf')
    values = []
    for tf in step.get('tfs', ()):
        if tf == 'SOURCE':
            tf = reference.get('tf') or machine.source_tf or actual
        elif tf == 'FINAL':
            tf = machine.final_tf or actual
            if tf == 'SOURCE':
                tf = reference.get('tf') or machine.source_tf or actual
        if tf and tf not in ('SOURCE', 'FINAL'):
            values.append(tf_label(tf))
    if not values and actual:
        values.append(tf_label(actual))
    separator = '·' if step.get('tf_combine') == 'ALL' else '/'
    return separator.join(dict.fromkeys(values))


def condition_text(step, machine, observation=None):
    """Describe this selected branch, including directional/TF references."""
    kind = step['kind']
    tf = _bound_tfs(step, machine, observation)
    chosen = step.get('_resolved_direction', step.get('direction', machine.direction))
    if chosen in ('BOTH', 'SAME_AS_PREVIOUS_DIRECTION'):
        chosen = machine.direction
    upward = chosen == 'LONG'
    side = step.get('side', '')
    ma = str(step.get('ma_family', '')) + str(step.get('slow_period', ''))
    relation = {'ABOVE': '위', 'BELOW': '아래', 'TOUCH': '터치',
                'BREAK_UP': '상향 돌파', 'BREAK_DOWN': '하향 돌파', 'BOTH': '돌파'}.get(step.get('relation'), step.get('relation', ''))
    if kind == 'TREND':
        detail = ('상승' if upward else '하락') + ' 추세'
    elif kind == 'CANDLE_STATE':
        detail = '양봉' if step['side'] == 'BULL' else '음봉'
    elif kind == 'TREND_METRIC':
        compare = {'GT': '>', 'GTE': '≥', 'LT': '<', 'LTE': '≤', 'EQ': '=', 'NE': '≠'}.get(step['metric_operator'], step['metric_operator'])
        detail = f"{step['metric']} {compare} {step['metric_value']}"
    elif kind == 'MA_STATE':
        sign = '>' if (side or ('ABOVE' if upward else 'BELOW')) == 'ABOVE' else '<'
        detail = f"{step['ma_family']}{step['fast_period']} {sign} {ma}"
    elif kind == 'MA_PRICE_STATE':
        sign = '>' if (side or ('ABOVE' if upward else 'BELOW')) == 'ABOVE' else '<'
        price_tf = step.get('price_tf')
        if price_tf == 'SOURCE': price_tf = machine.source_tf
        if price_tf == 'FINAL': price_tf = machine.final_tf
        detail = f"{tf_label(price_tf) + ' ' if price_tf else ''}가격 {sign} {ma}"
    elif kind == 'MA_SLOPE_STATE':
        detail = f"{ma} {'우상향' if (side or ('UP' if upward else 'DOWN')) == 'UP' else '우하향'}"
        if step.get('lookback'): detail += f" ({step['lookback']}봉 전 비교)"
    elif kind == 'MA_CROSS':
        detail = f"{step['ma_left']}/{step['ma_right']} {'골든' if upward else '데드'}크로스"
    elif kind == 'MA_PRICE_CROSS':
        detail = f'{ma} 가격 {relation}'
    elif kind == 'MA_PRICE_TOUCH':
        detail = f'{ma} 가격 터치'
    elif kind.startswith('FVG'):
        trend = '상승' if (side or ('BULL' if upward else 'BEAR')) == 'BULL' else '하락'
        operation = {'FVG_NEW': '생성', 'FVG_TOUCH': '터치', 'FVG_STATE': '존재' if step.get('state', 'EXISTS') == 'EXISTS' else '터치'}[kind]
        detail = f'{trend} FVG {operation}'
        resource = (observation or {}).get('resource') or machine.captures.get(step.get('ref')) or machine.captures.get(step.get('capture')) or {}
        if _number(resource.get('lower')) is not None and _number(resource.get('upper')) is not None:
            detail += f" ({_price(resource['lower'])}~{_price(resource['upper'])})"
    elif kind == 'WONBI_TOUCH':
        detail = ('하단' if (side or ('LOWER' if upward else 'UPPER')) == 'LOWER' else '상단') + ' 원비 터치'
    elif kind == 'EXTERNAL_LIQUIDITY_TOUCH':
        levels = step.get('level') or 'ALL'
        if isinstance(levels, (list, tuple)): levels = '/'.join(map(str, levels))
        level_side = {'LOW': '하단 ', 'HIGH': '상단 '}.get(side, '')
        detail = f'{level_side}외부유동성 {levels} 터치'
    elif kind == 'SESSION_START':
        detail = {'MAIN_ASIA': '아시아', 'MAIN_LONDON': '런던', 'MAIN_NEWYORK': '뉴욕',
                  'ASIA': '아시아', 'LONDON': '런던', 'NEWYORK': '뉴욕'}.get(step['session'], step['session']) + ' 세션 시작'
    elif kind == 'BAR_CLOSE':
        detail = '봉 마감'
    elif kind == 'OZ_ALERT':
        detail = _profile(step) + ' 발생'
    elif kind == 'REGIME_BAND':
        relation = {'IN': '밴드 내부', 'ABOVE': '밴드 상단 위', 'BELOW': '밴드 하단 아래',
                    'OUT_UPPER': '밴드 상단 이탈', 'OUT_LOWER': '밴드 하단 이탈',
                    'SLOPE_UP': '우상향', 'SLOPE_DOWN': '우하향'}.get(step['relation'], step['relation'])
        families = step.get('regime_families') or [step.get('regime_family', '')]
        detail = '/'.join(families) + ' 레짐 ' + relation
    elif kind in ('PERCENTILE_OUT', 'PERCENTILE_OUT_IN'):
        boundary = {'LOWER': '하단', 'UPPER': '상단', 'BOTH': '상·하단'}.get(side, '하단' if upward else '상단')
        detail = '/'.join(step['families']) + f" {boundary} 아웃밴드 " + ('이탈 후 복귀' if kind == 'PERCENTILE_OUT_IN' else '이탈')
    elif kind in ('PRICE_LEVEL', 'LIQUIDITY_LEVEL'):
        detail = f"{step['level']} {relation}"
    else:
        detail = kind
    if step.get('bar_state') == 'CLOSED': detail += ' (마감봉)'
    elif step.get('bar_state') == 'FORMING': detail += ' (진행봉)'
    if step.get('negated'): detail = '미성립: ' + detail
    return (tf + ' ' + detail).strip()


def render_strategy_alert(name, machine, event, *, observations=(), after_observations=(), final_observations=()):
    direction = str(event.get('direction') or machine.direction)
    icon, side = {'LONG': ('🟢', '매수'), 'SHORT': ('🔴', '매도')}.get(direction, ('⚪', '조건'))
    heading = '신호 발생' if direction in ('LONG', 'SHORT') else '성립'
    lines = [f'[{_text(name)[:200]}] {icon} {side} {heading}', SEPARATOR,
             '• 종목: ' + _text(event.get('symbol') or machine.symbol)]
    meaning = machine.meaning
    spatial = {'WONBI_TOUCH', 'EXTERNAL_LIQUIDITY_TOUCH', 'FVG_TOUCH', 'FVG_NEW', 'FVG_STATE',
               'MA_PRICE_TOUCH', 'MA_PRICE_STATE', 'MA_PRICE_CROSS', 'PRICE_LEVEL', 'LIQUIDITY_LEVEL',
               'REGIME_BAND', 'PERCENTILE_OUT', 'PERCENTILE_OUT_IN'}
    locations = []
    for key, observations_ in (('steps', observations), ('after_conditions', after_observations), ('final_conditions', final_observations)):
        for index, step in enumerate(meaning.get(key, ())):
            if step['kind'] in spatial:
                observation = observations_[index] if index < len(observations_) else None
                locations.append(condition_text(step, machine, observation))
    if locations: lines.append('• 위치: ' + ' / '.join(dict.fromkeys(locations))[:900])
    tf = event.get('source_tf') or event.get('tf')
    if not tf:
        tf = next((o.get('source_tf') for o in reversed((*observations, *after_observations))
                   if o.get('matched') and o.get('source_tf')), None)
    if not tf:
        tf = machine.final_tf if meaning['final']['kind'] == 'OZ' else machine.source_tf
    period = tf_label(tf)
    if meaning['final']['kind'] == 'OZ':
        profile = _profile(event) or _profile(meaning['final'])
        period = ' '.join(value for value in (period, profile) if value)
    if period: lines.append('• 주기: ' + period)
    # Indicator values come only from the accepted final event.
    if event.get('indicators_text'): lines.append('• 지표: ' + _text(event['indicators_text'])[:500])
    lines.append('• 현재 가격: ' + _price(event.get('current_price')))
    lines.append(SEPARATOR)
    return '\n'.join(lines)
