"""One validated contract for every post-alert virtual entry.

The policy is always explicit: immediate or conditional entry, listed
conditions, entry limits and one stop kind. A strategy's own policy is written in
its recipe (virtual_entry) and filled in as ordinary values (virtual_defaults);
without one, entry is immediate. 'SIGNAL' means each alert's own timeframe and
'ENV' the timeframe its recipe's first condition matched on, so one list
applies to every alert. Signal detection and market alerts are not changed here.
"""
from copy import deepcopy
import math
import sys
from .settings import PROGRAM

if str(PROGRAM) not in sys.path:
    sys.path.insert(0, str(PROGRAM))
# The command language's frame reading: the same as indicator_facts.normalize_tf for every input, without
# loading pandas when the backtest screen opens (수정본170).
from command_interpreter import normalize_tf
from strategy_recipe.contract import SHAPES

SCHEMA = 2
# MA_TOUCH: a candle after the alert touches the average, a later candle confirms.
# MA_TOUCH_CANDLE: the confirmation candle itself touches it. CANDLE_SHAPE: its shape.
CONDITIONS = ('CANDLE_CLOSE', 'CANDLE_SHAPE', 'MA_POSITION', 'MA_CROSS', 'MA_TOUCH', 'MA_TOUCH_CANDLE',
              'ENGULFING', 'NECKLINE_BREAK')
ATR_FILTERS = ('CANDLE_ATR', 'MA_DISTANCE_ATR')
# Entry limits: an ATR size check blocks one candle; an environment that breaks or
# a wait longer than N bars passes the alert.
FILTERS = (*ATR_FILTERS, 'ENVIRONMENT', 'MAX_BARS')
ENVIRONMENTS = ('TREND', 'MA_STATE', 'MA_SLOPE_STATE', 'MA_PRICE_STATE')
BAR_STATES = ('CLOSED', 'FORMING')
STOPS = ('OZ_B0', 'RECENT_EXTREME', 'ATR')
FAMILIES = ('SMA', 'EMA', 'WMA', 'HMA')


def immediate_virtual_entry():
    """The policy of a strategy whose recipe has no virtual entry: immediate entry, ATR stop."""
    return {'schema': SCHEMA, 'mode': 'IMMEDIATE', 'tf': 'SIGNAL',
            'conditions': [], 'atr': {'tf': 'SIGNAL', 'period': 14}, 'filters': [],
            'stop': {'kind': 'ATR', 'tf': 'SIGNAL', 'bars': 5, 'period': 14, 'multiplier': 1.0}}


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(label + ': 객체 형식이 필요합니다.')
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(label + ': 지원하지 않는 필드 ' + ', '.join(sorted(extra)))


def _tf(value, *, environment=False):
    if value == 'SIGNAL' or (environment and value == 'ENV'):
        return value
    if value == 'ENV':
        raise ValueError('가상진입: 환경 프레임은 환경조건에만 쓸 수 있습니다.')
    result = normalize_tf(value)
    if not result:
        raise ValueError('가상진입: 지원하지 않는 타임프레임 ' + str(value))
    return result


def _number(value, label, *, minimum=0, strictly=False):
    if isinstance(value, bool):
        raise ValueError(label + ': 숫자가 필요합니다.')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + ': 숫자가 필요합니다.') from None
    if not math.isfinite(result) or result < minimum or (strictly and result == minimum):
        raise ValueError(label + ': 허용 범위를 벗어났습니다.')
    return result


def _length(value, label):
    result = _number(value, label, minimum=1)
    if result != int(result) or result > 500:
        raise ValueError(label + ': 1~500 정수가 필요합니다.')
    return int(result)


def _family(value):
    family = value.get('family', 'HMA')
    if family not in FAMILIES:
        raise ValueError('가상진입: 이평 종류는 SMA/EMA/WMA/HMA입니다.')
    return family


def _recipe_indices(rows):
    """Separate a test row's recipe slot from its editable predicate fields.

    Recipes themselves need no slot metadata. A target's filled-in policy adds it,
    and an added row has none. Keep it through normalization without making it a
    field of the predicate or changing the caller's input.
    """
    indices = []
    for row in rows:
        has_index = isinstance(row, dict) and 'recipe_index' in row
        index = row.pop('recipe_index', None) if has_index else None
        if has_index and (type(index) is not int or index < 0):
            raise ValueError('가상진입: 레시피 칸 번호는 0 이상의 정수여야 합니다.')
        indices.append(index)
    return indices


