"""Owned Ollama teardown must confirm exit without touching unrelated models."""
from __future__ import annotations

import errno
from pathlib import Path
import sys
import threading
import time
import urllib.error
from unittest.mock import Mock

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from common_ai.model_runtime import ModelRuntime


def canonical(name):
    return name if ':' in name.rsplit('/', 1)[-1] else name + ':latest'


class Control:
    def __init__(self, loaded):
        self.loaded = list(loaded)
        self.calls = []
        self.sticky = False
        self.failure = None
        self.deadlines = []

    def http(self, route, deadline, data=None):
        self.deadlines.append(deadline)
        self.calls.append((route, None if data is None else dict(data)))
        if self.failure:
            self.failure(route)
        if route == '/api/ps':
            return {'models': [{'name': name} for name in self.loaded]}
        assert route == '/api/generate'
        assert data['keep_alive'] == 0 and data['stream'] is False
        if not self.sticky:
            self.loaded = [name for name in self.loaded if canonical(name) != canonical(data['model'])]
        return {'done': True}

    @property
    def unloaded(self):
        return [data['model'] for route, data in self.calls if route == '/api/generate']


def owned_runtime(monkeypatch, loaded, *, model='moses:4b', pending=None):
    runtime = ModelRuntime()
    runtime.ollama_model = model
    runtime.pending_ollama = pending
    lease = Mock()
    lease.handle = object()
    lease.release.side_effect = lambda: setattr(lease, 'handle', None)
    runtime.lease = lease
    control = Control(loaded)
    monkeypatch.setattr(runtime, '_http', control.http)
    return runtime, control, lease


@pytest.mark.parametrize('selected,resident', [
    ('moses', 'moses:latest'), ('moses:latest', 'moses'),
    ('localhost:5000/team/moses', 'localhost:5000/team/moses:latest')])
def test_owned_alias_is_unloaded_and_unrelated_models_are_preserved(monkeypatch, selected, resident):
    runtime, control, lease = owned_runtime(monkeypatch,
        [resident, 'another:9b', 'moses:different-tag'], model=selected)
    runtime.shutdown(timeout=1)
    assert control.unloaded == [resident]
    assert control.loaded == ['another:9b', 'moses:different-tag']
    assert control.calls[-1][0] == '/api/ps'
    assert runtime.ollama_model is None and runtime.pending_ollama is None
    assert runtime._closing
    lease.release.assert_called_once()


def test_pending_timed_out_model_is_explicitly_unloaded_even_with_empty_inventory(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch, [], model=None, pending='loading:4b')
    runtime.shutdown(timeout=1)
    assert control.unloaded == ['loading:4b']
    assert control.calls[0] == ('/api/ps', None)
    assert control.calls[-1] == ('/api/ps', None)
    assert runtime.pending_ollama is None
    lease.release.assert_called_once()


def test_normal_and_pending_owned_names_are_both_closed_once(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch,
        ['moses:4b', 'other:9b'], model='moses:4b', pending='loading:4b')
    runtime.shutdown(timeout=1)
    assert set(control.unloaded) == {'moses:4b', 'loading:4b'}
    assert control.loaded == ['other:9b']
    lease.release.assert_called_once()


def test_normal_and_pending_alias_of_same_model_are_not_unloaded_twice(monkeypatch):
    runtime, control, _ = owned_runtime(monkeypatch,
        ['moses:latest'], model='moses', pending='moses:latest')
    runtime.shutdown(timeout=1)
    assert control.unloaded == ['moses:latest']


def test_owned_model_already_absent_skips_unload_and_clears_owned_name(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch, ['unrelated:9b'])
    runtime.shutdown(timeout=1)
    assert not control.unloaded and control.loaded == ['unrelated:9b']
    assert runtime.ollama_model is None
    lease.release.assert_called_once()


@pytest.mark.parametrize('pending', [None, 'loading:4b'])
def test_refused_ollama_listener_has_no_resident_model_and_allows_close(monkeypatch, pending):
    runtime, control, lease = owned_runtime(monkeypatch, [], pending=pending)
    def refused(_route):
        raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'fixture listener absent'))
    control.failure = refused
    runtime.shutdown(timeout=1)
    assert runtime.ollama_model is None and runtime.pending_ollama is None
    lease.release.assert_called_once()


@pytest.mark.parametrize('failure', ['inventory_timeout', 'unload_http', 'unconfirmed_unload'])
def test_uncertain_shutdown_keeps_ownership_names_and_can_be_retried(monkeypatch, failure):
    runtime, control, lease = owned_runtime(monkeypatch,
        ['moses:4b', 'unrelated:9b'], pending='loading:4b')
    if failure == 'inventory_timeout':
        control.failure = lambda _route: (_ for _ in ()).throw(TimeoutError('fixture inventory timeout'))
    elif failure == 'unload_http':
        def reject(route):
            if route == '/api/generate':
                raise urllib.error.HTTPError('http://127.0.0.1/fixture', 503, 'fixture rejected unload', {}, None)
        control.failure = reject
    else:
        control.sticky = True
    with pytest.raises(ValueError, match='Ollama AI 모델 종료를 확인하지 못했습니다'):
        runtime.shutdown(timeout=0.08)
    assert runtime.ollama_model == 'moses:4b' and runtime.pending_ollama == 'loading:4b'
    assert not runtime._closing and not runtime.lock.locked()
    assert lease.handle is not None
    lease.release.assert_not_called()
    assert 'unrelated:9b' not in control.unloaded
    control.failure = None
    control.sticky = False
    runtime.shutdown(timeout=1)
    assert control.loaded == ['unrelated:9b']
    assert runtime.ollama_model is None and runtime.pending_ollama is None
    lease.release.assert_called_once()


def test_shutdown_does_not_unload_model_after_another_runtime_acquires_lease(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch, ['moses:4b'])
    lease.acquire.side_effect = ValueError('다른 MOSES 창에서 AI 모델을 사용 중입니다.')
    with pytest.raises(ValueError, match='다른 MOSES 창'):
        runtime.shutdown(timeout=1)
    assert not control.calls
    assert runtime.ollama_model == 'moses:4b' and not runtime._closing
    lease.release.assert_not_called()


def test_shutdown_with_no_owned_ollama_model_never_queries_or_unloads_ollama(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch, ['unrelated:9b'], model=None)
    runtime.shutdown(timeout=1)
    assert not control.calls
    lease.acquire.assert_not_called()
    lease.release.assert_called_once()


def test_shutdown_deadline_includes_time_waiting_for_running_request(monkeypatch):
    runtime, control, lease = owned_runtime(monkeypatch, ['moses:4b'])
    control.sticky = True
    runtime.lock.acquire()
    errors, started = [], []
    def close():
        started.append(time.monotonic())
        try:
            runtime.shutdown(timeout=0.2)
        except Exception as error:
            errors.append(error)
    closer = threading.Thread(target=close)
    closer.start()
    try:
        deadline = time.monotonic() + 2
        while not runtime._closing:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        time.sleep(0.08)
    finally:
        runtime.lock.release()
        closer.join(3)
    assert not closer.is_alive() and len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert control.deadlines
    assert len(set(control.deadlines)) == 1
    assert control.deadlines[0] <= started[0] + 0.23, 'shutdown restarted its timeout after the queued request'
    assert runtime.ollama_model == 'moses:4b' and not runtime._closing
    lease.release.assert_not_called()
