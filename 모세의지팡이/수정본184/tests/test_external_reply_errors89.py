"""Safe original-schema diagnostics; all responses and credentials are synthetic."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai.external_errors import ReplyValidationError, reply_error_message
from lab.ai.research_interpreter import response_schema
from lab.ai.schema import output_schema


SECRET = 'synthetic-private-key.server-content-never-display'


def canonical():
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
            'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}],
            'order_mode': 'SIMULTANEOUS',
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': SECRET}


def diagnose(value, schema=None, tools=None, provider='gemini'):
    return reply_error_message(provider, json.dumps(value), schema or response_schema('chat'), tools or [])


@pytest.mark.parametrize('provider', ['gemini', 'future_external'])
def test_real_malformed_response_shows_three_safe_paths_not_alternative_envelopes(provider):
    value = canonical()
    value['interpretation']['symbols'] = [['XAUUSD+', 'NAS100']]
    value['interpretation']['preset'] = None
    value['reason'] = None
    schema = response_schema('chat')
    before = copy.deepcopy((value, schema))
    assert not Draft202012Validator(schema).is_valid(value)
    message = diagnose(value, schema, provider=provider)
    for path in ('$.interpretation.symbols[0]', '$.interpretation.preset', '$.reason'):
        assert path in message
    assert message.count(': ') == 3
    assert message.count('자료형 오류') == 3
    for hidden in (SECRET, 'XAUUSD+', 'NAS100', '$.kind', '$.plan', '$.strategy', '$.tool_calls'):
        assert hidden not in message
    assert (value, schema) == before


def test_missing_required_parameter_is_reported_at_its_schema_path():
    value = canonical()
    value['interpretation']['steps'] = [{'kind': 'MA_CROSS', 'tfs': ['1m'], 'ma_right': 'EMA200'}]
    message = diagnose(value)
    assert '$.interpretation.steps[0].ma_left: 필수 필드 누락' in message
    assert SECRET not in message and 'kind: ' not in message


def test_per_condition_enum_error_does_not_list_all_other_condition_variants():
    value = canonical()
    value['interpretation']['steps'] = [{'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': SECRET}]
    message = diagnose(value)
    assert '$.interpretation.steps[0].side: 허용값 오류' in message
    assert 'ma_left' not in message and 'metric' not in message and SECRET not in message


def test_unexpected_fields_never_print_the_untrusted_field_names_or_values():
    value = canonical()
    value['interpretation'][SECRET] = {'token': SECRET}
    message = diagnose(value)
    assert '$.interpretation: 불필요한 필드' in message
    assert SECRET not in message and 'token' not in message


def test_dynamic_reference_keys_are_hidden_but_schema_field_paths_remain_useful():
    value = canonical()
    value['interpretation']['lifecycle'] = {'snapshots': {
        SECRET: {'tf': '15m', 'field': 'close', 'bar_state': SECRET}}}
    message = diagnose(value)
    assert '$.interpretation.lifecycle.snapshots[항목].bar_state: 허용값 오류' in message
    assert SECRET not in message


def test_backtest_error_selects_its_envelope_and_matching_draft_branch():
    command = {'supported': True, 'action': SECRET, 'request': None, 'job_id': None,
        'needs_clarification': False, 'clarification_question': None, 'message_ko': SECRET}
    value = {'kind': 'BACKTEST', 'strategy': None, 'plan': {'strategy_text': None,
        'steps': [{'draft': False, 'command': command}],
        'needs_clarification': False, 'clarification_question': None}, 'message_ko': SECRET}
    message = diagnose(value)
    assert '$.plan.steps[0].command.action: 허용값 오류' in message
    assert '$.supported' not in message and '$.intent' not in message
    assert 'request.target_mode' not in message and SECRET not in message


def test_tool_failure_selects_tool_contract_without_strategy_noise():
    tools = [{'type': 'function', 'function': {'name': 'vocabulary', 'parameters': {
        'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
    value = {'tool_calls': [{'name': SECRET, 'arguments': {}}]}
    message = diagnose(value, tools=tools)
    assert '$.tool_calls[0].name: 허용값 오류' in message
    assert '$.supported' not in message and '$.intent' not in message and SECRET not in message


def test_oneof_multiple_valid_options_reports_combination_at_parent_path():
    value = canonical()
    value['interpretation']['lifecycle'] = {'expires': {'seconds': 30, 'bars': 3, 'tf': '15m'}}
    message = diagnose(value)
    assert '$.interpretation.lifecycle.expires: 형식 오류' in message
    assert SECRET not in message


def test_oneof_incomplete_lifetime_selects_bars_branch_instead_of_unrelated_seconds():
    value = canonical()
    value['interpretation']['lifecycle'] = {'expires': {'bars': 3}}
    message = diagnose(value)
    assert '$.interpretation.lifecycle.expires.tf: 필수 필드 누락' in message
    assert '.seconds' not in message


def test_non_object_json_and_invalid_syntax_do_not_echo_response_contents():
    message = diagnose([SECRET], output_schema())
    assert '$: 자료형 오류' in message and SECRET not in message
    syntax = reply_error_message('gemini', '{' + SECRET, output_schema(), [])
    assert '완전한 JSON이 아닙니다' in syntax and SECRET not in syntax


def test_maximum_three_issues_are_returned_even_when_many_required_fields_are_missing():
    message = diagnose({})
    assert message.count(': ') <= 3
    assert '필수 필드 누락' in message and SECRET not in message


def test_typed_reply_failure_keeps_raw_json_only_in_private_repair_attribute():
    import traceback
    value = canonical()
    value['reason'] = None
    raw = json.dumps(value)
    public = diagnose(value)
    failure = ReplyValidationError(public, raw)
    assert isinstance(failure, ValueError)
    assert failure.reply_text == raw
    assert failure.args == (public,)
    assert str(failure) == public
    rendered = str(failure) + repr(failure) + ''.join(traceback.format_exception_only(failure))
    assert SECRET not in rendered and raw not in rendered
    assert '$.reason: 자료형 오류' in rendered
