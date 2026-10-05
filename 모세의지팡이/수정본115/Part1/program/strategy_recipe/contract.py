"""Data-only common Strategy/Recipe language, shared by LIVE and generation.

Validation and lowering happen at registration, never in the packet loop.
Calculations, I/O and strategy-number dispatch are deliberately absent.
"""
from __future__ import annotations
import copy
import math
import re

KINDS = {'TREND', 'TREND_METRIC', 'CANDLE_STATE', 'MA_STATE', 'MA_PRICE_STATE', 'MA_SLOPE_STATE',
    'MA_CROSS', 'MA_PRICE_CROSS', 'MA_PRICE_TOUCH', 'FVG_STATE', 'FVG_NEW',
    'FVG_TOUCH', 'WONBI_TOUCH', 'EXTERNAL_LIQUIDITY_TOUCH', 'SESSION_START',
    'BAR_CLOSE', 'OZ_ALERT', 'REGIME_BAND', 'PERCENTILE_OUT', 'PERCENTILE_OUT_IN',
    'PRICE_LEVEL', 'LIQUIDITY_LEVEL'}
COMMON = {'kind', 'tf', 'tfs', 'direction', 'bar_state', 'tf_combine',
    'capture', 'ref', 'scope_ref', 'negated'}
PARAMS = {'TREND': set(), 'TREND_METRIC': {'metric', 'metric_operator', 'metric_value'},
    'CANDLE_STATE': {'side'},
    'MA_STATE': {'ma_family', 'fast_period', 'slow_period', 'side'},
    'MA_PRICE_STATE': {'ma_family', 'slow_period', 'side', 'price_tf'},
    'MA_PRICE_CROSS': {'ma_family', 'slow_period', 'relation'},
    'MA_PRICE_TOUCH': {'ma_family', 'slow_period'},
    'MA_SLOPE_STATE': {'ma_family', 'slow_period', 'side', 'lookback'},
    'MA_CROSS': {'ma_left', 'ma_right'}, 'FVG_STATE': {'side', 'state'},
    'FVG_NEW': {'side'}, 'FVG_TOUCH': {'side'}, 'WONBI_TOUCH': {'side'},
    'EXTERNAL_LIQUIDITY_TOUCH': {'side', 'level'}, 'SESSION_START': {'session'},
    'BAR_CLOSE': set(), 'OZ_ALERT': {'validation_mode', 'trigger_mode'},
    'REGIME_BAND': {'regime_family', 'regime_families', 'family_combine', 'relation'},
    'PERCENTILE_OUT': {'families', 'family_combine', 'side'},
    'PERCENTILE_OUT_IN': {'families', 'family_combine', 'side'},
    'PRICE_LEVEL': {'level', 'relation'}, 'LIQUIDITY_LEVEL': {'level', 'relation'}}
MEANING_KEYS = {'direction', 'symbols', 'symbol_source', 'steps', 'order_mode',
    'global_combine', 'within_sec', 'final_window_sec', 'final_window_from', 'final', 'final_after',
    'cancel_conditions', 'final_conditions', 'after_conditions', 'branches',
    'persistent', 'preset', 'lifecycle', 'time_filters', 'final_time_filters'}
BRANCH_KEYS = MEANING_KEYS - {'symbols', 'symbol_source', 'branches', 'preset'}
FAMILIES = {'SMA', 'WMA', 'EMA', 'HMA'}
REGIMES = {'PRICE', 'RSI', 'STO', 'DI'}
LIFECYCLE_KEYS = {'expires', 'snapshots', 'excursion', 'replace', 'first_success',
    'invalidate_refs', 'restart_on', 'precondition_check'}
DIRECTIONS = ('LONG', 'SHORT', 'BOTH', 'SAME_AS_PREVIOUS_DIRECTION')
# The final wait (final_window_sec or lifecycle seconds) is counted either from the
# first condition's occurrence or from the moment every condition is gathered.
WINDOW_ANCHORS = ('ALL_CONDITIONS', 'FIRST_CONDITION')
# Whether state conditions are checked only when the strategy starts watching, or all
# the while it watches (the long-standing behaviour).
PRECONDITION_CHECKS = ('WHILE_ACTIVE', 'AT_START')
CAPTURE_KINDS = ('FVG_NEW', 'FVG_TOUCH', 'OZ_ALERT', 'EXTERNAL_LIQUIDITY_TOUCH')
SCOPE_KINDS = ('FVG_NEW', 'FVG_TOUCH', 'OZ_ALERT')


