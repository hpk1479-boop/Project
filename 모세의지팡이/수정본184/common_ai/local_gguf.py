"""Configured GGUF inference through an owned local llama.cpp server."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLAMA_SERVER_PATH = 'runtime/llama.cpp/llama-server.exe'
GGUF_KEYS = {'gguf_model_path', 'llama_server_path', 'gguf_context_size',
             'gguf_gpu_layers', 'gguf_threads', 'gguf_chat_template_path'}


def relative_path(value, root=PROJECT_ROOT):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('프로젝트 내부의 상대 경로를 입력하세요.')
    value = value.strip().replace('\\', '/')
    raw = Path(value)
    if (raw.is_absolute() or PureWindowsPath(value).drive or value.startswith('/') or
            '..' in raw.parts or any(ord(c) < 32 for c in value)):
        raise ValueError('GGUF 모델과 실행기는 프로젝트 내부 상대 경로만 사용할 수 있습니다.')
    path = (Path(root) / raw).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('GGUF 경로가 프로젝트 밖을 가리킵니다.')
    return path


def validate_gguf_settings(settings, *, require_model=True):
    result = dict(settings)
    if (result.get('provider') == 'local_gguf' or require_model) and result.get('llama_server_path') in (None, ''):
        result['llama_server_path'] = DEFAULT_LLAMA_SERVER_PATH
    for field, label in (('gguf_model_path', 'GGUF 모델 파일'),
                         ('llama_server_path', 'GGUF 실행기'),
                         ('gguf_chat_template_path', '대화 형식 파일')):
        value = result.get(field)
        if value is None or value == '':
            if require_model and field != 'gguf_chat_template_path':
                raise ValueError(label + ' 경로가 설정되지 않았습니다.')
            result.pop(field, None)
            continue
        relative_path(value)
        value = value.strip().replace('\\', '/')
        if field == 'gguf_model_path' and Path(value).suffix.lower() != '.gguf':
            raise ValueError('모델 파일은 .gguf 형식이어야 합니다.')
        result[field] = value
    for field, label, minimum, maximum in (
            ('gguf_context_size', 'AI 대화 처리 길이', 512, 262144),
            ('gguf_gpu_layers', 'GPU 처리 레이어 수', -1, 65536),
            ('gguf_threads', 'CPU 스레드 수', 1, 1024),
            ('max_new_tokens', 'AI 응답 길이', 1, 32768),
            ('timeout', 'AI 응답 제한 시간', 10, 300)):
        value = result.get(field)
        if value is None:
            result.pop(field, None)
        elif type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(label + ': ' + str(minimum) + '~' + str(maximum) + '의 정수를 입력하세요.')
    if result.get('provider') == 'local_gguf' or require_model:
        result.setdefault('gguf_context_size', 16384)
        result.setdefault('gguf_gpu_layers', 0)
        result.setdefault('timeout', 90)
    return result


def check_files(settings, root=PROJECT_ROOT):
    settings = validate_gguf_settings(settings)
    model = relative_path(settings.get('gguf_model_path'), root)
    server = relative_path(settings.get('llama_server_path'), root)
    template = relative_path(settings['gguf_chat_template_path'], root) if settings.get('gguf_chat_template_path') else None
    if not model.is_file():
        raise ValueError('GGUF 모델 파일이 없습니다: ' + settings['gguf_model_path'])
    with model.open('rb') as handle:
        header = handle.read(24)
    if model.suffix.lower() != '.gguf' or len(header) < 24 or header[:4] != b'GGUF':
        raise ValueError('올바른 GGUF 모델 파일을 선택하세요: ' + settings['gguf_model_path'])
    if not server.is_file() or (os.name == 'nt' and server.suffix.lower() != '.exe'):
        raise ValueError('GGUF 실행기가 없습니다. llama-server 실행 파일과 함께 제공된 라이브러리를 준비하세요: '
                         + settings['llama_server_path'])
    if template is not None and not template.is_file():
        raise ValueError('대화 형식 파일이 없습니다: ' + settings['gguf_chat_template_path'])
    return model, server, template


class LocalGGUF:
    permission_scope = 'local'
    def __init__(self, settings, *, root=None):
        self.settings = validate_gguf_settings(settings)
        self.root = PROJECT_ROOT if root is None else Path(root)
        self.model = self.settings['gguf_model_path']
        self.timeout = float(self.settings['timeout'])
        from .model_runtime import RUNTIME
        self.generation = RUNTIME.generation

    def chat(self, messages, tools, *, response_schema=None):
        from .model_runtime import RUNTIME
        from .provider import default_schema as output_schema
        from .security import external_payload
        schema = output_schema() if response_schema is None else response_schema
        messages, tools, schema = external_payload(messages, tools, schema, settings=self.settings)
        return RUNTIME.gguf_chat(self, messages, tools, schema)