def _ma(value):
    return {'family': _family(value), 'period': _length(value.get('period', 6), '이평 기간'),
            'tf': _tf(value.get('tf', 'SIGNAL'))}


def _environment(item):
    """A market state that must hold while the entry waits: the recipe's own state rules."""
    _keys(item, ('kind', 'condition', 'family', 'fast', 'slow', 'period', 'lookback', 'tf', 'bar_state'), '환경조건')
    condition = item.get('condition')
    if condition not in ENVIRONMENTS:
        raise ValueError('환경조건: 추세 / 이평 정배열 / 이평 기울기 / 가격과 이평')
    bar_state = item.get('bar_state', 'CLOSED')
    if bar_state not in BAR_STATES:
        raise ValueError('환경조건 봉 기준: 확정봉(CLOSED) / 진행봉(FORMING)')
    result = {'kind': 'ENVIRONMENT', 'condition': condition, 'tf': _tf(item.get('tf', 'SIGNAL'), environment=True),
              'bar_state': bar_state}
    allowed = {'TREND': set(), 'MA_STATE': {'family', 'fast', 'slow'},
               'MA_SLOPE_STATE': {'family', 'period', 'lookback'}, 'MA_PRICE_STATE': {'family', 'period'}}[condition]
    extra = set(item) & {'family', 'fast', 'slow', 'period', 'lookback'} - allowed
    if extra:
        raise ValueError('환경조건 ' + condition + ': 지원하지 않는 필드 ' + ', '.join(sorted(extra)))
    if condition == 'MA_STATE':
        result.update(family=_family(item), fast=_length(item.get('fast', 6), '빠른 이평 기간'),
                      slow=_length(item.get('slow', 17), '느린 이평 기간'))
        if result['fast'] == result['slow']:
            raise ValueError('환경조건: 이평 두 기간이 같습니다.')
    elif condition != 'TREND':
        family = _family(item)
        result.update(family=family, period=_length(item.get('period', 50), '이평 기간'))
        if condition == 'MA_SLOPE_STATE':
            result['lookback'] = _length(item.get('lookback', 2 if family == 'HMA' else 1), '기울기 비교 봉 수')
    return result