def level_gate_rule(gate):
    """The ATR rule of a final level_gate. The default is the OZ engine's own fixed rule,
    defined once in oz_engine.common and never repeated here."""
    from oz_engine.common import atr_rule
    period, mult = atr_rule(gate.get('atr_period'), gate.get('atr_mult'))
    return {'atr_period': period, 'atr_mult': mult}


def keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f'{label}에 허용되지 않거나 사용하지 않는 필드가 있습니다.')


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{label}은 양의 정수입니다.')


def finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label}은 유한한 숫자입니다.')


def ma_name(value):
    match = re.fullmatch(r'(SMA|WMA|EMA|HMA)([1-9][0-9]*)', str(value or ''))
    if not match: raise ValueError('MA 이름은 SMA/WMA/EMA/HMA + 양의 정수 기간입니다.')
    return match[1], int(match[2])


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value):
        raise ValueError(f'{label} 이름을 확인하세요.')


def context_defaults(context=None):
    from command_interpreter import MT5_TIMEFRAMES, OZ_BASE_TFS
    import oz_profiles
    from sweep_selectors import selector_codes
    from .registry import preset_meaning
    result = {'symbols': None, 'tf_labels': MT5_TIMEFRAMES, 'oz_tfs': OZ_BASE_TFS,
        'profiles': oz_profiles, 'session_keys': (), 'sweep_codes': selector_codes,
        'metrics': None, 'preset_meaning': preset_meaning}
    result.update(context or {})
    return result


def tfs(item, allowed, label, references=True):
    if 'tf' in item and 'tfs' in item: raise ValueError(f'{label}: tf와 tfs를 함께 쓰지 마세요.')
    values = item.get('tfs') or ([item['tf']] if item.get('tf') else None)
    accepted = set(allowed) | ({'SOURCE', 'FINAL'} if references else set())
    if not isinstance(values, list) or not values or any(x not in accepted for x in values):
        raise ValueError(f'{label} 시간봉을 확인하세요.')
    if len(set(values)) != len(values): raise ValueError('시간봉이 중복되었습니다.')
    item['tfs'] = list(values); item.pop('tf', None)


def sweep_selectors(step, context=None):
    ctx = context_defaults(context)
    codes = set(ctx['sweep_codes'](step.get('level') or 'ALL')); side = step.get('side')
    if side:
        codes = {code for code in codes if code.endswith('_' + side) or code in
            ({'PDL', 'PWL'} if side == 'LOW' else {'PDH', 'PWH'})}
    if not codes: raise ValueError('외부유동성 level과 HIGH/LOW 방향이 맞지 않습니다.')
    return sorted(codes)


