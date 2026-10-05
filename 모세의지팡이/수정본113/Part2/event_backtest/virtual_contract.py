"""One validated contract for every post-alert virtual entry.

AUTO supplies a preset through this same contract; it never selects another
entry algorithm. Signal detection and market alerts are not changed here.
"""
from copy import deepcopy
import math
import sys
from .settings import PROGRAM

if str(PROGRAM) not in sys.path:
    sys.path.insert(0, str(PROGRAM))
from indicator_facts import normalize_tf

CONDITIONS = ('MA_POSITION', 'MA_CROSS', 'MA_TOUCH', 'ENGULFING', 'NECKLINE_BREAK')
FAMILIES = ('SMA', 'EMA', 'WMA', 'HMA')


def default_virtual_entry():
    return {'schema': 1, 'mode': 'AUTO', 'tf': 'SIGNAL', 'conditions': [],
            'atr': {'tf': 'SIGNAL', 'period': 14}, 'filters': [],
            'stop': {'kind': 'AUTO', 'tf': 'SIGNAL', 'bars': 5, 'multiplier': 1.0}}


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(label + ': 객체 형식이 필요합니다.')
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(label + ': 지원하지 않는 필드 ' + ', '.join(sorted(extra)))


def _tf(value):
    if value == 'SIGNAL':
        return value
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


def _ma(value):
    family = value.get('family', 'HMA')
    if family not in FAMILIES:
        raise ValueError('가상진입: 이평 종류는 SMA/EMA/WMA/HMA입니다.')
    return {'family': family, 'period': _length(value.get('period', 6), '이평 기간'),
            'tf': _tf(value.get('tf', 'SIGNAL'))}


def normalize_virtual_entry(value=None):
    base = default_virtual_entry()
    if value is None:
        return base
    _keys(value, base, 'virtual_entry')
    base.update(deepcopy(value))
    if base['schema'] != 1 or isinstance(base['schema'], bool):
        raise ValueError('가상진입 계약 버전은 1이어야 합니다.')
    if base['mode'] not in ('AUTO', 'IMMEDIATE', 'CONFIRM'):
        raise ValueError('가상진입 방식: AUTO / IMMEDIATE / CONFIRM')
    base['tf'] = _tf(base['tf'])
    if not isinstance(base['conditions'], list) or not isinstance(base['filters'], list):
        raise ValueError('가상진입 조건·필터는 목록이어야 합니다.')
    normalized = []
    for item in base['conditions']:
        _keys(item, ('kind', 'family', 'period', 'tf'), '확인 조건')
        kind = item.get('kind')
        if kind not in CONDITIONS:
            raise ValueError('지원하지 않는 가상진입 조건: ' + str(kind))
        if not kind.startswith('MA_') and set(item) - {'kind'}:
            raise ValueError(kind + ': 이평 설정을 넣을 수 없습니다.')
        normalized.append({'kind': kind, **(_ma(item) if kind.startswith('MA_') else {})})
    base['conditions'] = normalized
    if base['mode'] != 'CONFIRM' and normalized:
        raise ValueError('확인 조건을 지정하려면 확인 진입(CONFIRM)을 선택하세요.')
    _keys(base['atr'], ('tf', 'period'), 'ATR')
    base['atr'] = {'tf': _tf(base['atr'].get('tf', 'SIGNAL')),
                   'period': _length(base['atr'].get('period', 14), 'ATR 기간')}
    filters = []
    for item in base['filters']:
        _keys(item, ('kind', 'measure', 'family', 'period', 'tf', 'min', 'max'), 'ATR 필터')
        kind = item.get('kind')
        if kind not in ('CANDLE_ATR', 'MA_DISTANCE_ATR'):
            raise ValueError('지원하지 않는 ATR 필터: ' + str(kind))
        limits = {key: None if item.get(key) is None else _number(item[key], 'ATR ' + key)
                  for key in ('min', 'max')}
        if limits['min'] is None and limits['max'] is None:
            raise ValueError('ATR 필터에는 최소 또는 최대 배수가 필요합니다.')
        if limits['max'] is not None and (limits['max'] <= (limits['min'] or 0)):
            raise ValueError('ATR 최대 배수는 최소 배수보다 커야 합니다.')
        if kind == 'CANDLE_ATR':
            if set(item) & {'family', 'period', 'tf'}:
                raise ValueError('봉 크기 필터에 이평 설정을 넣을 수 없습니다.')
            measure = item.get('measure', 'BODY')
            if measure not in ('BODY', 'RANGE'):
                raise ValueError('봉 크기: BODY / RANGE')
            extra = {'measure': measure}
        else:
            if 'measure' in item:
                raise ValueError('이평 거리 필터에 봉 크기 종류를 넣을 수 없습니다.')
            extra = _ma(item)
        filters.append({'kind': kind, **extra, **limits})
    base['filters'] = filters
    _keys(base['stop'], ('kind', 'tf', 'bars', 'multiplier'), '손절')
    stop = base['stop']
    if stop.get('kind', 'AUTO') not in ('AUTO', 'OZ_B0', 'RECENT_EXTREME', 'ATR'):
        raise ValueError('손절 방식: AUTO / OZ_B0 / RECENT_EXTREME / ATR')
    base['stop'] = {'kind': stop.get('kind', 'AUTO'), 'tf': _tf(stop.get('tf', 'SIGNAL')),
                    'bars': _length(stop.get('bars', 5), '손절 N봉'),
                    'multiplier': _number(stop.get('multiplier', 1), '손절 ATR 배수', strictly=True)}
    return base


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