def normalize_virtual_entry(value=None):
    """The checked explicit policy; None stays None (the strategy's own policy applies)."""
    if value is None:
        return None
    base = immediate_virtual_entry()
    _keys(value, base, 'virtual_entry')
    base.update(deepcopy(value))
    if base['schema'] != SCHEMA or isinstance(base['schema'], bool):
        raise ValueError('가상진입 설정 형식이 다릅니다. 가상진입 설정을 다시 확인하세요.')
    if base['mode'] not in ('IMMEDIATE', 'CONFIRM'):
        raise ValueError('진입 방식: 즉시 진입 / 조건 진입')
    base['tf'] = _tf(base['tf'])
    if not isinstance(base['conditions'], list) or not isinstance(base['filters'], list):
        raise ValueError('가상진입 조건·진입 제한은 목록이어야 합니다.')
    indices = {key: _recipe_indices(base[key]) for key in ('conditions', 'filters')}
    normalized = []
    for item in base['conditions']:
        _keys(item, ('kind', 'family', 'period', 'tf', 'shape'), '진입 조건')
        kind = item.get('kind')
        if kind not in CONDITIONS:
            raise ValueError('지원하지 않는 진입 조건: ' + str(kind))
        if kind == 'CANDLE_SHAPE':
            if set(item) - {'kind', 'shape'}:
                raise ValueError(kind + ': 이평 설정을 넣을 수 없습니다.')
            if item.get('shape') not in SHAPES:
                raise ValueError('캔들 모양: 망치형(HAMMER) / 역망치형(INVERTED_HAMMER)')
            normalized.append({'kind': kind, 'shape': item['shape']})
            continue
        if 'shape' in item:
            raise ValueError(kind + ': 캔들 모양을 넣을 수 없습니다.')
        if not kind.startswith('MA_') and set(item) - {'kind'}:
            raise ValueError(kind + ': 이평 설정을 넣을 수 없습니다.')
        normalized.append({'kind': kind, **(_ma(item) if kind.startswith('MA_') else {})})
    base['conditions'] = normalized
    if base['mode'] == 'IMMEDIATE' and normalized:
        raise ValueError('진입 조건을 쓰려면 조건 진입을 선택하세요.')
    if base['mode'] == 'CONFIRM' and not normalized:
        raise ValueError('조건 진입에는 진입 조건이 하나 이상 필요합니다.')
    _keys(base['atr'], ('tf', 'period'), 'ATR')
    base['atr'] = {'tf': _tf(base['atr'].get('tf', 'SIGNAL')),
                   'period': _length(base['atr'].get('period', 14), 'ATR 기간')}
    filters = []
    for item in base['filters']:
        kind = item.get('kind') if isinstance(item, dict) else None
        if kind not in FILTERS:
            raise ValueError('지원하지 않는 진입 제한: ' + str(kind))
        if kind == 'ENVIRONMENT':
            filters.append(_environment(item))
            continue
        if kind == 'MAX_BARS':
            _keys(item, ('kind', 'bars'), '알림 후 N봉')
            if base['mode'] != 'CONFIRM':
                raise ValueError('알림 후 N봉 제한은 조건 진입에서 씁니다.')
            if any(row['kind'] == 'MAX_BARS' for row in filters):
                raise ValueError('알림 후 N봉 제한은 하나만 넣을 수 있습니다.')
            filters.append({'kind': kind, 'bars': _length(item.get('bars'), '알림 후 봉 수')})
            continue
        _keys(item, ('kind', 'measure', 'family', 'period', 'tf', 'min', 'max'), 'ATR 제한')
        limits = {key: None if item.get(key) is None else _number(item[key], 'ATR ' + key)
                  for key in ('min', 'max')}
        if limits['min'] is None and limits['max'] is None:
            raise ValueError('ATR 제한에는 최소 또는 최대 배수가 필요합니다.')
        if limits['max'] is not None and (limits['max'] <= (limits['min'] or 0)):
            raise ValueError('ATR 최대 배수는 최소 배수보다 커야 합니다.')
        if kind == 'CANDLE_ATR':
            if set(item) & {'family', 'period', 'tf'}:
                raise ValueError('봉 크기 제한에 이평 설정을 넣을 수 없습니다.')
            measure = item.get('measure', 'BODY')
            if measure not in ('BODY', 'RANGE'):
                raise ValueError('봉 크기: BODY / RANGE')
            extra = {'measure': measure}
        else:
            if 'measure' in item:
                raise ValueError('이평 거리 제한에 봉 크기 종류를 넣을 수 없습니다.')
            extra = _ma(item)
        filters.append({'kind': kind, **extra, **limits})
    base['filters'] = filters
    _keys(base['stop'], ('kind', 'tf', 'bars', 'period', 'multiplier'), '손절')
    stop = base['stop']
    if stop.get('kind') not in STOPS:
        raise ValueError('손절 방식: 올존 B0 / 직전 N봉 저점·고점 / ATR')
    base['stop'] = {'kind': stop['kind'], 'tf': _tf(stop.get('tf', 'SIGNAL')),
                    'bars': _length(stop.get('bars', 5), '손절 N봉'),
                    'period': _length(stop.get('period', 14), '손절 ATR 기간'),
                    'multiplier': _number(stop.get('multiplier', 1), '손절 ATR 배수', strictly=True)}
    for key, origins in indices.items():
        for row, index in zip(base[key], origins):
            if index is not None:
                row['recipe_index'] = index
    return base


def check_for_strategy(policy, oz):
    """B0 and neckline exist only on OZ signals; other strategies cannot use them."""
    if oz or policy is None:
        return policy
    if policy['stop']['kind'] == 'OZ_B0':
        raise ValueError('올존 B0 손절은 올존 전략에서만 쓸 수 있습니다.')
    if any(item['kind'] == 'NECKLINE_BREAK' for item in policy['conditions']):
        raise ValueError('넥라인 종가 돌파는 올존 전략에서만 쓸 수 있습니다.')
    return policy