def validate_step(step, direction, context=None):
    ctx = context_defaults(context); kind = step.get('kind') if isinstance(step, dict) else None
    if kind not in KINDS: raise ValueError(f'지원되지 않는 단계: {kind}')
    keys(step, COMMON | PARAMS[kind], kind)
    tfs(step, ctx['oz_tfs'] if kind == 'OZ_ALERT' else ctx['tf_labels'], kind)
    if step.get('direction', direction) not in DIRECTIONS: raise ValueError('단계 방향을 확인하세요.')
    if step.get('bar_state', 'UNSPECIFIED') not in ('UNSPECIFIED', 'CLOSED', 'FORMING'):
        raise ValueError('봉 기준을 확인하세요.')
    if step.get('tf_combine', 'ANY') not in ('ALL', 'ANY', 'INDEPENDENT'):
        raise ValueError('TF 연결을 확인하세요.')
    for name in ('capture', 'ref', 'scope_ref'):
        if name in step: identifier(step[name], name)
    if step.get('ref') and kind not in ('FVG_STATE', 'FVG_TOUCH', 'OZ_ALERT'):
        raise ValueError('동일 사건 참조는 FVG/OZ 조건에 사용하세요.')
    if 'negated' in step and type(step['negated']) is not bool: raise ValueError('negated는 true/false입니다.')
    if kind == 'CANDLE_STATE':
        if step.get('side') not in ('BULL', 'BEAR'):
            raise ValueError('봉 방향은 양봉(BULL)/음봉(BEAR)으로 지정하세요.')
        if step.get('bar_state') not in ('FORMING', 'CLOSED'):
            raise ValueError('양봉·음봉 조건은 진행봉(FORMING)/확정봉(CLOSED)을 지정하세요.')
    if kind == 'MA_CROSS':
        if ma_name(step.get('ma_left')) == ma_name(step.get('ma_right')): raise ValueError('MA 두 기간이 같습니다.')
    elif kind.startswith('MA_'):
        if step.get('ma_family') not in FAMILIES: raise ValueError('MA 종류를 확인하세요.')
        positive(step.get('slow_period'), 'MA 기간')
        if kind == 'MA_PRICE_CROSS' and step.get('relation') not in ('BREAK_UP', 'BREAK_DOWN', 'BOTH'):
            raise ValueError('가격 MA 교차 관계는 BREAK_UP/BREAK_DOWN/BOTH입니다.')
        if kind == 'MA_STATE':
            positive(step.get('fast_period'), 'MA 기간')
            if step['fast_period'] == step['slow_period']: raise ValueError('MA 두 기간이 같습니다.')
        sides = ('UP', 'DOWN') if kind == 'MA_SLOPE_STATE' else ('ABOVE', 'BELOW')
        if step.get('side') not in (None, *sides): raise ValueError('MA 비교 방향을 확인하세요.')
        if 'lookback' in step: positive(step['lookback'], '기울기 비교 봉 수')
        if 'price_tf' in step: tfs({'tf': step['price_tf']}, ctx['tf_labels'], '가격 시간봉')
    if kind == 'TREND_METRIC':
        metric = step.get('metric'); identifier(metric, '지표')
        metrics = ctx['metrics']
        if metrics is None:
            from strategy_INDICATOR import TREND_METRIC_FIELDS
            metrics = TREND_METRIC_FIELDS
        if metric not in metrics: raise ValueError('등록된 지표 필드를 확인하세요.')
        if step.get('metric_operator') not in ('GT', 'GTE', 'LT', 'LTE', 'EQ', 'NE'):
            raise ValueError('지표 비교 연산자를 확인하세요.')
        finite(step.get('metric_value'), '지표 비교 값')
    if kind.startswith('FVG'):
        if step.get('side') not in (None, 'BULL', 'BEAR'): raise ValueError('FVG 방향은 BULL/BEAR입니다.')
        if kind == 'FVG_STATE' and step.get('state', 'EXISTS') not in ('EXISTS', 'AREA'): raise ValueError('FVG 상태를 확인하세요.')
    if kind == 'WONBI_TOUCH' and step.get('side') not in (None, 'LOWER', 'UPPER'): raise ValueError('원비 방향을 확인하세요.')
    if kind == 'EXTERNAL_LIQUIDITY_TOUCH':
        if step.get('side') not in (None, 'LOW', 'HIGH'): raise ValueError('유동성 방향을 확인하세요.')
        sweep_selectors(step, ctx)
    if kind == 'SESSION_START':
        identifier(step.get('session'), '세션')
        if ctx['session_keys'] and step['session'] not in ctx['session_keys']:
            raise ValueError('현재 config에 없는 세션입니다.')
    if kind == 'BAR_CLOSE' and step.get('bar_state') == 'FORMING': raise ValueError('봉 마감은 확정봉 사건입니다.')
    if kind == 'REGIME_BAND':
        if 'regime_family' in step and 'regime_families' in step: raise ValueError('레짐 계열을 두 방식으로 섞지 마세요.')
        families = step.get('regime_families') or [step.get('regime_family')]
        if not isinstance(families, list) or not families or any(f not in REGIMES for f in families): raise ValueError('레짐 계열을 확인하세요.')
        step['regime_families'] = list(dict.fromkeys(families)); step.pop('regime_family', None)
        if step.get('relation') not in ('SLOPE_UP', 'SLOPE_DOWN', 'ABOVE', 'BELOW', 'IN', 'OUT_LOWER', 'OUT_UPPER'):
            raise ValueError('레짐 비교 관계를 확인하세요.')
    if kind in ('PERCENTILE_OUT', 'PERCENTILE_OUT_IN'):
        families = step.get('families')
        if not isinstance(families, list) or not families or any(f not in REGIMES for f in families): raise ValueError('Percentile 계열을 확인하세요.')
        if len(set(families)) != len(families): raise ValueError('Percentile 계열이 중복되었습니다.')
        if step.get('side') not in (None, 'LOWER', 'UPPER'): raise ValueError('Percentile 방향을 확인하세요.')
    if kind in ('REGIME_BAND', 'PERCENTILE_OUT', 'PERCENTILE_OUT_IN') and step.get('family_combine', 'ANY') not in ('ALL', 'ANY', 'MATCHING_FAMILY'):
        raise ValueError('지표 계열 연결을 확인하세요.')
    if kind in ('PRICE_LEVEL', 'LIQUIDITY_LEVEL'):
        level = step.get('level')
        if kind == 'PRICE_LEVEL' and type(level) in (int, float): finite(level, '가격')
        elif kind == 'PRICE_LEVEL' and level == 'DAY_OPEN': pass
        elif not isinstance(level, str) or not ctx['sweep_codes'](level): raise ValueError('공식 유동성 level을 확인하세요.')
        if step.get('relation') not in ('ABOVE', 'BELOW', 'TOUCH', 'BREAK_UP', 'BREAK_DOWN'):
            raise ValueError('level 비교 관계를 확인하세요.')
    if kind == 'OZ_ALERT':
        if step.get('validation_mode') not in ctx['profiles'].VALIDATION_MODES: raise ValueError('선행 OZ 모드를 확인하세요.')
        step['validation_mode'], step['trigger_mode'] = ctx['profiles'].normalize_profile(step['validation_mode'], step.get('trigger_mode'))


