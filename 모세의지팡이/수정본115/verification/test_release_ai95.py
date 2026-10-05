"""Release 95 AI ownership, UI connection lifetime and manual preset boundaries."""
from __future__ import annotations

import gc
import json
from pathlib import Path
import sys
import threading
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]

from common_ai import client as client_module, provider
from common_ai.model_runtime import ModelRuntime
from common_ai.service import Service
from lab import server, unified_settings
from lab.ai import research_presets


def store(root, data):
    path = root / 'settings/ai_settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def runtime(owned='owned:4b', pending=None):
    value = ModelRuntime()
    value.ollama_model, value.pending_ollama = owned, pending
    value.lease = Mock(handle=object())
    value.lease.release.side_effect = lambda: setattr(value.lease, 'handle', None)
    value.lease.acquire.side_effect = lambda: setattr(value.lease, 'handle', object())
    loaded = ['owned:4b', 'unrelated:9b']
    calls = []
    def http(route, _deadline, data=None):
        calls.append((route, data))
        if route == '/api/ps':return {'models': [{'name': name} for name in loaded]}
        assert route == '/api/generate' and data['keep_alive'] == 0
        if data['model'] in loaded:loaded.remove(data['model'])
        return {'done': True}
    value._http = http
    return value, loaded, calls


@pytest.mark.parametrize('mode', ['disabled', 'gemini', 'local_gguf', 'local_lora'])
def test_settings_change_releases_only_owned_ollama(tmp_path, mode):
    store(tmp_path, {'provider': 'ollama', 'model': 'owned:4b'})
    selected, loaded, calls = runtime()
    service = Service(tmp_path, runtime=selected, provider_factory=Mock(side_effect=AssertionError('no inference')))
    try:
        store(tmp_path, {'provider': mode})
        service._sync_settings()
        assert loaded == ['unrelated:9b']
        assert [data['model'] for route, data in calls if route == '/api/generate'] == ['owned:4b']
        assert selected.ollama_model is None and selected.lease.handle is None
        assert selected.generation == 1 and service.settings['provider'] == mode
        service.factory.assert_not_called()
    finally:service.server.server_close()


