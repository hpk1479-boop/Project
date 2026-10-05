"""Opt-in real JSON decoder check; no GPU packages, model or network required.

Pass a folder containing lm-format-enforcer/jsonschema as the first argument.
The official default suite uses isolated optional-dependency stubs instead.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) > 1:
    sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
sys.path.insert(0, str(ROOT / 'Part3'))
sys.path.insert(0, str(ROOT / 'Part3' / 'tests'))

from confirmed_answers import cases
from lab.ai.schema import output_schema
from lab.ai.backtest_commands import command_schema
from lab.ai.tools import TOOL_SPECS
from lab.ai.lora_worker import decoding_schema, reply_schema, parse_reply
from lmformatenforcer import JsonSchemaParser, TokenEnforcer, TokenEnforcerTokenizerData
from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig


def check(value, schema, tools=()):
    config = CharacterLevelParserConfig()
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    config.max_json_array_length = sys.maxsize
    config.alphabet = ''.join(sorted(set(config.alphabet + text)))
    parser = JsonSchemaParser(decoding_schema(reply_schema(schema, list(tools))), config=config)
    # Also exercise the actual enforcer initialization/config restoration used
    # by the worker, using a character vocabulary instead of a downloaded model.
    vocabulary = [(i, c, False) for i, c in enumerate(config.alphabet)]
    data = TokenEnforcerTokenizerData(vocabulary,
        lambda ids: ''.join(config.alphabet[i] for i in ids), len(vocabulary), False, len(vocabulary) + 1)
    enforcer = TokenEnforcer(data, parser)
    parser.config = config
    sequence = []
    indexes = {c: i for i, c in enumerate(config.alphabet)}
    for index, char in enumerate(text):
        allowed = enforcer.get_allowed_tokens(sequence)
        token_ids = getattr(allowed, 'allowed_tokens', allowed)
        if indexes[char] not in token_ids:
            raise AssertionError(f'token enforcer rejected valid JSON at character {index}: {char!r}')
        sequence.append(indexes[char])
        if char not in parser.get_allowed_characters():
            raise AssertionError(f'decoder rejected valid JSON at character {index}: {char!r}')
        parser = parser.add_character(char)
    if not parser.can_end():
        raise AssertionError('decoder did not accept complete JSON')
    result = parse_reply(text, schema, list(tools))
    assert isinstance(result['tool_calls'], list)
    return result


def main():
    count = 0
    schema = output_schema()
    for case in cases():
        check(case['intent'], schema)
        count += 1
    unsupported = {'supported': False, 'reason': 'MOSES_SCOPE_ONLY', 'message_ko': '범위 밖 요청입니다.',
        'intent': None, 'interpretation': None, 'needs_clarification': False, 'clarification_question': None}
    check(unsupported, schema); count += 1
    for size in (21, 64):
        intent = copy.deepcopy(next(cases())['intent'])
        intent['interpretation']['steps'] = [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}] * size
        check(intent, schema); count += 1
    for tool in TOOL_SPECS:
        function = tool['function']
        args = {}
        properties = function['parameters'].get('properties') or {}
        for key in function['parameters'].get('required') or []:
            prop = properties[key]
            args[key] = 1 if prop.get('type') == 'integer' else 'Part3/README.md'
        check({'tool_calls': [{'name': function['name'], 'arguments': args}]}, schema, TOOL_SPECS)
        count += 1
    command = {'supported': True, 'action': 'RECENT', 'request': None, 'job_id': None,
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '최근 작업 조회'}
    check(command, command_schema()); count += 1
    for invalid in ('not json', '[]', '{}', '{"supported":42}',
                    json.dumps({'tool_calls': [{'name': 'not_allowed', 'arguments': {}}]})):
        try:
            parse_reply(invalid, schema, TOOL_SPECS)
        except ValueError:
            count += 1
        else:
            raise AssertionError('invalid reply accepted')
    print(json.dumps({'check': 'local_lora_actual_json_decoder', 'pass': count, 'fail': 0,
        'model_inference': False, 'gpu_inference': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