def signal_is_oz(alert):
    """Source metadata is authoritative; a stop price alone is not an OZ signal."""
    raw = alert.get('is_oz')
    if raw is not None and raw != '':
        if raw in (True, 1, 'true', 'True', '1'):
            return True
        if raw in (False, 0, 'false', 'False', '0'):
            return False
        raise ValueError('알림 is_oz 값 형식 오류')
    source=str(alert.get('signal_source') or '').upper()
    if source == 'OZ':return True
    if source == 'SIGNAL':return False
    if str(alert.get('trigger') or '').upper()=='OZ':return True
    raise ValueError('가상진입: 알림 신호 출처 정보가 없습니다. 새 버전으로 알림 결과를 다시 생성하세요.')


def alert_tf(value, alert):
    """A policy timeframe for one alert: its own frame (SIGNAL), its environment frame (ENV) or a fixed one."""
    if value == 'SIGNAL':
        return alert.get('signal_tf') or alert['tf']
    if value == 'ENV':
        env = alert.get('env_tf')
        if not env:
            raise ValueError('가상진입: 이 알림에는 환경 프레임 정보가 없습니다. 새 버전으로 알림 결과를 다시 생성하세요.')
        return env
    return value


def max_bars(config):
    return next((item['bars'] for item in config['filters'] if item['kind'] == 'MAX_BARS'), None)


def required_timeframes(config, alert):
    def selected(tf):
        return alert_tf(tf, alert)
    values = {selected('SIGNAL'), '1m'}
    atr = [f for f in config['filters'] if f['kind'] in ATR_FILTERS]
    if config['mode'] == 'CONFIRM' or any(f['kind'] == 'CANDLE_ATR' for f in atr):
        values.add(selected(config['tf']))
    if atr:
        values.add(selected(config['atr']['tf']))
    if config['stop']['kind'] in ('RECENT_EXTREME','ATR'):
        values.add(selected(config['stop']['tf']))
    for item in config['conditions'] + config['filters']:
        if 'tf' in item:
            values.add(selected(item['tf']))
    return values


def schema():
    """JSON response schema shared by UI/AI validation; semantics stay above."""
    def obj(properties, required):
        return {'type': 'object', 'additionalProperties': False,
                'properties': properties, 'required': required}
    from indicator_facts import MT5_TIMEFRAMES
    tf = {'type': 'string', 'enum': ['SIGNAL', *MT5_TIMEFRAMES]}
    period = {'type': 'integer', 'minimum': 1, 'maximum': 500}
    recipe_index = {'type': 'integer', 'minimum': 0}
    family = {'type': 'string', 'enum': list(FAMILIES)}
    ma = {'family': family, 'period': period, 'tf': tf}
    limit = {'type': ['number', 'null'], 'minimum': 0}
    return obj({
        'schema': {'type': 'integer', 'enum': [SCHEMA]},
        'mode': {'type': 'string', 'enum': ['IMMEDIATE', 'CONFIRM']}, 'tf': tf,
        'conditions': {'type': 'array', 'items': obj({
            'kind': {'type': 'string', 'enum': list(CONDITIONS)}, **ma,
            'shape': {'type': 'string', 'enum': list(SHAPES)}, 'recipe_index': recipe_index}, ['kind'])},
        'atr': obj({'tf': tf, 'period': period}, ['tf', 'period']),
        'filters': {'type': 'array', 'items': obj({
            'kind': {'type': 'string', 'enum': list(FILTERS)},
            'measure': {'type': 'string', 'enum': ['BODY', 'RANGE']}, **ma,
            'tf': {'type': 'string', 'enum': ['SIGNAL', 'ENV', *MT5_TIMEFRAMES]},
            'min': limit, 'max': limit,
            'condition': {'type': 'string', 'enum': list(ENVIRONMENTS)},
            'fast': period, 'slow': period, 'lookback': period,
            'bar_state': {'type': 'string', 'enum': list(BAR_STATES)}, 'bars': period,
            'recipe_index': recipe_index}, ['kind'])},
        'stop': obj({'kind': {'type': 'string', 'enum': list(STOPS)},
                     'tf': tf, 'bars': period, 'period': period,
                     'multiplier': {'type': 'number', 'exclusiveMinimum': 0}},
                    ['kind', 'tf', 'bars', 'period', 'multiplier']),
    }, ['schema', 'mode', 'tf', 'conditions', 'atr', 'filters', 'stop'])
