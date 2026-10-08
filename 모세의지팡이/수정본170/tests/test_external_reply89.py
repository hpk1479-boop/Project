"""Malformed external-model JSON remains rejected by the original contract.

These are response regressions, not model-tuning examples. No HTTP request,
strategy application, file generation, or automatic repair is performed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai.reply_format import parse_reply
from lab.ai.schema import output_schema


def canonical():
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
            'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}],
            'order_mode': 'SIMULTANEOUS',
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL',
                'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '15분봉 상승추세일 때 1분봉 올존 알림'}


def malformed(case):
    value = canonical()
    if case in ('nested-enum-array', 'real-combined-failure'):
        value['interpretation']['symbols'] = [['XAUUSD+', 'NAS100']]
    if case in ('null-reason', 'real-combined-failure'):
        value['reason'] = None
    if case in ('null-preset', 'real-combined-failure'):
        value['interpretation']['preset'] = None
    return value


@pytest.mark.parametrize('case', [
    'nested-enum-array', 'null-reason', 'null-preset', 'real-combined-failure'])
def test_real_failure_values_are_not_silently_flattened_removed_or_accepted(case):
    schema = output_schema()
    value = malformed(case)
    before = copy.deepcopy((schema, value))
    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ValueError, match='형식'):
        parse_reply(json.dumps(value, ensure_ascii=False), schema, [])
    assert (schema, value) == before


def test_individual_enum_values_and_omitted_unused_optional_fields_remain_valid():
    value = canonical()
    schema = output_schema()
    assert Draft202012Validator(schema).is_valid(value)
    result = parse_reply(json.dumps(value, ensure_ascii=False), schema, [])
    assert json.loads(result['content']) == value
    assert result['tool_calls'] == []
    assert value['interpretation']['steps'][0]['tfs'] == ['15m']
    assert value['interpretation']['final']['tfs'] == ['1m']


def test_optional_null_is_accepted_only_where_the_original_schema_allows_it():
    value = canonical()
    value['interpretation']['within_sec'] = None
    result = parse_reply(json.dumps(value, ensure_ascii=False), output_schema(), [])
    assert json.loads(result['content'])['interpretation']['within_sec'] is None
    # Other optional fields are not made nullable to accommodate bad replies.
    value['interpretation']['preset'] = None
    with pytest.raises(ValueError, match='형식'):
        parse_reply(json.dumps(value, ensure_ascii=False), output_schema(), [])
