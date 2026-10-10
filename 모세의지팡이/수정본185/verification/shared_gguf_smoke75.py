"""Optional real-model test of common IPC, cache reuse, and portable paths."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import psutil
    from common_ai.client import Client
    from common_ai.model_runtime import RUNTIME
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--runner', default='runtime/llama.cpp/llama-server.exe')
    args = parser.parse_args()
    portable = ROOT / '검증결과' / ('공통AI_이동시험_' + str(os.getpid()))
    portable.mkdir(parents=True)
    shutil.copytree(ROOT / 'common_ai', portable / 'common_ai', ignore=shutil.ignore_patterns('__pycache__'))
    for source in (ROOT / args.runner).parent.iterdir():
        if source.is_file() and (source.suffix.lower() == '.dll' or source.name == Path(args.runner).name):
            target = portable / 'runtime' / 'llama.cpp' / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
    target = portable / 'models' / 'gguf' / Path(args.model).name
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(ROOT / args.model, target)
    settings = {'provider': 'local_gguf', 'gguf_model_path': target.relative_to(portable).as_posix(),
        'gguf_context_size': 4096, 'gguf_gpu_layers': 0, 'max_new_tokens': 256, 'timeout': 120}
    config = portable / 'settings' / 'ai_settings.json'
    config.parent.mkdir()
    config.write_text(json.dumps(settings), encoding='utf-8')
    watch, strategy = Client(portable), Client(portable)
    report = {'provider': 'local_gguf', 'model': args.model,
        'runner': args.runner, 'explicit_runner_setting': False, 'portable_root': portable.relative_to(ROOT).as_posix()}
    started = time.monotonic()
    schema = {'type': 'object', 'properties': {'message': {'type': 'string'}},
        'required': ['message'], 'additionalProperties': False}
    workers = []
    service = None
    try:
        if RUNTIME._loaded(time.monotonic() + 3, offline_ok=True):
            raise ValueError('Ollama 모델이 실행 중이라 실제 검증을 보류했습니다.')
        report['watch'] = watch.chat([{'role': 'user', 'content': '연결 성공이라고 짧게 답하세요.'}],
            [], response_schema=schema, role='watch')
        assert json.loads(report['watch']['content'])['message']
        record = json.loads((portable / 'runtime' / 'ai_service.json').read_text(encoding='utf-8'))
        service = psutil.Process(record['pid'])
        workers = [child for child in service.children(recursive=True) if child.name().lower() == 'llama-server.exe']
        assert len(workers) == 1
        report['one_resident_worker'] = True
        tools = [{'type': 'function', 'function': {'name': 'read_value',
            'description': '읽기 전용 값 조회', 'parameters': {'type': 'object',
            'properties': {'key': {'type': 'string', 'enum': ['test']}},
            'required': ['key'], 'additionalProperties': False}}}]
        call = strategy.chat([{'role': 'user', 'content':
            '답변하기 전에 read_value 도구를 key=test로 한 번 호출하세요.'}],
            tools, response_schema=schema, role='strategy')
        assert call['tool_calls'][0]['name'] == 'read_value'
        assert call['tool_calls'][0]['arguments'] == {'key': 'test'}
        report['tool_request'] = call
        call_id = call['tool_calls'][0]['id']
        history = [{'role': 'user', 'content': 'read_value 도구에서 받은 값을 알려주세요.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': call_id, 'type': 'function',
                'function': {'name': 'read_value', 'arguments': json.dumps({'key': 'test'})}}]},
            {'role': 'tool', 'tool_call_id': call_id, 'name': 'read_value',
                'content': json.dumps({'value': '공통 연결 성공'}, ensure_ascii=False)}]
        report['tool_final'] = strategy.chat(history, tools, response_schema=schema, role='strategy')
        assert json.loads(report['tool_final']['content'])['message']
        after = [child.pid for child in service.children(recursive=True) if child.name().lower() == 'llama-server.exe']
        assert after == [workers[0].pid]
        report['cache_reused'] = True
        watch.close()
        report['after_watch_closed'] = strategy.chat([{'role': 'user', 'content': '아직 연결되어 있다고 답하세요.'}],
            [], response_schema=schema, role='backtest')
        assert workers[0].is_running()
        report['pass'] = True
    except Exception as exc:
        report.update({'pass': False, 'error': str(exc)})
    finally:
        watch.close()
        strategy.close()
        try:
            Client(portable).shutdown(timeout=45)
        except Exception as exc:
            report['close_error'] = str(exc)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (any(worker.is_running() for worker in workers) or service is not None and service.is_running()):
            time.sleep(0.05)
        report['service_exited'] = service is None or not service.is_running()
        report['closed'] = report['service_exited'] and not any(worker.is_running() for worker in workers) and not (portable / 'runtime' / 'ai_service.json').exists() and not report.get('close_error')
        report['total_sec'] = round(time.monotonic() - started, 2)
        report_path = ROOT / '검증결과' / 'GGUF_기본실행기_실모델75.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        if report['closed']:
            # Only this newly-created, verified project-owned fixture is removed.
            assert portable.resolve().is_relative_to((ROOT / '검증결과').resolve())
            shutil.rmtree(portable)
    return int(not (report.get('pass') and report['closed']))


if __name__ == '__main__':
    raise SystemExit(main())
