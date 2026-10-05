"""Shipped HTTP paths expose the editor assets and token-protected preset flow."""
import http.client
import json
from pathlib import Path
import sys
import threading
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import server


@pytest.fixture
def host():
    current = server.LabServer(0)
    worker = threading.Thread(target=current.serve_forever, daemon=True)
    worker.start()
    try:
        yield current
    finally:
        current.shutdown()
        current.server_close()
        worker.join(timeout=5)


def request(host, method, path, data=None, token=True):
    client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
    headers = {'X-Lab-Token': host.token} if token else {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    try:
        client.request(method, path, body=body, headers=headers)
        result = client.getresponse()
        return result.status, result.getheader('Content-Type'), result.read()
    finally:
        client.close()


@pytest.mark.parametrize('filename', ['ai_editor.js', 'ai_editor.css'])
def test_actual_server_serves_current_editor_assets(host, filename):
    status, content_type, body = request(host, 'GET', '/' + filename)
    assert status == 200
    assert body == (ROOT / 'Part3/web' / filename).read_bytes()
    assert 'text/plain' not in content_type


def test_preset_routes_require_token_and_research_mode(host, monkeypatch):
    session = Mock()
    session.presets.return_value = {'items': [{'id': 'MY_PRESET', 'name': '원본 전략'}], 'revision': 'r:0'}
    session.load_preset.return_value = {'kind': 'STRATEGY', 'revision': 'r:1', 'can_apply': True}
    get_session = Mock(return_value=session)
    monkeypatch.setattr(server, 'research_session', get_session)
    data = {'session': 'test-dialog', 'research': True}
    status, _, _ = request(host, 'POST', '/api/ai/presets', data, token=False)
    assert status == 403
    get_session.assert_not_called()
    status, _, body = request(host, 'POST', '/api/ai/presets', data)
    assert status == 200 and json.loads(body)['items'][0]['id'] == 'MY_PRESET'
    session.presets.assert_called_once_with()
    status, _, body = request(host, 'POST', '/api/ai/preset/load',
                              {**data, 'preset_id': 'MY_PRESET', 'revision': 'r:0'})
    assert status == 200 and json.loads(body)['can_apply']
    session.load_preset.assert_called_once_with('MY_PRESET', 'r:0')
    assert get_session.call_args.args == ('test-dialog',)
    status, _, _ = request(host, 'POST', '/api/ai/presets', {'session': 'test-dialog'})
    assert status == 404

