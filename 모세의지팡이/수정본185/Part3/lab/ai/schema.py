"""AI envelope and JSON schema for the shared, data-only Recipe language."""
from __future__ import annotations
import copy
import re
import sys
from .. import catalog

if str(catalog.CURRENT_PROGRAM) not in sys.path:
    sys.path.insert(0, str(catalog.CURRENT_PROGRAM))
from strategy_recipe.contract import (KINDS, COMMON, PARAMS, MEANING_KEYS, BRANCH_KEYS,
    FAMILIES, REGIMES, SHAPES, LIFECYCLE_KEYS, WINDOW_ANCHORS, PRECONDITION_CHECKS, RECENT_KINDS, keys, positive, ma_name, tfs,
    validate_meaning, validate_step, sweep_selectors as _shared_sweep_selectors)
from strategy_recipe.windows import MAX_BARS


def sweep_codes(level):
    from sweep_selectors import selector_codes
    return selector_codes(level)


def sweep_selectors(step):
    return _shared_sweep_selectors(step, contract_context())


def session_keys():
    result = []
    for line in (catalog.CURRENT_PROGRAM / 'config.txt').read_text('utf-8-sig').splitlines():
        match = re.fullmatch(r'([A-Z_]+)\s*=\s*\d{4}-\d{4}\s*', line.strip())
        if match: result.append(match[1])
    return sorted(set(result))


def contract_context():
    from strategy_recipe.registry import preset_meaning
    return {'symbols': catalog.SYMBOLS, 'tf_labels': catalog.TF_LABELS,
        'oz_tfs': catalog.OZ_TFS, 'profiles': catalog.PROFILES, 'metrics': catalog.METRICS,
        'session_keys': session_keys(), 'sweep_codes': sweep_codes, 'preset_meaning': preset_meaning}


def step_contract(step, direction):
    return validate_step(step, direction, contract_context())


