"""First Gemini setup and confirmation UI state; only synthetic credentials and HTTP."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
_paths = sys.path[:]
try:
    sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
    from common_ai import client, gemini, model_runtime
    from lab import server, unified_settings
finally:
    sys.path[:] = _paths


def test_first_key_can_be_saved_before_selecting_a_model(monkeypatch, tmp_path):
    path = tmp_path / 'settings/ai_settings.json'
    path.parent.mkdir()
    path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(server, 'AI_SETTINGS', path)
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    for name in ('AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS', 'RESEARCH_SESSIONS'):
        monkeypatch.setattr(server, name, {})
    monkeypatch.setattr(client.Client, 'invalidate', lambda *a: None)
    monkeypatch.setattr(client.Client, 'close', lambda *a: None)
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', lambda: None)
    sent = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return b'{"models": []}'
    def google(request, **kwargs):
        sent.append(request.get_header('X-goog-api-key'))
        return Reply()
    monkeypatch.setattr(gemini, '_open_request', google)
    result = unified_settings.confirm_gemini_key({'key': 'gemini_api_key', 'value': 'synthetic-key-178'})
    saved = json.loads(path.read_text('utf-8'))
    assert result['configured'] is True and sent == ['synthetic-key-178']
    assert saved['gemini_api_key'] == 'synthetic-key-178' and 'gemini_model' not in saved
    # A key alone is not enough to run Gemini or save unrelated incomplete settings.
    with pytest.raises(ValueError, match='모델명'):
        unified_settings.save_ai({'timeout': 120})
    unified_settings.save_ai({'gemini_model': 'chosen-model'})
    assert json.loads(path.read_text('utf-8'))['gemini_model'] == 'chosen-model'
    assert sent == ['synthetic-key-178']


@pytest.mark.parametrize('route', ['/api/mo/settings', '/api/ai/settings'])
@pytest.mark.parametrize('value', ['', '   '])
def test_general_settings_reject_an_empty_key_without_deleting_it(monkeypatch, tmp_path, route, value):
    path = tmp_path / 'settings/ai_settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'provider': 'gemini', 'gemini_model': 'chosen-model',
                               'gemini_api_key': 'synthetic-saved-key-178'}), encoding='utf-8')
    before = path.read_bytes()
    monkeypatch.setattr(server, 'AI_SETTINGS', path)
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    sessions = ('AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS', 'RESEARCH_SESSIONS')
    for name in sessions:
        monkeypatch.setattr(server, name, {'existing': object()})
    monkeypatch.setattr(client.Client, 'invalidate', lambda *a: None)
    monkeypatch.setattr(client.Client, 'close', lambda *a: None)
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', lambda: None)
    monkeypatch.setattr(gemini, '_open_request', lambda *a, **k: pytest.fail('Invalid input must not contact Google'))
    changes = {'gemini_api_key': value}
    payload = {'group': 'ai', 'changes': changes} if route == '/api/mo/settings' else changes
    raw = json.dumps(payload).encode('utf-8')
    handler = server.Handler.__new__(server.Handler)
    handler.path = route
    handler.headers = {'Content-Length': str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    handler.authorized = lambda: True
    captured = []
    handler.send = lambda status, body, *args: captured.append((status, body))
    handler.do_POST()
    assert captured[0][0] == 400
    assert path.read_bytes() == before
    assert all('existing' in getattr(server, name) for name in sessions)


@pytest.mark.parametrize('scenario', [
    'recheck', 'gemini-clear', 'gemini-edit', 'gemini-redraw', 'gemini-delete',
    'token-clear', 'token-edit', 'token-redraw',
])
def test_confirmation_keeps_the_saved_state_and_current_work(scenario):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, scenario],
                               capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {'passed': True, 'scenario': scenario}