def test_setting_change_during_ollama_inference_discards_response_and_unloads_after_turn(monkeypatch):
    selected, loaded, calls = runtime()
    selected._check_orphan_worker = lambda: None
    selected._ensure_ollama = lambda *_args, **_kwargs: None
    entered, release = threading.Event(), threading.Event()
    def infer(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return {'content': '{}', 'tool_calls': []}
    monkeypatch.setattr(provider.OpenAICompatible, 'chat', infer)
    old = SimpleNamespace(model='owned:4b', timeout=3, generation=0)
    results, errors = [], []
    def ask():
        try:results.append(selected.ollama_chat(old, [], [], None))
        except ValueError as exc:errors.append(str(exc))
    worker = threading.Thread(target=ask)
    worker.start()
    try:
        assert entered.wait(3)
        selected.invalidate()
        assert selected.generation == 1 and not calls and 'owned:4b' in loaded
    finally:
        release.set();worker.join(3)
    assert not worker.is_alive() and not results and len(errors) == 1
    assert '설정이 변경' in errors[0]
    assert loaded == ['unrelated:9b'] and selected.lease.handle is None


@pytest.mark.parametrize('pending', [None, 'pending:4b'])
def test_failed_unload_keeps_ownership_until_retry_succeeds(pending):
    selected, loaded, calls = runtime(pending=pending)
    original = selected._http
    def failing(route, deadline, data=None):
        if route == '/api/generate':raise TimeoutError('offline rejected unload')
        return original(route, deadline, data)
    selected._http = failing
    with pytest.raises(ValueError, match='종료를 확인'):
        selected.invalidate()
    assert selected.ollama_model == 'owned:4b' and selected.pending_ollama == pending
    assert selected.lease.handle is not None and not selected.lock.locked()
    selected._http = original
    selected.invalidate()
    assert loaded == ['unrelated:9b'] and selected.lease.handle is None
    assert selected.ollama_model is None and selected.pending_ollama is None
    if pending:assert pending in [data['model'] for route, data in calls if route == '/api/generate']


def test_invalidate_cannot_unload_another_runtime_owner():
    selected, loaded, calls = runtime()
    selected.lease.acquire.side_effect = ValueError('different owner')
    with pytest.raises(ValueError, match='different owner'):selected.invalidate()
    assert not calls and selected.ollama_model == 'owned:4b'
    assert loaded == ['owned:4b', 'unrelated:9b'] and not selected.lock.locked()


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', tmp_path / 'settings/ai_settings.json')
    monkeypatch.setattr(server, '_AI_CLIENT', None)
    monkeypatch.setattr(server, '_AI_SERVER_COUNT', 0)
    for name in ('AI_SESSIONS', 'RESEARCH_SESSIONS', 'BACKTEST_COMMAND_SESSIONS'):
        monkeypatch.setattr(server, name, {})
    store(tmp_path, {'provider': 'ollama', 'model': 'owned:4b'})
    yield tmp_path
    server.close_ai_client()


@pytest.fixture
def registered(ui, monkeypatch):
    selected, loaded, calls = runtime(owned=None)
    selected._http = Mock(side_effect=AssertionError('no model management needed'))
    service = Service(ui, runtime=selected, provider_factory=Mock(side_effect=AssertionError('no inference')))
    record = {'service_id': service.service_id}
    monkeypatch.setattr(client_module.Client, '_read_record', lambda self: record)
    monkeypatch.setattr(client_module.Client, '_request',
                        lambda self, _record, route, payload=None, **_kwargs: service.dispatch(route, payload))
    try:yield service
    finally:
        server.close_ai_client()
        service.server.server_close()


def test_repeated_resets_keep_one_ui_client_one_registration_and_model_cache(ui, registered, monkeypatch):
    callbacks = set()
    monkeypatch.setattr(client_module.atexit, 'register', callbacks.add)
    monkeypatch.setattr(client_module.atexit, 'unregister', callbacks.discard)
    old_sessions = []
    client_ids = set()
    for _ in range(6):
        agent = server.ai_agent('one')
        research = server.research_session('one')
        backtest = server.backtest_command_session('one')
        assert agent.provider.client is research.provider.client is backtest.provider.client
        agent.provider.client._connect()
        client_ids.add(agent.provider.client.client_id)
        old_sessions.append(weakref.ref(agent))
        server.ai_post('/api/ai/reset', {'session': 'one', 'research': True})
        del agent, research, backtest
    gc.collect()
    assert all(ref() is None for ref in old_sessions)
    assert len(client_ids) == len(registered.clients) == len(callbacks) == 1
    assert registered.runtime.generation == 0
    registered.runtime._http.assert_not_called()
    assert not any((server.AI_SESSIONS, server.RESEARCH_SESSIONS, server.BACKTEST_COMMAND_SESSIONS))
    server.close_ai_client()
    assert not registered.clients and not callbacks


def test_settings_temporary_clients_close_and_do_not_disconnect_shared_ui(ui, registered, monkeypatch):
    from lab.ai.model_runtime import RUNTIME
    monkeypatch.setattr(RUNTIME, 'invalidate', lambda: None)
    callbacks = set()
    monkeypatch.setattr(client_module.atexit, 'register', callbacks.add)
    monkeypatch.setattr(client_module.atexit, 'unregister', callbacks.discard)
    shared = server.ai_agent('first').provider.client
    shared._connect()
    for timeout in (100, 110, 120, 130, 140):
        unified_settings.save_ai({'timeout': timeout})
        assert not shared._closed and len(registered.clients) == len(callbacks) == 1
        assert not server.AI_SESSIONS
        assert server.ai_agent('new').provider.client is shared
    assert registered.settings['timeout'] == 140


def test_failed_settings_invalidation_closes_temporary_client(ui, monkeypatch):
    callbacks = set()
    monkeypatch.setattr(client_module.atexit, 'register', callbacks.add)
    monkeypatch.setattr(client_module.atexit, 'unregister', callbacks.discard)
    def fail(*_args, **_kwargs):raise ValueError('fake invalidation failure')
    monkeypatch.setattr(client_module.Client, 'invalidate', fail)
    with pytest.raises(ValueError, match='fake invalidation failure'):
        unified_settings.save_ai({'timeout': 100})
    assert not callbacks


def test_last_ui_server_close_unregisters_shared_client(registered):
    first, second = server.LabServer(0), server.LabServer(0)
    try:
        shared = server.ai_agent('one').provider.client
        shared._connect()
        first.server_close()
        assert not shared._closed and len(registered.clients) == 1
        second.server_close()
        assert shared._closed and not registered.clients and server._AI_CLIENT is None
    finally:
        first.server_close();second.server_close()


@pytest.mark.parametrize('action', ['reset', 'settings'])
def test_reset_or_settings_during_request_retains_shared_connection_and_fresh_session(registered, monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    class FakeProvider:
        def chat(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(3)
            return {'content': '{}', 'tool_calls': []}
    registered.factory = lambda _: FakeProvider()
    old = server.ai_agent('one')
    shared = old.provider.client
    responses, errors = [], []
    def chat():
        try:responses.append(shared.chat([{'role': 'user', 'content': 'offline'}], []))
        except Exception as exc:errors.append(exc)
    worker = threading.Thread(target=chat)
    worker.start()
    try:
        assert entered.wait(3)
        if action=='reset':server.ai_post('/api/ai/reset', {'session': 'one'})
        else:
            from lab.ai.model_runtime import RUNTIME
            monkeypatch.setattr(RUNTIME, 'invalidate', lambda: None)
            unified_settings.save_ai({'provider': 'disabled'})
        fresh = server.ai_agent('one')
        assert fresh is not old and fresh.provider.client is shared
    finally:release.set();worker.join(3)
    assert not worker.is_alive()
    if action=='reset':assert not errors and len(responses) == 1
    else:assert not responses and len(errors)==1 and '설정이 변경' in str(errors[0])
    assert fresh.last_intent is None and fresh.revision == 0
    assert not shared._closed and len(registered.clients) == 1


@pytest.mark.parametrize('settings,error', [
    ({}, '모델이 설정되지'),
    ({'provider': 'gemini', 'gemini_model': 'selected'}, '키'),
    ({'provider': 'ollama'}, '모델이 설정되지'),
])
def test_unconfigured_manual_presets_work_but_inference_is_rejected(ui, monkeypatch, settings, error):
    store(ui, settings)
    network = Mock(side_effect=AssertionError('No model connection for manual presets'))
    monkeypatch.setattr(client_module.Client, '_connect', network)
    result = server.ai_post('/api/ai/presets', {'session': 'manual', 'research': True})
    assert result['items'] and isinstance(result['revision'], str)
    research = server.research_session('manual')
    research.context_loader = lambda: {'options': {'symbols': ['XAUUSD+']}, 'today': '2026-10-03'}
    monkeypatch.setattr(research_presets.project_catalog, 'current_symbols', lambda: ['XAUUSD+'])
    loaded = server.ai_post('/api/ai/preset/load', {'session': 'manual', 'research': True,
                'preset_id': result['items'][0]['id'], 'revision': result['revision']})
    assert loaded['ok'] and loaded['can_apply']
    edited = server.ai_post('/api/ai/edit', {'session': 'manual', 'research': True,
                'revision': loaded['revision'], 'strategy': loaded['editor_strategy'], 'operation': 'STRATEGY'})
    assert edited['ok'] and edited['can_apply']
    selected = research.provider
    with pytest.raises((ValueError, RuntimeError), match=error):
        selected.chat([{'role': 'user', 'content': 'offline'}], [])
    network.assert_not_called()
    assert not (ui / 'runtime').exists()


def test_concurrent_session_creation_uses_one_client(ui):
    barrier = threading.Barrier(9)
    clients, failures = [], []
    def create(index):
        try:
            barrier.wait(timeout=3)
            clients.append(server.ai_agent(str(index)).provider.client)
        except Exception as exc:failures.append(exc)
    workers = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for worker in workers:worker.start()
    barrier.wait(timeout=3)
    for worker in workers:worker.join(3)
    assert not failures and len(clients) == 8 and len({id(client) for client in clients}) == 1
