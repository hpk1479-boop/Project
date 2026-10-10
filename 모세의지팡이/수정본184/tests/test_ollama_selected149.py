"""Check Ollama only when selected, or when closing this runtime's own model."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common_ai import gemini, gguf_engine, local_gguf, model_runtime, provider


SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}
MESSAGES = [{'role': 'user', 'content': 'Return an answer.'}]
ANSWER = {'content': '{"answer":"ok"}', 'tool_calls': []}


class Lease:
    def __init__(self):
        self.held = False
        self.release_count = 0

    def acquire(self):
        self.held = True

    def release(self):
        self.held = False
        self.release_count += 1


class Ollama:
    def __init__(self, loaded):
        self.loaded = list(loaded)
        self.calls = []
        self.failure = None

    def http(self, route, _deadline, data=None):
        self.calls.append((route, None if data is None else dict(data)))
        if self.failure:
            self.failure(route)
        if route == '/api/ps':
            return {'models': [{'name': name} for name in self.loaded]}
        assert route == '/api/generate'
        assert data['keep_alive'] == 0 and data['stream'] is False
        self.loaded = [name for name in self.loaded if name != data['model']]
        return {'done': True}

    @property
    def unloaded(self):
        return [data['model'] for route, data in self.calls if route == '/api/generate']


@pytest.fixture
def runtime(monkeypatch):
    value = model_runtime.ModelRuntime()
    value.lease = Lease()
    monkeypatch.setattr(model_runtime, 'RUNTIME', value)
    monkeypatch.setattr(model_runtime.urllib.request, 'urlopen',
        Mock(side_effect=AssertionError('Real network access is not allowed.')))
    return value


def non_ollama_call(monkeypatch, kind):
    """Use the actual provider entry point, replacing only its inference backend."""
    inference = Mock(return_value=ANSWER)
    starts = []
    if kind == 'gemini':
        selected = gemini.Gemini({'provider': 'gemini', 'gemini_model': 'selected-model',
            'gemini_api_key': 'synthetic_key', 'timeout': 10})
        monkeypatch.setattr(selected, '_chat', inference)
    else:
        class Server:
            def __init__(self, _settings, _root):
                self.process = SimpleNamespace(poll=lambda: None)

            def start(self, deadline):
                starts.append(deadline)

            def chat(self, *args):
                return inference(*args)

            def close(self):
                pass

        monkeypatch.setattr(local_gguf, 'check_files', Mock())
        monkeypatch.setattr(gguf_engine, 'GGUFServer', Server)
        selected = local_gguf.LocalGGUF({'provider': 'local_gguf',
            'gguf_model_path': 'models/selected.gguf',
            'llama_server_path': 'runtime/llama.cpp/llama-server.exe', 'timeout': 10}, root=ROOT)
    return lambda: selected.chat(MESSAGES, [], response_schema=SCHEMA), inference, starts


@pytest.mark.parametrize('kind', ['local_gguf', 'gemini'])
@pytest.mark.parametrize('probe', ['timeout', 'invalid_inventory', 'unrelated_models'])
def test_fresh_non_ollama_provider_never_probes_or_unloads_ollama(runtime, monkeypatch, kind, probe):
    if probe == 'timeout':
        http = Mock(side_effect=TimeoutError('Ollama is unresponsive.'))
    elif probe == 'invalid_inventory':
        http = Mock(return_value={'unexpected': 'invalid Ollama inventory'})
    else:
        http = Mock(return_value={'models': [{'name': 'another-application:9b'}]})
    monkeypatch.setattr(runtime, '_http', http)
    call, inference, starts = non_ollama_call(monkeypatch, kind)
    assert call() == ANSWER
    http.assert_not_called()
    inference.assert_called_once()
    assert len(starts) == (1 if kind == 'local_gguf' else 0)
    assert runtime.lease.held == (kind == 'local_gguf')


def test_selected_ollama_still_checks_inventory_and_cleans_other_models(runtime, monkeypatch):
    control = Ollama(['selected:4b', 'old:9b'])
    monkeypatch.setattr(runtime, '_http', control.http)
    inference = Mock(return_value=ANSWER)
    monkeypatch.setattr(provider.OpenAICompatible, 'chat', inference)
    selected = SimpleNamespace(model='selected:4b', timeout=1, generation=runtime.generation)
    assert runtime.ollama_chat(selected, MESSAGES, [], SCHEMA) == ANSWER
    assert control.calls[0] == ('/api/ps', None)
    assert control.unloaded == ['old:9b'] and control.loaded == ['selected:4b']
    assert runtime.ollama_model == 'selected:4b' and runtime.lease.held
    inference.assert_called_once()


@pytest.mark.parametrize('failure', ['timeout', 'invalid_inventory', 'http_error'])
def test_selected_ollama_inventory_failure_still_blocks_inference(runtime, monkeypatch, failure):
    if failure == 'timeout':
        http = Mock(side_effect=TimeoutError('Inventory timed out.'))
    elif failure == 'http_error':
        http = Mock(side_effect=urllib.error.HTTPError(
            provider.BASE_URL + '/api/ps', 503, 'Inventory unavailable.', {}, None))
    else:
        http = Mock(return_value={'models': 'invalid'})
    monkeypatch.setattr(runtime, '_http', http)
    inference = Mock(return_value=ANSWER)
    monkeypatch.setattr(provider.OpenAICompatible, 'chat', inference)
    selected = SimpleNamespace(model='selected:4b', timeout=1, generation=runtime.generation)
    with pytest.raises(ValueError, match='Ollama'):
        runtime.ollama_chat(selected, MESSAGES, [], SCHEMA)
    assert http.call_args.args[0] == '/api/ps'
    inference.assert_not_called()
    assert runtime.ollama_model is None and not runtime.lease.held


@pytest.mark.parametrize('kind', ['local_gguf', 'gemini'])
@pytest.mark.parametrize('pending', [None, 'loading:4b'])
def test_switch_closes_only_previous_owned_models_before_new_inference(runtime, monkeypatch, kind, pending):
    runtime.ollama_model = 'owned:4b'
    runtime.pending_ollama = pending
    control = Ollama(['owned:4b', 'another-application:9b'])
    monkeypatch.setattr(runtime, '_http', control.http)
    call, inference, starts = non_ollama_call(monkeypatch, kind)
    def infer(*_args):
        assert control.loaded == ['another-application:9b']
        assert runtime.ollama_model is None and runtime.pending_ollama is None
        return ANSWER
    inference.side_effect = infer
    assert call() == ANSWER
    assert set(control.unloaded) == {'owned:4b'} | ({pending} if pending else set())
    assert control.loaded == ['another-application:9b']
    assert len(starts) == (1 if kind == 'local_gguf' else 0)
    inference.assert_called_once()


@pytest.mark.parametrize('kind', ['local_gguf', 'gemini'])
def test_pending_request_is_closed_even_when_not_listed_without_touching_external_model(runtime, monkeypatch, kind):
    runtime.pending_ollama = 'loading:4b'
    control = Ollama(['another-application:9b'])
    monkeypatch.setattr(runtime, '_http', control.http)
    call, inference, _starts = non_ollama_call(monkeypatch, kind)
    assert call() == ANSWER
    assert control.unloaded == ['loading:4b']
    assert control.loaded == ['another-application:9b'] and runtime.pending_ollama is None
    inference.assert_called_once()


@pytest.mark.parametrize('kind', ['local_gguf', 'gemini'])
@pytest.mark.parametrize('pending', [None, 'loading:4b'])
@pytest.mark.parametrize('failure', ['inventory_timeout', 'unload_http'])
def test_failed_owned_cleanup_blocks_new_provider_keeps_lease_and_allows_retry(
        runtime, monkeypatch, kind, pending, failure):
    runtime.ollama_model = 'owned:4b'
    runtime.pending_ollama = pending
    control = Ollama(['owned:4b', 'another-application:9b'])
    def reject(route):
        if failure == 'inventory_timeout':
            raise TimeoutError('Inventory timed out.')
        if route == '/api/generate':
            raise urllib.error.HTTPError(provider.BASE_URL + route, 503, 'Unload rejected.', {}, None)
    control.failure = reject
    monkeypatch.setattr(runtime, '_http', control.http)
    call, inference, starts = non_ollama_call(monkeypatch, kind)
    with pytest.raises(ValueError, match='Ollama'):
        call()
    inference.assert_not_called()
    assert not starts and runtime.worker is None
    assert runtime.ollama_model == 'owned:4b' and runtime.pending_ollama == pending
    assert runtime.lease.held and runtime.lease.release_count == 0
    assert not runtime.lock.locked()
    assert 'another-application:9b' not in control.unloaded
    control.failure = None
    assert call() == ANSWER
    assert control.loaded == ['another-application:9b']
    assert runtime.ollama_model is None and runtime.pending_ollama is None
    assert runtime.lease.held == (kind == 'local_gguf')
    inference.assert_called_once()