def resolve_virtual_entry(config, alert):
    resolved = deepcopy(config)
    oz = signal_is_oz(alert)
    if resolved['mode'] == 'AUTO':
        resolved['mode'] = 'CONFIRM' if oz else 'IMMEDIATE'
        if oz and not resolved['conditions']:
            resolved['conditions'] = [{'kind': 'MA_POSITION', 'family': 'HMA', 'period': 6, 'tf': 'SIGNAL'}]
    if resolved['stop']['kind'] == 'AUTO':
        resolved['stop']['kind'] = 'OZ_B0' if oz else 'ATR'
    return resolved


def required_timeframes(config, signal_tf):
    def selected(tf):
        return signal_tf if tf == 'SIGNAL' else tf
    values = {signal_tf, '1m'}
    if config['mode'] in ('AUTO','CONFIRM') or any(f['kind']=='CANDLE_ATR' for f in config['filters']):
        values.add(selected(config['tf']))
    if config['filters']:
        values.add(selected(config['atr']['tf']))
    if config['stop']['kind'] in ('AUTO','RECENT_EXTREME','ATR'):
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
    ma = {'family': {'type': 'string', 'enum': list(FAMILIES)}, 'period': period, 'tf': tf}
    limit = {'type': ['number', 'null'], 'minimum': 0}
    return obj({
        'schema': {'type': 'integer', 'enum': [1]},
        'mode': {'type': 'string', 'enum': ['AUTO', 'IMMEDIATE', 'CONFIRM']}, 'tf': tf,
        'conditions': {'type': 'array', 'items': obj({
            'kind': {'type': 'string', 'enum': list(CONDITIONS)}, **ma}, ['kind'])},
        'atr': obj({'tf': tf, 'period': period}, ['tf', 'period']),
        'filters': {'type': 'array', 'items': obj({
            'kind': {'type': 'string', 'enum': ['CANDLE_ATR', 'MA_DISTANCE_ATR']},
            'measure': {'type': 'string', 'enum': ['BODY', 'RANGE']}, **ma,
            'min': limit, 'max': limit}, ['kind', 'min', 'max'])},
        'stop': obj({'kind': {'type': 'string', 'enum': ['AUTO', 'OZ_B0', 'RECENT_EXTREME', 'ATR']},
                     'tf': tf, 'bars': period, 'multiplier': {'type': 'number', 'exclusiveMinimum': 0}},
                    ['kind', 'tf', 'bars', 'multiplier']),
    }, ['schema', 'mode', 'tf', 'conditions', 'atr', 'filters', 'stop'])