def output_schema():
    """Model syntax is constrained; the common validator owns all permissions."""
    from strategy_recipe.registry import list_presets
    def enum(values): return {'type': 'string', 'enum': list(values)}
    integer = {'type': 'integer', 'minimum': 1}
    identifier = {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_]*$'}
    timeframe = enum((*catalog.TF_LABELS, 'SOURCE', 'FINAL'))
    direction = enum(('LONG', 'SHORT', 'BOTH', 'SAME_AS_PREVIOUS_DIRECTION'))
    # A window after an event (수정본173): a time in seconds, or a count of one real frame's bars.
    window = {'type': 'object', 'additionalProperties': False,
        'properties': {'seconds': integer, 'bars': {'type': 'integer', 'minimum': 1, 'maximum': MAX_BARS},
            'tf': enum(catalog.TF_LABELS)},
        'oneOf': [{'required': ['seconds']}, {'required': ['bars', 'tf']}]}
    properties = {'kind': enum(sorted(KINDS)), 'tfs': {'type': 'array', 'minItems': 1, 'items': timeframe},
        'direction': enum(('LONG', 'SHORT', 'BOTH', 'SAME_AS_PREVIOUS_DIRECTION', 'OPPOSITE')),
        'recent': window, 'bar_state': enum(('UNSPECIFIED', 'CLOSED', 'FORMING')),
        'tf_combine': enum(('ALL', 'ANY', 'INDEPENDENT')), 'capture': identifier,
        'ref': identifier, 'scope_ref': identifier, 'negated': {'type': 'boolean'},
        'ma_family': enum(sorted(FAMILIES)), 'fast_period': integer, 'slow_period': integer,
        'price_tf': timeframe,
        'ma_left': {'type': 'string', 'pattern': '^(SMA|WMA|EMA|HMA)[1-9][0-9]*$'},
        'ma_right': {'type': 'string', 'pattern': '^(SMA|WMA|EMA|HMA)[1-9][0-9]*$'},
        'side': enum(('ABOVE', 'BELOW', 'UP', 'DOWN', 'BULL', 'BEAR', 'UPPER', 'LOWER', 'HIGH', 'LOW')),
        'state': enum(('EXISTS', 'AREA')), 'shape': enum(SHAPES), 'lookback': integer, 'session': enum(session_keys()),
        'level': {'anyOf': [{'type': 'number'}, {'type': 'string'}]},
        'relation': enum(('SLOPE_UP', 'SLOPE_DOWN', 'ABOVE', 'BELOW', 'IN', 'OUT_LOWER', 'OUT_UPPER', 'TOUCH', 'BREAK_UP', 'BREAK_DOWN')),
        'regime_families': {'type': 'array', 'minItems': 1, 'items': enum(sorted(REGIMES))},
        'families': {'type': 'array', 'minItems': 1, 'items': enum(sorted(REGIMES))},
        'family_combine': enum(('ALL', 'ANY', 'MATCHING_FAMILY')),
        'metric': enum(sorted(catalog.METRICS)), 'metric_operator': enum(('GT', 'GTE', 'LT', 'LTE', 'EQ', 'NE')),
        'metric_value': {'type': 'number'},
        'validation_mode': enum(catalog.PROFILES.VALIDATION_MODES), 'trigger_mode': enum(catalog.PROFILES.TRIGGER_MODES)}
    required = {'MA_STATE': ['ma_family', 'fast_period', 'slow_period'],
        'CANDLE_STATE': ['side', 'bar_state'], 'CANDLE_SHAPE': ['shape', 'bar_state'],
        'MA_PRICE_CROSS': ['ma_family', 'slow_period', 'relation'],
        'MA_PRICE_TOUCH': ['ma_family', 'slow_period'], 'MA_PRICE_STATE': ['ma_family', 'slow_period'],
        'MA_SLOPE_STATE': ['ma_family', 'slow_period'], 'MA_CROSS': ['ma_left', 'ma_right'],
        'TREND_METRIC': ['metric', 'metric_operator', 'metric_value'], 'SESSION_START': ['session'],
        'REGIME_BAND': ['regime_families', 'relation'], 'PRICE_LEVEL': ['level', 'relation'],
        'LIQUIDITY_LEVEL': ['level', 'relation'], 'OZ_ALERT': ['validation_mode', 'trigger_mode'],
        'PERCENTILE_OUT': ['families'], 'PERCENTILE_OUT_IN': ['families']}
    variants = []
    for kind in sorted(KINDS):
        permitted = (COMMON - {'tf'}) | PARAMS[kind]
        fields = {key: ({'const': kind} if key == 'kind' else properties[key])
            for key in sorted(permitted) if key in properties}
        if kind == 'MA_PRICE_CROSS': fields['relation'] = enum(('BREAK_UP', 'BREAK_DOWN', 'BOTH'))
        if 'side' in fields:
            fields['side'] = enum(('BULL', 'BEAR') if kind.startswith('FVG') or kind == 'CANDLE_STATE' else
                ('LOWER', 'UPPER') if kind == 'WONBI_TOUCH' or kind.startswith('PERCENTILE_') else
                ('HIGH', 'LOW') if kind == 'EXTERNAL_LIQUIDITY_TOUCH' else
                ('UP', 'DOWN') if kind == 'MA_SLOPE_STATE' else ('ABOVE', 'BELOW'))
        if kind in ('CANDLE_STATE', 'CANDLE_SHAPE'): fields['bar_state'] = enum(('FORMING', 'CLOSED'))
        if kind not in RECENT_KINDS: fields.pop('recent', None)
        variants.append({'type': 'object', 'additionalProperties': False,
            'properties': fields, 'required': ['kind', 'tfs', *required.get(kind, [])]})
    steps = {'type': 'array', 'items': {'$ref': '#/$defs/step'}}
    final = {'type': 'object', 'additionalProperties': False, 'required': ['kind'],
        'properties': {'kind': enum(('OZ', 'NOTIFY', 'DEFINE')),
            'tfs': {'type': 'array', 'minItems': 1, 'items': enum((*catalog.OZ_TFS, 'SOURCE', 'FINAL'))},
            'direction': direction, 'validation_mode': enum(catalog.PROFILES.VALIDATION_MODES),
            'trigger_mode': enum(catalog.PROFILES.TRIGGER_MODES), 'scope_ref': identifier,
            'tf_combine': enum(('ALL', 'ANY', 'INDEPENDENT')),
            'regime_family': enum((*sorted(REGIMES), 'MATCHING_FAMILY')),
            # Ties the final OZ to a captured external-liquidity touch and its ATR distance rule.
            'level_gate': {'type': 'object', 'additionalProperties': False, 'required': ['ref'],
                'properties': {'ref': identifier, 'atr_period': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                    'atr_mult': {'type': 'number', 'exclusiveMinimum': 0}}}}}
    snapshot = {'type': 'object', 'additionalProperties': False, 'required': ['tf', 'field'],
        'properties': {'tf': timeframe, 'field': enum(('open', 'high', 'low', 'close', 'ATR14')),
            'bar_state': {'const': 'CLOSED'}}}
    lifecycle = {'type': 'object', 'additionalProperties': False,
        'properties': {'expires': {'type': 'object', 'additionalProperties': False,
            'properties': {'seconds': integer, 'bars': integer, 'tf': timeframe,
                'bars_setting': {'type': 'string', 'pattern': '^[A-Z][A-Z0-9_]*$'}},
            'oneOf': [{'required': ['seconds']}, {'required': ['bars', 'tf']}]},
            'precondition_check': enum(PRECONDITION_CHECKS),
            'snapshots': {'type': 'object', 'additionalProperties': snapshot},
            'excursion': {'type': 'object', 'additionalProperties': False,
                'required': ['anchor', 'snapshot', 'multiplier'],
                'properties': {'tf': timeframe, 'anchor': identifier, 'snapshot': identifier,
                    'multiplier': {'type': 'number', 'exclusiveMinimum': 0}, 'direction': enum(('FAVORABLE', 'ADVERSE'))}},
            'replace': {'type': 'object', 'additionalProperties': False, 'required': ['scope'],
                'properties': {'scope': enum(('SYMBOL', 'SYMBOL_DIRECTION'))}},
            'first_success': {'type': 'boolean'}, 'invalidate_refs': {'type': 'boolean'}, 'restart_on': steps}}
    time_filters = {'anyOf': [{'type': 'array', 'items': {'type': 'string'}},
        {'type': 'object', 'additionalProperties': {'anyOf': [{'type': 'string'}, {'type': 'boolean'},
            {'type': 'object', 'additionalProperties': False, 'properties': {'enabled': {'type': 'boolean'},
                'start': {'type': 'string'}, 'end': {'type': 'string'}}}]}}, {'const': 0}]}
    meaning = {'type': 'object', 'additionalProperties': False,
        'required': ['direction', 'symbols', 'steps', 'order_mode', 'final'],
        'properties': {'direction': enum(('LONG', 'SHORT', 'BOTH')),
            'symbols': {'type': 'array', 'minItems': 1, 'items': enum(catalog.SYMBOLS)},
            'steps': steps, 'cancel_conditions': steps, 'final_conditions': steps, 'after_conditions': steps,
            'final': final, 'lifecycle': lifecycle, 'time_filters': time_filters, 'final_time_filters': time_filters,
            'order_mode': enum(('SIMULTANEOUS', 'SEQUENTIAL', 'UNORDERED')),
            'global_combine': enum(('ALL', 'ANY', 'INDEPENDENT')),
            'within_sec': {'anyOf': [integer, {'type': 'null'}]},
            'within': {'anyOf': [window, {'type': 'null'}]},
            'final_window_sec': {'anyOf': [integer, {'type': 'null'}]},
            'final_window_from': enum(WINDOW_ANCHORS),
            'final_after': enum(('FVG_NEW', 'FVG_TOUCH')), 'persistent': {'type': 'boolean'},
            'preset': enum(list_presets())}}
    meaning['properties']['branches'] = {'type': 'array', 'items': {'type': 'object',
        'additionalProperties': False, 'required': ['steps'],
        'properties': {key: value for key, value in meaning['properties'].items() if key in BRANCH_KEYS}}}
    return {'type': 'object', 'additionalProperties': False,
        '$defs': {'step': {'anyOf': variants}},
        'required': ['supported', 'intent', 'interpretation', 'needs_clarification', 'clarification_question', 'message_ko'],
        'properties': {'supported': {'type': 'boolean'}, 'intent': {'enum': ['CREATE_STRATEGY', None]},
            'interpretation': {'anyOf': [meaning, {'type': 'null'}]}, 'reason': {'type': 'string'},
            'needs_clarification': {'type': 'boolean'}, 'clarification_question': {'type': ['string', 'null']},
            'message_ko': {'type': 'string'}}}