def validate_lifecycle(lifecycle, direction, ctx):
    keys(lifecycle, LIFECYCLE_KEYS, '수명 규칙')
    expires = lifecycle.get('expires')
    if expires is not None:
        keys(expires, {'seconds', 'bars', 'tf', 'bars_setting'}, '수명 제한')
        if ('seconds' in expires) == ('bars' in expires): raise ValueError('수명은 seconds 또는 bars 중 하나로 지정하세요.')
        if 'seconds' in expires:
            positive(expires['seconds'], '수명 seconds')
            if 'tf' in expires: raise ValueError('초 수명에는 tf가 필요 없습니다.')
            if 'bars_setting' in expires: raise ValueError('초 수명에는 봉 수 설정이 필요 없습니다.')
        else:
            positive(expires['bars'], '수명 bars')
            tfs({'tf': expires.get('tf')}, ctx['tf_labels'], '봉 수명')
            # The configured bar count (for example MAX_BARS_AFTER_B0) wins when it is a
            # positive integer; the declared bars is the value used when it is absent.
            if 'bars_setting' in expires and (not isinstance(expires['bars_setting'], str)
                    or not re.fullmatch(r'[A-Z][A-Z0-9_]*', expires['bars_setting'])):
                raise ValueError('봉 수 설정 이름은 대문자 config 키입니다.')
    if lifecycle.get('precondition_check', 'WHILE_ACTIVE') not in PRECONDITION_CHECKS:
        raise ValueError('선행 조건 검사 방식은 ' + '/'.join(PRECONDITION_CHECKS) + '입니다.')
    snapshots = lifecycle.get('snapshots', {})
    if not isinstance(snapshots, dict): raise ValueError('스냅샷은 이름별 정의입니다.')
    for name, definition in snapshots.items():
        identifier(name, '스냅샷'); keys(definition, {'tf', 'field', 'bar_state'}, '스냅샷')
        tfs({'tf': definition.get('tf')}, ctx['tf_labels'], '스냅샷')
        if definition.get('field') not in ('open', 'high', 'low', 'close', 'ATR14'):
            raise ValueError('스냅샷 필드는 OHLC/ATR14입니다.')
        if definition.get('bar_state', 'CLOSED') != 'CLOSED': raise ValueError('스냅샷은 확정봉을 고정합니다.')
    excursion = lifecycle.get('excursion')
    if excursion is not None:
        keys(excursion, {'tf', 'anchor', 'snapshot', 'multiplier', 'direction'}, '이동 폭 취소')
        for key in ('anchor', 'snapshot'):
            if excursion.get(key) not in snapshots: raise ValueError('이동 폭의 스냅샷 참조가 없습니다.')
        finite(excursion.get('multiplier'), '이동 폭 배수')
        if excursion['multiplier'] <= 0: raise ValueError('이동 폭 배수는 양수입니다.')
        if excursion.get('direction', 'FAVORABLE') not in ('FAVORABLE', 'ADVERSE'): raise ValueError('이동 폭 방향을 확인하세요.')
        if 'tf' in excursion: tfs({'tf': excursion['tf']}, ctx['tf_labels'], '이동 폭')
    if 'replace' in lifecycle:
        keys(lifecycle['replace'], {'scope'}, '수명 교체')
        if lifecycle['replace'].get('scope') not in ('SYMBOL', 'SYMBOL_DIRECTION'):
            raise ValueError('교체 범위는 SYMBOL/SYMBOL_DIRECTION입니다.')
    for key in ('first_success', 'invalidate_refs'):
        if key in lifecycle and type(lifecycle[key]) is not bool: raise ValueError(f'{key}는 true/false입니다.')
    if not isinstance(lifecycle.get('restart_on', []), list): raise ValueError('restart_on은 사건 조건 목록입니다.')
    for step in lifecycle.get('restart_on', []): validate_step(step, direction, ctx)


