"""Part3 adapter to the independent, shared AI service.

The existing Agent and backtest session keep their own schemas and tool loops.
Constructing this adapter never starts AI. Manual UI sessions may defer required
model selection until inference while still validating supplied setting values.
"""
from __future__ import annotations

from pathlib import Path

from .provider import configured_settings, model_name


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def validate_selection(settings):
    data = configured_settings(settings)
    if data['provider'] == 'disabled':
        return data
    if not any(data.get(key) for key in ('model', 'gguf_model_path', 'gemini_model')):
        raise RuntimeError('AI 모델이 설정되지 않았습니다.')
    selected = data['provider']
    if selected == 'ollama':
        data['model'] = model_name(data.get('model'))
    elif selected == 'local_gguf':
        from .local_gguf import validate_gguf_settings
        data = validate_gguf_settings(data)
    elif selected == 'gemini':
        from common_ai.gemini import validate_settings
        data = validate_settings(data, required=True)
    return data


class SharedProvider:
    def __init__(self, settings, *, role='strategy', root=None, client=None, allow_incomplete=False):
        from common_ai.client import Client
        self.settings = configured_settings(settings) if allow_incomplete else validate_selection(settings)
        self._allow_incomplete = allow_incomplete
        self.role = role
        self.root = Path(root or PROJECT_ROOT)
        self._owns_client = client is None
        self.client = Client(self.root) if client is None else client
        selected = self.settings['provider']
        self.model = self.settings.get('model' if selected == 'ollama' else
                                       'gemini_model' if selected == 'gemini' else
                                       'gguf_model_path' if selected == 'local_gguf' else None)
        self.timeout = self.settings['timeout']

    @property
    def permission_scope(self):
        """Use the settings the shared service will execute, including file changes."""
        from common_ai.security import provider_scope
        from common_ai.settings import read_settings
        # A failed read must not turn an external request into a local lookup.
        return provider_scope(read_settings(self.root))

    def chat(self, messages, tools, *, response_schema=None):
        from .schema import output_schema
        if self._allow_incomplete:
            from common_ai.settings import read_settings
            validate_selection(read_settings(self.root))
        schema = output_schema() if response_schema is None else response_schema
        return self.client.chat(messages, tools, response_schema=schema, role=self.role)

    def close(self):
        if self._owns_client:self.client.close()


def shared_from_settings(settings, *, role='strategy', root=None, client=None, allow_incomplete=False):
    return SharedProvider(settings, role=role, root=root, client=client, allow_incomplete=allow_incomplete)