def unsupported(reason='UNSUPPORTED_CONDITION', message='지원되지 않는 전략 조건입니다.'):
    return {'supported': False, 'reason': reason, 'message_ko': message, 'intent': None,
        'interpretation': None, 'needs_clarification': False, 'clarification_question': None}


def validate_intent(raw):
    keys(raw, {'supported', 'reason', 'message_ko', 'intent', 'interpretation', 'needs_clarification', 'clarification_question'}, 'AI 결과')
    if type(raw.get('supported')) is not bool: raise ValueError('supported는 true/false입니다.')
    if not raw['supported']: return unsupported(str(raw.get('reason') or 'MOSES_SCOPE_ONLY')[:80], str(raw.get('message_ko') or '모세 전략 해석 범위의 요청이 아닙니다.')[:400])
    if raw.get('intent') != 'CREATE_STRATEGY': raise ValueError('전략 생성 의도를 확인하세요.')
    result = copy.deepcopy(raw)
    result['interpretation'] = validate_meaning(result.get('interpretation'), contract_context())
    if type(result.get('needs_clarification', False)) is not bool: raise ValueError('needs_clarification은 true/false입니다.')
    result['needs_clarification'] = result.get('needs_clarification', False)
    question = result.get('clarification_question')
    if result['needs_clarification'] and not str(question or '').strip(): raise ValueError('확인 질문이 없습니다.')
    result['clarification_question'] = str(question)[:400] if question else None
    result['message_ko'] = str(result.get('message_ko') or '')[:400]; result.pop('reason', None)
    return result