def _merge(base, patch):
    # A different discriminator is a different object contract. Retaining the
    # previous action's fields would turn a valid branch into a malformed one.
    if 'kind' in patch and base.get('kind') != patch['kind']:
        return copy.deepcopy(patch)
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def validate_tf_bindings(item, ctx):
    steps = [s for key in ('steps', 'cancel_conditions', 'after_conditions', 'final_conditions')
        for s in item.get(key, [])]
    lifecycle = item.get('lifecycle') or {}
    steps += lifecycle.get('restart_on', [])
    refs = {tf for s in steps for tf in (*s.get('tfs', ()), *([s['price_tf']] if s.get('price_tf') else []))}
    refs.update(v['tf'] for v in lifecycle.get('snapshots', {}).values())
    refs.add((lifecycle.get('expires') or {}).get('tf'))
    refs.add((lifecycle.get('excursion') or {}).get('tf'))
    final_tfs = item['final'].get('tfs', [])
    if 'FINAL' in final_tfs: raise ValueError('최종 시간봉은 자신을 참조할 수 없습니다.')
    if 'SOURCE' in refs or 'SOURCE' in final_tfs:
        if not any(tf in ctx['tf_labels'] for step in item['steps'] for tf in step['tfs']):
            raise ValueError('SOURCE의 선행 시간봉이 없습니다.')
    if 'FINAL' in refs:
        if not final_tfs: raise ValueError('FINAL의 최종 시간봉이 없습니다.')
        split = item['final'].get('tf_combine') == 'INDEPENDENT' or lifecycle.get('first_success') or (lifecycle.get('expires') or {}).get('tf') == 'FINAL'
        if len(final_tfs) > 1 and not split: raise ValueError('FINAL 참조는 최종 시간봉별 독립 감시에서 사용하세요.')


def validate_time_filters(filters, ctx):
    if filters == 0 and type(filters) is int: return
    if isinstance(filters, list):
        values = filters
    elif isinstance(filters, dict):
        values = []
        for name, definition in filters.items():
            identifier(name, '세션')
            if isinstance(definition, str): values.append(definition); continue
            if type(definition) is bool: continue
            keys(definition, {'enabled', 'start', 'end'}, '세션 시간')
            if 'enabled' in definition and type(definition['enabled']) is not bool:
                raise ValueError('세션 enabled는 true/false입니다.')
            for key in ('start', 'end'):
                if key in definition and not re.fullmatch(r'(?:(?:[01][0-9]|2[0-3]):?[0-5][0-9]|24:?00)', str(definition[key])):
                    raise ValueError('세션 시각을 확인하세요.')
        return
    else: raise ValueError('시간 필터는 세션/시간 구간 목록 또는 세션별 설정입니다.')
    for value in values:
        if not isinstance(value, str): raise ValueError('세션/시간 구간을 확인하세요.')
        if re.fullmatch(r'(?:[01][0-9]|2[0-3])[0-5][0-9]-(?:(?:[01][0-9]|2[0-3])[0-5][0-9]|2400)', value): continue
        identifier(value, '세션')
        if ctx['session_keys'] and value not in ctx['session_keys']: raise ValueError('세션/시간 구간을 확인하세요.')


