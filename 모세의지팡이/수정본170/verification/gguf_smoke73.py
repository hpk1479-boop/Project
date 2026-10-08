"""Optional actual GGUF test. No Ollama/model/settings changes outside this run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))


def main():
    from lab.ai.provider import from_settings
    from lab.ai.model_runtime import RUNTIME
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help='Project-relative GGUF file')
    parser.add_argument('--runner', required=True, help='Project-relative llama-server executable')
    args = parser.parse_args()
    settings = {'provider': 'local_gguf', 'gguf_model_path': args.model,
                'llama_server_path': args.runner, 'gguf_context_size': 16384,
                'gguf_gpu_layers': 0, 'max_new_tokens': 512, 'timeout': 300}
    report = {'provider': 'local_gguf', 'model': args.model, 'runner': args.runner,
              'gpu_layers': 0}
    started = time.monotonic()
    try:
        if RUNTIME._loaded(time.monotonic() + 3, offline_ok=True):
            raise ValueError('현재 Ollama 모델이 실행 중입니다. 실제 검증을 보류했습니다.')
        ai = from_settings(settings)
        schema = {'type': 'object', 'properties': {'message': {'type': 'string'}},
                  'required': ['message'], 'additionalProperties': False}
        report['first'] = ai.chat([{'role': 'user', 'content':
            '직접 실행 성공이라고 한국어로 짧게 답하세요.'}], [], response_schema=schema)
        assert json.loads(report['first']['content'])['message']
        process = RUNTIME.worker.process
        tools = [{'type': 'function', 'function': {'name': 'read_value',
            'description': '읽기 전용 값 조회', 'parameters': {'type': 'object',
            'properties': {'key': {'type': 'string', 'enum': ['test']}},
            'required': ['key'], 'additionalProperties': False}}}]
        call = ai.chat([{'role': 'user', 'content':
            '답변하기 전에 read_value 도구를 key=test로 한 번 호출해 주세요.'}],
            tools, response_schema=schema)
        report['tool_request'] = call
        assert call['tool_calls'][0]['name'] == 'read_value'
        assert call['tool_calls'][0]['arguments'] == {'key': 'test'}
        callid = call['tool_calls'][0]['id']
        history = [{'role': 'user', 'content': 'read_value 도구에서 받은 값이 무엇인지 알려주세요.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': callid, 'type': 'function',
                'function': {'name': 'read_value', 'arguments': json.dumps({'key': 'test'})}}]},
            {'role': 'tool', 'tool_call_id': callid, 'name': 'read_value',
             'content': json.dumps({'value': '직접 실행 성공'}, ensure_ascii=False)}]
        report['tool_final'] = ai.chat(history, tools, response_schema=schema)
        assert json.loads(report['tool_final']['content'])['message']
        assert RUNTIME.worker.process is process
        report['cache_reused'] = True
        report['pass'] = True
    except Exception as exc:
        report.update({'pass': False, 'error': str(exc)})
    finally:
        RUNTIME.shutdown()
        report['closed'] = RUNTIME.worker is None
        report['total_sec'] = round(time.monotonic() - started, 2)
        path = ROOT / '검증결과/GGUF_provider_actual73.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    return int(not (report.get('pass') and report['closed']))


if __name__ == '__main__':
    raise SystemExit(main())
