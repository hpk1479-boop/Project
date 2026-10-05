"""Lazy, isolated PEFT inference. No optional ML imports in the web server."""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import queue
import subprocess
import sys
import threading
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def relative_path(value, root=PROJECT_ROOT):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('LoRA 학습 폴더를 입력하세요.')
    value = value.strip().replace('\\', '/')
    raw = Path(value)
    if raw.is_absolute() or PureWindowsPath(value).drive or '..' in raw.parts or any(ord(c) < 32 for c in value):
        raise ValueError('모델 폴더는 프로젝트 내부 상대 경로만 사용할 수 있습니다.')
    path = (Path(root) / raw).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('모델 폴더가 프로젝트 밖을 가리킵니다.')
    return path


def validate_local_settings(settings, *, require_model=True):
    result = dict(settings)
    for field, label in (('base_model', '기본 모델'), ('adapter_path', 'LoRA 학습 폴더')):
        value = result.get(field)
        if value is None or value == '':
            if require_model:
                raise ValueError(label + '이 설정되지 않았습니다.')
            continue
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError(label + ' 설정을 확인하세요.')
        value = value.strip().replace('\\', '/')
        relative_path(value)
        if field == 'base_model' and any(c.isspace() for c in value) and not value.startswith('./'):
            raise ValueError('공백이 있는 로컬 기본 모델 경로는 ./로 시작하세요.')
        result[field] = value
    for field in ('load_in_4bit', 'local_files_only'):
        if field in result and type(result[field]) is not bool:
            raise ValueError(field + ': 켜기 또는 끄기를 선택하세요.')
    tokens = result.get('max_new_tokens')
    if tokens is not None and (type(tokens) is not int or not 1 <= tokens <= 32768):
        raise ValueError('AI 응답 길이는 비우거나 1~32768의 정수여야 합니다.')
    if tokens is None:
        result.pop('max_new_tokens', None)
    return result


def resolve_base_model(value, root=PROJECT_ROOT):
    candidate = relative_path(value, root)
    if candidate.is_dir():
        return str(candidate)
    if value.startswith('./') or value.split('/')[0] == 'models':
        raise ValueError('프로젝트 내부 기본 모델 폴더가 없습니다: ' + value)
    # Hub IDs are identifiers, not stored machine paths; never enable remote code.
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?', value):
        raise ValueError('기본 모델 ID 또는 프로젝트 내부 폴더를 확인하세요.')
    return value


def check_adapter(settings, root=PROJECT_ROOT):
    path = relative_path(settings['adapter_path'], root)
    if not path.is_dir() or not (path / 'adapter_config.json').is_file():
        raise ValueError('LoRA 학습 파일이 없습니다: ' + settings['adapter_path'] + ' (adapter_config.json 필요)')
    if not any((path / name).is_file() for name in ('adapter_model.safetensors', 'adapter_model.bin',
                                                  'adapter_model.safetensors.index.json', 'adapter_model.bin.index.json')):
        raise ValueError('LoRA 가중치 파일이 없습니다: ' + settings['adapter_path'])
    return path


def worker_command():
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    return [str(executable), '-u', str(Path(__file__).with_name('lora_worker.py')),
            '--parent-pid', str(os.getpid())]


class WorkerClient:
    def __init__(self, settings, root=PROJECT_ROOT):
        self.settings = settings
        self.root = Path(root)
        self.process = None
        self.replies = queue.Queue()
        self.sequence = 0

    def start(self, deadline):
        check_adapter(self.settings, self.root)
        resolve_base_model(self.settings['base_model'], self.root)
        kwargs = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE, 'stderr': subprocess.DEVNULL,
                  'text': True, 'encoding': 'utf-8', 'bufsize': 1, 'cwd': str(self.root)}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(worker_command(), **kwargs)
        pipe = self.process.stdout
        replies = self.replies
        def read():
            try:
                for line in pipe:
                    replies.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                replies.put(None)
                pipe.close()
        threading.Thread(target=read, daemon=True).start()
        self.request({'op': 'load', 'settings': self.settings, 'root': str(self.root)}, deadline)

    def request(self, payload, deadline):
        if self.process is None or self.process.poll() is not None:
            raise ValueError('로컬 LoRA 추론 프로세스가 종료되었습니다. 다시 요청하세요.')
        self.sequence += 1
        payload = {**payload, 'id': self.sequence}
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            reply = self.replies.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise ValueError('로컬 LoRA 로딩·추론 대기 시간이 초과되었습니다. 설정의 대기 시간을 확인하세요.') from None
        except (OSError, BrokenPipeError):
            raise ValueError('로컬 LoRA 추론 프로세스와 연결이 끊겼습니다.') from None
        if not isinstance(reply, dict) or reply.get('id') != self.sequence:
            raise ValueError('로컬 LoRA 추론 프로세스 응답이 올바르지 않습니다.')
        if not reply.get('ok'):
            raise ValueError(reply.get('error') or '로컬 LoRA 로딩·추론에 실패했습니다.')
        return reply.get('result')

    def close(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin:
            import contextlib
            with contextlib.suppress(OSError):
                process.stdin.close()
        self.process = None


class LocalLoRA:
    permission_scope = 'local'
    def __init__(self, settings, *, root=None):
        self.settings = validate_local_settings(settings)
        self.root = PROJECT_ROOT if root is None else Path(root)
        self.model = self.settings['base_model']
        self.timeout = float(self.settings.get('timeout', 90))
        from .model_runtime import RUNTIME
        self.generation = RUNTIME.generation

    def chat(self, messages, tools, *, response_schema=None):
        from .model_runtime import RUNTIME
        from .provider import default_schema as output_schema
        from .security import external_payload
        schema = output_schema() if response_schema is None else response_schema
        messages, tools, schema = external_payload(messages, tools, schema, settings=self.settings)
        return RUNTIME.local_chat(self, messages, tools, schema)