def validate_meaning(raw, context=None):
    ctx = context_defaults(context); keys(raw, MEANING_KEYS, '전략 의도'); item = copy.deepcopy(raw)
    if item.get('preset'):
        resolver = ctx['preset_meaning']
        if resolver is None: raise ValueError('등록된 preset을 확인하세요.')
        base = resolver(item['preset'], symbols=item.get('symbols'))
        item = _merge(base, item)
    direction = item.get('direction')
    if direction not in ('LONG', 'SHORT', 'BOTH'): raise ValueError('방향은 LONG/SHORT/BOTH입니다.')
    symbols = item.get('symbols')
    if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_.+\-]+|\*', s) for s in symbols):
        raise ValueError('종목을 확인하세요.')
    if ctx['symbols'] is not None and any(s not in ctx['symbols'] for s in symbols): raise ValueError('현재 Part1 허용 종목을 확인하세요.')
    if len(set(symbols)) != len(symbols): raise ValueError('종목이 중복되었습니다.')
    if item.get('symbol_source') not in (None, 'USER', 'CURRENT_DEFAULT'): raise ValueError('종목 출처를 확인하세요.')
    if not isinstance(item.get('steps'), list): raise ValueError('steps는 조건 목록입니다.')
    for key in ('steps', 'cancel_conditions', 'final_conditions', 'after_conditions'):
        if not isinstance(item.get(key, []), list): raise ValueError(f'{key}는 조건 목록입니다.')
        for step in item.get(key, []): validate_step(step, direction, ctx)
    order = item.get('order_mode') or 'SIMULTANEOUS'; item['order_mode'] = order
    if order not in ('SIMULTANEOUS', 'SEQUENTIAL', 'UNORDERED'): raise ValueError('단계 관계를 확인하세요.')
    if item.get('after_conditions') and order == 'SIMULTANEOUS': raise ValueError('setup 이후 조건은 사건 연쇄에 사용합니다.')
    if item.get('global_combine', 'ALL') not in ('ALL', 'ANY', 'INDEPENDENT'): raise ValueError('조건 연결을 확인하세요.')
    if order != 'SIMULTANEOUS' and item.get('global_combine', 'ALL') != 'ALL': raise ValueError('연쇄 사건 연결은 ALL입니다.')
    for key in ('within_sec', 'final_window_sec'):
        if item.get(key) is not None: positive(item[key], key)
    if order == 'SIMULTANEOUS' and item.get('within_sec') is not None: raise ValueError('동시 조건에 사건 간 시간 제한을 넣지 마세요.')
    if item.get('final_after') not in (None, 'FVG_NEW', 'FVG_TOUCH'): raise ValueError('최종 시작 시점을 확인하세요.')
    if item.get('final_after') and not any(s['kind'] == item['final_after'] for s in item['steps']): raise ValueError('최종 시작 사건을 steps에 명시하세요.')
    if 'persistent' in item and type(item['persistent']) is not bool: raise ValueError('persistent는 true/false입니다.')
    for key in ('time_filters', 'final_time_filters'):
        validate_time_filters(item.get(key, []), ctx)
    if 'lifecycle' in item: validate_lifecycle(item['lifecycle'], direction, ctx)
    if item.get('final_window_from') is not None:
        if item['final_window_from'] not in WINDOW_ANCHORS:
            raise ValueError('대기 시간 기준은 ' + '/'.join(WINDOW_ANCHORS) + '입니다.')
        # A branched definition is checked again per branch, where its window is visible.
        if not item.get('branches') and item.get('final_window_sec') is None and (
                (item.get('lifecycle') or {}).get('expires') or {}).get('seconds') is None:
            raise ValueError('대기 시간 기준은 final_window_sec 또는 수명 seconds와 함께 지정하세요.')
    final = item.get('final'); keys(final, {'kind', 'tf', 'tfs', 'validation_mode', 'trigger_mode', 'direction', 'regime_family', 'scope_ref', 'tf_combine', 'level_gate'}, '최종 판정')
    if final.get('kind') not in ('OZ', 'NOTIFY', 'DEFINE'): raise ValueError('최종 행동은 OZ/NOTIFY/DEFINE입니다.')
    if final['kind'] == 'OZ':
        tfs(final, ctx['oz_tfs'], '최종 OZ')
        if final.get('validation_mode') not in ctx['profiles'].VALIDATION_MODES: raise ValueError('최종 OZ 모드를 확인하세요.')
        final['validation_mode'], final['trigger_mode'] = ctx['profiles'].normalize_profile(final['validation_mode'], final.get('trigger_mode'))
    elif set(final) - {'kind', 'direction', 'scope_ref'}: raise ValueError('NOTIFY/DEFINE에 OZ 전용 필드를 넣지 마세요.')
    if final.get('direction') not in (None, *DIRECTIONS): raise ValueError('최종 방향을 확인하세요.')
    if final.get('regime_family') not in (None, *REGIMES, 'MATCHING_FAMILY'): raise ValueError('최종 지표 계열을 확인하세요.')
    if final.get('tf_combine', 'ANY') not in ('ALL', 'ANY', 'INDEPENDENT'): raise ValueError('최종 TF 연결을 확인하세요.')
    if final.get('scope_ref'): identifier(final['scope_ref'], '최종 사건 범위')
    gate = final.get('level_gate')
    if gate is not None:
        keys(gate, {'ref', 'atr_period', 'atr_mult'}, '외부유동성 연결')
        identifier(gate.get('ref'), '외부유동성 연결 대상')
        if 'atr_period' in gate:
            positive(gate['atr_period'], 'ATR 기간')
            if gate['atr_period'] > 500: raise ValueError('ATR 기간은 500 이하입니다.')
        if 'atr_mult' in gate:
            finite(gate['atr_mult'], 'ATR 배수')
            if gate['atr_mult'] <= 0: raise ValueError('ATR 배수는 양수입니다.')
        if final['kind'] != 'OZ': raise ValueError('외부유동성 연결은 최종 OZ에서 사용하세요.')
        if final.get('tfs') != ['SOURCE']:
            raise ValueError('외부유동성 연결은 터치한 시간봉(SOURCE)의 OZ에서만 사용합니다.')
    captures, capture_kinds = set(), {}
    for step in item['steps'] + item.get('after_conditions', []):
        for key in ('ref', 'scope_ref'):
            if step.get(key) and step[key] not in captures: raise ValueError('앞단계 사건 참조가 없습니다.')
            if step.get(key) and capture_kinds[step[key]] not in SCOPE_KINDS:
                raise ValueError('외부유동성 캡처는 최종 level_gate에서만 참조합니다.')
        if step.get('capture'):
            if step['capture'] in captures: raise ValueError('사건 참조 이름이 중복되었습니다.')
            if step['kind'] not in CAPTURE_KINDS: raise ValueError('FVG/OZ/외부유동성 사건만 참조할 수 있습니다.')
            captures.add(step['capture']); capture_kinds[step['capture']] = step['kind']
    for step in item.get('cancel_conditions', []) + item.get('final_conditions', []):
        if any(step.get(key) and step[key] not in captures for key in ('ref', 'scope_ref')):
            raise ValueError('앞단계 사건 참조가 없습니다.')
        if any(step.get(key) and capture_kinds[step[key]] not in SCOPE_KINDS for key in ('ref', 'scope_ref')):
            raise ValueError('외부유동성 캡처는 최종 level_gate에서만 참조합니다.')
    if final.get('scope_ref') and not item.get('branches') and final['scope_ref'] not in captures:
        raise ValueError('최종 사건 범위 참조가 없습니다.')
    if final.get('scope_ref') and capture_kinds.get(final['scope_ref']) not in (None, *SCOPE_KINDS):
        raise ValueError('외부유동성 캡처는 최종 level_gate에서만 참조합니다.')
    if gate is not None and not item.get('branches'):
        target = next((s for s in item['steps'] if s.get('capture') == gate['ref']), None)
        if target is None or target['kind'] != 'EXTERNAL_LIQUIDITY_TOUCH' or target.get('negated'):
            raise ValueError('외부유동성 연결 대상은 steps의 외부유동성 터치 캡처여야 합니다.')
    if order == 'SIMULTANEOUS' and any(s.get('ref') or s.get('scope_ref') for s in item['steps']):
        raise ValueError('후속 사건 참조는 SEQUENTIAL/UNORDERED에서 사용하세요.')
    if not item['steps'] and not item.get('branches') and final['kind'] != 'OZ': raise ValueError('알림 발생 조건이 없습니다.')
    branches = item.get('branches', [])
    if not isinstance(branches, list): raise ValueError('branches는 독립 분기 목록입니다.')
    if branches:
        if item['steps']: raise ValueError('독립 branches와 공통 steps를 함께 넣지 마세요.')
        for i, branch in enumerate(branches):
            keys(branch, BRANCH_KEYS, '독립 분기')
            if 'steps' not in branch: raise ValueError('독립 분기의 steps가 없습니다.')
            expanded = {k:v for k,v in item.items() if k not in ('branches', 'preset')}
            expanded = _merge(expanded, branch)
            normalized = validate_meaning(expanded, ctx)
            branches[i] = {key: normalized[key] for key in branch}
    else: validate_tf_bindings(item, ctx)
    return item


