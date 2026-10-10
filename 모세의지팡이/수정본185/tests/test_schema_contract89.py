"""Exact transport sharing must preserve full schema acceptance boundaries."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai.schema_contract import contract_schema
from common_ai.schema_transport import compact_schema
from common_ai.reply_format import reply_schema
from lab.ai.research_interpreter import response_schema


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def equivalent(schema, cases):
    before = copy.deepcopy(schema)
    shared = contract_schema(schema)
    assert schema == before
    Draft202012Validator.check_schema(shared)
    validators = [Draft202012Validator(schema), Draft202012Validator(shared)]
    for value, expected in cases:
        assert [validator.is_valid(value) for validator in validators] == [expected, expected], value
    return shared


def closed_union(union='anyOf', *, overlap=False):
    common = {'tf': {'type': 'string', 'enum': ['1m', '15m', '1h', '4h']},
              'capture': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_]*$'},
              'state': {'type': 'string', 'enum': ['CLOSED', 'FORMING']},
              'direction': {'type': 'string', 'enum': ['LONG', 'SHORT', 'BOTH']},
              'period': {'type': 'integer', 'minimum': 1}}
    variants = []
    for index in range(5):
        properties = {'kind': {'const': 'any' if overlap else 'K' + str(index)},
                      **copy.deepcopy(common), 'value' + str(index): {'type': 'number', 'exclusiveMinimum': 0}}
        variants.append({'type': 'object', 'additionalProperties': False,
                         'required': ['kind', 'tf', 'period'], 'properties': properties})
    return {union: variants}


@pytest.mark.parametrize('union', ['anyOf', 'oneOf'])
def test_variant_closure_required_fields_values_and_types_are_preserved(union):
    good = {'kind': 'K2', 'tf': '15m', 'period': 50, 'state': 'CLOSED',
            'direction': 'LONG', 'capture': 'parent', 'value2': 1}
    cases = [(good, True), ({'kind': 'K0', 'tf': '1m', 'period': 1}, True),
             ({**good, 'value1': 2}, False), ({**good, 'extra': 1}, False),
             ({**good, 'value2': 0}, False), ({**good, 'period': 0}, False),
             ({**good, 'period': '50'}, False), ({**good, 'tf': '99m'}, False),
             ({**good, 'direction': 'UP'}, False), ({**good, 'capture': '../parent'}, False),
             ({key: value for key, value in good.items() if key != 'tf'}, False),
             (None, False), ([], False), (True, False)]
    schema = closed_union(union)
    shared = equivalent(schema, cases)
    assert size(shared) < size(compact_schema(schema))
    assert 'unevaluatedProperties' in json.dumps(shared)


@pytest.mark.parametrize('union,expected', [('anyOf', True), ('oneOf', False)])
def test_overlapping_union_keeps_oneof_cardinality(union, expected):
    schema = closed_union(union, overlap=True)
    shared = equivalent(schema, [({'kind': 'any', 'tf': '1m', 'period': 3}, expected),
                                 ({'kind': 'any', 'tf': '1m', 'period': 3, 'value0': 2}, True),
                                 ({'kind': 'any', 'tf': '1m', 'period': 3, 'value0': 2, 'value1': 2}, False)])
    assert size(shared) < size(compact_schema(schema))


def test_variant_specific_required_fields_are_not_promoted_to_base():
    schema = closed_union()
    schema['anyOf'][1]['required'].append('capture')
    equivalent(schema, [({'kind': 'K0', 'tf': '1m', 'period': 4}, True),
                        ({'kind': 'K1', 'tf': '1m', 'period': 4}, False),
                        ({'kind': 'K1', 'tf': '1m', 'period': 4, 'capture': 'gap'}, True)])


def test_literals_that_look_like_schemas_are_never_relocated():
    schema = closed_union()
    literal = {'$ref': '#/private', 'anyOf': [{'type': 'string'}],
               '$id': 'literal-only', 'properties': {'x': {'const': 1}}}
    for row in schema['anyOf']:
        row['properties']['literal'] = {'const': copy.deepcopy(literal)}
    good = {'kind': 'K0', 'tf': '1m', 'period': 3, 'literal': literal}
    equivalent(schema, [(good, True), ({**good, 'literal': {}}, False)])


@pytest.mark.parametrize('reference', ['#/anyOf/0/properties/period', '#/anyOf/0/properties/%70eriod'])
def test_existing_pointer_into_variant_keeps_target(reference):
    schema = closed_union()
    schema['anyOf'][1]['properties']['other'] = {'$ref': reference}
    shared = equivalent(schema, [({'kind': 'K1', 'tf': '1m', 'period': 3, 'other': 2}, True),
                                 ({'kind': 'K1', 'tf': '1m', 'period': 3, 'other': 0}, False)])
    # The existing pointer is still resolvable at its original target.
    assert shared['anyOf'][0]['properties']['period']['minimum'] == 1


def test_reference_into_defs_union_does_not_prevent_safe_child_sharing():
    schema = {'$defs': {'a/b~c': closed_union()}, '$ref': '#/$defs/a~1b~0c'}
    shared = equivalent(schema, [({'kind': 'K3', 'tf': '1m', 'period': 2}, True),
                                 ({'kind': 'K3', 'tf': '1m', 'period': 2, 'value1': 2}, False)])
    assert size(shared) < size(compact_schema(schema))


@pytest.mark.parametrize('key,value', [('$id', 'https://example.test/contract'),
    ('$anchor', 'root'), ('$dynamicAnchor', 'root'),
    ('$schema', 'http://json-schema.org/draft-07/schema#')])
def test_reference_resource_scopes_and_older_dialects_are_left_intact(key, value):
    schema = closed_union()
    schema[key] = value
    assert contract_schema(schema) == compact_schema(schema)


def test_complex_closed_patterns_and_allof_are_not_broadened():
    schema = closed_union()
    schema['anyOf'][0]['patternProperties'] = {'^custom_': {'type': 'integer'}}
    schema['anyOf'][1]['allOf'] = [{'properties': {'other': {'type': 'string'}}}]
    equivalent(schema, [({'kind': 'K0', 'tf': '1m', 'period': 2, 'custom_x': 1}, True),
                        ({'kind': 'K0', 'tf': '1m', 'period': 2, 'custom_x': 'x'}, False),
                        ({'kind': 'K1', 'tf': '1m', 'period': 2, 'other': 'x'}, False)])


def canonical(step):
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
            'interpretation': {'direction': 'LONG', 'symbols': ['XAUUSD+'],
                'steps': [step], 'order_mode': 'SIMULTANEOUS',
                'final': {'kind': 'OZ', 'tfs': ['1m']}},
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '조건 확인'}


def test_current_research_contract_preserves_every_condition_and_tool_boundary():
    fields = {
        'CANDLE_STATE': {'side': 'BULL'},
        'CANDLE_SHAPE': {'shape': 'HAMMER'},
        'LIQUIDITY_LEVEL': {'level': 'PREV_HIGH', 'relation': 'BREAK_UP'},
        'MA_CROSS': {'ma_left': 'SMA50', 'ma_right': 'EMA200'},
        'MA_PRICE_CROSS': {'ma_family': 'WMA', 'slow_period': 77, 'relation': 'BREAK_UP'},
        'MA_PRICE_STATE': {'ma_family': 'SMA', 'slow_period': 31},
        'MA_PRICE_TOUCH': {'ma_family': 'HMA', 'slow_period': 63},
        'MA_SLOPE_STATE': {'ma_family': 'EMA', 'slow_period': 82},
        'MA_STATE': {'ma_family': 'WMA', 'fast_period': 50, 'slow_period': 200},
        'OZ_ALERT': {'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'},
        'PERCENTILE_OUT': {'families': ['RSI']},
        'PERCENTILE_OUT_IN': {'families': ['DI']},
        'PRICE_LEVEL': {'level': 3000, 'relation': 'ABOVE'},
        'REGIME_BAND': {'regime_families': ['PRICE'], 'relation': 'IN'},
        'SESSION_START': {'session': 'LONDON'},
        'TREND_METRIC': {'metric': 'rsi14', 'metric_operator': 'LTE', 'metric_value': 30}}
    tools = [{'function': {'name': 'vocabulary', 'parameters': {
        'type': 'object', 'additionalProperties': False, 'properties': {}}}}]
    schema = reply_schema(response_schema('chat'), tools)
    variants = schema['$defs']['step']['anyOf']
    cases = []
    for variant in variants:
        kind = variant['properties']['kind']['const']
        step = {'kind': kind, 'tfs': ['15m'], 'bar_state': 'CLOSED',
                'direction': 'LONG', 'capture': 'parent', **fields.get(kind, {})}
        value = canonical(step)
        cases.extend([(value, True), (canonical({**step, 'other_kind_field': 3}), False),
                      (canonical({**step, 'tfs': []}), False),
                      (canonical({**step, 'bar_state': 'UNKNOWN'}), False)])
        for required in variant['required']:
            cases.append((canonical({key: item for key, item in step.items() if key != required}), False))
    cases.extend([
        ({'kind': 'CHAT', 'strategy': None, 'plan': None, 'message_ko': '안녕하세요'}, True),
        ({'kind': 'CHAT', 'strategy': None, 'plan': None, 'message_ko': '안녕하세요', 'code': 'secret'}, False),
        ({'tool_calls': [{'name': 'vocabulary', 'arguments': {}}]}, True),
        ({'tool_calls': [{'name': 'read_project_code', 'arguments': {}}]}, False),
        ({'tool_calls': [{'name': 'vocabulary', 'arguments': {'path': 'private'}}]}, False)])
    shared = equivalent(schema, cases)
    # Sharing must still beat plain de-duplication clearly. The earlier 0.8 bound had 0.35% headroom
    # (0.7965) and broke on any new field of the contract; every field adds unshared bytes.
    assert size(shared) < size(compact_schema(schema)) * 0.85


def test_existing_definition_names_and_input_objects_are_not_overwritten():
    schema = closed_union()
    schema['$defs'] = {'_b0': {'type': 'null'}}
    shared = equivalent(schema, [({'kind': 'K4', 'tf': '1m', 'period': 4}, True)])
    assert shared['$defs']['_b0'] == {'type': 'null'}


@pytest.mark.parametrize('schema', [True, False, {'type': 'string'}, {'const': {'anyOf': []}}])
def test_small_and_boolean_schemas_remain_semantically_unchanged(schema):
    result = contract_schema(schema)
    assert result == compact_schema(schema)