def recipe_from_intent(raw):
    intent = validate_intent(raw)
    if not intent['supported'] or intent['needs_clarification']: raise ValueError('확인되지 않은 전략 의도는 적용할 수 없습니다.')
    recipe = {'schema_version': 2, 'base': 'AI', 'name': 'AI 자연어 전략', 'description': intent['message_ko'],
        'symbols': list(intent['interpretation']['symbols']), 'strategy_intent': intent['interpretation']}
    validate_recipe(recipe)
    return recipe


def validate_recipe(recipe):
    keys(recipe, {'schema_version', 'base', 'name', 'description', 'symbols', 'strategy_intent', 'virtual_entry'}, 'AI Recipe')
    if recipe.get('schema_version') != 2 or recipe.get('base') != 'AI': raise ValueError('AI Recipe 형식을 확인하세요.')
    if not isinstance(recipe.get('name'), str) or not recipe['name'].strip(): raise ValueError('전략 이름이 없습니다.')
    item = validate_meaning(recipe.get('strategy_intent'), contract_context())
    if item['symbols'] != recipe.get('symbols'): raise ValueError('Recipe 종목과 의도가 다릅니다.')
    from ..ai_compiler import execution_plan
    execution_plan(item)
    if 'virtual_entry' in recipe:
        # The recipe's virtual-entry 칸 is checked by the function Part2 reads it with.
        from .backtest_commands import virtual_contract
        virtual_contract()
        from event_backtest.virtual_defaults import recipe_policy
        recipe_policy(dict(recipe, strategy_intent=item))