def execution_plan(raw, context=None):
    ctx = context_defaults(context); item = validate_meaning(raw, ctx)
    if item.get('branches'):
        lowered = []
        for branch in item['branches']:
            unit = {k:v for k,v in item.items() if k not in ('branches', 'preset')}
            unit = _merge(unit, branch)
            lowered.append(execution_plan(unit, ctx)['meaning'])
        item['branches'] = lowered
        return {'mode': 'CANONICAL', 'meaning': item}
    previous_direction = item['direction']
    for step in item['steps'] + item.get('after_conditions', []):
        chosen = step.get('direction', item['direction'])
        if chosen == 'SAME_AS_PREVIOUS_DIRECTION': chosen = previous_direction
        if step['kind'] == 'MA_PRICE_CROSS' and chosen == 'BOTH': chosen = {'BREAK_UP': 'LONG', 'BREAK_DOWN': 'SHORT'}.get(step['relation'], chosen)
        step['_resolved_direction'] = chosen; previous_direction = chosen
    if item['final'].get('direction') == 'SAME_AS_PREVIOUS_DIRECTION': item['final']['direction'] = previous_direction
    all_steps = [s for key in ('steps', 'cancel_conditions', 'final_conditions', 'after_conditions') for s in item.get(key, [])]
    all_steps += item.get('lifecycle', {}).get('restart_on', [])
    for step in all_steps:
        kind = step['kind']
        if step.get('direction') == 'SAME_AS_PREVIOUS_DIRECTION' and '_resolved_direction' not in step:
            step['_resolved_direction'] = previous_direction
        if kind in ('EXTERNAL_LIQUIDITY_TOUCH', 'LIQUIDITY_LEVEL') or (kind == 'PRICE_LEVEL' and isinstance(step['level'], str) and step['level'] != 'DAY_OPEN'):
            step['_level_codes'] = sweep_selectors(step, ctx) if kind == 'EXTERNAL_LIQUIDITY_TOUCH' else sorted(ctx['sweep_codes'](step['level']))
        if kind in ('FVG_NEW', 'EXTERNAL_LIQUIDITY_TOUCH', 'BAR_CLOSE') and step.get('bar_state') == 'FORMING':
            raise ValueError(f'{kind}는 기존 처리기의 확정봉 사건입니다.')
        if kind == 'OZ_ALERT' and step.get('bar_state', 'UNSPECIFIED') != 'UNSPECIFIED':
            raise ValueError('OZ_ALERT의 시각은 기존 OZ 판정 사건입니다. 별도 봉 기준으로 바꾸지 않습니다.')
        step['_event_mode'] = item['order_mode'] != 'SIMULTANEOUS' or kind in ('MA_PRICE_CROSS', 'MA_PRICE_TOUCH', 'BAR_CLOSE', 'PERCENTILE_OUT_IN') or (
            kind == 'PERCENTILE_OUT' and item['final']['kind'] == 'NOTIFY')
    gate = item['final'].get('level_gate')
    if gate:
        # The linked touch carries the strategy's ATR rule into its SWEEP watch.
        for step in item['steps']:
            if step.get('capture') == gate['ref']:
                step['_level_gate'] = level_gate_rule(gate)
    return {'mode': 'CANONICAL', 'meaning': item}
