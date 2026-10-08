"""Opt-in real external AI probe; never applies, compiles or runs a strategy.

Run from the project root. Credentials remain in memory and HTTP headers.
Only public model JSON and sanitized validation evidence are written.
"""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import sys
import time
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai.settings import read_settings
from common_ai.security import filter_tools, secret_values, _text
from common_ai import reply_format as lora_worker
from lab.ai.agent import external_system_prompt
from lab.ai.tools import ReadOnlyWorkspace, TOOL_SPECS
from lab.ai.research_interpreter import response_schema
from lab.ai.intent import validate_intent
from jsonschema import Draft202012Validator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('provider', choices=['gemini'])
    parser.add_argument('--label', required=True)
    parser.add_argument('--message', default='15분 상승추세에 1분 올존 전략 만들어줘')
    parser.add_argument('--previous', help='Previous evidence label, from this provider and project only.')
    parser.add_argument('--research', action='store_true', help='Include the common one-time validation correction.')
    parser.add_argument('--replay-first', help='Replay a recorded invalid reply before a real correction call.')
    args = parser.parse_args()
    if not args.label.replace('_', '').replace('-', '').isalnum():
        raise ValueError('Use a simple evidence label.')
    settings = read_settings(ROOT)
    from common_ai import gemini as module
    from common_ai.gemini import Gemini
    provider = Gemini(settings)
    tools = filter_tools(provider, TOOL_SPECS)
    schema = response_schema('strategy')
    previous = None
    if args.previous:
        if not args.previous.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Use a simple evidence label.')
        source = ROOT / '검증결과' / ('external89_' + args.previous + '_' + args.provider + '.json')
        previous_proof = json.loads(source.read_text(encoding='utf-8'))
        if previous_proof.get('intent_valid') is not True:
            raise ValueError('Previous strategy must pass the original intent validation.')
        previous = previous_proof['response']
    messages = [{'role': 'system', 'content': external_system_prompt(ReadOnlyWorkspace())},
        {'role': 'user', 'content': json.dumps({'mode': 'strategy', 'message': args.message,
          'context': {'current_strategy': previous, 'previous_plan': None}, 'discussion': []}, ensure_ascii=False)}]
    proof = {'provider': args.provider, 'model': provider.model,
        'request': args.message, 'schema_valid': False, 'intent_valid': False,
        'api_calls': 0, 'responses': []}
    original = lora_worker.parse_reply
    secrets = secret_values(settings)
    original_open = module._open_request
    def capture_http(request, *, timeout):
        proof['api_calls'] += 1
        proof['request_bytes'] = len(request.data or b'')
        try:
            response = original_open(request, timeout=timeout)
            class CapturedResponse:
                def __enter__(self):
                    response.__enter__()
                    return self
                def __exit__(self, *args):
                    return response.__exit__(*args)
                def read(self, limit):
                    raw = response.read(limit)
                    try:
                        value = json.loads(raw)
                        if isinstance(value.get('usage'), dict):
                            proof['usage'] = value['usage']
                        if value.get('choices'):
                            choice = value['choices'][0]
                            proof['finish_reason'] = choice.get('finish_reason')
                            proof['content_chars'] = len(choice.get('message', {}).get('content') or '')
                    except (ValueError, TypeError, AttributeError):
                        pass
                    return raw
            return CapturedResponse()
        except urllib.error.HTTPError as exc:
            raw = exc.read(32768)
            try:
                error = json.loads(raw).get('error', {})
                proof['api_error'] = {key: _text(str(error[key]), secrets, 2000)
                    for key in ('message', 'code', 'type', 'status') if key in error}
                if isinstance(error.get('failed_generation'), str):
                    proof['failed_generation'] = _text(error['failed_generation'], secrets, 20000)
            except (ValueError, TypeError, AttributeError):
                pass
            raise urllib.error.HTTPError(exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(raw)) from None
    module._open_request = capture_http
    def capture(text, original_schema, allowed_tools):
        try:
            value = json.loads(text)
            proof['response'] = json.loads(_text(json.dumps(value, ensure_ascii=False), secrets, 60000))
            proof['responses'].append(proof['response'])
            errors = list(Draft202012Validator(lora_worker.reply_schema(original_schema, allowed_tools)).iter_errors(value))
            leaves = []
            def collect(error):
                if error.context:
                    for child in error.context:
                        collect(child)
                else:
                    leaves.append({'path': list(error.absolute_path), 'validator': error.validator,
                        'reason': _text(error.message, secrets, 600)})
            for error in errors:
                collect(error)
            proof['validation_errors'] = leaves[:70]
        except (ValueError, TypeError):
            proof['response_is_json'] = False
        return original(text, original_schema, allowed_tools)
    lora_worker.parse_reply = capture
    start = time.monotonic()
    try:
        if args.research:
            from lab.ai.agent import Agent
            from lab.ai.research_interpreter import interpret
            from common_ai.external_errors import ReplyValidationError, reply_error_message
            # Exercise real interpretation/validation without taking the
            # machine model lease or unloading the user's local model.
            provider_calls = 0
            def research_chat(rows, allowed, *, response_schema=None):
                nonlocal provider_calls
                provider_calls += 1
                proof['provider_calls'] = provider_calls
                if args.replay_first and provider_calls == 1:
                    label = args.replay_first
                    if not label.replace('_', '').replace('-', '').isalnum():
                        raise ValueError('Use a simple evidence label.')
                    recorded = json.loads((ROOT / '검증결과' / (
                        'external89_' + label + '_' + args.provider + '.json')).read_text('utf-8'))
                    text = json.dumps(recorded['response'], ensure_ascii=False)
                    proof['replayed_first_response'] = label
                    proof['responses'].append(recorded['response'])
                    try:
                        original(text, response_schema, allowed)
                    except ValueError:
                        raise ReplyValidationError(reply_error_message(args.provider,
                            text, response_schema, allowed), text) from None
                    raise ValueError('Replay must be an invalid schema reply.')
                return provider._chat(rows, allowed, response_schema, time.monotonic() + provider.timeout)
            provider.chat = research_chat
            context = {'current_strategy': previous, 'previous_plan': None}
            result, _ = interpret(provider, Agent(provider), 'strategy', [], context, args.message)
            result = {'content': json.dumps(result['strategy'], ensure_ascii=False), 'tool_calls': []}
        else:
            result = provider._chat(messages, tools, schema, start + provider.timeout)
        proof['schema_valid'] = True
        data = json.loads(result['content']) if result.get('content') else None
        if isinstance(data, dict) and 'interpretation' in data:
            validate_intent(data)
            proof['intent_valid'] = True
        elif result.get('tool_calls'):
            proof['read_only_tool_calls'] = [row['name'] for row in result['tool_calls']]
    except (ValueError, OSError) as exc:
        proof['error'] = _text(str(exc), secrets, 1000)
    finally:
        lora_worker.parse_reply = original
        module._open_request = original_open
    proof['elapsed_sec'] = round(time.monotonic() - start, 2)
    destination = ROOT / '검증결과' / ('external89_' + args.label + '_' + args.provider + '.json')
    destination.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: proof[key] for key in ('provider', 'model', 'schema_valid', 'intent_valid', 'elapsed_sec')}, ensure_ascii=False))
    print(destination.relative_to(ROOT).as_posix())
    return int(not proof['intent_valid'])


if __name__ == '__main__':
    raise SystemExit(main())
