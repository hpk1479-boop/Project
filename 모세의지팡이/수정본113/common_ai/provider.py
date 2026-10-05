"""Configured AI factories; models and credentials remain explicit."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_MODEL = None
BASE_URL = 'http://127.0.0.1:11434'
DEFAULT_PROVIDER = 'gemini'
PROVIDERS = ('gemini', 'ollama', 'local_gguf', 'local_lora', 'disabled')
DEFAULT_SCHEMA_FACTORY = None

def default_schema():
    return DEFAULT_SCHEMA_FACTORY() if DEFAULT_SCHEMA_FACTORY else {'type': 'object'}

def selected_provider(data):
    # Model-only settings predate the provider slot and already selected Ollama.
    return data.get('provider') or ('ollama' if data.get('model') is not None else DEFAULT_PROVIDER)


def require_ai_enabled(settings):
    if selected_provider(settings) == 'disabled':
        raise ValueError('AI 사용이 꺼져 있습니다. 공통 AI 설정에서 실행 방식을 선택하고 저장하세요.')



def model_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Ollama 모델명을 입력하세요.')
    value = value.strip()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Ollama 모델명에는 공백이나 제어 문자를 넣을 수 없습니다.')
    return value


class OpenAICompatible:
    permission_scope = 'local'
    def __init__(self, timeout: float = 90, *, model: str | None = DEFAULT_MODEL):
        if not model or (isinstance(model, str) and not model.strip()):
            raise RuntimeError('AI 모델이 설정되지 않았습니다.')
        self.url = BASE_URL + '/api/chat'
        self.model = model_name(model)
        self.timeout = timeout

    def chat(self, messages: list, tools: list, *, response_schema: dict | None = None) -> dict:
        from .provider import default_schema as output_schema
        from .security import external_payload
        schema = output_schema() if response_schema is None else response_schema
        # Direct callers use the same boundary as the shared AI service.
        messages, tools, schema = external_payload(messages, tools, schema,
            settings={'provider': 'ollama', 'model': self.model})
        # Native Ollama explicitly sets context size. With the default small
        # context, system contracts can be truncated into unrelated examples.
        body = {'model': self.model, 'messages': messages, 'tools': tools,
                'stream': False, 'think': False, 'format': schema,
                'options': {'temperature': 0.1, 'num_ctx': 16384}}
        request = urllib.request.Request(self.url,
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ValueError(f"로컬 모델 {self.model}이 없습니다. ollama pull {self.model}로 설치하세요.") from exc
            raise ValueError(f'Ollama 응답 오류: HTTP {exc.code}') from exc
        except urllib.error.URLError as exc:
            raise ValueError('로컬 Ollama에 연결하지 못했습니다. Ollama 실행 상태를 확인하세요.') from exc
        message = data['message']
        calls = []
        for call in message.get('tool_calls') or []:
            raw = call['function'].get('arguments') or '{}'
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (ValueError, TypeError):
                args = {'_unparsed': str(raw)[:200]}
            calls.append({'id': call.get('id') or call['function']['name'],
                          'name': call['function']['name'], 'arguments': args})
        return {'content': message.get('content') or '', 'tool_calls': calls}


class ScriptedProvider:
    """Offline, deterministic model for tests; never opens a socket."""
    permission_scope = 'local'
    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.seen = []
        self.response_schemas = []

    def chat(self, messages, tools, *, response_schema=None):
        self.seen.append(messages[-1])
        self.response_schemas.append(response_schema)
        return self.script.pop(0) if self.script else {'content': '', 'tool_calls': []}


def from_settings(settings: dict | None = None):
    settings = dict(settings or {})
    settings['provider'] = selected_provider(settings)
    if settings['provider'] not in PROVIDERS:
        raise ValueError('AI 실행 방식은 Gemini, Ollama, GGUF, 로컬 LoRA 또는 사용 안 함을 선택하세요.')
    require_ai_enabled(settings)
    if not any(settings.get(k) for k in ('model', 'gguf_model_path', 'base_model', 'gemini_model')):
        raise RuntimeError('AI 모델이 설정되지 않았습니다.')
    if settings.get('provider') == 'gemini':
        from .gemini import Gemini
        return Gemini(settings)
    if settings.get('provider') == 'local_gguf':
        from .local_gguf import LocalGGUF
        return LocalGGUF(settings)
    if settings.get('provider') == 'local_lora':
        from .local_lora import LocalLoRA
        return LocalLoRA(settings)
    if settings.get('provider') != 'ollama':
        raise ValueError('AI 실행 방식은 GGUF, Ollama, 로컬 LoRA 또는 Gemini를 선택하세요.')
    model = settings.get('model')
    if not model or (isinstance(model, str) and not model.strip()):
        raise RuntimeError('AI 모델이 설정되지 않았습니다.')
    # Ignore legacy base_url/key settings: neither code nor documents can
    # redirect model traffic to an external host.
    from .model_runtime import GuardedOllama
    return GuardedOllama(OpenAICompatible(float(settings.get('timeout', 90)), model=model))


AI_SETTING_KEYS = {'provider', 'model', 'timeout', 'base_model', 'adapter_path',
                   'load_in_4bit', 'local_files_only', 'max_new_tokens',
                   'gguf_model_path', 'llama_server_path', 'gguf_context_size',
                   'gguf_gpu_layers', 'gguf_threads', 'gguf_chat_template_path',
                   'gemini_model', 'gemini_api_key', 'watch_enabled'}


def configured_settings(data):
    """Readable incomplete settings; no implicit model or external endpoint."""
    provider = selected_provider(data)
    if provider not in PROVIDERS:
        raise ValueError('AI 실행 방식은 Gemini, Ollama, GGUF, 로컬 LoRA 또는 사용 안 함을 선택하세요.')
    model = data.get('model')
    if isinstance(model, str):
        model = model.strip() or None
    result = {'provider': provider, 'model': model_name(model) if model is not None else None,
              'timeout': data.get('timeout', 90)}
    result.update({key: data[key] for key in AI_SETTING_KEYS - {'provider', 'model', 'timeout'} if key in data})
    if 'watch_enabled' in result and type(result['watch_enabled']) is not bool:
        raise ValueError('WATCH AI 보조 해석은 켜기 또는 끄기를 선택하세요.')
    from .gemini import validate_settings
    result = validate_settings(result, required=False)
    from .local_lora import validate_local_settings
    result = validate_local_settings(result, require_model=False)
    from .local_gguf import validate_gguf_settings
    return validate_gguf_settings(result, require_model=False)
